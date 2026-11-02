"""Live previews for the Design page: the notch and edge animations, played in the settings window by
the very classes that draw them on screen, so a preview cannot drift from the real thing. The crossing
effects' previews at the end of the file do the same through effects_overlay's replay.

Each renderer is hosted in a view rather than a screen-level panel. Only where it draws, and how it
is shown and hidden, is redirected; everything else runs as it does on screen. PreviewFeed stands in
for the controller: the live settings, so a change shows at once, with a scripted push instead of a
trackpad. The script builds pressure, goes through, keeps animating for the chosen time and rests,
on a loop. PreviewLoop plays a notch tile only while the pointer is over it, and only while the Design
page is showing.

Main thread only. A failing preview disables itself as the real renderer does; nothing depends on it.
"""

import time
import types
import math

import AppKit
import Quartz
import objc

import crossing
from core import effects
import effects_overlay
from frame_clock import PREVIEW_MAX_RATE, FrameClock
import motion
import notch_beam
import notch_island
from core.i18n import N_
import theme
import tokens

PUSH_S = 1.8
REST_S = 1.0
FALLBACK_NOTCH = (185.0, 32.0)


def preview_frame(elapsed, after):
    """(pressure, phase) `elapsed` seconds into the loop: pressure builds for PUSH_S and goes
    through, the crossing plays for `after` seconds, then everything rests for REST_S and it starts
    again. Phases are push, after and rest."""
    t = elapsed % (PUSH_S + after + REST_S)
    if t < PUSH_S:
        return (t / PUSH_S) ** 1.6, "push"
    return 0.0, "after" if t < PUSH_S + after else "rest"


def notch_size():
    """This Mac's notch as (width, height), or a typical MacBook's when it has none at the top of
    the desktop, so the Notch previews still show what the styles look like."""
    try:
        geometry = notch_island.notch_geometry()
    except Exception:
        geometry = None
    if geometry is None:
        return FALLBACK_NOTCH
    left, right, _top, height = geometry
    return (right - left, height)


class PreviewFeed:
    """What a hosted renderer reads from its controller. `overrides` pins crossing settings for one
    preview, such as the style a tile shows, while the rest follow the live settings."""

    def __init__(self, controller, **overrides):
        self.controller = controller
        self.overrides = overrides
        self.level = 0.0
        self.crossing = types.SimpleNamespace(touching=False)

    @property
    def cfg(self):
        cfg = self.controller.cfg
        if not self.overrides:
            return cfg
        return types.SimpleNamespace(crossing=dict(cfg.crossing, **self.overrides))

    def crossing_pressure_now(self):
        return self.level

    def _current_desktop_bounds(self):
        """A preview draws in its own small scene, not on the desktop, so there are no displays
        to cut its band to."""
        return None


class _Panel:
    """The panel calls the renderers make, answered by a view in the settings window."""

    def __init__(self, view, set_hidden):
        self.view = view
        self.set_hidden = set_hidden

    def contentView(self):
        return self.view

    def backingScaleFactor(self):
        window = self.view.window()
        return window.backingScaleFactor() if window is not None else 2.0

    def setFrame_display_(self, frame, _display):
        self.view.setFrame_(frame)

    def orderFrontRegardless(self):
        self.set_hidden(False)

    def orderOut_(self, _sender):
        self.set_hidden(True)


def hosted_notch(base):
    """NotchBeam or NotchIsland, drawing into a PreviewScreen instead of over the notch."""

    class HostedNotch(base):
        # On the window's screen, not the desktop: the window's palette decides which variant draws.
        dark_appearance = staticmethod(theme.is_dark)

        def __init__(self, feed, logger, screen):
            super().__init__(feed, logger)
            self.screen = screen

        def _show(self):
            if self.panel is None:
                width, height = self.screen.notch
                self._build((0.0, width, 0.0, height))
            self.panel.orderFrontRegardless()
            self.visible = True
            return True

        def _panel(self, frame):
            stage = Quartz.CALayer.layer()
            stage.setBounds_(((0.0, 0.0), (frame.size.width, frame.size.height)))
            stage.setGeometryFlipped_(True)
            stage.setAnchorPoint_((0.5, 1.0))
            self.screen.hold(stage)
            return _Panel(self.screen, stage.setHidden_), stage

    return HostedNotch


