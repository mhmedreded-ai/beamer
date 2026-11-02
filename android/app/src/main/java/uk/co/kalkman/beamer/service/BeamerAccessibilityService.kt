package uk.co.kalkman.beamer.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Bundle
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.chaquo.python.Python

/**
 * AccessibilityService enabling Tablet Responder Mode:
 * - Tracks virtual remote pointer position across the tablet display.
 * - Dispatches synthetic touch taps, long-presses (right-click), drags, 2-finger scroll wheels,
 *   and system navigation gestures (Mission Control -> Recents, Show Desktop -> Home, Spaces -> Swipe).
 * - Injects Unicode text into focused input fields and routes hardware keyboard events.
 */
class BeamerAccessibilityService : AccessibilityService() {

    @Volatile
    var cursorX: Float = 540f
        private set

    @Volatile
    var cursorY: Float = 400f
        private set

    override fun onServiceConnected() {
        super.onServiceConnected()
        val dm = resources.displayMetrics
        cursorX = dm.widthPixels / 2f
        cursorY = dm.heightPixels / 2f
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Tracks active window focus for text injection & full-screen hold
    }

    override fun onInterrupt() {}

    override fun onKeyEvent(event: KeyEvent): Boolean {
        // Forward hardware keyboard presses to BeamerAndroidBridge when Owner is driving a remote peer
        return try {
            val py = Python.getInstance()
            val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
            val stateJson = bridge.callAttr("get_state_json").toString()
            if (stateJson.contains("\"owner_away\": true")) {
                val keyLabel = KeyEvent.keyCodeToString(event.keyCode)
                    .removePrefix("KEYCODE_")
                    .lowercase()
                val isDown = event.action == KeyEvent.ACTION_DOWN
                bridge.callAttr("record_key_event", keyLabel, isDown)
                true
            } else {
                super.onKeyEvent(event)
            }
        } catch (_: Exception) {
            super.onKeyEvent(event)
        }
    }

    fun moveCursorBy(dx: Int, dy: Int) {
        val dm = resources.displayMetrics
        val maxW = dm.widthPixels.toFloat().coerceAtLeast(100f)
        val maxH = dm.heightPixels.toFloat().coerceAtLeast(100f)
        cursorX = (cursorX + dx).coerceIn(1f, maxW - 1f)
        cursorY = (cursorY + dy).coerceIn(1f, maxH - 1f)
    }

    fun injectMouseButton(button: String, down: Boolean) {
        if (!down) return
        when (button.lowercase()) {
            "left" -> injectTap(cursorX, cursorY, durationMs = 45L)
            "right" -> injectTap(cursorX, cursorY, durationMs = 520L) // Long-press context menu
            "middle" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            "back" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "forward" -> injectSwipe(cursorX, cursorY, (cursorX - 240f).coerceAtLeast(10f), cursorY, 100L)
        }
    }

    fun injectWheel(dx: Float, dy: Float) {
        val endX = (cursorX + dx * 6f).coerceIn(10f, resources.displayMetrics.widthPixels - 10f)
        val endY = (cursorY + dy * 6f).coerceIn(10f, resources.displayMetrics.heightPixels - 10f)
        if (kotlin.math.abs(endX - cursorX) > 2f || kotlin.math.abs(endY - cursorY) > 2f) {
            injectSwipe(cursorX, cursorY, endX, endY, durationMs = 90L)
        }
    }

    fun injectSystemGesture(kind: String, direction: String) {
        when (direction.lowercase()) {
            "up" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            "down" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "left" -> {
                val dm = resources.displayMetrics
                injectSwipe(dm.widthPixels * 0.8f, dm.heightPixels * 0.5f, dm.widthPixels * 0.2f, dm.heightPixels * 0.5f, 160L)
            }
            "right" -> {
                val dm = resources.displayMetrics
                injectSwipe(dm.widthPixels * 0.2f, dm.heightPixels * 0.5f, dm.widthPixels * 0.8f, dm.heightPixels * 0.5f, 160L)
            }
            "in", "out" -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
            else -> if (kind == "pinch") performGlobalAction(GLOBAL_ACTION_HOME)
        }
    }

    fun injectText(text: String) {
        if (text.isEmpty()) return
        val root = rootInActiveWindow ?: return
        val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return
        val currentText = focused.text?.toString() ?: ""
        val args = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                currentText + text
            )
        }
        focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    fun injectTap(x: Float, y: Float, durationMs: Long = 45L) {
        try {
            val path = Path().apply { moveTo(x, y) }
            val stroke = GestureDescription.StrokeDescription(path, 0L, durationMs.coerceAtLeast(10L))
            val gesture = GestureDescription.Builder().addStroke(stroke).build()
            dispatchGesture(gesture, null, null)
        } catch (_: Exception) {
        }
    }

    fun injectSwipe(startX: Float, startY: Float, endX: Float, endY: Float, durationMs: Long = 120L) {
        try {
            val path = Path().apply {
                moveTo(startX, startY)
                lineTo(endX, endY)
            }
            val stroke = GestureDescription.StrokeDescription(path, 0L, durationMs.coerceAtLeast(16L))
            val gesture = GestureDescription.Builder().addStroke(stroke).build()
            dispatchGesture(gesture, null, null)
        } catch (_: Exception) {
        }
    }

    override fun onDestroy() {
        if (instance === this) {
            instance = null
        }
        super.onDestroy()
    }

    companion object {
        @Volatile
        var instance: BeamerAccessibilityService? = null
            private set
    }
}
