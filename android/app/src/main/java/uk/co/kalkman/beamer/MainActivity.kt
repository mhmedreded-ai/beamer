package uk.co.kalkman.beamer

import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chaquo.python.Python
import kotlin.math.hypot
import org.json.JSONObject
import uk.co.kalkman.beamer.permissions.AndroidPermissionSnapshot
import uk.co.kalkman.beamer.permissions.BeamerPermissionManager
import uk.co.kalkman.beamer.service.BeamerLinkService

/**
 * High-precision DPI-aware & velocity-accelerated motion accumulator for Android Tablets.
 * - Converts raw touch/mouse hardware pixel deltas into WIRE.md logical pixels (dp).
 * - Applies a smooth velocity acceleration curve scaled by the user's pointerSpeed (25% - 400%).
 * - Preserves sub-pixel fractional remainders across frames so fine diagonal movements never stall.
 */
class LogicalMotionAccumulator(
    private val density: Float,
    var pointerSpeed: Float = 1.0f,
) {
    private var remX = 0f
    private var remY = 0f

    fun toWireDelta(rawPxX: Float, rawPxY: Float): Pair<Int, Int> {
        val safeDensity = density.coerceAtLeast(1f)
        val dpX = rawPxX / safeDensity
        val dpY = rawPxY / safeDensity
        val magnitude = hypot(dpX, dpY)
        // Smooth acceleration curve (1.0x for fine precision, up to 1.85x for fast sweeps)
        val accel = (1.0f + (magnitude / 28.0f).coerceAtMost(0.85f)) * pointerSpeed.coerceIn(0.25f, 4.0f)

        val totalX = (dpX * accel) + remX
        val totalY = (dpY * accel) + remY
        val wireDx = totalX.toInt().coerceIn(-65535, 65535)
        val wireDy = totalY.toInt().coerceIn(-65535, 65535)
        remX = totalX - wireDx
        remY = totalY - wireDy
        return wireDx to wireDy
    }

    fun reset() {
        remX = 0f
        remY = 0f
    }
}

data class PeerItem(
    val id: String,
    val name: String,
    val platform: String,
    val host: String,
    val port: Int,
    val side: String,
)

data class CoreUiState(
    val wireVersion: Int = 6,
    val machineId: String = "",
    val deviceName: String = "Android Tablet",
    val port: Int = 24820,
    val pairingPort: Int = 24821,
    val resistancePx: Int = 120,
    val pointerSpeed: Float = 1.0f,
    val scrollSpeed: Float = 1.0f,
    val reverseScroll: Boolean = false,
    val sameOnAll: Boolean = true,
    val effectStyle: String = "glow",
    val responderEnabled: Boolean = true,
    val ownerOn: String? = null,
    val edgePressure: Float = 0f,
    val activeEdge: String = "right",
    val pairingCode: String = "",
    val lastEvent: String = "Ready",
    val peers: List<PeerItem> = emptyList(),
    val availableStyles: List<String> = listOf("glow", "beam", "aperture"),
)

enum class BeamerPage(val key: String, val title: String, val badge: String) {
    OVERVIEW("overview", "Overview", "OV"),
    PERMISSIONS("permissions", "Permissions", "PM"),
    CROSSING("crossing", "Crossing", "CR"),
    KEYBOARD("keyboard", "Keyboard", "KB"),
    DESIGN("design", "Design", "DS"),
    CONNECTION("connection", "Connection", "CN"),
}

private val BeamerDarkPalette = darkColorScheme(
    primary = Color(0xFF5FD4F4),
    secondary = Color(0xFF9CE3F8),
    background = Color(0xFF161714),
    surface = Color(0xFF21221E),
    onPrimary = Color(0xFF0E0F0D),
    onBackground = Color(0xFFEFEEE6),
    onSurface = Color(0xFFEFEEE6),
)

class MainActivity : ComponentActivity() {

    private lateinit var permissionManager: BeamerPermissionManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        permissionManager = BeamerPermissionManager(this)
        startBeamerForegroundService()