class HostedNotchScene(effects_overlay.EffectsCanvas):
    """A complete notch crossing with the real hosted NotchBeam or NotchIsland layer tree."""

    @objc.python_method
    def setup(self, base, controller, logger, style, colour, notch):
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.widthAnchor().constraintEqualToAnchor_multiplier_(self.heightAnchor(), 2.0).setActive_(True)
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(theme.RADIUS["field"])
        self.layer().setMasksToBounds_(True)
        self.logger = logger
        self.playing_since = None
        self.style = style
        self.colour = colour
        self.notch = notch
        self.controller = controller
        self.still_at = 1.9
        self.after = lambda: controller.cfg.crossing.get("notch_after_ms", 1200) / 1000.0
        self.feed = PreviewFeed(controller, glow_colour=colour(), notch_style=style)
        self.renderer = hosted_notch(base)(self.feed, logger, self._layer_host(notch))
        self.renderer._show()
        self.stage = self.renderer.screen.stage
        self.stage.removeFromSuperlayer()
        with notch_island._no_actions():
            self.stage.setAnchorPoint_((0.0, 0.0))
            self.stage.setPosition_((0.0, 0.0))
            self.stage.setAffineTransform_(Quartz.CGAffineTransformIdentity)
        self.painter = lambda ctx, width, height: self.paint_frame(ctx, width, height, self._time(),
                                                                   self._live())
        return self

    @staticmethod
    def _layer_host(notch):
        host = PreviewScreen.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 600, 540))
        host.setup(notch)
        host.setFrame_(AppKit.NSMakeRect(0, 0, 600, 540))
        host.arrange()
        return host

    @objc.python_method
    def _time(self):
        since = self.playing_since
        elapsed = None if since is None else time.monotonic() - since
        return effects.preview_time(self.still_at, elapsed, effects_overlay.reduce_motion())

    @objc.python_method
    def _live(self):
        return (self.playing_since is not None and time.monotonic() >= self.playing_since
                and not effects_overlay.reduce_motion())

    @objc.python_method
    def paint_frame(self, ctx, width, height, at=None, live=False):
        """Paint a deterministic frame; callers may supply `t` for offline judging renders."""
        t = self._time() if at is None else at
        t %= effects.PREVIEW_LOOP_S
        current_colour = self.colour()
        dark = theme.is_dark()
        feel = self.controller.cfg.crossing
        pace = feel.get("effect_pace", effects.pace(feel.get("effect_length")))
        size = feel.get("effect_size", "medium")
        size_fraction = feel.get("effect_size_fraction")
        fx = effects.preview_effect("glow")
        crop = effects.still_view(fx, "notch", size, pace, size_fraction)
        scene = effects.preview_still_scene(fx, t, "notch", effects_overlay.palette(current_colour),
                                            dark=dark, pace=pace, effect_size=size, live=live,
                                            view_width=crop[2], effect_size_fraction=size_fraction,
                                            notch_geometry=self.notch)
        scene["pens"] = []
        effects_overlay.draw_scene(Quartz, ctx, scene, width, height, "notch", dark, crop=crop, pointer=11.0)
        self._set_renderer_frame(t, current_colour, dark)
        x, y, w, h, _radius = scene["notch"]
        stage_bounds = self.stage.bounds()
        stage_width = stage_bounds.size.width
        scene_w, scene_h = crop[2:]
        scale = min(width / scene_w, height / scene_h)
        Quartz.CGContextSaveGState(ctx)
        try:
            Quartz.CGContextTranslateCTM(ctx, (width - scene_w * scale) / 2.0,
                                          (height - scene_h * scale) / 2.0)
            Quartz.CGContextScaleCTM(ctx, scale, scale)
            Quartz.CGContextTranslateCTM(ctx, -crop[0], -crop[1])
            Quartz.CGContextClipToRect(ctx, ((crop[0], crop[1]), (scene_w, scene_h)))
            # The renderer's layers are a real notch's size, as the scene's effects are drawn on a
            # screen PREVIEW_REAL times its own and shrunk.
            Quartz.CGContextTranslateCTM(ctx, x + w / 2.0, y)
            Quartz.CGContextScaleCTM(ctx, 1.0 / effects.PREVIEW_REAL, 1.0 / effects.PREVIEW_REAL)
            Quartz.CGContextTranslateCTM(ctx, -stage_width / 2.0, 0.0)
            self.stage.renderInContext_(ctx)
        finally:
            Quartz.CGContextRestoreGState(ctx)

    @objc.python_method
    def _set_renderer_frame(self, t, colour, dark):
        """Set the existing renderer's layers directly from the shared crossing clock."""
        renderer = self.renderer
        push_start, cross_at = effects._P_PUSH, effects._P_CROSS
        after = self.after()
        pressure_end = cross_at
        if t < push_start:
            level, flash, elapsed = 0.0, 0.0, 0.0
        elif t < pressure_end:
            u = min(1.0, max(0.0, (t - push_start) / (pressure_end - push_start)))
            level, flash, elapsed = (1.0 - math.cos(math.pi * u)) / 2.0, 0.0, 0.0
        else:
            elapsed = t - cross_at
            phase = notch_island.breakthrough_phase(elapsed, after)
            level, flash = (1.0, phase[1]) if phase is not None else (0.0, 0.0)
        if isinstance(renderer, notch_beam.NotchBeam):
            self._set_beam_frame(renderer, level, flash, elapsed, colour, dark, after, t)
        else:
            self._set_island_frame(renderer, level, flash, elapsed, after)

    @staticmethod
    def _set_beam_frame(renderer, level, flash, elapsed, colour, dark, after, t):
        base = notch_beam.LAP * 0.3
        if t < effects._P_CROSS:
            position = (base + notch_beam.LAP * max(0.0, t - effects._P_PUSH)
                        / notch_beam.turn_seconds(level)) % notch_beam.LAP
        else:
            crossed = (base + notch_beam.LAP * (effects._P_CROSS - effects._P_PUSH)
                       / notch_beam.turn_seconds(1.0)) % notch_beam.LAP
            travel = notch_beam.lap_finish(crossed, after)
            position = (crossed + travel * notch_island.ease_out(elapsed / max(after, 0.001))) % notch_beam.LAP
        spans = notch_beam.comet_segments(position)
        with notch_island._no_actions():
            for gradient in renderer.gradients:
                gradient.setColors_(notch_beam.palette_colours(colour, closed=True, dark=dark))
            for segments in renderer.comets:
                for layer, (start, end) in zip(segments, spans):
                    layer.setHidden_(end - start < 0.002)
                    layer.setStrokeStart_(start)
                    layer.setStrokeEnd_(end)
            renderer.island.setOpacity_(min(1.0, level * 20.0))
            renderer.beam.setOpacity_(min(1.0, level + flash))
            renderer.bloom.setOpacity_(min(1.0, level * 0.8 + flash * 0.6))
            renderer.halo.setOpacity_(max(0.0, 1.0 - elapsed / notch_beam.HALO_S) * 0.9 if elapsed > 0 else 0.0)

    @staticmethod
    def _set_island_frame(renderer, level, flash, elapsed, after):
        grow_w = level * tokens.ISLAND["grow_width"]
        grow_d = level * tokens.ISLAND["grow_depth"]
        with notch_island._no_actions():
            renderer.island.removeAllAnimations()
            renderer.rim.removeAllAnimations()
            renderer.island.setPath_(renderer._island_path(grow_w, grow_d))
            renderer.rim.setPath_(renderer._rim_path(grow_w, grow_d))
            renderer.trace.setPath_(renderer._rim_path(grow_w, grow_d))
            renderer.island.setOpacity_(1.0 if level > 0.0 else 0.0)
            renderer.rim.setStrokeColor_(notch_island._colour(notch_island.FLASH_RIM) if flash else
                                          notch_island._colour("signal"))
            renderer.rim.setOpacity_(min(1.0, 0.25 + level * 0.9 + flash) if level or flash else 0.0)
            renderer.rim.setShadowOpacity_(min(1.0, level * 0.75 + flash))
            renderer.rim.setShadowRadius_(7.0 + flash * 7.0)
            if elapsed > 0.0:
                progress = min(1.0, elapsed / max(after, 0.001))
                head = notch_island.trace_head(progress, after)
                renderer.trace.setStrokeStart_(max(0.0, head - notch_island.TRACE_LENGTH))
                renderer.trace.setStrokeEnd_(min(1.0, head))
                renderer.trace.setOpacity_(1.0)
            else:
                renderer.trace.setOpacity_(0.0)
            meter = max(0.0, min(1.0, (grow_d - tokens.ISLAND["meter_after_depth"]) / 8.0))
            renderer._set_meter(level, meter)
            renderer.meter.setOpacity_(meter)


