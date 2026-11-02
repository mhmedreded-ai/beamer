"""The notch island: crossing feedback for a push through the notch.

The edge glow cannot do this job. It draws a band inward from the strip being pushed, and the notch
strip sits in the top 32pt of a notched screen, where the display has no pixels at all: the band
topped out at 20pt, so every frame of it was drawn behind the camera housing and nobody ever saw it.

So the notch grows instead, drawn to the Vernier design. A black shape with the notch's own concave
shoulders sits over the cutout, invisible at rest. Pressure springs it wider and down past the
cutout; a signal-coloured rim and glow trace its lower outline, and once it is deep enough a
four-segment meter and the percentage appear inside it. Each trackpad tick gives the width a small
velocity kick, so the tick is seen as well as felt. Breakthrough plays as its own fixed sequence,
see `breakthrough_phase`. Everything that glows or reads is masked to below the cutout.

Core Animation does the motion: every pressure change retargets a CASpringAnimation on the shape's
path from its presentation value, so a push that wavers never jumps. Reduce Motion replaces growth
with a fixed island whose fill, rim and meter fade with pressure.

Main thread only, like EdgeGlow. Any failure disables it for the run; crossing never depends on it.
"""

import time

import AppKit
import Quartz
from frame_clock import FrameClock
import tokens

NOTCH_FALLBACK_HEIGHT = 32.0
SHOULDER = 8.0
PAD = 24.0
OVERSHOOT = 1.25
FLASH_RIM = "#d8f5fd"
BREAKTHROUGH_GROW_S = 0.28
BREAKTHROUGH_HOLD_S = 0.32
TRACE_PASS_S = 0.7
TRACE_LENGTH = 0.22
GRACE_S = 0.9


def breakthrough_phase(elapsed, total=BREAKTHROUGH_GROW_S + BREAKTHROUGH_HOLD_S):
    """(level, flash) while a breakthrough plays, or None once it has. The island goes to full size
    with the rim flashing pale, holds, then the ordinary spring takes it home. It cannot simply
    follow pressure: replaying trackpad deltas through the crossing engine, a steady push breaks
    through 0.17s after pressure starts and a flick in 0.02 to 0.05s, while the island's spring
    needs about half a second to grow, so a push-driven island read as a flicker or not at all."""
    if elapsed < 0 or elapsed >= max(total, BREAKTHROUGH_GROW_S):
        return None
    return 1.0, max(0.0, 1.0 - elapsed / BREAKTHROUGH_GROW_S)


def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def after_seconds(controller):
    """How long a crossing keeps playing once it has gone through: crossing.notch_after_ms. Before
    it was a setting both styles stopped after a fixed 0.6s, mid-animation, which read as a pause."""
    return controller.cfg.crossing["notch_after_ms"] / 1000.0


def trace_head(progress, after):
    """Where the light that runs along the rim after a crossing is: its head as a fraction of the
    rim, running up to TRACE_LENGTH past the end so the tail leaves too. It makes whole passes only,
    as many as fit at about TRACE_PASS_S each, and is off the end by 80% of the way through, so it
    never stops part way along."""
    passes = max(1, round(0.8 * after / TRACE_PASS_S))
    scaled = min(1.0, max(0.0, progress) / 0.8) * passes
    fraction = 1.0 if scaled >= passes else scaled % 1.0
    return ease_out(fraction) * (1.0 + TRACE_LENGTH)


def notch_geometry():
    """(left, right, top, height) of the notch in AppKit global coordinates, or None. Same
    derivation as notch_x_range: the two auxiliary top areas of the screen that forms the top of
    the desktop, with the height taken from that screen's top safe-area inset."""
    screens = AppKit.NSScreen.screens()
    if not screens:
        return None
    desktop_top = max(s.frame().origin.y + s.frame().size.height for s in screens)
    for screen in screens:
        frame = screen.frame()
        top = frame.origin.y + frame.size.height
        if top != desktop_top:
            continue
        try:
            height = screen.safeAreaInsets().top
            left_area = screen.auxiliaryTopLeftArea()
            right_area = screen.auxiliaryTopRightArea()
        except AttributeError:
            continue
        if height <= 0 or left_area is None or right_area is None:
            continue
        if left_area.size.width <= 0 or right_area.size.width <= 0:
            continue
        return (left_area.origin.x + left_area.size.width, right_area.origin.x, top, height)
    return None


def _colour(name_or_hex, alpha=1.0):
    value = tokens.PALETTE.get(name_or_hex, name_or_hex).lstrip("#")
    red, green, blue = (int(value[index:index + 2], 16) / 255 for index in (0, 2, 4))
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, alpha).CGColor()


