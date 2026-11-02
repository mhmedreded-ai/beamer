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
import uk.co.kalkman.beamer.MainActivity
import uk.co.kalkman.beamer.R

/**
 * Persistent foreground service keeping Beamer's Wire v6 TCP 24820 links and 400ms idle heartbeats
 * active while preventing Android Doze or Wi-Fi power-save modes from dropping packets.
 * Also drains remote events from BeamerAndroidBridge and dispatches them to BeamerAccessibilityService
 * and Android's ClipboardManager.
 */
class BeamerLinkService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onCreate() {
        super.onCreate()
        startForeground(NOTIFICATION_ID, buildNotification())
        acquirePowerAndWifiLocks()
        startRuntimeLoop()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_RETURN_HOME) {
            try {
                val py = Python.getInstance()
                val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
                bridge.callAttr("return_home")
            } catch (_: Exception) {
            }
        }
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // Ensure service stays alive if the user swipes away the task in tablet multi-window
        val restartIntent = Intent(applicationContext, BeamerLinkService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            applicationContext.startForegroundService(restartIntent)
        } else {
            applicationContext.startService(restartIntent)
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
        } catch (_: Exception) {
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
        } catch (_: Exception) {
        }
    }

    private fun startRuntimeLoop() {
        serviceScope.launch {
            while (isActive) {
                try {
                    val py = Python.getInstance()
                    val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
                    bridge.callAttr("tick_runtime")
                    val eventsJson = bridge.callAttr("drain_remote_events_json").toString()
                    dispatchRemoteEvents(eventsJson)
                } catch (_: Exception) {
                }
                delay(32L)
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
            .setContentText("Wire v6 encrypted link ready on TCP 24820 (Low-Latency Wi-Fi Lock)")
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
        } catch (_: Exception) {
        }
        try {
            if (wifiLock?.isHeld == true) wifiLock?.release()
        } catch (_: Exception) {
        }
        super.onDestroy()
    }

    companion object {
        private const val NOTIFICATION_ID = 24820
        const val ACTION_RETURN_HOME = "uk.co.kalkman.beamer.ACTION_RETURN_HOME"
    }
}