def notch_crossing_still(base, colour, logger, controller=None, notch=None):
    """A notch style tile backed by its real renderer and the shared two-screen crossing scene."""
    if controller is None:
        cfg = types.SimpleNamespace(crossing={"notch_after_ms": 1200, "glow_colour": colour()})
        controller = types.SimpleNamespace(cfg=cfg)
    style = "beam" if issubclass(base, notch_beam.NotchBeam) else "island"
    view = HostedNotchScene.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 1, 1))
    if notch is None:
        notch = (effects.PREVIEW_NOTCH["w"], effects.PREVIEW_NOTCH["h"])
    return view.setup(base, controller, logger, style, colour, notch)


def hosted_edge(base):
    """The app's EdgeGlow, drawing its band along the right side of a PreviewScreen."""

    class HostedEdge(base):
        # On the window's screen, not the desktop: the window's palette decides which variant draws.
        dark_appearance = staticmethod(theme.is_dark)

        def __init__(self, feed, logger, screen):
            super().__init__(feed, logger)
            self.screen = screen

        def _ensure_panel(self):
            if self.panel is not None:
                return
            band = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 1, 1))
            band.setWantsLayer_(True)
            self.screen.addSubview_(band)
            self.fill = Quartz.CAGradientLayer.layer()
            band.layer().addSublayer_(self.fill)
            self.comet = Quartz.CAGradientLayer.layer()
            self.panel = _Panel(band, band.setHidden_)

        def _band_frame(self, band):
            bounds = self.screen.bounds()
            return AppKit.NSMakeRect(bounds.size.width - band, 0, band, bounds.size.height)

    return HostedEdge