def _spring(key, stiffness, damping, start, end, velocity=0.0):
    spring = Quartz.CASpringAnimation.animationWithKeyPath_(key)
    spring.setMass_(1.0)
    spring.setStiffness_(stiffness)
    spring.setDamping_(damping)
    spring.setInitialVelocity_(velocity)
    spring.setFromValue_(start)
    spring.setToValue_(end)
    spring.setDuration_(spring.settlingDuration())
    return spring


class NotchIsland:
    def __init__(self, controller, logger):
        self.controller = controller
        self.logger = logger
        self.panel = None
        self.geometry = None
        self.visible = False
        self.disabled = False
        self.cross_at = None
        self.rest_at = None
        self.target = None
        self.kick = 0.0
        self.held = 0.0
        self.held_at = None
        self.timer = FrameClock(None, lambda: self._tick(None))

    def _held(self, level, now):
        """Pressure as the animation shows it. Lifting a finger to reposition lets real pressure
        drain to nothing in 0.4s, which dropped the animation and started it again from scratch;
        while the pointer is still pinned at the notch, the highest recent pressure is held for
        GRACE_S instead. That includes a slow push, whose pressure drains to nothing between every
        pair of events: reading the engine's pin, which it drops each time, showed and hid the
        notch several times a second, which strobes. Moving off the notch still lets
        go at once, since the engine stops touching."""
        if not getattr(getattr(self.controller, "crossing", None), "touching", False):
            self.held, self.held_at = level, now
        elif level >= self.held or self.held_at is None or now - self.held_at > GRACE_S:
            self.held, self.held_at = level, now
        return self.held

    def update(self, kind):
        if self.disabled or kind not in ("pressure", "tick", "cross"):
            return
        try:
            if kind == "tick":
                self.kick += tokens.SPRING["tick_velocity"]
            elif kind == "cross":
                self.cross_at = time.monotonic()
                self.held = 0.0
            self._draw()
        except Exception:
            self._fail()

    def _tick(self, _timer):
        if self.disabled:
            self.timer.stop()
            return
        try:
            self._draw()
        except Exception:
            self._fail()

    def _draw(self):
        now = time.monotonic()
        level = self._held(self.controller.crossing_pressure_now(), now)
        flash = 0.0
        trace = None
        if self.cross_at is not None:
            after = after_seconds(self.controller)
            phase = breakthrough_phase(now - self.cross_at, after)
            if phase is None:
                self.cross_at = None
            else:
                level, flash = max(level, phase[0]), phase[1]
                trace = trace_head((now - self.cross_at) / after, after)
        if not self.visible:
            if level <= 0.0:
                return
            if not self._show():
                return
        reduce_motion = AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()
        if reduce_motion:
            self._draw_reduced(level)
        else:
            self._draw_moving(level, flash, trace)
        if level > 0.0 or self.cross_at is not None:
            self.rest_at = None
        elif self.rest_at is None:
            self.rest_at = now
        elif now - self.rest_at > 0.6:
            self._hide()
            return
        if not getattr(self.timer, "is_alive", lambda: False)():
            self.timer.start()

    def _draw_moving(self, level, flash, trace=None):
        grow_w = level * tokens.ISLAND["grow_width"]
        grow_d = level * tokens.ISLAND["grow_depth"]
        kick, self.kick = self.kick, 0.0
        if self.target is None or kick or abs(self.target[0] - grow_w) >= 0.5 or abs(self.target[1] - grow_d) >= 0.5:
            self.target = (grow_w, grow_d)
            params = tokens.SPRING["island"]
            span = max(1.0, abs(grow_w - self._presented_width()))
            self._retarget(grow_w, grow_d, params["stiffness"], params["damping"], kick / span)
        with _no_actions():
            self.island.setOpacity_(1.0)
            self.rim.setStrokeColor_(_colour(FLASH_RIM) if flash > 0 else _colour("signal"))
            self.rim.setOpacity_(min(1.0, 0.25 + level * 0.9 + flash) if level > 0 or flash > 0 else 0.0)
            self.rim.setShadowOpacity_(min(1.0, level * 0.75 + flash))
            self.rim.setShadowRadius_(7.0 + flash * 7.0)
            if trace is None:
                self.trace.setOpacity_(0.0)
            else:
                self.trace.setStrokeStart_(max(0.0, trace - TRACE_LENGTH))
                self.trace.setStrokeEnd_(min(1.0, trace))
                self.trace.setOpacity_(1.0)
            meter = max(0.0, min(1.0, (grow_d - tokens.ISLAND["meter_after_depth"]) / 8.0))
            self._set_meter(level, meter)

    def _draw_reduced(self, level):
        full_w, full_d = tokens.ISLAND["grow_width"], tokens.ISLAND["grow_depth"]
        if self.target != (full_w, full_d):
            self.target = (full_w, full_d)
            with _no_actions():
                self.island.removeAllAnimations()
                self.rim.removeAllAnimations()
                self.island.setPath_(self._island_path(full_w, full_d))
                self.rim.setPath_(self._rim_path(full_w, full_d))
        with _no_actions():
            self.island.setOpacity_(min(1.0, level * 4.0))
            self.rim.setStrokeColor_(_colour("signal"))
            self.rim.setOpacity_(level)
            self.rim.setShadowOpacity_(level * 0.75)
            self._set_meter(level, level)

    def _retarget(self, grow_w, grow_d, stiffness, damping, velocity):
        layers = (
            (self.island, self._island_path(grow_w, grow_d)),
            (self.rim, self._rim_path(grow_w, grow_d)),
            (self.trace, self._rim_path(grow_w, grow_d)),
        )
        for layer, path in layers:
            presented = layer.presentationLayer()
            start = (presented or layer).path()
            with _no_actions():
                layer.setPath_(path)
            layer.addAnimation_forKey_(_spring("path", stiffness, damping, start, path, velocity), "path")

    def _presented_width(self):
        presented = self.island.presentationLayer()
        box = Quartz.CGPathGetBoundingBox((presented or self.island).path())
        return max(0.0, (box.size.width - 2 * SHOULDER - self.notch_width) / 2.0)

    def _set_meter(self, level, opacity):
        self.meter.setOpacity_(opacity)
        for index, bar in enumerate(self.segments):
            share = max(0.0, min(1.0, level * len(self.segments) - index))
            frame = bar.frame()
            bar.setFrame_(((frame.origin.x, frame.origin.y), (self.segment_width * share, frame.size.height)))
        self.percent.setString_(str(round(level * 100)))

    def _show(self):
        geometry = notch_geometry()
        if geometry is None:
            return False
        if geometry != self.geometry or self.panel is None:
            self.geometry = geometry
            self._build(geometry)
        self.panel.orderFrontRegardless()
        self.visible = True
        return True

    def _build(self, geometry):
        left, right, top, height = geometry
        self.notch_width = right - left
        self.notch_height = height or NOTCH_FALLBACK_HEIGHT
        spare = tokens.ISLAND["grow_width"] * OVERSHOOT + SHOULDER + PAD
        width = self.notch_width + 2 * spare
        panel_height = self.notch_height + tokens.ISLAND["grow_depth"] * OVERSHOOT + PAD
        self.centre = width / 2.0
        panel, stage = self._panel(AppKit.NSMakeRect(left - spare, top - panel_height, width, panel_height))
        scale = panel.backingScaleFactor()

        island = Quartz.CAShapeLayer.layer()
        island.setFrame_(stage.bounds())
        island.setFillColor_(AppKit.NSColor.blackColor().CGColor())
        island.setPath_(self._island_path(0.0, 0.0))
        stage.addSublayer_(island)

        below = Quartz.CALayer.layer()
        below.setFrame_(((0, self.notch_height), (width, panel_height - self.notch_height)))
        below.setBackgroundColor_(AppKit.NSColor.blackColor().CGColor())

        rim = Quartz.CAShapeLayer.layer()
        rim.setFrame_(stage.bounds())
        rim.setFillColor_(None)
        rim.setStrokeColor_(_colour("signal"))
        rim.setLineWidth_(2.0)
        rim.setPath_(self._rim_path(0.0, 0.0))
        rim.setShadowColor_(_colour("signal"))
        rim.setShadowOffset_((0.0, 0.0))
        rim.setShadowRadius_(7.0)
        rim.setOpacity_(0.0)
        rim.setMask_(below)
        stage.addSublayer_(rim)

        # A layer can mask only one other, so the trace gets its own copy of `below`.
        trace_below = Quartz.CALayer.layer()
        trace_below.setFrame_(below.frame())
        trace_below.setBackgroundColor_(AppKit.NSColor.blackColor().CGColor())
        trace = Quartz.CAShapeLayer.layer()
        trace.setFrame_(stage.bounds())
        trace.setFillColor_(None)
        trace.setStrokeColor_(_colour(FLASH_RIM))
        trace.setLineWidth_(2.5)
        trace.setLineCap_(Quartz.kCALineCapRound)
        trace.setPath_(self._rim_path(0.0, 0.0))
        trace.setShadowColor_(_colour(FLASH_RIM))
        trace.setShadowOffset_((0.0, 0.0))
        trace.setShadowRadius_(6.0)
        trace.setShadowOpacity_(1.0)
        trace.setOpacity_(0.0)
        trace.setMask_(trace_below)
        stage.addSublayer_(trace)

        meter = Quartz.CALayer.layer()
        meter.setFrame_(stage.bounds())
        meter.setOpacity_(0.0)
        stage.addSublayer_(meter)
        count = tokens.ISLAND["meter_segments"]
        self.segment_width = 30.0
        pitch = 34.0
        first = self.centre - 98.0
        bar_top = self.notch_height + 7.0
        self.segments = []
        for index in range(count):
            track = Quartz.CALayer.layer()
            track.setFrame_(((first + index * pitch, bar_top), (self.segment_width, 4.0)))
            track.setCornerRadius_(1.0)
            track.setBackgroundColor_(_colour("rule"))
            meter.addSublayer_(track)
            bar = Quartz.CALayer.layer()
            bar.setFrame_(((first + index * pitch, bar_top), (0.0, 4.0)))
            bar.setCornerRadius_(1.0)
            bar.setBackgroundColor_(_colour("signal"))
            meter.addSublayer_(bar)
            self.segments.append(bar)
        percent = Quartz.CATextLayer.layer()
        percent.setFrame_(((self.centre + 98.0 - 40.0, bar_top - 5.0), (40.0, 14.0)))
        percent.setFont_(tokens.MONO_FAMILY)
        percent.setFontSize_(10.0)
        percent.setForegroundColor_(_colour("ink"))
        percent.setAlignmentMode_(Quartz.kCAAlignmentRight)
        percent.setContentsScale_(scale)
        meter.addSublayer_(percent)

        self.panel = panel
        self.island = island
        self.rim = rim
        self.trace = trace
        self.meter = meter
        self.percent = percent
        self.target = None

    def _panel(self, frame):
        """A borderless, click-through panel over the notch holding one top-left-origin stage
        layer, shared with the beam style so both sit exactly where the notch is."""
        width, panel_height = frame.size.width, frame.size.height
        if self.panel is not None:
            self.panel.orderOut_(None)
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        # Above the menu bar, which is where most of the island's width lives.
        panel.setLevel_(AppKit.NSPopUpMenuWindowLevel)
        panel.setOpaque_(False)
        panel.setHasShadow_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        view = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, width, panel_height))
        view.setWantsLayer_(True)
        panel.setContentView_(view)

        # Top-left origin inside the island, so every path below reads like the design's SVG. The
        # window server puts a window on whole points, so the stage is offset by whatever that
        # rounding moved: the island asked for 88.5pt from x 689.5 and got 89 from x 689, which
        # anchored at the bottom left a one-pixel row of wallpaper above the black.
        actual = panel.frame()
        stage = Quartz.CALayer.layer()
        stage.setFrame_(((frame.origin.x - actual.origin.x, frame.origin.y - actual.origin.y), (width, panel_height)))
        stage.setGeometryFlipped_(True)
        view.layer().addSublayer_(stage)
        return panel, stage

    def _outline(self, grow_w, grow_d, closed, shoulders=None):
        """The notch's outline grown by `grow_w` each side and `grow_d` down. `shoulders` adds the
        concave curves out along the top edge, which a closed fill always has and the beam's open
        border wants too; a closed outline also closes along the top."""
        shoulders = closed if shoulders is None else shoulders
        width = self.notch_width + 2 * grow_w
        depth = self.notch_height + grow_d
        x0 = self.centre - width / 2.0
        x1 = self.centre + width / 2.0
        radius = 10.0 + grow_d * 0.35
        path = Quartz.CGPathCreateMutable()
        if shoulders:
            Quartz.CGPathMoveToPoint(path, None, x0 - SHOULDER, 0.0)
            Quartz.CGPathAddQuadCurveToPoint(path, None, x0, 0.0, x0, SHOULDER)
        else:
            Quartz.CGPathMoveToPoint(path, None, x0, SHOULDER)
        Quartz.CGPathAddLineToPoint(path, None, x0, depth - radius)
        Quartz.CGPathAddQuadCurveToPoint(path, None, x0, depth, x0 + radius, depth)
        Quartz.CGPathAddLineToPoint(path, None, x1 - radius, depth)
        Quartz.CGPathAddQuadCurveToPoint(path, None, x1, depth, x1, depth - radius)
        Quartz.CGPathAddLineToPoint(path, None, x1, SHOULDER)
        if shoulders:
            Quartz.CGPathAddQuadCurveToPoint(path, None, x1, 0.0, x1 + SHOULDER, 0.0)
        if closed:
            Quartz.CGPathCloseSubpath(path)
        return path

    def _island_path(self, grow_w, grow_d):
        return self._outline(grow_w, grow_d, closed=True)

    def _rim_path(self, grow_w, grow_d):
        return self._outline(grow_w, grow_d, closed=False)

    def _hide(self):
        self.timer.stop()
        self.rest_at = None
        self.target = None
        if self.panel is not None and self.visible:
            self.panel.orderOut_(None)
        self.visible = False

    def _fail(self):
        self.disabled = True
        self.logger.exception("notch island failed; notch feedback off for this run")
        try:
            self._hide()
        except Exception:
            pass


class _no_actions:
    def __enter__(self):
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)

    def __exit__(self, *_exc):
        Quartz.CATransaction.commit()
        return False
