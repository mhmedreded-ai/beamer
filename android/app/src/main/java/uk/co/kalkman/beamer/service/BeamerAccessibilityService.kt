package uk.co.kalkman.beamer.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.chaquo.python.Python
import kotlin.math.abs

/**
 * Full-screen hardware-accelerated pass-through overlay that renders the remote PC mouse cursor
 * and edge glow on Honor MagicPad 2 (3000x1920 @ 290 DPI) & Android tablets at 120Hz without
 * WindowManager IPC overhead on each motion packet.
 */
class FloatingCursorOverlayView(context: Context) : View(context) {

    @Volatile
    var pointerX: Float = 1500f

    @Volatile
    var pointerY: Float = 960f

    @Volatile
    var pointerVisible: Boolean = false

    @Volatile
    var buttonPressed: Boolean = false

    @Volatile
    var activeEdgeHit: String = ""

    private val cursorPath = Path()
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.WHITE
    }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3.2f
        strokeJoin = Paint.Join.ROUND
        color = Color.parseColor("#0D131E")
    }
    private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 3.5f
        color = Color.parseColor("#AA00E5FF")
    }
    private val clickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.parseColor("#5500E5FF")
    }
    private val edgeGlowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.parseColor("#8800E5FF")
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (!pointerVisible) return

        val x = pointerX
        val y = pointerY
        val density = resources.displayMetrics.density.coerceAtLeast(1f)
        val scale = (density / 1.75f).coerceIn(1.0f, 2.2f)

        // Draw subtle edge return indicator bar when at screen boundary
        val w = width.toFloat()
        val h = height.toFloat()
        val barThickness = 6f * scale
        when (activeEdgeHit) {
            "left" -> canvas.drawRect(0f, (y - 90f * scale).coerceAtLeast(0f), barThickness, (y + 90f * scale).coerceAtMost(h), edgeGlowPaint)
            "right" -> canvas.drawRect((w - barThickness).coerceAtLeast(0f), (y - 90f * scale).coerceAtLeast(0f), w, (y + 90f * scale).coerceAtMost(h), edgeGlowPaint)
            "top" -> canvas.drawRect((x - 90f * scale).coerceAtLeast(0f), 0f, (x + 90f * scale).coerceAtMost(w), barThickness, edgeGlowPaint)
            "bottom" -> canvas.drawRect((x - 90f * scale).coerceAtLeast(0f), (h - barThickness).coerceAtLeast(0f), (x + 90f * scale).coerceAtMost(w), h, edgeGlowPaint)
        }

        // Draw click/hover halo
        if (buttonPressed) {
            canvas.drawCircle(x, y, 22f * scale, clickPaint)
        }
        canvas.drawCircle(x, y, 14f * scale, glowPaint)

        // Draw crisp desktop pointer arrow
        cursorPath.reset()
        cursorPath.moveTo(x, y)
        cursorPath.lineTo(x, y + 28f * scale)
        cursorPath.lineTo(x + 7.5f * scale, y + 21.5f * scale)
        cursorPath.lineTo(x + 13.5f * scale, y + 32f * scale)
        cursorPath.lineTo(x + 18f * scale, y + 29.5f * scale)
        cursorPath.lineTo(x + 12f * scale, y + 19f * scale)
        cursorPath.lineTo(x + 21f * scale, y + 19f * scale)
        cursorPath.close()

        canvas.drawPath(cursorPath, fillPaint)
        canvas.drawPath(cursorPath, strokePaint)
    }
}

/**
 * AccessibilityService enabling Tablet Responder Mode on Honor MagicPad 2 (3000x1920 @ 290 DPI):
 * - Renders a real-time floating remote pointer overlay across all apps.
 * - Tracks virtual remote pointer position and detects boundary return-to-host crossings.
 * - Dispatches synthetic touch taps, long-presses (right-click), drags, scroll wheels,
 *   and system navigation gestures.
 * - Injects keyboard keys, special navigation keys, and Unicode text into focused input fields.
 */
