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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
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
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chaquo.python.Python
import kotlin.math.hypot
import kotlinx.coroutines.delay
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

data class NearbyCandidateItem(
    val name: String,
    val address: String,
    val platform: String,
)

data class NearbyUiState(
    val state: String = "inactive",
    val secondsLeft: Int = 0,
    val sas: String = "",
    val attempt: String = "",
    val peerName: String = "",
    val peerAddress: String = "",
    val candidates: List<NearbyCandidateItem> = emptyList(),
)

data class DiscoveredMachineItem(
    val name: String,
    val address: String,
    val port: Int,
    val platform: String,
    val hasCode: Boolean,
    val nearbyActive: Boolean,
)

data class CoreUiState(
    val wireVersion: Int = 6,
    val machineId: String = "",
    val deviceName: String = "Android Tablet",
    val localIp: String = "",
    val uiLang: String = "ar",
    val port: Int = 24820,
    val pairingPort: Int = 24821,
    val resistancePx: Int = 120,
    val pointerSpeed: Float = 1.0f,
    val scrollSpeed: Float = 1.0f,
    val reverseScroll: Boolean = false,
    val sameOnAll: Boolean = true,
    val effectStyle: String = "glow",
    val responderEnabled: Boolean = true,
    val responderDriven: Boolean = false,
    val screenWidth: Int = 3000,
    val screenHeight: Int = 1920,
    val cursorX: Int = 1500,
    val cursorY: Int = 960,
    val ownerOn: String? = null,
    val edgePressure: Float = 0f,
    val activeEdge: String = "right",
    val pairingCode: String = "",
    val lastEvent: String = "Ready",
    val nearby: NearbyUiState = NearbyUiState(),
    val discovered: List<DiscoveredMachineItem> = emptyList(),
    val peers: List<PeerItem> = emptyList(),
    val availableStyles: List<String> = listOf("glow", "beam", "aperture"),
)

enum class BeamerPage(val key: String, val titleEn: String, val titleAr: String, val badge: String) {
    OVERVIEW("overview", "Overview", "نظرة عامة", "OV"),
    PERMISSIONS("permissions", "Permissions", "الصلاحيات", "PM"),
    CROSSING("crossing", "Crossing", "العبور", "CR"),
    KEYBOARD("keyboard", "Keyboard", "الكيبورد", "KB"),
    DESIGN("design", "Design", "التصميم", "DS"),
    CONNECTION("connection", "Connection", "الشبكة", "CN");

    fun title(isAr: Boolean): String = if (isAr) titleAr else titleEn
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
        BeamerApplication.ensureBridgeReady(this)
        startBeamerForegroundService()

        setContent {
            MaterialTheme(colorScheme = BeamerDarkPalette) {
                val permSnapshot by permissionManager.state.collectAsState()
                var coreState by remember { mutableStateOf(loadCoreState()) }

                // Poll state every 800ms so Press Nearby SAS codes, countdowns, and LAN discovery update live
                LaunchedEffect(Unit) {
                    while (true) {
                        delay(800L)
                        coreState = loadCoreState()
                    }
                }

                val isAr = coreState.uiLang == "ar"
                CompositionLocalProvider(
                    LocalLayoutDirection provides if (isAr) LayoutDirection.Rtl else LayoutDirection.Ltr
                ) {
                    BeamerAdaptiveShell(
                        coreState = coreState,
                        permSnapshot = permSnapshot,
                        onRefreshState = { json -> coreState = parseCoreState(json) },
                        onDismissNotice = { permissionManager.dismissNotice() },
                        onRequestStandardPerms = { permissionManager.requestStandardPermissions(includeCamera = true) },
                        onRequestOverlay = { permissionManager.requestOverlayPermission() },
                        onOpenAccessibility = { permissionManager.openAccessibilitySettings() },
                        onRequestBatteryExemption = { permissionManager.requestIgnoreBatteryOptimizations() },
                        onOpenAppDetails = { permissionManager.openAppDetailsSettings() },
                        onStartLinkService = { startBeamerForegroundService() },
                    )
                }
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
        } catch (_: Throwable) {
        }
    }