        setContent {
            MaterialTheme(colorScheme = BeamerDarkPalette) {
                val permSnapshot by permissionManager.state.collectAsState()
                var coreState by remember { mutableStateOf(loadCoreState()) }

                BeamerAdaptiveShell(
                    coreState = coreState,
                    permSnapshot = permSnapshot,
                    onRefreshState = { json -> coreState = parseCoreState(json) },
                    onDismissNotice = { permissionManager.dismissNotice() },
                    onRequestStandardPerms = { permissionManager.requestStandardPermissions(includeCamera = true) },
                    onRequestOverlay = { permissionManager.requestOverlayPermission() },
                    onOpenAccessibility = { permissionManager.openAccessibilitySettings() },
                    onRequestBatteryExemption = { permissionManager.requestIgnoreBatteryOptimizations() },
                    onStartLinkService = { startBeamerForegroundService() },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        permissionManager.refresh()
    }

    private fun startBeamerForegroundService() {
        try {
            val serviceIntent = Intent(this, BeamerLinkService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(serviceIntent)
            } else {
                startService(serviceIntent)
            }
        } catch (_: Exception) {
            // Avoid crash if background start is restricted before user grants notification permission
        }
    }

    private fun loadCoreState(): CoreUiState {
        return try {
            val py = Python.getInstance()
            val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
            val json = bridge.callAttr("get_state_json").toString()
            parseCoreState(json)
        } catch (e: Exception) {
            CoreUiState(lastEvent = "Python core init: ${e.message}")
        }
    }

    private fun parseCoreState(jsonString: String): CoreUiState {
        return try {
            val root = JSONObject(jsonString)
            val peersArray = root.optJSONArray("peers")
            val peers = mutableListOf<PeerItem>()
            if (peersArray != null) {
                for (i in 0 until peersArray.length()) {
                    val item = peersArray.getJSONObject(i)
                    peers += PeerItem(
                        id = item.optString("id"),
                        name = item.optString("name", "Desktop"),
                        platform = item.optString("platform", "windows"),
                        host = item.optString("host", ""),
                        port = item.optInt("port", 24820),
                        side = item.optString("side", "right"),
                    )
                }
            }
            val stylesArray = root.optJSONArray("available_styles")
            val styles = mutableListOf<String>()
            if (stylesArray != null) {
                for (i in 0 until stylesArray.length()) {
                    styles += stylesArray.getString(i)
                }
            }
            val rawOwner = if (root.isNull("owner_on")) null else root.optString("owner_on")
            CoreUiState(
                wireVersion = root.optInt("wire_version", 6),
                machineId = root.optString("machine_id", ""),
                deviceName = root.optString("name", "Android Tablet"),
                port = root.optInt("port", 24820),
                pairingPort = root.optInt("pairing_port", 24821),
                resistancePx = root.optInt("resistance_px", 120),
                pointerSpeed = root.optDouble("pointer_speed", 1.0).toFloat(),
                scrollSpeed = root.optDouble("scroll_speed", 1.0).toFloat(),
                reverseScroll = root.optBoolean("reverse_scroll", false),
                sameOnAll = root.optBoolean("same_on_all", true),
                effectStyle = root.optString("effect_style", "glow"),
                responderEnabled = root.optBoolean("responder_enabled", true),
                ownerOn = rawOwner?.takeIf { it.isNotEmpty() },
                edgePressure = root.optDouble("edge_pressure", 0.0).toFloat(),
                activeEdge = root.optString("active_edge", "right"),
                pairingCode = root.optString("pairing_code", ""),
                lastEvent = root.optString("last_event", "Ready"),
                peers = peers,
                availableStyles = if (styles.isNotEmpty()) styles else listOf("glow", "beam", "aperture"),
            )
        } catch (e: Exception) {
            CoreUiState(lastEvent = "Parse error: ${e.message}")
        }
    }
}

private fun callBridge(method: String, vararg args: Any): String {
    return try {
        val py = Python.getInstance()
        val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
        bridge.callAttr(method, *args).toString()
    } catch (e: Exception) {
        "{}"
    }
}

@Composable
fun BeamerAdaptiveShell(
    coreState: CoreUiState,
    permSnapshot: AndroidPermissionSnapshot,
    onRefreshState: (String) -> Unit,
    onDismissNotice: () -> Unit,
    onRequestStandardPerms: () -> Unit,
    onRequestOverlay: () -> Unit,
    onOpenAccessibility: () -> Unit,
    onRequestBatteryExemption: () -> Unit,
    onStartLinkService: () -> Unit,
) {
    val config = LocalConfiguration.current
    // Three-tier responsive breakpoints for Tablet Fullscreen (>= 840dp), Tablet Split-Screen (>= 600dp), and Compact (< 600dp)
    val isExpandedTablet = config.screenWidthDp >= 840
    val isMediumSplitTablet = config.screenWidthDp in 600..839
    val useTwoPane = isExpandedTablet || (isMediumSplitTablet && config.screenWidthDp > config.screenHeightDp)

    var currentPage by remember {
        mutableStateOf(
            if (permSnapshot.canRespondWithVisuals) BeamerPage.OVERVIEW else BeamerPage.PERMISSIONS
        )
    }

    if (useTwoPane || isMediumSplitTablet) {
        Row(
            modifier = Modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.background)
        ) {
            NavigationRail(
                containerColor = Color(0xFF1B1C18),
                modifier = Modifier.fillMaxHeight()
            ) {
                Spacer(modifier = Modifier.height(14.dp))
                Text(
                    text = "BEAMER",
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold,
                    fontSize = 11.sp,
                    modifier = Modifier.padding(bottom = 10.dp)
                )
                BeamerPage.entries.forEach { page ->
                    val needsAttention = page == BeamerPage.PERMISSIONS && !permSnapshot.canRespondWithVisuals
                    NavigationRailItem(
                        selected = currentPage == page,
                        onClick = { currentPage = page },
                        icon = {
                            Box(
                                contentAlignment = Alignment.Center,
                                modifier = Modifier
                                    .size(32.dp)
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(
                                        if (currentPage == page) Color(0xFF2E383B) else Color.Transparent
                                    )
                            ) {
                                Text(
                                    text = if (needsAttention) "${page.badge}•" else page.badge,
                                    color = if (needsAttention) Color(0xFFF3B452) else MaterialTheme.colorScheme.onSurface,
                                    fontWeight = FontWeight.SemiBold,
                                    fontSize = 12.sp
                                )
                            }
                        },
                        label = { Text(page.title, fontSize = 11.sp) }
                    )
                }
            }

            if (useTwoPane) {
                // Full Tablet Landscape / Expanded: Dual-pane side-by-side Settings + Interactive Trackpad
                Column(
                    modifier = Modifier
                        .weight(0.50f)
                        .fillMaxHeight()
                        .verticalScroll(rememberScrollState())
                        .padding(20.dp)
                ) {
                    PermissionWarningBanner(permSnapshot.userNotice, onDismissNotice)
                    PageContent(
                        page = currentPage,
                        coreState = coreState,
                        permSnapshot = permSnapshot,
                        onRefreshState = onRefreshState,
                        onRequestStandardPerms = onRequestStandardPerms,
                        onRequestOverlay = onRequestOverlay,
                        onOpenAccessibility = onOpenAccessibility,
                        onRequestBatteryExemption = onRequestBatteryExemption,
                        onStartLinkService = onStartLinkService,
                    )
                }

                Column(
                    modifier = Modifier
                        .weight(0.50f)
                        .fillMaxHeight()
                        .padding(top = 20.dp, end = 20.dp, bottom = 20.dp)
                ) {
                    TabletTrackpadAndModifierSurface(
                        coreState = coreState,
                        onRefreshState = onRefreshState,
                    )
                }
            } else {
                // Medium Tablet Portrait / Split-Screen: NavigationRail + Stacked Scrollable Content & Trackpad
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxHeight()
                        .verticalScroll(rememberScrollState())
                        .padding(18.dp)
                ) {
                    PermissionWarningBanner(permSnapshot.userNotice, onDismissNotice)
                    PageContent(
                        page = currentPage,
                        coreState = coreState,
                        permSnapshot = permSnapshot,
                        onRefreshState = onRefreshState,
                        onRequestStandardPerms = onRequestStandardPerms,
                        onRequestOverlay = onRequestOverlay,
                        onOpenAccessibility = onOpenAccessibility,
                        onRequestBatteryExemption = onRequestBatteryExemption,
                        onStartLinkService = onStartLinkService,
                    )
                    Spacer(modifier = Modifier.height(16.dp))
                    Box(modifier = Modifier.height(380.dp)) {
                        TabletTrackpadAndModifierSurface(
                            coreState = coreState,
                            onRefreshState = onRefreshState,
                        )
                    }
                }
            }
        }
    } else {
        // Compact Multi-Window / Phone Layout
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            bottomBar = {
                NavigationBar(containerColor = Color(0xFF1B1C18)) {
                    BeamerPage.entries.forEach { page ->
                        NavigationBarItem(
                            selected = currentPage == page,
                            onClick = { currentPage = page },
                            icon = { Text(page.badge, fontWeight = FontWeight.Bold, fontSize = 11.sp) },
                            label = { Text(page.title, fontSize = 10.sp) }
                        )
                    }
                }
            }
        ) { innerPadding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding)
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp)
            ) {
                PermissionWarningBanner(permSnapshot.userNotice, onDismissNotice)
                PageContent(
                    page = currentPage,
                    coreState = coreState,
                    permSnapshot = permSnapshot,
                    onRefreshState = onRefreshState,
                    onRequestStandardPerms = onRequestStandardPerms,
                    onRequestOverlay = onRequestOverlay,
                    onOpenAccessibility = onOpenAccessibility,
                    onRequestBatteryExemption = onRequestBatteryExemption,
                    onStartLinkService = onStartLinkService,
                )
                Spacer(modifier = Modifier.height(16.dp))
                Box(modifier = Modifier.height(340.dp)) {
                    TabletTrackpadAndModifierSurface(
                        coreState = coreState,
                        onRefreshState = onRefreshState,
                    )
                }
            }
        }
    }
}

