package uk.co.kalkman.beamer

import android.app.Application
import android.os.Build
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class BeamerApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        val py = Python.getInstance()
        val module = py.getModule("beamer_android_bridge")
        val deviceName = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
        module.callAttr("init_bridge", filesDir.absolutePath, deviceName)
    }
}

