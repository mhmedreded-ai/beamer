package uk.co.kalkman.beamer.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.view.WindowManager
import com.chaquo.python.Python
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONArray
import uk.co.kalkman.beamer.BeamerApplication
import uk.co.kalkman.beamer.MainActivity
import uk.co.kalkman.beamer.R

/**
 * Persistent foreground service compatible with Android 14, 15, and Android 16 (API 36).
 * - Syncs Honor MagicPad 2 display metrics (3000x1920 @ 290 DPI) with Python bridge.
 * - Runs a 120Hz (8ms) remote input event dispatcher to BeamerAccessibilityService.
 * - Provides a fallback TYPE_APPLICATION_OVERLAY cursor if AccessibilityService is not yet active.
 */
class BeamerLinkService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val mainHandler = Handler(Looper.getMainLooper())
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var multicastLock: WifiManager.MulticastLock? = null
    private var fallbackOverlayView: FloatingCursorOverlayView? = null
    private var fallbackCursorX: Float = 1500f
    private var fallbackCursorY: Float = 960f

    override fun onCreate() {
        super.onCreate()
        promoteToForegroundSafely()
        acquirePowerAndWifiLocks()
        syncScreenGeometry()
        startRuntimeLoop()
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        syncScreenGeometry()
    }

    private fun syncScreenGeometry() {
        try {
            if (BeamerApplication.ensureBridgeReady(applicationContext)) {
                val dm = resources.displayMetrics
                val w = dm.widthPixels.coerceAtLeast(320)
                val h = dm.heightPixels.coerceAtLeast(320)
                val density = dm.density.coerceAtLeast(0.5f)
                fallbackCursorX = (w / 2f)
                fallbackCursorY = (h / 2f)
                val py = Python.getInstance()
                val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
                bridge.callAttr("set_screen_geometry", w, h, density)
            }
        } catch (_: Throwable) {
        }
    }

    private fun ensureFallbackOverlay(): FloatingCursorOverlayView? {
        if (BeamerAccessibilityService.instance != null) {
            removeFallbackOverlay()
            return null
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(this)) {
            return null
        }
        fallbackOverlayView?.let { return it }
        return try {
            val wm = getSystemService(Context.WINDOW_SERVICE) as? WindowManager ?: return null
            val view = FloatingCursorOverlayView(this)
            val overlayType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            } else {
                @Suppress("DEPRECATION")
                WindowManager.LayoutParams.TYPE_PHONE
            }
            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                overlayType,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                    WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT
            ).apply {
                gravity = Gravity.TOP or Gravity.START
            }
            wm.addView(view, params)
            fallbackOverlayView = view
            view
        } catch (_: Throwable) {
            null
        }
    }

    private fun removeFallbackOverlay() {
        val view = fallbackOverlayView ?: return
        fallbackOverlayView = null
        try {
            val wm = getSystemService(Context.WINDOW_SERVICE) as? WindowManager
            wm?.removeViewImmediate(view)
        } catch (_: Throwable) {
        }
    }

    private fun updateFallbackCursor(x: Int, y: Int, visible: Boolean = true, edgeHit: String = "") {
        val dm = resources.displayMetrics
        val maxW = (dm.widthPixels - 1).coerceAtLeast(100).toFloat()
        val maxH = (dm.heightPixels - 1).coerceAtLeast(100).toFloat()
        fallbackCursorX = x.toFloat().coerceIn(0f, maxW)
        fallbackCursorY = y.toFloat().coerceIn(0f, maxH)
        mainHandler.post {
            if (!visible) {
                fallbackOverlayView?.let {
                    it.pointerVisible = false
                    it.activeEdgeHit = ""
                    it.postInvalidateOnAnimation()
                }
                return@post
            }
            val view = ensureFallbackOverlay() ?: return@post
            view.pointerX = fallbackCursorX
            view.pointerY = fallbackCursorY
            view.pointerVisible = true
            view.activeEdgeHit = edgeHit
            view.postInvalidateOnAnimation()
        }
    }

    private fun promoteToForegroundSafely() {
        try {
            val notification = buildNotification()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
                )
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
        } catch (_: Throwable) {
            // Guard against Android 14/15/16 ForegroundServiceStartNotAllowedException
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        promoteToForegroundSafely()
        syncScreenGeometry()
        if (intent?.action == ACTION_RETURN_HOME) {
            try {
                BeamerApplication.ensureBridgeReady(applicationContext)
                val py = Python.getInstance()
                val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
                bridge.callAttr("return_home")
                BeamerAccessibilityService.instance?.hideCursor()
                updateFallbackCursor(0, 0, visible = false)
            } catch (_: Throwable) {
            }
        }
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        try {
            val restartIntent = Intent(applicationContext, BeamerLinkService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                applicationContext.startForegroundService(restartIntent)
            } else {
                applicationContext.startService(restartIntent)
            }
        } catch (_: Throwable) {
        }
        super.onTaskRemoved(rootIntent)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun acquirePowerAndWifiLocks() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as? PowerManager
            wakeLock = pm?.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "Beamer::KvmBridgeWakeLock"
            )?.apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (_: Throwable) {
        }

        try {
            val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            val lockMode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                WifiManager.WIFI_MODE_FULL_LOW_LATENCY
            } else {
                @Suppress("DEPRECATION")
                WifiManager.WIFI_MODE_FULL_HIGH_PERF
            }
            wifiLock = wm?.createWifiLock(lockMode, "Beamer::KvmLowLatencyWifiLock")?.apply {
                setReferenceCounted(false)
                acquire()
            }
            // Essential for UDP 24821 Broadcast Beacons & "Press Nearby" (Easy Pair) on Android / MagicOS
            multicastLock = wm?.createMulticastLock("Beamer::DiscoveryMulticastLock")?.apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (_: Throwable) {
        }
    }

    private fun startRuntimeLoop() {
        serviceScope.launch {
            var tickCounter = 0
            while (isActive) {
                try {
                    if (BeamerApplication.ensureBridgeReady(applicationContext)) {
                        val py = Python.getInstance()
                        val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
                        if (tickCounter % 10 == 0) {
                            val dm = resources.displayMetrics
                            bridge.callAttr("set_screen_geometry", dm.widthPixels, dm.heightPixels, dm.density)
                            bridge.callAttr("tick_runtime")
                        }
                        tickCounter++
                        val eventsJson = bridge.callAttr("drain_remote_events_json").toString()
                        dispatchRemoteEvents(eventsJson)
                    }
                } catch (_: Throwable) {
                }
                delay(8L)
            }
        }
    }

    private fun dispatchRemoteEvents(eventsJson: String) {
        if (eventsJson.isEmpty() || eventsJson == "[]") return
        val array = JSONArray(eventsJson)
        val a11y = BeamerAccessibilityService.instance

        var accumDx = 0
        var accumDy = 0
        var lastMoveX = -1
        var lastMoveY = -1
        var hasMove = false

        fun flushPendingMove() {
            if (!hasMove) return
            if (a11y != null) {
                a11y.moveCursorBy(accumDx, accumDy, lastMoveX, lastMoveY)
            } else if (lastMoveX >= 0 && lastMoveY >= 0) {
                updateFallbackCursor(lastMoveX, lastMoveY, visible = true)
            }
            accumDx = 0
            accumDy = 0
            lastMoveX = -1
            lastMoveY = -1
            hasMove = false
        }

        for (i in 0 until array.length()) {
            val item = array.getJSONObject(i)
            when (item.optString("kind")) {
                "move" -> {
                    accumDx += item.optInt("dx", 0)
                    accumDy += item.optInt("dy", 0)
                    if (item.has("x")) lastMoveX = item.optInt("x", -1)
                    if (item.has("y")) lastMoveY = item.optInt("y", -1)
                    hasMove = true
                }
                "cursor_pos", "arrival" -> {
                    flushPendingMove()
                    val x = item.optInt("x", 1500)
                    val y = item.optInt("y", 960)
                    val edge = item.optString("edge", "")
                    if (a11y != null) {
                        a11y.setCursorPosition(x, y, edge)
                    } else {
                        updateFallbackCursor(x, y, visible = true, edgeHit = edge)
                    }
                }
                "cursor_hide", "switch_host" -> {
                    flushPendingMove()
                    a11y?.hideCursor()
                    updateFallbackCursor(0, 0, visible = false)
                }
                "button" -> {
                    flushPendingMove()
                    a11y?.injectMouseButton(
                        item.optString("button", "left"),
                        item.optBoolean("down", true)
                    )
                }
                "wheel" -> {
                    flushPendingMove()
                    a11y?.injectWheel(
                        item.optDouble("dx", 0.0).toFloat(),
                        item.optDouble("dy", 0.0).toFloat()
                    )
                }
                "key" -> {
                    flushPendingMove()
                    a11y?.injectKeyEvent(
                        item.optString("key", ""),
                        item.optBoolean("down", true),
                        item.optString("us", "")
                    )
                }
                "gesture" -> {
                    flushPendingMove()
                    a11y?.injectSystemGesture(
                        item.optString("gesture", "swipe"),
                        item.optString("direction", "up")
                    )
                }
                "text" -> {
                    flushPendingMove()
                    a11y?.injectText(item.optString("text", ""))
                }
                "clipboard" -> {
                    val textVal = item.optString("text", "")
                    if (textVal.isNotEmpty()) {
                        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                        cm?.setPrimaryClip(ClipData.newPlainText("Beamer", textVal))
                    }
                }
            }
        }
        flushPendingMove()
    }

    private fun buildNotification(): Notification {
        val channelId = "beamer_kvm_link_channel"
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                channelId,
                "Beamer KVM Link",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Keeps Beamer's encrypted Wire v6 link active on the local network."
            }
            manager.createNotificationChannel(channel)
        }

        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val homeIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, BeamerLinkService::class.java).apply { action = ACTION_RETURN_HOME },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val actionReturnHome = Notification.Action.Builder(
            R.drawable.ic_beamer_logo,
            "Return Input Home",
            homeIntent
        ).build()

        return Notification.Builder(this, channelId)
            .setContentTitle("Beamer KVM Active")
            .setContentText("Wire v6 encrypted link ready on TCP 24820")
            .setSmallIcon(R.drawable.ic_beamer_logo)
            .setContentIntent(openIntent)
            .addAction(actionReturnHome)
            .setOngoing(true)
            .build()
    }

    override fun onDestroy() {
        serviceScope.cancel()
        mainHandler.post { removeFallbackOverlay() }
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (_: Throwable) {
        }
        try {
            if (wifiLock?.isHeld == true) wifiLock?.release()
        } catch (_: Throwable) {
        }
        try {
            if (multicastLock?.isHeld == true) multicastLock?.release()
        } catch (_: Throwable) {
        }
        super.onDestroy()
    }

    companion object {
        private const val NOTIFICATION_ID = 24820
        const val ACTION_RETURN_HOME = "uk.co.kalkman.beamer.ACTION_RETURN_HOME"
    }
}