@Composable
fun PermissionWarningBanner(notice: String?, onDismiss: () -> Unit) {
    if (notice.isNullOrEmpty()) return
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xFF3A2E1B)),
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 12.dp)
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp)
        ) {
            Text(
                text = notice,
                fontSize = 12.sp,
                color = Color(0xFFF3B452),
                modifier = Modifier.weight(1f)
            )
            OutlinedButton(
                onClick = onDismiss,
                modifier = Modifier.padding(start = 8.dp)
            ) {
                Text("Dismiss", fontSize = 11.sp)
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun PageContent(
    page: BeamerPage,
    coreState: CoreUiState,
    permSnapshot: AndroidPermissionSnapshot,
    onRefreshState: (String) -> Unit,
    onRequestStandardPerms: () -> Unit,
    onRequestOverlay: () -> Unit,
    onOpenAccessibility: () -> Unit,
    onRequestBatteryExemption: () -> Unit,
    onStartLinkService: () -> Unit,
) {
    val context = LocalContext.current

    Text(
        text = page.title,
        fontSize = 24.sp,
        fontWeight = FontWeight.Bold,
        color = MaterialTheme.colorScheme.onBackground
    )
    Text(
        text = "Wire v${coreState.wireVersion} · ID ${coreState.machineId.take(10)}… · ${coreState.lastEvent}",
        fontSize = 12.sp,
        color = Color(0xFF9EA096),
        fontFamily = FontFamily.Monospace,
        modifier = Modifier.padding(top = 4.dp, bottom = 14.dp)
    )

    when (page) {
        BeamerPage.OVERVIEW -> {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = if (coreState.ownerOn == null) "Input is on this Android Tablet"
                        else "Driving peer: ${coreState.ownerOn}",
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 16.sp,
                        color = MaterialTheme.colorScheme.primary
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(onClick = {
                            onStartLinkService()
                            onRefreshState(callBridge("generate_pairing_code"))
                        }) {
                            Text("Pair a machine (6-digit code)")
                        }
                        OutlinedButton(onClick = {
                            val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                            val clipText = cm?.primaryClip?.getItemAt(0)?.coerceToText(context)?.toString() ?: ""
                            if (clipText.isNotEmpty()) {
                                onRefreshState(callBridge("on_local_clipboard_changed", clipText))
                            }
                        }) {
                            Text("Sync Clipboard")
                        }
                        if (coreState.ownerOn != null) {
                            OutlinedButton(onClick = { onRefreshState(callBridge("return_home")) }) {
                                Text("Return input home")
                            }
                        }
                    }
                    if (coreState.pairingCode.isNotEmpty()) {
                        Spacer(modifier = Modifier.height(12.dp))
                        Text(
                            text = coreState.pairingCode.chunked(3).joinToString(" "),
                            fontSize = 28.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                            color = MaterialTheme.colorScheme.primary
                        )
                        Text(
                            text = "Listening on UDP/TCP ${coreState.pairingPort} for CPace exchange",
                            fontSize = 12.sp,
                            color = Color(0xFF9EA096)
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))
            Text("Paired Machines", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
            Spacer(modifier = Modifier.height(8.dp))
            coreState.peers.forEach { peer ->
                Card(
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 4.dp)
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(14.dp)
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("${peer.name} (${peer.platform})", fontWeight = FontWeight.SemiBold)
                            Text(
                                "${peer.host}:${peer.port} · Edge: ${peer.side}",
                                fontSize = 12.sp,
                                color = Color(0xFF9EA096),
                                fontFamily = FontFamily.Monospace
                            )
                        }
                        Button(
                            onClick = { onRefreshState(callBridge("switch_to_peer", peer.id, peer.side, 0.5)) },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = if (coreState.ownerOn == peer.id) Color(0xFF3E8E5A)
                                else MaterialTheme.colorScheme.primary
                            )
                        ) {
                            Text(if (coreState.ownerOn == peer.id) "Driving" else "Send input")
                        }
                    }
                }
            }
        }

        BeamerPage.PERMISSIONS -> {
            PermissionRowCard(
                title = "Foreground Link & QR Camera (POST_NOTIFICATIONS / CAMERA)",
                description = "Keeps encrypted TCP 24820 links active and allows scanning desktop QR codes.",
                granted = permSnapshot.notificationsGranted && permSnapshot.cameraGranted,
                buttonText = "Grant Runtime Permissions",
                onClick = onRequestStandardPerms
            )
            PermissionRowCard(
                title = "Unrestricted Battery & Low-Latency Wi-Fi Lock",
                description = "Prevents Android Doze from throttling BeamerLinkService while driving another machine.",
                granted = permSnapshot.batteryOptimizationIgnored,
                buttonText = "Disable Battery Optimization",
                onClick = onRequestBatteryExemption
            )
            PermissionRowCard(
                title = "Edge Glow & Cursor Overlay (SYSTEM_ALERT_WINDOW)",
                description = "Draws Beamer's crossing effects (Glow, Beam, Aperture) at the tablet's screen edges.",
                granted = permSnapshot.overlayGranted,
                buttonText = "Open Overlay Settings",
                onClick = onRequestOverlay
            )
            PermissionRowCard(
                title = "Accessibility Input Bridge (BIND_ACCESSIBILITY_SERVICE)",
                description = "Allows a paired Mac, Windows PC, or Linux desktop to move the pointer and inject gestures on this tablet.",
                granted = permSnapshot.accessibilityEnabled,
                buttonText = "Open Accessibility Settings",
                onClick = onOpenAccessibility
            )
        }

        BeamerPage.CROSSING -> {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Display Arrangement & Edge Resistance", fontWeight = FontWeight.SemiBold)
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        "Push the pointer against the outer edge facing a paired machine. Once pressure reaches 100%, input crosses automatically.",
                        fontSize = 13.sp,
                        color = Color(0xFFB5B6AC)
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    var sliderVal by remember(coreState.resistancePx) {
                        mutableFloatStateOf(coreState.resistancePx.toFloat())
                    }
                    Text("Edge Resistance: ${sliderVal.toInt()} dp", fontFamily = FontFamily.Monospace)
                    Slider(
                        value = sliderVal,
                        onValueChange = { sliderVal = it },
                        onValueChangeFinished = {
                            onRefreshState(
                                callBridge(
                                    "update_preferences",
                                    coreState.effectStyle,
                                    sliderVal.toInt(),
                                    coreState.responderEnabled,
                                    coreState.pointerSpeed.toDouble(),
                                    coreState.scrollSpeed.toDouble(),
                                    coreState.reverseScroll,
                                    coreState.sameOnAll
                                )
                            )
                        },
                        valueRange = 0f..300f
                    )
                }
            }
        }

        BeamerPage.KEYBOARD -> {
            var ptrSpeed by remember(coreState.pointerSpeed) { mutableFloatStateOf(coreState.pointerSpeed) }
            var scrSpeed by remember(coreState.scrollSpeed) { mutableFloatStateOf(coreState.scrollSpeed) }
            var revScroll by remember(coreState.reverseScroll) { mutableStateOf(coreState.reverseScroll) }

            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Pointer Speed, Scroll & Unicode Text Bridge", fontWeight = FontWeight.SemiBold)
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        "Per WIRE.md Section 7, Android belongs to the PC key family and translates shortcuts automatically when driving a Mac (Ctrl -> Cmd, Alt -> Option).",
                        fontSize = 13.sp,
                        color = Color(0xFFB5B6AC)
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Text("Pointer Speed: ${(ptrSpeed * 100).toInt()}%", fontFamily = FontFamily.Monospace)
                    Slider(
                        value = ptrSpeed,
                        onValueChange = { ptrSpeed = it },
                        onValueChangeFinished = {
                            onRefreshState(
                                callBridge(
                                    "update_preferences",
                                    coreState.effectStyle,
                                    coreState.resistancePx,
                                    coreState.responderEnabled,
                                    ptrSpeed.toDouble(),
                                    scrSpeed.toDouble(),
                                    revScroll,
                                    coreState.sameOnAll
                                )
                            )
                        },
                        valueRange = 0.25f..4.0f
                    )
                    Text("Scroll Speed: ${(scrSpeed * 100).toInt()}%", fontFamily = FontFamily.Monospace)
                    Slider(
                        value = scrSpeed,
                        onValueChange = { scrSpeed = it },
                        onValueChangeFinished = {
                            onRefreshState(
                                callBridge(
                                    "update_preferences",
                                    coreState.effectStyle,
                                    coreState.resistancePx,
                                    coreState.responderEnabled,
                                    ptrSpeed.toDouble(),
                                    scrSpeed.toDouble(),
                                    revScroll,
                                    coreState.sameOnAll
                                )
                            )
                        },
                        valueRange = 0.25f..4.0f
                    )
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text("Reverse scroll direction", fontSize = 14.sp)
                        Switch(
                            checked = revScroll,
                            onCheckedChange = {
                                revScroll = it
                                onRefreshState(
                                    callBridge(
                                        "update_preferences",
                                        coreState.effectStyle,
                                        coreState.resistancePx,
                                        coreState.responderEnabled,
                                        ptrSpeed.toDouble(),
                                        scrSpeed.toDouble(),
                                        it,
                                        coreState.sameOnAll
                                    )
                                )
                            }
                        )
                    }
                    HorizontalDivider(modifier = Modifier.padding(vertical = 10.dp))
                    var textToSend by remember { mutableStateOf("") }
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        OutlinedTextField(
                            value = textToSend,
                            onValueChange = { textToSend = it },
                            label = { Text("Send Unicode text / emoji frame") },
                            modifier = Modifier.weight(1f)
                        )
                        Button(onClick = {
                            if (textToSend.isNotEmpty()) {
                                onRefreshState(callBridge("record_text", textToSend))
                                textToSend = ""
                            }
                        }) {
                            Text("Send")
                        }
                    }
                }
            }
        }

        BeamerPage.DESIGN -> {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Crossing Styles (core/effects.py)", fontWeight = FontWeight.SemiBold)
                    Spacer(modifier = Modifier.height(10.dp))
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        coreState.availableStyles.forEach { style ->
                            val selected = style == coreState.effectStyle
                            Box(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(if (selected) MaterialTheme.colorScheme.primary else Color(0xFF2D2E29))
                                    .clickable {
                                        onRefreshState(
                                            callBridge(
                                                "update_preferences",
                                                style,
                                                coreState.resistancePx,
                                                coreState.responderEnabled,
                                                coreState.pointerSpeed.toDouble(),
                                                coreState.scrollSpeed.toDouble(),
                                                coreState.reverseScroll,
                                                coreState.sameOnAll
                                            )
                                        )
                                    }
                                    .padding(horizontal = 14.dp, vertical = 10.dp)
                            ) {
                                Text(
                                    text = style.replaceFirstChar { it.uppercase() },
                                    color = if (selected) Color(0xFF0E0F0D) else MaterialTheme.colorScheme.onSurface,
                                    fontWeight = FontWeight.Medium
                                )
                            }
                        }
                    }
                }
            }
        }

        BeamerPage.CONNECTION -> {
            var newPeerName by remember { mutableStateOf("Office Desktop") }
            var newPeerHost by remember { mutableStateOf("192.168.1.50") }
            var newPeerPort by remember { mutableStateOf("24820") }
            var responderToggle by remember(coreState.responderEnabled) {
                mutableStateOf(coreState.responderEnabled)
            }

            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text("Tablet Responder Mode (TCP ${coreState.port})", fontWeight = FontWeight.SemiBold)
                            Text(
                                "Allow paired desktops to cross onto this tablet screen",
                                fontSize = 12.sp,
                                color = Color(0xFF9EA096)
                            )
                        }
                        Switch(
                            checked = responderToggle,
                            onCheckedChange = {
                                responderToggle = it
                                onRefreshState(
                                    callBridge(
                                        "update_preferences",
                                        coreState.effectStyle,
                                        coreState.resistancePx,
                                        it,
                                        coreState.pointerSpeed.toDouble(),
                                        coreState.scrollSpeed.toDouble(),
                                        coreState.reverseScroll,
                                        coreState.sameOnAll
                                    )
                                )
                            }
                        )
                    }
                    HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp))
                    Text("Add Peer Manually", fontWeight = FontWeight.SemiBold)
                    Spacer(modifier = Modifier.height(8.dp))
                    OutlinedTextField(
                        value = newPeerName,
                        onValueChange = { newPeerName = it },
                        label = { Text("Peer Name") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = newPeerHost,
                            onValueChange = { newPeerHost = it },
                            label = { Text("IPv4 Host") },
                            modifier = Modifier.weight(0.65f)
                        )
                        OutlinedTextField(
                            value = newPeerPort,
                            onValueChange = { newPeerPort = it },
                            label = { Text("Port") },
                            modifier = Modifier.weight(0.35f)
                        )
                    }
                    Spacer(modifier = Modifier.height(10.dp))
                    Button(onClick = {
                        val portInt = newPeerPort.toIntOrNull() ?: 24820
                        onRefreshState(callBridge("add_or_update_peer", newPeerName, newPeerHost, portInt, "right"))
                    }) {
                        Text("Save Peer Entry")
                    }
                }
            }
        }
    }
}