    private fun loadCoreState(): CoreUiState {
        return try {
            BeamerApplication.ensureBridgeReady(this)
            val py = Python.getInstance()
            val bridge = py.getModule("beamer_android_bridge").callAttr("get_bridge")
            val json = bridge.callAttr("get_state_json").toString()
            parseCoreState(json)
        } catch (t: Throwable) {
            CoreUiState(lastEvent = "Python core init: ${t.message}")
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

            val nearbyObj = root.optJSONObject("nearby")
            val nearbyState = if (nearbyObj != null) {
                val candsArr = nearbyObj.optJSONArray("candidates")
                val cands = mutableListOf<NearbyCandidateItem>()
                if (candsArr != null) {
                    for (i in 0 until candsArr.length()) {
                        val c = candsArr.getJSONObject(i)
                        cands += NearbyCandidateItem(
                            name = c.optString("name", "Machine"),
                            address = c.optString("address", ""),
                            platform = c.optString("platform", "desktop"),
                        )
                    }
                }
                NearbyUiState(
                    state = nearbyObj.optString("state", "inactive"),
                    secondsLeft = nearbyObj.optInt("seconds_left", 0),
                    sas = nearbyObj.optString("sas", ""),
                    attempt = nearbyObj.optString("attempt", ""),
                    peerName = nearbyObj.optString("peer_name", ""),
                    peerAddress = nearbyObj.optString("peer_address", ""),
                    candidates = cands,
                )
            } else {
                NearbyUiState()
            }

            val discArr = root.optJSONArray("discovered")
            val discovered = mutableListOf<DiscoveredMachineItem>()
            if (discArr != null) {
                for (i in 0 until discArr.length()) {
                    val d = discArr.getJSONObject(i)
                    discovered += DiscoveredMachineItem(
                        name = d.optString("name", "Machine"),
                        address = d.optString("address", ""),
                        port = d.optInt("port", 24820),
                        platform = d.optString("platform", "desktop"),
                        hasCode = d.optBoolean("has_code", false),
                        nearbyActive = d.optBoolean("nearby_active", false),
                    )
                }
            }

            val rawOwner = if (root.isNull("owner_on")) null else root.optString("owner_on")
            CoreUiState(
                wireVersion = root.optInt("wire_version", 6),
                machineId = root.optString("machine_id", ""),
                deviceName = root.optString("name", "Android Tablet"),
                localIp = root.optString("local_ip", ""),
                uiLang = root.optString("ui_lang", "ar"),
                port = root.optInt("port", 24820),
                pairingPort = root.optInt("pairing_port", 24821),
                resistancePx = root.optInt("resistance_px", 120),
                pointerSpeed = root.optDouble("pointer_speed", 1.0).toFloat(),
                scrollSpeed = root.optDouble("scroll_speed", 1.0).toFloat(),
                reverseScroll = root.optBoolean("reverse_scroll", false),
                sameOnAll = root.optBoolean("same_on_all", true),
                effectStyle = root.optString("effect_style", "glow"),
                responderEnabled = root.optBoolean("responder_enabled", true),
                responderDriven = root.optBoolean("responder_driven", false),
                screenWidth = root.optInt("screen_width", 3000),
                screenHeight = root.optInt("screen_height", 1920),
                cursorX = root.optInt("cursor_x", 1500),
                cursorY = root.optInt("cursor_y", 960),
                ownerOn = rawOwner?.takeIf { it.isNotEmpty() },
                edgePressure = root.optDouble("edge_pressure", 0.0).toFloat(),
                activeEdge = root.optString("active_edge", "right"),
                pairingCode = root.optString("pairing_code", ""),
                lastEvent = root.optString("last_event", "Ready"),
                nearby = nearbyState,
                discovered = discovered,
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
    onOpenAppDetails: () -> Unit,
    onStartLinkService: () -> Unit,
) {
    val config = LocalConfiguration.current
    val isAr = coreState.uiLang == "ar"
    val isExpandedTablet = config.screenWidthDp >= 800
    val isMediumSplitTablet = config.screenWidthDp in 580..799
    val useTwoPane = isExpandedTablet || (isMediumSplitTablet && config.screenWidthDp > config.screenHeightDp)

    var currentPage by remember {
        mutableStateOf(BeamerPage.OVERVIEW)
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
                Spacer(modifier = Modifier.height(10.dp))
                Text(
                    text = "BEAMER",
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold,
                    fontSize = 11.sp,
                    modifier = Modifier.padding(bottom = 6.dp)
                )
                // Instant Arabic / English Language Toggle Pill in NavigationRail
                Box(
                    contentAlignment = Alignment.Center,
                    modifier = Modifier
                        .padding(bottom = 8.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(Color(0xFF2B3538))
                        .clickable { onRefreshState(callBridge("toggle_language")) }
                        .padding(horizontal = 10.dp, vertical = 6.dp)
                ) {
                    Text(
                        text = if (isAr) "EN / ع" else "عربي / EN",
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.Bold,
                        fontSize = 11.sp
                    )
                }
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
                        label = { Text(page.title(isAr), fontSize = 11.sp) }
                    )
                }
            }

            if (useTwoPane) {
                Column(
                    modifier = Modifier
                        .weight(0.52f)
                        .fillMaxHeight()
                        .verticalScroll(rememberScrollState())
                        .padding(20.dp)
                ) {
                    PermissionWarningBanner(
                        notice = permSnapshot.userNotice,
                        isAr = isAr,
                        onDismiss = onDismissNotice,
                        permSnapshot = permSnapshot,
                        onOpenAccessibility = onOpenAccessibility,
                        onRequestOverlay = onRequestOverlay,
                        onOpenAppDetails = onOpenAppDetails,
                    )
                    PageContent(
                        page = currentPage,
                        coreState = coreState,
                        permSnapshot = permSnapshot,
                        onRefreshState = onRefreshState,
                        onRequestStandardPerms = onRequestStandardPerms,
                        onRequestOverlay = onRequestOverlay,
                        onOpenAccessibility = onOpenAccessibility,
                        onRequestBatteryExemption = onRequestBatteryExemption,
                        onOpenAppDetails = onOpenAppDetails,
                        onStartLinkService = onStartLinkService,
                    )
                }

                Column(
                    modifier = Modifier
                        .weight(0.48f)
                        .fillMaxHeight()
                        .padding(top = 20.dp, end = 20.dp, bottom = 20.dp)
                ) {
                    TabletTrackpadAndModifierSurface(
                        coreState = coreState,
                        onRefreshState = onRefreshState,
                    )
                }
            } else {
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxHeight()
                        .verticalScroll(rememberScrollState())
                        .padding(18.dp)
                ) {
                    PermissionWarningBanner(
                        notice = permSnapshot.userNotice,
                        isAr = isAr,
                        onDismiss = onDismissNotice,
                        permSnapshot = permSnapshot,
                        onOpenAccessibility = onOpenAccessibility,
                        onRequestOverlay = onRequestOverlay,
                        onOpenAppDetails = onOpenAppDetails,
                    )
                    PageContent(
                        page = currentPage,
                        coreState = coreState,
                        permSnapshot = permSnapshot,
                        onRefreshState = onRefreshState,
                        onRequestStandardPerms = onRequestStandardPerms,
                        onRequestOverlay = onRequestOverlay,
                        onOpenAccessibility = onOpenAccessibility,
                        onRequestBatteryExemption = onRequestBatteryExemption,
                        onOpenAppDetails = onOpenAppDetails,
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
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            bottomBar = {
                NavigationBar(containerColor = Color(0xFF1B1C18)) {
                    BeamerPage.entries.forEach { page ->
                        NavigationBarItem(
                            selected = currentPage == page,
                            onClick = { currentPage = page },
                            icon = { Text(page.badge, fontWeight = FontWeight.Bold, fontSize = 11.sp) },
                            label = { Text(page.title(isAr), fontSize = 10.sp) }
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
                PermissionWarningBanner(permSnapshot.userNotice, isAr, onDismissNotice)
                PageContent(
                    page = currentPage,
                    coreState = coreState,
                    permSnapshot = permSnapshot,
                    onRefreshState = onRefreshState,
                    onRequestStandardPerms = onRequestStandardPerms,
                    onRequestOverlay = onRequestOverlay,
                    onOpenAccessibility = onOpenAccessibility,
                    onRequestBatteryExemption = onRequestBatteryExemption,
                    onOpenAppDetails = onOpenAppDetails,
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

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun PermissionWarningBanner(
    notice: String?,
    isAr: Boolean,
    onDismiss: () -> Unit,
    permSnapshot: AndroidPermissionSnapshot? = null,
    onOpenAccessibility: (() -> Unit)? = null,
    onRequestOverlay: (() -> Unit)? = null,
    onOpenAppDetails: (() -> Unit)? = null,
) {
    val missingInputPerms = permSnapshot != null && (!permSnapshot.accessibilityEnabled || !permSnapshot.overlayGranted)
    if (notice.isNullOrEmpty() && !missingInputPerms) return
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xFF3A2E1B)),
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 12.dp)
            .border(1.dp, Color(0xFFF3B452), RoundedCornerShape(10.dp))
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    text = if (missingInputPerms) {
                        if (isAr) {
                            "⚠️ هام لعمل الماوس والكيبورد على Honor MagicPad 2: يجب تفعيل (خدمة إمكانية الوصول) و(الظهور فوق التطبيقات) ليظهر مؤشر الماوس ويتحرك وينقر على التابلت."
                        } else {
                            "⚠️ Required for Remote Mouse & Keyboard: Enable 'Accessibility Service' and 'Display over other apps' so the cursor appears and clicks on your tablet."
                        }
                    } else {
                        notice ?: ""
                    },
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = Color(0xFFF3B452),
                    modifier = Modifier.weight(1f)
                )
                OutlinedButton(
                    onClick = onDismiss,
                    modifier = Modifier.padding(start = 8.dp)
                ) {
                    Text(if (isAr) "إخفاء" else "Dismiss", fontSize = 11.sp)
                }
            }
            if (missingInputPerms) {
                Spacer(modifier = Modifier.height(8.dp))
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    if (permSnapshot?.accessibilityEnabled == false && onOpenAccessibility != null) {
                        Button(
                            onClick = onOpenAccessibility,
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFF3B452))
                        ) {
                            Text(
                                text = if (isAr) "1. تفعيل Accessibility (إمكانية الوصول)" else "1. Enable Accessibility Service",
                                color = Color(0xFF161714),
                                fontWeight = FontWeight.Bold,
                                fontSize = 11.sp
                            )
                        }
                    }
                    if (permSnapshot?.overlayGranted == false && onRequestOverlay != null) {
                        Button(
                            onClick = onRequestOverlay,
                            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary)
                        ) {
                            Text(
                                text = if (isAr) "2. تفعيل الظهور فوق التطبيقات (Overlay)" else "2. Enable Overlay Permission",
                                color = Color(0xFF161714),
                                fontWeight = FontWeight.Bold,
                                fontSize = 11.sp
                            )
                        }
                    }
                    if (onOpenAppDetails != null) {
                        OutlinedButton(onClick = onOpenAppDetails) {
                            Text(
                                text = if (isAr) "السماح بالإعدادات المقيدة (App Info)" else "Allow Restricted Settings",
                                fontSize = 11.sp
                            )
                        }
                    }
                }
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
    onOpenAppDetails: () -> Unit,
    onStartLinkService: () -> Unit,
) {
    val context = LocalContext.current
    val isAr = coreState.uiLang == "ar"

    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
        modifier = Modifier.fillMaxWidth()
    ) {
        Text(
            text = page.title(isAr),
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onBackground
        )
        OutlinedButton(
            onClick = { onRefreshState(callBridge("toggle_language")) }
        ) {
            Text(
                text = if (isAr) "🌐 English" else "🌐 العربية",
                fontWeight = FontWeight.Bold,
                fontSize = 12.sp
            )
        }
    }

    val ipLabel = if (coreState.localIp.isNotEmpty()) " · IP ${coreState.localIp}" else ""
    Text(
        text = "Wire v${coreState.wireVersion} · ID ${coreState.machineId.take(10)}…$ipLabel · ${coreState.lastEvent}",
        fontSize = 12.sp,
        color = Color(0xFF9EA096),
        fontFamily = FontFamily.Monospace,
        modifier = Modifier.padding(top = 4.dp, bottom = 14.dp)
    )

    when (page) {
        BeamerPage.OVERVIEW -> {
            // 1. Main Status & Quick Pairing Actions ("Press Nearby" + "6-digit Code" + "Clipboard")
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = when {
                            coreState.responderDriven -> {
                                if (isAr) {
                                    "🖱️ ماوس الكمبيوتر يتحكم في التابلت الآن · الموقع (${coreState.cursorX}, ${coreState.cursorY}) على شاشة ${coreState.screenWidth}×${coreState.screenHeight}"
                                } else {
                                    "🖱️ PC Cursor Active on Tablet · Pos (${coreState.cursorX}, ${coreState.cursorY}) on ${coreState.screenWidth}×${coreState.screenHeight}"
                                }
                            }
                            coreState.ownerOn == null -> {
                                if (isAr) "التحكم حالياً على هذا الجهاز (التابلت / الهاتف)" else "Input is on this Android Tablet / Phone"
                            }
                            else -> {
                                if (isAr) "يتم التحكم حالياً في الجهاز: ${coreState.ownerOn}" else "Driving peer: ${coreState.ownerOn}"
                            }
                        },
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 16.sp,
                        color = MaterialTheme.colorScheme.primary
                    )
                    Spacer(modifier = Modifier.height(10.dp))
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(
                            onClick = {
                                onStartLinkService()
                                onRefreshState(callBridge("begin_nearby"))
                            },
                            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary)
                        ) {
                            Text(
                                text = if (isAr) "📡 اقتران قريب (Press Nearby)" else "📡 Press Nearby (Easy Pair)",
                                fontWeight = FontWeight.Bold
                            )
                        }
                        OutlinedButton(onClick = {
                            onStartLinkService()
                            onRefreshState(callBridge("generate_pairing_code"))
                        }) {
                            Text(if (isAr) "إظهار كود من 6 أرقام" else "Show 6-digit code")
                        }
                        Button(
                            onClick = { onRefreshState(callBridge("return_home")) },
                            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFD96B43))
                        ) {
                            Text(
                                text = if (isAr) "⏎ إرجاع الماوس للكمبيوتر فوراً" else "⏎ Force Return Cursor to PC",
                                color = Color.White,
                                fontWeight = FontWeight.Bold
                            )
                        }
                        OutlinedButton(onClick = {
                            val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                            val clipText = cm?.primaryClip?.getItemAt(0)?.coerceToText(context)?.toString() ?: ""
                            if (clipText.isNotEmpty()) {
                                onRefreshState(callBridge("on_local_clipboard_changed", clipText))
                            }
                        }) {
                            Text(if (isAr) "مزامنة الحافظة" else "Sync Clipboard")
                        }
                    }

                    // Active "Press Nearby" (EasyPairWindow SAS v1) Live Status Box
                    if (coreState.nearby.state != "inactive") {
                        Spacer(modifier = Modifier.height(12.dp))
                        Card(
                            colors = CardDefaults.cardColors(containerColor = Color(0xFF1B292D)),
                            modifier = Modifier
                                .fillMaxWidth()
                                .border(1.dp, MaterialTheme.colorScheme.primary, RoundedCornerShape(10.dp))
                        ) {
                            Column(modifier = Modifier.padding(14.dp)) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Text(
                                        text = if (isAr) {
                                            "📡 الاقتران القريب السريع نشط (${coreState.nearby.secondsLeft} ثانية)"
                                        } else {
                                            "📡 Press Nearby Active (${coreState.nearby.secondsLeft}s left)"
                                        },
                                        fontWeight = FontWeight.Bold,
                                        color = MaterialTheme.colorScheme.primary,
                                        fontSize = 14.sp
                                    )
                                    OutlinedButton(onClick = { onRefreshState(callBridge("cancel_nearby")) }) {
                                        Text(if (isAr) "إلغاء" else "Cancel", fontSize = 11.sp)
                                    }
                                }
                                Spacer(modifier = Modifier.height(6.dp))
                                when (coreState.nearby.state) {
                                    "searching", "exchanging" -> {
                                        Text(
                                            text = if (isAr) {
                                                "اضغط الآن على زر (Press Nearby) في برنامج Beamer على الكمبيوتر أو الهاتف الآخر المتصل بنفس شبكة الـ Wi-Fi ليتم الاكتشاف والربط تلقائياً…"
                                            } else {
                                                "Now press 'Press Nearby' in Beamer on your PC, Mac, or phone on the same Wi-Fi network…"
                                            },
                                            fontSize = 13.sp,
                                            color = Color(0xFFD5D6CE)
                                        )
                                    }
                                    "choosing" -> {
                                        Text(
                                            text = if (isAr) "اختر الجهاز القريب المراد الاقتران به:" else "Select the nearby device to pair with:",
                                            fontSize = 13.sp,
                                            fontWeight = FontWeight.SemiBold
                                        )
                                        Spacer(modifier = Modifier.height(6.dp))
                                        coreState.nearby.candidates.forEach { cand ->
                                            Button(
                                                onClick = { onRefreshState(callBridge("choose_nearby", cand.address)) },
                                                modifier = Modifier
                                                    .fillMaxWidth()
                                                    .padding(vertical = 3.dp)
                                            ) {
                                                Text("${cand.name} (${cand.platform}) · ${cand.address}")
                                            }
                                        }
                                    }
                                    "confirm", "waiting" -> {
                                        Text(
                                            text = if (isAr) {
                                                "تأكد من تطابق هذا الرقم مع جهاز (${coreState.nearby.peerName}):"
                                            } else {
                                                "Confirm this verification code matches on ${coreState.nearby.peerName}:"
                                            },
                                            fontSize = 13.sp,
                                            color = Color(0xFFD5D6CE)
                                        )
                                        Spacer(modifier = Modifier.height(6.dp))
                                        Text(
                                            text = coreState.nearby.sas,
                                            fontSize = 32.sp,
                                            fontWeight = FontWeight.Bold,
                                            fontFamily = FontFamily.Monospace,
                                            color = MaterialTheme.colorScheme.primary
                                        )
                                        Spacer(modifier = Modifier.height(8.dp))
                                        if (coreState.nearby.state == "confirm") {
                                            Button(
                                                onClick = { onRefreshState(callBridge("confirm_nearby")) },
                                                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF3E8E5A))
                                            ) {
                                                Text(
                                                    text = if (isAr) "✓ تأكيد التطابق والاقتران الآن" else "✓ Confirm Match & Pair Now",
                                                    color = Color.White,
                                                    fontWeight = FontWeight.Bold
                                                )
                                            }
                                        } else {
                                            Text(
                                                text = if (isAr) "بانتظار التأكيد على الجهاز الآخر…" else "Waiting for confirmation on the other device…",
                                                fontSize = 12.sp,
                                                color = Color(0xFFF3B452)
                                            )
                                        }
                                    }
                                    "paired" -> {
                                        Text(
                                            text = if (isAr) "✓ تم الاقتران بنجاح!" else "✓ Paired successfully!",
                                            color = Color(0xFF4CAF50),
                                            fontWeight = FontWeight.Bold
                                        )
                                    }
                                    else -> {
                                        Text(
                                            text = if (isAr) "الحالة: ${coreState.nearby.state}" else "State: ${coreState.nearby.state}",
                                            fontSize = 12.sp
                                        )
                                    }
                                }
                            }
                        }
                    }

                    // Active 6-digit CPace Host Code Box
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
                            text = if (isAr) {
                                "أدخل هذا الكود في الكمبيوتر أو الهاتف الآخر (منفذ UDP/TCP ${coreState.pairingPort} · عنوان IP: ${coreState.localIp.ifEmpty { "Wi-Fi" }})"
                            } else {
                                "Enter this code on the other device (UDP/TCP ${coreState.pairingPort} · IP: ${coreState.localIp.ifEmpty { "Wi-Fi" }})"
                            },
                            fontSize = 12.sp,
                            color = Color(0xFF9EA096)
                        )
                    }
                }
            }

            // 2. Discovered LAN Machines & Quick Connect by IP + 6-Digit Code
            Spacer(modifier = Modifier.height(14.dp))
            var targetIp by remember { mutableStateOf("") }
            var targetCode by remember { mutableStateOf("") }
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = if (isAr) "الاتصال بجهاز آخر يعرض كود من 6 أرقام" else "Connect to a Device Showing a 6-Digit Code",
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 15.sp
                    )
                    if (coreState.discovered.isNotEmpty()) {
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = if (isAr) "أجهزة مكتشفة تلقائياً على نفس الـ Wi-Fi (اضغط لاختيارها):" else "Auto-discovered on Wi-Fi (tap to select):",
                            fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.primary
                        )
                        Spacer(modifier = Modifier.height(6.dp))
                        FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            coreState.discovered.forEach { mach ->
                                OutlinedButton(onClick = { targetIp = mach.address }) {
                                    Text("${mach.name} (${mach.address})", fontSize = 11.sp)
                                }
                            }
                        }
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        OutlinedTextField(
                            value = targetIp,
                            onValueChange = { targetIp = it },
                            label = { Text(if (isAr) "عنوان IP للجهاز الآخر" else "Other Device IP (e.g. 192.168.1.x)") },
                            modifier = Modifier.weight(0.55f),
                            singleLine = true
                        )
                        OutlinedTextField(
                            value = targetCode,
                            onValueChange = { targetCode = it.take(6) },
                            label = { Text(if (isAr) "الكود (6 أرقام)" else "6-Digit Code") },
                            modifier = Modifier.weight(0.45f),
                            singleLine = true
                        )
                    }
                    Spacer(modifier = Modifier.height(8.dp))
                    Button(
                        onClick = {
                            onStartLinkService()
                            onRefreshState(callBridge("pair_by_ip", targetIp, targetCode, coreState.pairingPort))
                        }
                    ) {
                        Text(if (isAr) "🔗 اقتران واتصال الآن" else "🔗 Pair & Connect Now")
                    }
                }
            }

            // 3. Paired Machines List
            Spacer(modifier = Modifier.height(16.dp))
            Text(
                text = if (isAr) "الأجهزة المقترنة (${coreState.peers.size})" else "Paired Machines (${coreState.peers.size})",
                fontWeight = FontWeight.SemiBold,
                fontSize = 16.sp
            )
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
                                "${peer.host}:${peer.port} · ${if (isAr) "الحافة:" else "Edge:"} ${peer.side}",
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
                            Text(
                                if (coreState.ownerOn == peer.id) {
                                    if (isAr) "متصل ويتحكم" else "Driving"
                                } else {
                                    if (isAr) "إرسال الماوس والكيبورد" else "Send input"
                                }
                            )
                        }
                    }
                }
            }
        }

        BeamerPage.PERMISSIONS -> {
            PermissionRowCard(
                title = if (isAr) "خدمة الخلفية والكاميرا (الإشعارات ومسح QR)" else "Foreground Link & QR Camera (POST_NOTIFICATIONS / CAMERA)",
                description = if (isAr) "لإبقاء اتصال TCP 24820 المشفر نشطاً ومسح رمز QR الخاص بالكمبيوتر." else "Keeps encrypted TCP 24820 links active and allows scanning desktop QR codes.",
                granted = permSnapshot.notificationsGranted && permSnapshot.cameraGranted,
                buttonText = if (isAr) "منح الصلاحيات الأساسية" else "Grant Runtime Permissions",
                readyText = if (isAr) "مفعّل وجاهز" else "Ready",
                onClick = onRequestStandardPerms
            )
            PermissionRowCard(
                title = if (isAr) "استثناء توفير البطارية وقفل الـ Wi-Fi فائق السرعة" else "Unrestricted Battery & Low-Latency Wi-Fi Lock",
                description = if (isAr) "يمنع نظام Android / MagicOS من إيقاف الخدمة أثناء التحكم في الأجهزة الأخرى." else "Prevents Android Doze from throttling BeamerLinkService while driving another machine.",
                granted = permSnapshot.batteryOptimizationIgnored,
                buttonText = if (isAr) "إيقاف قيود البطارية" else "Disable Battery Optimization",
                readyText = if (isAr) "مفعّل وجاهز" else "Ready",
                onClick = onRequestBatteryExemption
            )
            PermissionRowCard(
                title = if (isAr) "الظهور فوق التطبيقات وتأثيرات الحواف (SYSTEM_ALERT_WINDOW)" else "Edge Glow & Cursor Overlay (SYSTEM_ALERT_WINDOW)",
                description = if (isAr) "لرسم مؤشر الماوس وتأثيرات العبور الضوئية عند حواف شاشة التابلت." else "Draws Beamer's crossing effects (Glow, Beam, Aperture) at the tablet's screen edges.",
                granted = permSnapshot.overlayGranted,
                buttonText = if (isAr) "فتح إعدادات الظهور فوق التطبيقات" else "Open Overlay Settings",
                readyText = if (isAr) "مفعّل وجاهز" else "Ready",
                onClick = onRequestOverlay
            )
            PermissionRowCard(
                title = if (isAr) "خدمة إمكانية الوصول للتحكم بالتابلت (Accessibility Service)" else "Accessibility Input Bridge (BIND_ACCESSIBILITY_SERVICE)",
                description = if (isAr) "تسمح للكمبيوتر أو الهاتف الآخر بتحريك المؤشر والنقر والتمرير داخل شاشة التابلت." else "Allows a paired Mac, Windows PC, or phone to move the pointer and inject gestures on this tablet.",
                granted = permSnapshot.accessibilityEnabled,
                buttonText = if (isAr) "فتح إعدادات إمكانية الوصول" else "Open Accessibility Settings",
                readyText = if (isAr) "مفعّل وجاهز" else "Ready",
                onClick = onOpenAccessibility
            )
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 6.dp)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = if (isAr) "فك الإعدادات المقيدة في Android 15 / 16 و Honor MagicPad 2" else "Unlock Restricted Settings (Android 15 / 16 & Honor MagicOS)",
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = if (isAr) {
                            "إذا ظهرت رسالة (إعداد مقيّد / Restricted setting) عند تفعيل إمكانية الوصول، اضغط الزر أدناه لفتح معلومات التطبيق ثم اضغط (⋮) في الأعلى واختر (السماح بالإعدادات المقيدة)."
                        } else {
                            "If Android 15/16 shows 'Restricted setting' when enabling Accessibility, tap below to open App Info, tap the top-right (⋮) menu, and choose 'Allow restricted settings'."
                        },
                        fontSize = 13.sp,
                        color = Color(0xFFB5B6AC)
                    )
                    Spacer(modifier = Modifier.height(10.dp))
                    OutlinedButton(onClick = onOpenAppDetails) {
                        Text(if (isAr) "فتح معلومات التطبيق (App Info)" else "Open App Info (Allow Restricted Settings)")
                    }
                }
            }
        }

        BeamerPage.CROSSING -> {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        text = if (isAr) "ترتيب الشاشات ومقاومة الحواف عند العبور" else "Display Arrangement & Edge Resistance",
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = if (isAr) {
                            "اسحب المؤشر باتجاه حافة الشاشة المقابلة للجهاز الآخر؛ عند وصول ضغط الحافة إلى 100% ينتقل الماوس والكيبورد تلقائياً."
                        } else {
                            "Push the pointer against the outer edge facing a paired machine. Once pressure reaches 100%, input crosses automatically."
                        },
                        fontSize = 13.sp,
                        color = Color(0xFFB5B6AC)
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    var sliderVal by remember(coreState.resistancePx) {
                        mutableFloatStateOf(coreState.resistancePx.toFloat())
                    }
                    Text(
                        text = if (isAr) "مقاومة الحافة: ${sliderVal.toInt()} dp" else "Edge Resistance: ${sliderVal.toInt()} dp",
                        fontFamily = FontFamily.Monospace
                    )
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
                    Text(
                        text = if (isAr) "سرعة المؤشر والتمرير وإرسال النصوص الفورية" else "Pointer Speed, Scroll & Unicode Text Bridge",
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = if (isAr) {
                            "يقوم Beamer بترجمة اختصارات لوحة المفاتيح تلقائياً بين أندرويد/ويندوز وأجهزة الماك (Ctrl <-> Cmd) ويدعم إرسال النصوص العربية والإيموجي مباشرة."
                        } else {
                            "Per WIRE.md Section 7, Android belongs to the PC key family and translates shortcuts automatically when driving a Mac (Ctrl -> Cmd, Alt -> Option)."
                        },
                        fontSize = 13.sp,
                        color = Color(0xFFB5B6AC)
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(
                        text = if (isAr) "سرعة المؤشر: ${(ptrSpeed * 100).toInt()}%" else "Pointer Speed: ${(ptrSpeed * 100).toInt()}%",
                        fontFamily = FontFamily.Monospace
                    )
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
                    Text(
                        text = if (isAr) "سرعة التمرير (Scroll): ${(scrSpeed * 100).toInt()}%" else "Scroll Speed: ${(scrSpeed * 100).toInt()}%",
                        fontFamily = FontFamily.Monospace
                    )
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
                        Text(if (isAr) "عكس اتجاه التمرير" else "Reverse scroll direction", fontSize = 14.sp)
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
                            label = { Text(if (isAr) "اكتب نصاً عربياً أو إنجليزياً لإرساله للجهاز الآخر" else "Send Unicode text / emoji frame") },
                            modifier = Modifier.weight(1f)
                        )
                        Button(onClick = {
                            if (textToSend.isNotEmpty()) {
                                onRefreshState(callBridge("record_text", textToSend))
                                textToSend = ""
                            }
                        }) {
                            Text(if (isAr) "إرسال" else "Send")
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
                    Text(
                        text = if (isAr) "تأثيرات العبور البصرية (core/effects.py)" else "Crossing Styles (core/effects.py)",
                        fontWeight = FontWeight.SemiBold
                    )
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
                            Text(
                                text = if (isAr) "وضع استقبال التحكم على التابلت (TCP ${coreState.port})" else "Tablet Responder Mode (TCP ${coreState.port})",
                                fontWeight = FontWeight.SemiBold
                            )
                            Text(
                                text = if (isAr) "السماح للأجهزة المقترنة بالدخول بالماوس والكيبورد إلى شاشة هذا الجهاز" else "Allow paired desktops to cross onto this tablet screen",
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
                    Text(
                        text = if (isAr) "إضافة جهاز يدوياً" else "Add Peer Manually",
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    OutlinedTextField(
                        value = newPeerName,
                        onValueChange = { newPeerName = it },
                        label = { Text(if (isAr) "اسم الجهاز" else "Peer Name") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = newPeerHost,
                            onValueChange = { newPeerHost = it },
                            label = { Text(if (isAr) "عنوان IPv4" else "IPv4 Host") },
                            modifier = Modifier.weight(0.65f)
                        )
                        OutlinedTextField(
                            value = newPeerPort,
                            onValueChange = { newPeerPort = it },
                            label = { Text(if (isAr) "المنفذ (Port)" else "Port") },
                            modifier = Modifier.weight(0.35f)
                        )
                    }
                    Spacer(modifier = Modifier.height(10.dp))
                    Button(onClick = {
                        val portInt = newPeerPort.toIntOrNull() ?: 24820
                        onRefreshState(callBridge("add_or_update_peer", newPeerName, newPeerHost, portInt, "right"))
                    }) {
                        Text(if (isAr) "حفظ الجهاز" else "Save Peer Entry")
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
    readyText: String = "Ready",
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
                Text(readyText, color = Color(0xFF4CAF50), fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
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
    val isAr = coreState.uiLang == "ar"
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
                    text = if (isAr) {
                        "لوحة اللمس Trackpad (${coreState.effectStyle} · ${(coreState.pointerSpeed * 100).toInt()}%)"
                    } else {
                        "Trackpad (${coreState.effectStyle} · ${(coreState.pointerSpeed * 100).toInt()}%)"
                    },
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
                    if (size.width > 1f && size.height > 1f) {
                        val maxX = (size.width - 12f).coerceAtLeast(12f)
                        val maxY = (size.height - 12f).coerceAtLeast(12f)
                        drawCircle(
                            color = Color(0xFF5FD4F4),
                            radius = 9.dp.toPx(),
                            center = Offset(
                                pointerPos.x.coerceIn(12f, maxX),
                                pointerPos.y.coerceIn(12f, maxY)
                            )
                        )
                        if (edgePressure > 0.01f) {
                            val glowWidth = (38.dp.toPx() * (0.4f + edgePressure)).coerceAtMost(size.width)
                            drawRect(
                                brush = Brush.horizontalGradient(
                                    colors = listOf(
                                        Color.Transparent,
                                        Color(0xFF5FD4F4).copy(alpha = 0.78f * edgePressure)
                                    ),
                                    startX = (size.width - glowWidth).coerceAtLeast(0f),
                                    endX = size.width
                                ),
                                topLeft = Offset((size.width - glowWidth).coerceAtLeast(0f), 0f),
                                size = androidx.compose.ui.geometry.Size(glowWidth, size.height)
                            )
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(8.dp))
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
                ) { Text(if (isAr) "نقر يسار" else "L-Click", fontSize = 11.sp) }

                OutlinedButton(
                    onClick = {
                        callBridge("record_mouse_button", "right", true)
                        onRefreshState(callBridge("record_mouse_button", "right", false))
                    },
                    modifier = Modifier.weight(1f)
                ) { Text(if (isAr) "نقر يمين" else "R-Click", fontSize = 11.sp) }

                OutlinedButton(
                    onClick = { onRefreshState(callBridge("record_wheel", 0.0, -3.0, "line")) },
                    modifier = Modifier.weight(1f)
                ) { Text(if (isAr) "تمرير ↑" else "Scroll ↑", fontSize = 11.sp) }

                OutlinedButton(
                    onClick = { onRefreshState(callBridge("record_wheel", 0.0, 3.0, "line")) },
                    modifier = Modifier.weight(1f)
                ) { Text(if (isAr) "تمرير ↓" else "Scroll ↓", fontSize = 11.sp) }
            }

            Spacer(modifier = Modifier.height(6.dp))
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                listOf(
                    (if (isAr) "المهام (3↑)" else "Task View (3F↑)") to { callBridge("record_gesture", "swipe", "up", 3) },
                    (if (isAr) "سطح المكتب (3↓)" else "Desktop (3F↓)") to { callBridge("record_gesture", "swipe", "down", 3) },
                    (if (isAr) "مساحة ←" else "Space ←") to { callBridge("record_gesture", "swipe", "left", 3) },
                    (if (isAr) "مساحة →" else "Space →") to { callBridge("record_gesture", "swipe", "right", 3) },
                    (if (isAr) "نسخ Ctrl+C" else "Cmd/Ctrl+C") to { callBridge("record_key_chord", "ctrl", "c") },
                    (if (isAr) "لصق Ctrl+V" else "Cmd/Ctrl+V") to { callBridge("record_key_chord", "ctrl", "v") },
                ).forEach { (label, action) ->
                    OutlinedButton(onClick = { onRefreshState(action()) }) {
                        Text(label, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    }
                }
            }
        }
    }
}