class PreviewScreen(AppKit.NSView):
    """The little desktop a preview plays on: a wallpaper-toned ground so a black notch tab reads
    against it and, for a notch preview, the notch itself drawn over whatever the renderer puts
    there, as the hardware covers it. The renderer's stage is scaled so the notch takes about 60% of
    the width, and never larger than life."""

    def layout(self):
        objc.super(PreviewScreen, self).layout()
        self.arrange()

    @objc.python_method
    def setup(self, notch=None):
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.setWantsLayer_(True)
        layer = self.layer()
        layer.setCornerRadius_(theme.RADIUS["field"])
        layer.setMasksToBounds_(True)
        layer.setBorderWidth_(1)
        theme.tint(layer, border="rule")
        self.ground = Quartz.CAGradientLayer.layer()
        theme.tint(self.ground, colours=["edge", "well"])
        self.ground.setStartPoint_((0.5, 1.0))
        self.ground.setEndPoint_((0.5, 0.0))
        layer.addSublayer_(self.ground)
        self.notch = notch
        self.stage = None
        self.cutout = None
        if notch is not None:
            self.cutout = Quartz.CAShapeLayer.layer()
            self.cutout.setFillColor_(AppKit.NSColor.blackColor().CGColor())
            layer.addSublayer_(self.cutout)
        return self

    @objc.python_method
    def hold(self, stage):
        if self.stage is not None:
            self.stage.removeFromSuperlayer()
        self.stage = stage
        if self.cutout is not None:
            self.layer().insertSublayer_below_(stage, self.cutout)
        else:
            self.layer().addSublayer_(stage)
        self.arrange()

    @objc.python_method
    def arrange(self):
        if getattr(self, "ground", None) is None:
            return
        bounds = self.bounds()
        width, height = bounds.size.width, bounds.size.height
        scale = min(1.0, width * 0.6 / self.notch[0]) if self.notch is not None else 1.0
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        try:
            self.ground.setFrame_(bounds)
            if self.stage is not None:
                self.stage.setPosition_((width / 2.0, height))
                self.stage.setAffineTransform_(Quartz.CGAffineTransformMakeScale(scale, scale))
            if self.cutout is not None:
                notch_width, notch_height = self.notch[0] * scale, self.notch[1] * scale
                radius = 7.0 * scale
                # Taller than the notch by its radius, so only the bottom corners show rounded.
                rect = ((width / 2.0 - notch_width / 2.0, height - notch_height), (notch_width, notch_height + radius))
                self.cutout.setPath_(Quartz.CGPathCreateWithRoundedRect(rect, radius, radius, None))
        finally:
            Quartz.CATransaction.commit()


HOVER_INTENT_S = 0.1