@Composable
fun PermissionRowCard(
    title: String,
    description: String,
    granted: Boolean,
    buttonText: String,
    onClick: () -> Unit,
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(title, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                Box(
                    modifier = Modifier
                        .size(10.dp)
                        .clip(CircleShape)
                        .background(if (granted) Color(0xFF4CAF50) else Color(0xFFF3B452))
                )
            }
            Spacer(modifier = Modifier.height(4.dp))
            Text(description, fontSize = 13.sp, color = Color(0xFFB5B6AC))
            Spacer(modifier = Modifier.height(10.dp))
            if (!granted) {
                Button(onClick = onClick) {
                    Text(buttonText)
                }
            } else {
                Text("Ready", color = Color(0xFF4CAF50), fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TabletTrackpadAndModifierSurface(
    coreState: CoreUiState,
    onRefreshState: (String) -> Unit,
) {
    val density = LocalDensity.current.density
    val accumulator = remember(density, coreState.pointerSpeed) {
        LogicalMotionAccumulator(density, coreState.pointerSpeed)
    }
    var pointerPos by remember { mutableStateOf(Offset(220f, 180f)) }
    var edgePressure by remember { mutableFloatStateOf(0f) }
    var lastDeltaLabel by remember { mutableStateOf("mousemove(0, 0)") }

    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xFF1A1B18)),
        modifier = Modifier
            .fillMaxSize()
            .border(1.dp, Color(0xFF34352F), RoundedCornerShape(14.dp))
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(14.dp)
        ) {
            Row(
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    text = "Trackpad (${coreState.effectStyle} · ${(coreState.pointerSpeed * 100).toInt()}%)",
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 13.sp
                )
                Text(
                    text = lastDeltaLabel,
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.primary,
                    fontFamily = FontFamily.Monospace
                )
            }
            Spacer(modifier = Modifier.height(6.dp))

            // Trackpad Canvas with tap-to-click, drag motion, and right/left edge crossing glow
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .background(Color(0xFF121310))
                    .pointerInput(Unit) {
                        detectTapGestures(
                            onTap = {
                                callBridge("record_mouse_button", "left", true)
                                onRefreshState(callBridge("record_mouse_button", "left", false))
                            },
                            onLongPress = {
                                callBridge("record_mouse_button", "right", true)
                                onRefreshState(callBridge("record_mouse_button", "right", false))
                            }
                        )
                    }
                    .pointerInput(density, coreState.pointerSpeed, coreState.resistancePx) {
                        detectDragGestures(
                            onDragEnd = {
                                edgePressure = 0f
                                accumulator.reset()
                            },
                            onDragCancel = {
                                edgePressure = 0f
                                accumulator.reset()
                            }
                        ) { change, dragAmount ->
                            change.consume()
                            val widthF = size.width.toFloat().coerceAtLeast(1f)
                            val heightF = size.height.toFloat().coerceAtLeast(1f)
                            pointerPos = Offset(
                                (pointerPos.x + dragAmount.x).coerceIn(0f, widthF),
                                (pointerPos.y + dragAmount.y).coerceIn(0f, heightF)
                            )
                            val (dx, dy) = accumulator.toWireDelta(dragAmount.x, dragAmount.y)
                            val atEdge = when {
                                pointerPos.x >= widthF - 24f && dragAmount.x > 0f -> "right"
                                pointerPos.x <= 24f && dragAmount.x < 0f -> "left"
                                else -> ""
                            }
                            val offsetNorm = (pointerPos.y / heightF).coerceIn(0f, 1f)
                            if (dx != 0 || dy != 0) {
                                lastDeltaLabel = callBridge("record_mousemove", dx, dy, atEdge, offsetNorm.toDouble())
                            }
                            if (atEdge.isNotEmpty()) {
                                val denom = (coreState.resistancePx * density).coerceAtLeast(40f)
                                edgePressure = (edgePressure + kotlin.math.abs(dragAmount.x) / denom).coerceIn(0f, 1f)
                                if (edgePressure >= 1f) {
                                    onRefreshState(callBridge("get_state_json"))
                                    edgePressure = 0f
                                }
                            } else {
                                edgePressure = (edgePressure * 0.85f).coerceIn(0f, 1f)
                            }
                        }
                    }
            ) {
                Canvas(modifier = Modifier.fillMaxSize()) {
                    drawCircle(
                        color = Color(0xFF5FD4F4),
                        radius = 9.dp.toPx(),
                        center = Offset(
                            pointerPos.x.coerceIn(12f, size.width - 12f),
                            pointerPos.y.coerceIn(12f, size.height - 12f)
                        )
                    )
                    if (edgePressure > 0.01f) {
                        val glowWidth = (38.dp.toPx() * (0.4f + edgePressure))
                        drawRect(
                            brush = Brush.horizontalGradient(
                                colors = listOf(
                                    Color.Transparent,
                                    Color(0xFF5FD4F4).copy(alpha = 0.78f * edgePressure)
                                ),
                                startX = size.width - glowWidth,
                                endX = size.width
                            ),
                            topLeft = Offset(size.width - glowWidth, 0f),
                            size = androidx.compose.ui.geometry.Size(glowWidth, size.height)
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(8.dp))
            // Mouse Buttons & Scroll Wheel Strip
            Row(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                OutlinedButton(
                    onClick = {
                        callBridge("record_mouse_button", "left", true)
                        onRefreshState(callBridge("record_mouse_button", "left", false))
                    },
                    modifier = Modifier.weight(1f)
                ) { Text("L-Click", fontSize = 11.sp) }

                OutlinedButton(
                    onClick = {
                        callBridge("record_mouse_button", "right", true)
                        onRefreshState(callBridge("record_mouse_button", "right", false))
                    },
                    modifier = Modifier.weight(1f)
                ) { Text("R-Click", fontSize = 11.sp) }

                OutlinedButton(
                    onClick = { onRefreshState(callBridge("record_wheel", 0.0, -3.0, "line")) },
                    modifier = Modifier.weight(1f)
                ) { Text("Scroll ↑", fontSize = 11.sp) }

                OutlinedButton(
                    onClick = { onRefreshState(callBridge("record_wheel", 0.0, 3.0, "line")) },
                    modifier = Modifier.weight(1f)
                ) { Text("Scroll ↓", fontSize = 11.sp) }
            }

            Spacer(modifier = Modifier.height(6.dp))
            // Multi-Finger Trackpad Gestures & Modifier Shortcuts
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                listOf(
                    "Task View (3F↑)" to { callBridge("record_gesture", "swipe", "up", 3) },
                    "Desktop (3F↓)" to { callBridge("record_gesture", "swipe", "down", 3) },
                    "Space ←" to { callBridge("record_gesture", "swipe", "left", 3) },
                    "Space →" to { callBridge("record_gesture", "swipe", "right", 3) },
                    "Cmd/Ctrl+C" to { callBridge("record_key_chord", "ctrl", "c") },
                    "Cmd/Ctrl+V" to { callBridge("record_key_chord", "ctrl", "v") },
                ).forEach { (label, action) ->
                    OutlinedButton(onClick = { onRefreshState(action()) }) {
                        Text(label, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    }
                }
            }
        }
    }
}
