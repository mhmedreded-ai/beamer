package uk.co.kalkman.beamer

import android.app.Application
import android.content.Context
import android.os.Build
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class BeamerApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        ensureBridgeReady(this)
    }

    companion object {
        @Volatile
        var lastStartupError: String? = null
            private set

        @Synchronized
        fun ensureBridgeReady(context: Context): Boolean {
            return try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(context.applicationContext))
                }
                val py = Python.getInstance()
                val module = py.getModule("beamer_android_bridge")
                val deviceName = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
                module.callAttr("init_bridge", context.filesDir.absolutePath, deviceName)
                lastStartupError = null
                true
            } catch (t: Throwable) {
                Log.e("BeamerApplication", "Python bridge startup error", t)
                lastStartupError = "${t.javaClass.simpleName}: ${t.message}"
                false
            }
        }
    }
}