def on_screen(view):
    """Whether any of `view` can be seen: in a window on screen and not covered, not hidden, and not
    scrolled out of its page."""
    window = view.window()
    if window is None or not window.isVisible() or view.isHiddenOrHasHiddenAncestor():
        return False
    if not window.occlusionState() & AppKit.NSWindowOcclusionStateVisible:
        return False
    visible = view.visibleRect()
    return visible.size.width > 0 and visible.size.height > 0


class PreviewLoop:
    """Plays the page's hosted renderers from one clock: the tile the pointer is over, else the
    tiles set `resting`. Every other tile holds a still, the push part way in, and a tile that starts
    playing goes on from that push rather than from nothing."""

    HOLD = 0.7

    def __init__(self, controller, host=None):
        """`host` is a view on screen whenever the loop runs, the page itself, to time it by: a
        view's display link stops while the view is hidden, as a tile's is in the other mode."""
        self.controller = controller
        self.players = []
        self.running = False
        self.resting = ()
        self.hovered = None
        self.hovered_at = 0.0
        self.clock = None if host is None else FrameClock(host, self._tick, PREVIEW_MAX_RATE)

    def add(self, feed, renderer, update, screen):
        """`update(kind, pressure)` passes an event to `renderer` the way the app's crossing
        feedback would. The renderer's own timer is swapped for a FrameClock on `screen`."""
        renderer.timer = FrameClock(screen, lambda: renderer._tick(None), PREVIEW_MAX_RATE)
        self.players.append(types.SimpleNamespace(feed=feed, renderer=renderer, update=update, screen=screen,
                                                  started_at=None, phase=None, quarter=0))
        if self.clock is None:
            self.clock = FrameClock(screen, self._tick, PREVIEW_MAX_RATE)

    def owns(self, view):
        return any(player.screen is view for player in self.players)

    def set_resting(self, screens):
        self.resting = tuple(screens)

    def hover(self, view):
        """`view` is the preview of the tile under the pointer, one of this loop's or not, or None."""
        self.hovered = view
        self.hovered_at = time.monotonic()
        # Under Reduce Motion nothing plays, so the clock has nothing to drive.
        if self.running and self.owns(view) and self.clock is not None and not effects_overlay.reduce_motion():
            self.clock.start()

    def start(self):
        if self.running:
            self.sync_clock()
            return
        self.running = True
        self.repaint()
        if self.owns(self.hovered) and self.clock is not None:
            self.clock.start()

    def sync_clock(self):
        if self.clock is None:
            return
        wanted = (self.running and not effects_overlay.reduce_motion()
                  and any(on_screen(player.screen) and
                          (player.screen is self.hovered or player.screen in self.resting)
                          for player in self.players))
        if wanted:
            self.clock.start()
        else:
            for player in self.players:
                if player.started_at is not None:
                    player.started_at = None
                    self._hold(player)
            self.clock.stop()

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.clock is not None:
            self.clock.stop()
        for player in self.players:
            player.started_at = None
            player.feed.level = 0.0
            player.feed.crossing.touching = False
            try:
                player.renderer._hide()
                player.renderer.timer.stop()
            except Exception:
                pass

    def repaint(self):
        """Draws every tile that is not playing again, as a colour or palette change needs."""
        for player in self.players:
            if player.started_at is None:
                self._hold(player)

    def _wanted(self, player):
        # A pointer only passing over, or any under Reduce Motion, leaves the resting tile playing.
        if (self.hovered is not None and time.monotonic() - self.hovered_at >= HOVER_INTENT_S
                and not effects_overlay.reduce_motion()):
            return player.screen is self.hovered
        return player.screen in self.resting

    def _hold(self, player):
        feed, renderer = player.feed, player.renderer
        feed.level = self.HOLD
        feed.crossing.touching = True
        try:
            # Left mid-breakthrough, a renderer would freeze its flash, halo and run-off into the
            # still and snap to the end of the crossing when it next played.
            for name, value in (("cross_at", None), ("halo_at", None), ("flash_at", None), ("flare", 0.0),
                                ("kick", 0.0), ("held", 0.0), ("finish", 0.0)):
                if hasattr(renderer, name):
                    setattr(renderer, name, value)
            player.update("pressure", self.HOLD)
            # Drawn once and left: a moving light part way along, at full strength rather than
            # easing up to it from nothing.
            if hasattr(renderer, "finish"):
                renderer.centre = 0.5
            if hasattr(renderer, "cross_travel"):
                renderer.position = notch_beam.LAP * 0.3
                renderer.strength = self.HOLD
            if hasattr(renderer, "drawn_at"):
                renderer.drawn_at = None
            renderer._draw()
            renderer.timer.stop()
        except Exception:
            pass

    def _tick(self):
        if not self.running:
            return
        now = time.monotonic()
        after = self.controller.cfg.crossing["notch_after_ms"] / 1000.0
        for player in self.players:
            playing = self._wanted(player) and on_screen(player.screen)
            if not playing:
                if player.started_at is not None:
                    player.started_at = None
                    motion.cross_fade(player.screen)
                    self._hold(player)
                continue
            if player.started_at is None:
                # Where the script's push reaches the held still's pressure.
                player.started_at = now - PUSH_S * self.HOLD ** (1 / 1.6)
                player.phase, player.quarter = "push", min(3, int(4 * self.HOLD))
            level, phase = preview_frame(now - player.started_at, after)
            quarter = min(3, int(4 * level)) if phase == "push" else 0
            crossed = phase == "after" and player.phase == "push"
            ticked = quarter > player.quarter
            player.phase, player.quarter = phase, quarter
            player.feed.level = level
            player.feed.crossing.touching = phase == "push"
            if crossed:
                player.update("cross", 1.0)
            elif phase == "push" and level > 0.0:
                player.update("tick" if ticked else "pressure", level)
        self.sync_clock()


