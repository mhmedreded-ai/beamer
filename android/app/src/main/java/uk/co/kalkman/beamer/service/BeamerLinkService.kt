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
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
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
 * Uses SPECIAL_USE foreground service type with full exception guarding so Android 16 never
 * terminates the app process if background/notification restrictions are active.
 */
class BeamerLinkService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var multicastLock: WifiManager.MulticastLock? = null

    override fun onCreate() {
        super.onCreate()
        promoteToForegroundSafely()
        acquirePowerAndWifiLocks()
        startRuntimeLoop()
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
        if (intent?.action == ACTION_RETURN_HOME) {
            try {
                BeamerApplication.ensureBridgeReady(applicationContext)
                val py = Python.getInstance()
                val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
                bridge.callAttr("return_home")
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
            while (isActive) {
                try {
                    if (BeamerApplication.ensureBridgeReady(applicationContext)) {
                        val py = Python.getInstance()
                        val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
                        bridge.callAttr("tick_runtime")
                        val eventsJson = bridge.callAttr("drain_remote_events_json").toString()
                        dispatchRemoteEvents(eventsJson)
                    }
                } catch (_: Throwable) {
                }
                delay(40L)
            }
        }
    }

    private fun dispatchRemoteEvents(eventsJson: String) {
        if (eventsJson.isEmpty() || eventsJson == "[]") return
        val array = JSONArray(eventsJson)
        val a11y = BeamerAccessibilityService.instance
        for (i in 0 until array.length()) {
            val item = array.getJSONObject(i)
            when (item.optString("kind")) {
                "move" -> a11y?.moveCursorBy(item.optInt("dx", 0), item.optInt("dy", 0))
                "button" -> a11y?.injectMouseButton(
                    item.optString("button", "left"),
                    item.optBoolean("down", true)
                )
                "wheel" -> a11y?.injectWheel(
                    item.optDouble("dx", 0.0).toFloat(),
                    item.optDouble("dy", 0.0).toFloat()
                )
                "gesture" -> a11y?.injectSystemGesture(
                    item.optString("gesture", "swipe"),
                    item.optString("direction", "up")
                )
                "text" -> a11y?.injectText(item.optString("text", ""))
                "clipboard" -> {
                    val textVal = item.optString("text", "")
                    if (textVal.isNotEmpty()) {
                        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                        cm?.setPrimaryClip(ClipData.newPlainText("Beamer", textVal))
                    }
                }
            }
        }
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
