package uk.co.kalkman.beamer.permissions

import android.Manifest
import android.accessibilityservice.AccessibilityServiceInfo
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import uk.co.kalkman.beamer.service.BeamerAccessibilityService

data class AndroidPermissionSnapshot(
    val notificationsGranted: Boolean,
    val cameraGranted: Boolean,
    val overlayGranted: Boolean,
    val accessibilityEnabled: Boolean,
    val batteryOptimizationIgnored: Boolean,
    val userNotice: String? = null,
) {
    val canInitiate: Boolean get() = notificationsGranted
    val canRespondWithVisuals: Boolean
        get() = notificationsGranted && overlayGranted && accessibilityEnabled
}

class BeamerPermissionManager(private val activity: ComponentActivity) {

    private var lastNotice: String? = null
    private val _state = MutableStateFlow(evaluate(activity, null))
    val state: StateFlow<AndroidPermissionSnapshot> = _state

    private val runtimeLauncher = activity.registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        val denied = results.filterValues { !it }.keys
        lastNotice = if (denied.isNotEmpty()) {
            "Some optional runtime permissions were declined (${denied.joinToString { it.substringAfterLast('.') }}). " +
                "Beamer continues running safely; you can grant them anytime from Settings."
        } else {
            null
        }
        refresh()
    }

    private val settingsLauncher = activity.registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        refresh()
    }

    fun refresh() {
        _state.value = evaluate(activity, lastNotice)
    }

    fun dismissNotice() {
        lastNotice = null
        refresh()
    }

    fun requestStandardPermissions(includeCamera: Boolean = true) {
        try {
            val perms = mutableListOf<String>()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                perms += Manifest.permission.POST_NOTIFICATIONS
            }
            if (includeCamera) {
                perms += Manifest.permission.CAMERA
            }
            if (perms.isNotEmpty()) {
                runtimeLauncher.launch(perms.toTypedArray())
            } else {
                refresh()
            }
        } catch (e: Exception) {
            lastNotice = "Could not launch permission dialog: ${e.localizedMessage ?: "unknown error"}"
            refresh()
        }
    }

    fun requestOverlayPermission() {
        if (Settings.canDrawOverlays(activity)) {
            refresh()
            return
        }
        val pkgUri = Uri.parse("package:${activity.packageName}")
        val candidates = listOf(
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, pkgUri),
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION),
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkgUri),
        )
        launchFirstAvailableIntent(
            candidates,
            fallbackNotice = "Overlay settings screen is unavailable on this device ROM. Edge effects will stay inside the app window."
        )
    }

    fun openAccessibilitySettings() {
        val pkgUri = Uri.parse("package:${activity.packageName}")
        val candidates = listOf(
            Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS),
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkgUri),
            Intent(Settings.ACTION_SETTINGS),
        )
        launchFirstAvailableIntent(
            candidates,
            fallbackNotice = "Could not open Accessibility Settings automatically. Please enable 'Beamer Input Bridge' in System Settings -> Accessibility."
        )
    }

    @SuppressLint("BatteryLife")
    fun requestIgnoreBatteryOptimizations() {
        if (isIgnoringBatteryOptimizations(activity)) {
            refresh()
            return
        }
        val pkgUri = Uri.parse("package:${activity.packageName}")
        val honorStartupIntent = Intent().apply {
            setClassName(
                "com.hihonor.systemmanager",
                "com.hihonor.systemmanager.startupmgr.ui.StartupNormalAppListActivity"
            )
        }
        val candidates = listOf(
            Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, pkgUri),
            honorStartupIntent,
            Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS),
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkgUri),
        )
        launchFirstAvailableIntent(
            candidates,
            fallbackNotice = "Battery optimization settings could not be opened automatically. Set Beamer to 'Unrestricted' in App Info -> Battery."
        )
    }

    fun openAppDetailsSettings() {
        val pkgUri = Uri.parse("package:${activity.packageName}")
        val candidates = listOf(
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkgUri),
            Intent(Settings.ACTION_MANAGE_APPLICATIONS_SETTINGS),
            Intent(Settings.ACTION_SETTINGS),
        )
        launchFirstAvailableIntent(
            candidates,
            fallbackNotice = "Please open System Settings -> Apps -> Beamer -> (⋮) -> Allow restricted settings."
        )
    }

    private fun launchFirstAvailableIntent(candidates: List<Intent>, fallbackNotice: String) {
        for (intent in candidates) {
            try {
                settingsLauncher.launch(intent)
                return
            } catch (_: Throwable) {
                // Try next fallback intent
            }
        }
        lastNotice = fallbackNotice
        refresh()
    }

    companion object {
        fun evaluate(context: Context, notice: String? = null): AndroidPermissionSnapshot {
            val notifications = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                ContextCompat.checkSelfPermission(
                    context, Manifest.permission.POST_NOTIFICATIONS
                ) == PackageManager.PERMISSION_GRANTED
            } else true

            val camera = ContextCompat.checkSelfPermission(
                context, Manifest.permission.CAMERA
            ) == PackageManager.PERMISSION_GRANTED

            val overlay = try {
                Settings.canDrawOverlays(context)
            } catch (_: Exception) {
                false
            }

            val accessibility = isAccessibilityServiceEnabled(context)
            val batteryIgnored = isIgnoringBatteryOptimizations(context)

            return AndroidPermissionSnapshot(
                notificationsGranted = notifications,
                cameraGranted = camera,
                overlayGranted = overlay,
                accessibilityEnabled = accessibility,
                batteryOptimizationIgnored = batteryIgnored,
                userNotice = notice,
            )
        }

        private fun isIgnoringBatteryOptimizations(context: Context): Boolean {
            return try {
                val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
                pm?.isIgnoringBatteryOptimizations(context.packageName) ?: false
            } catch (_: Exception) {
                false
            }
        }

        private fun isAccessibilityServiceEnabled(context: Context): Boolean {
            return try {
                val am = context.getSystemService(Context.ACCESSIBILITY_SERVICE) as? AccessibilityManager
                    ?: return false
                val enabledServices = am.getEnabledAccessibilityServiceList(
                    AccessibilityServiceInfo.FEEDBACK_ALL_MASK
                )
                val expectedId = "${context.packageName}/${BeamerAccessibilityService::class.java.canonicalName}"
                val shortId = "${context.packageName}/.service.${BeamerAccessibilityService::class.java.simpleName}"
                enabledServices.any { it.id == expectedId || it.id == shortId }
            } catch (_: Exception) {
                false
            }
        }
    }
}