class TileStack(AppKit.NSView):
    """A tile's picture that is one of several views laid over each other, only one shown: Glow and
    Beam's still, or at the notch, where the notch style plays in their place, that style's own
    hosted preview."""

    def layout(self):
        objc.super(TileStack, self).layout()
        if self.shown is not None:
            self.shown.setFrame_(self.bounds())
            self.shown.setNeedsLayout_(True)

    @objc.python_method
    def setup(self, children, height=84):
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.widthAnchor().constraintEqualToAnchor_multiplier_(self.heightAnchor(), 2.0).setActive_(True)
        self.children = list(children)
        for child in self.children:
            child.setTranslatesAutoresizingMaskIntoConstraints_(False)
            AppKit.NSLayoutConstraint.deactivateConstraints_([
                c for c in child.constraints()
                if c.firstItem() == child and c.secondItem() in (None, child)
            ])
            self.addSubview_(child)
        self.shown = None
        self.show(self.children[0])
        return self

    @objc.python_method
    def show(self, child):
        if child is self.shown:
            return
        if self.shown is not None:
            motion.cross_fade(self)
        self.shown = child
        for other in self.children:
            other.setHidden_(other is not child)
        self.setNeedsLayout_(True)

    @objc.python_method
    def current(self):
        return self.shown


class _HoverOwner(AppKit.NSObject):
    def mouseEntered_(self, _event):
        self.entered()

    def mouseExited_(self, _event):
        self.exited()


class TileHover:
    """The hovered tile plays after intent and restores its still on leaving. Crossings begin
    with approach; shortcut arrivals resume their still. Reduce Motion keeps the still."""

    def __init__(self, loop):
        self.loop = loop
        self.owners = []
        self.current = None
        # What plays for the tile under the pointer: its preview, or the one a TileStack shows.
        self.target = None
        self.clock = None

    def track(self, tile, preview):
        owner = _HoverOwner.alloc().init()
        owner.entered = lambda: (tile.set_tile_state(hovered=True), self.enter(preview))
        owner.exited = lambda: (tile.set_tile_state(hovered=False), self.leave(preview))
        options = (AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInActiveApp
                   | AppKit.NSTrackingInVisibleRect)
        tile.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            AppKit.NSZeroRect, options, owner, None))
        tile._tile_hover_owner = owner
        # A tracking area does not keep its owner.
        self.owners.append(owner)

    def enter(self, preview):
        if self.current is preview:
            return
        self.stop()
        self.current = preview
        target = self.target = preview.current() if isinstance(preview, TileStack) else preview
        self.loop.hover(target)
        if self.loop.owns(target) or effects_overlay.reduce_motion():
            return
        # A pointer passing over on its way elsewhere leaves the tiles it crosses still.
        target.playing_since = time.monotonic() + HOVER_INTENT_S
        self.clock = FrameClock(target, self._tick, PREVIEW_MAX_RATE)
        self.clock.start()

    def sync_clock(self):
        if self.clock is None:
            return
        if on_screen(self.target) and not effects_overlay.reduce_motion():
            self.clock.start()
        else:
            self.clock.stop()

    def _tick(self):
        self.sync_clock()
        if self.clock is not None and self.clock.is_alive():
            self.target.setNeedsDisplay_(True)

    def leave(self, preview):
        if preview is self.current:
            self.stop()

    def stop(self):
        preview, self.current, self.target = self.target, None, None
        self.loop.hover(None)
        if self.clock is not None:
            self.clock.stop()
            self.clock = None
        if preview is not None and getattr(preview, "playing_since", None) is not None:
            if time.monotonic() > preview.playing_since:
                motion.cross_fade(preview)
            preview.playing_since = None
            preview.setNeedsDisplay_(True)