class BeamerAccessibilityService : AccessibilityService() {

    private val mainHandler = Handler(Looper.getMainLooper())
    private var windowManager: WindowManager? = null
    private var overlayView: FloatingCursorOverlayView? = null

    @Volatile
    var cursorX: Float = 1500f
        private set

    @Volatile
    var cursorY: Float = 960f
        private set

    private val autoHideRunnable = Runnable {
        overlayView?.let {
            it.pointerVisible = false
            it.activeEdgeHit = ""
            it.postInvalidateOnAnimation()
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        syncDisplayMetricsToBridge()
        mainHandler.post { ensureOverlayAttached() }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        syncDisplayMetricsToBridge()
    }

    fun syncDisplayMetricsToBridge() {
        val dm = resources.displayMetrics
        val w = dm.widthPixels.coerceAtLeast(320)
        val h = dm.heightPixels.coerceAtLeast(320)
        val density = dm.density.coerceAtLeast(0.5f)
        cursorX = cursorX.coerceIn(0f, (w - 1).toFloat())
        cursorY = cursorY.coerceIn(0f, (h - 1).toFloat())
        try {
            if (Python.isStarted()) {
                val py = Python.getInstance()
                val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
                bridge.callAttr("set_screen_geometry", w, h, density)
            }
        } catch (_: Throwable) {
        }
    }

    private fun ensureOverlayAttached() {
        if (overlayView != null) return
        try {
            val wm = getSystemService(Context.WINDOW_SERVICE) as? WindowManager ?: return
            windowManager = wm
            val view = FloatingCursorOverlayView(this)
            val overlayType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP_MR1) {
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && Settings.canDrawOverlays(this)) {
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            } else {
                @Suppress("DEPRECATION")
                WindowManager.LayoutParams.TYPE_SYSTEM_OVERLAY
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
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    layoutInDisplayCutoutMode =
                        WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                }
            }
            wm.addView(view, params)
            overlayView = view
        } catch (_: Throwable) {
        }
    }

    private fun removeOverlay() {
        val view = overlayView ?: return
        overlayView = null
        try {
            windowManager?.removeViewImmediate(view)
        } catch (_: Throwable) {
        }
    }

    private fun scheduleCursorRefresh(visible: Boolean = true, edgeHit: String = "") {
        mainHandler.post {
            ensureOverlayAttached()
            overlayView?.let { view ->
                view.pointerX = cursorX
                view.pointerY = cursorY
                view.pointerVisible = visible
                view.activeEdgeHit = edgeHit
                view.postInvalidateOnAnimation()
            }
            mainHandler.removeCallbacks(autoHideRunnable)
            if (visible) {
                mainHandler.postDelayed(autoHideRunnable, 15_000L)
            }
        }
    }

    fun hideCursor() {
        mainHandler.removeCallbacks(autoHideRunnable)
        scheduleCursorRefresh(visible = false, edgeHit = "")
    }

    fun setCursorPosition(x: Int, y: Int, edge: String = "") {
        val dm = resources.displayMetrics
        val maxW = (dm.widthPixels - 1).coerceAtLeast(100).toFloat()
        val maxH = (dm.heightPixels - 1).coerceAtLeast(100).toFloat()
        cursorX = x.toFloat().coerceIn(0f, maxW)
        cursorY = y.toFloat().coerceIn(0f, maxH)
        scheduleCursorRefresh(visible = true, edgeHit = edge)
    }

    fun moveCursorBy(dx: Int, dy: Int, absX: Int = -1, absY: Int = -1) {
        val dm = resources.displayMetrics
        val maxW = (dm.widthPixels - 1).coerceAtLeast(100).toFloat()
        val maxH = (dm.heightPixels - 1).coerceAtLeast(100).toFloat()

        val rawX = if (absX >= 0) absX.toFloat() else (cursorX + dx)
        val rawY = if (absY >= 0) absY.toFloat() else (cursorY + dy)
        cursorX = rawX.coerceIn(0f, maxW)
        cursorY = rawY.coerceIn(0f, maxH)

        var hitEdge = ""
        var offset = 0.5f
        if (cursorX <= 1f && dx < 0) {
            hitEdge = "left"
            offset = (cursorY / maxH).coerceIn(0f, 1f)
        } else if (cursorX >= maxW - 1f && dx > 0) {
            hitEdge = "right"
            offset = (cursorY / maxH).coerceIn(0f, 1f)
        } else if (cursorY <= 1f && dy < 0) {
            hitEdge = "top"
            offset = (cursorX / maxW).coerceIn(0f, 1f)
        } else if (cursorY >= maxH - 1f && dy > 0) {
            hitEdge = "bottom"
            offset = (cursorX / maxW).coerceIn(0f, 1f)
        }

        scheduleCursorRefresh(visible = true, edgeHit = hitEdge)

        if (hitEdge.isNotEmpty()) {
            try {
                if (Python.isStarted()) {
                    val py = Python.getInstance()
                    val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
                    bridge.callAttr("on_cursor_edge_hit", hitEdge, offset.toDouble())
                }
            } catch (_: Throwable) {
            }
        }
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

    fun injectMouseButton(button: String, down: Boolean) {
        overlayView?.let {
            it.buttonPressed = down
            it.pointerVisible = true
            it.postInvalidateOnAnimation()
        }
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
        val maxX = (resources.displayMetrics.widthPixels - 10f).coerceAtLeast(10f)
        val maxY = (resources.displayMetrics.heightPixels - 10f).coerceAtLeast(10f)
        // Natural touch scroll on tablet when wheel scrolls
        val deltaX = (dx * 18f).coerceIn(-600f, 600f)
        val deltaY = (dy * 18f).coerceIn(-600f, 600f)
        val endX = (cursorX + deltaX).coerceIn(10f, maxX)
        val endY = (cursorY + deltaY).coerceIn(10f, maxY)
        if (abs(endX - cursorX) > 2f || abs(endY - cursorY) > 2f) {
            injectSwipe(cursorX, cursorY, endX, endY, durationMs = 85L)
        }
    }

    fun injectKeyEvent(key: String, down: Boolean, us: String = "") {
        if (!down) return
        val norm = key.lowercase().trim()
        when (norm) {
            "enter", "numpad_enter", "return" -> {
                val focused = findFocusedInputNode()
                if (focused != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    val sent = focused.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
                    if (!sent) injectText("\n")
                } else {
                    injectText("\n")
                }
            }
            "backspace", "delete" -> deleteFromFocusedInput()
            "space" -> injectText(" ")
            "tab" -> injectText("\t")
            "esc", "escape" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "browser_home" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "up" -> injectSwipe(cursorX, cursorY, cursorX, (cursorY + 220f).coerceAtMost(resources.displayMetrics.heightPixels - 10f), 90L)
            "down" -> injectSwipe(cursorX, cursorY, cursorX, (cursorY - 220f).coerceAtLeast(10f), 90L)
            "left" -> injectSwipe(cursorX, cursorY, (cursorX + 220f).coerceAtMost(resources.displayMetrics.widthPixels - 10f), cursorY, 90L)
            "right" -> injectSwipe(cursorX, cursorY, (cursorX - 220f).coerceAtLeast(10f), cursorY, 90L)
            else -> {
                val printable = when {
                    us.length == 1 -> us
                    key.length == 1 -> key
                    else -> ""
                }
                if (printable.isNotEmpty()) {
                    injectText(printable)
                }
            }
        }
    }

    private fun findFocusedInputNode(): AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        return root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?: root.findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY)
    }

    private fun deleteFromFocusedInput() {
        val focused = findFocusedInputNode() ?: return
        val currentText = focused.text?.toString() ?: ""
        if (currentText.isEmpty()) return
        val updated = currentText.dropLast(1)
        val args = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                updated
            )
        }
        focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
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
        val focused = findFocusedInputNode() ?: return
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
        mainHandler.removeCallbacksAndMessages(null)
        removeOverlay()
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