def edge_update(renderer):
    step = crossing.Step
    return lambda kind, level: renderer.update(kind, step(pressure=level, mac_edge="right", region=(0, 0, 1, 1)))


def notch_update(renderer):
    return lambda kind, _level: renderer.update(kind)


# The crossing effects' previews: effects.preview_scene drawn by effects_overlay.draw_scene, the same
# replay the overlay uses on screen. Every tile shows one still frame at the place the page's Show at
# chooses, and plays the full journey while TileHover says the pointer is over it.
STAGE_METHODS = (("edge", N_("Edge")), ("corner", N_("Corner")), ("notch", N_("Notch")))


def _played(view, still_at, switch=False):
    """Crossings start at approach; shortcut arrivals resume their specimen frame."""
    since = view.playing_since
    return effects.preview_time(still_at, None if since is None else time.monotonic() - since,
                                effects_overlay.reduce_motion(), switch=switch)


def _live(view):
    return (view.playing_since is not None and time.monotonic() >= view.playing_since
            and not effects_overlay.reduce_motion())


def effect_still(effect_id, colour, logger, height=84, place=lambda: "edge", pace=lambda: 1.0,
                 effect_size=lambda: "medium", effect_size_fraction=lambda: None):
    """A tile's still frame of `effect_id`, in the selected colour, place, length and size."""
    view = effects_overlay.EffectsCanvas.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 1, 1))
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    view.widthAnchor().constraintEqualToAnchor_multiplier_(view.heightAnchor(), 2.0).setActive_(True)
    view.setWantsLayer_(True)
    view.layer().setCornerRadius_(theme.RADIUS["field"])
    view.layer().setMasksToBounds_(True)
    view.layer().setBorderWidth_(0)
    view.logger = logger
    view.playing_since = None

    def paint(ctx, width, height):
        fx = effects.preview_effect(effect_id)
        dark = theme.is_dark()
        where = place()
        current_pace, current_size, size_fraction = pace(), effect_size(), effect_size_fraction()
        held_at = effects.still_time(fx, where, current_size, current_pace, size_fraction)
        crop = effects.still_view(fx, where, current_size, current_pace, size_fraction)
        scene = effects.preview_still_scene(
            fx, _played(view, held_at), where, effects_overlay.palette(colour()), dark=dark,
            pace=current_pace, effect_size=current_size, live=_live(view), view_width=crop[2],
            effect_size_fraction=size_fraction)
        effects_overlay.draw_scene(Quartz, ctx, scene, width, height, where, dark, crop=crop, pointer=11.0)

    view.painter = paint
    return view


def switch_still(fx, colour, logger, height=84, pace=lambda: 1.0, effect_size=lambda: "medium",
                 effect_size_fraction=lambda: None):
    """A tile's still frame of what a switch plays: `fx()` is the Effect, asked when it draws, since
    Same as crossing follows the crossing style."""
    view = effect_still(None, colour, logger, height)

    def paint(ctx, width, height):
        dark = theme.is_dark()
        effect = fx()
        current_pace, current_size, size_fraction = pace(), effect_size(), effect_size_fraction()
        t = _played(view, effects.still_time(effect, "switch", current_size, current_pace, size_fraction), switch=True)
        crop = effects.still_view(effect, "switch", current_size, current_pace, size_fraction)
        scene = effects.preview_still_scene(
            effect, t, "switch", effects_overlay.palette(colour()), dark=dark, pace=current_pace,
            effect_size=current_size, live=_live(view), view_width=crop[2],
            effect_size_fraction=size_fraction)
        effects_overlay.draw_scene(Quartz, ctx, scene, width, height, "switch", dark,
                                   crop=crop, pointer=11.0)

    view.painter = paint
    return view
