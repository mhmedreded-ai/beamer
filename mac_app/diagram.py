"""The Crossing page's two drawings, in Core Animation layers so every change can move: the
arrangement, which shows where the other machine sits and what crosses, and the push strip, which shows how
far the pointer is pushed past the edge before it gives."""

from __future__ import annotations

import math
import copy
import platform
import AppKit
import Quartz
import objc

import motion
import pages
import theme
from core.resistance_strip import MAX_RESISTANCE, resistance_x
from core import ways as core_ways
from core import identify_screens
from core.i18n import N_, tr

POSITION_WORDS = {"right of": N_("right of"), "left of": N_("left of"),
                  "below": N_("below"), "above": N_("above")}
THIRD_WORDS = {"start": N_("start"), "middle": N_("middle"), "end": N_("end")}


def _rect(frame):
    x, y, w, h = frame
    return ((x, y), (w, h))


def _beside_display_numbers(frame, badges):
    x, y, width, height = frame
    spans = [(x, x + width)]
    for badge in badges:
        if badge.isHidden():
            continue
        box = badge.frame()
        if y + height <= box.origin.y or y >= box.origin.y + box.size.height:
            continue
        left, right = box.origin.x - 4, box.origin.x + box.size.width + 4
        spans = [(start, end) for a, b in spans
                 for start, end in ((a, min(b, left)), (max(a, right), b)) if end > start]
    left, right = max(spans, key=lambda span: span[1] - span[0]) if spans else (x, x + 1)
    return left, y, right - left, height


def _set_layer_frame_if_changed(layer, frame):
    x, y, width, height = frame[0][0], frame[0][1], frame[1][0], frame[1][1]
    current = layer.frame()
    if (abs(current.origin.x - x) < 0.01 and abs(current.origin.y - y) < 0.01
            and abs(current.size.width - width) < 0.01
            and abs(current.size.height - height) < 0.01):
        return
    layer.setFrame_(frame)


def _text_width(text, font):
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(
        text, {AppKit.NSFontAttributeName: font}
    ).size().width


def _text_layer(size, ink, align=Quartz.kCAAlignmentCenter):
    layer = Quartz.CATextLayer.layer()
    layer.setFont_(theme.font(size, 500))
    layer.setFontSize_(size)
    theme.tint(layer, foreground=ink)
    layer.setAlignmentMode_(align)
    layer.setTruncationMode_(Quartz.kCATruncationEnd)
    layer.setContentsScale_(2.0)
    return layer


def _text_height(text, font, width):
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(
        text, {AppKit.NSFontAttributeName: font}
    ).boundingRectWithSize_options_((max(1, width), 10000), AppKit.NSStringDrawingUsesLineFragmentOrigin).size.height


def _fit_text(layer, text, frame):
    x, y, width, height = frame
    layer.setString_(text)
    layer.setWrapped_(True)
    layer.setTruncationMode_(Quartz.kCATruncationNone)
    text_height = _text_height(text, layer.font(), width)
    layer.setFrame_(((x, y + max(0, (height - text_height) / 2)), (width, text_height)))


def _box(fill=None, stroke=None, radius=0.0):
    layer = Quartz.CALayer.layer()
    theme.tint(layer, background=fill, border=stroke)
    if stroke:
        layer.setBorderWidth_(1)
    layer.setCornerRadius_(radius)
    return layer


def _collapsed(frame, side):
    """`frame` shrunk to its middle along the side it runs down, where a mark grows from."""
    x, y, w, h = frame
    if side in ("left", "right"):
        return (x, y + h / 2, w, 0.0)
    return (x + w / 2, y, 0.0, h)


# The clear space centred on each third boundary of the selected machine's seam and tracks.
THIRD_GAP = 2.0
CYCLE_BOX = 16.0
RESIZE_GRIP = 12.0


def _at_break(value, breaks):
    return any(abs(value - point) < 1e-6 for point in breaks)


def _split_at_breaks(stretch, breaks):
    side, low, high = stretch
    cuts = [low] + sorted(point for point in breaks if low + 1e-6 < point < high - 1e-6) + [high]
    return [(side, one, two) for one, two in zip(cuts, cuts[1:])]


def notch_span(notch_range, displays):
    """(display key, left, right) of the notch as fractions of its display's width, or None: the
    display whose x-range holds `notch_range` and whose top is the desktop's, from global rects with
    x/y/width/height as `desktop_mac.display_map()` gives them."""
    if not notch_range or not displays:
        return None
    left, right = notch_range
    top = min(rect.y for rect in displays.values())
    for key, rect in displays.items():
        if rect.y == top and rect.width > 0 and rect.x <= left and right <= rect.x + rect.width:
            return key, (left - rect.x) / rect.width, (right - rect.x) / rect.width
    return None


class ArrangementDiagram(AppKit.NSView):
    """This Mac's screen in the middle, each placed machine's beside the side it is on, and this
    Mac lit where a way in crosses: the chosen machine's ways in the signal colour, the others'
    quieter, so the picture says which edge leads where."""

    # The screens take the space above the legend row (the key cap and the machines not placed),
    # which they would otherwise run into once neighbours push this Mac off the middle.
    HEIGHT = 210.0
    LEGEND = 36.0
    # A mark's colour by its tone in pages.diagram_marks. A third the chosen machine does not use
    # stays as a dim track while Part of the edge is on, so the gaps read as walls, not as nothing.
    # signal_dim all but vanishes on the dark ground, so the others' marks are signal held back.
    TONES = {"chosen": "signal", "other": ("signal", 0.45), "track": "edge"}

    def isFlipped(self):
        return True

    @objc.python_method
    def setup(self):
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.heightAnchor().constraintEqualToConstant_(self.HEIGHT).setActive_(True)
        self.setWantsLayer_(True)
        root = self.layer()
        theme.tint(root, background="ground", border="rule")
        root.setBorderWidth_(1)
        root.setCornerRadius_(3)
        self.this = _box("well", "edge", 4)
        self.this_label = _text_layer(theme.TYPE["small"], "ink_2")
        self.notch = _box("ground", None, 2)
        self.key = _box("well", "edge", theme.RADIUS["keycap"])
        # The shortcut as a key cap in the corner, and how it is pressed beside it: a legend, not a
        # sentence, since the note above the drawing already says it in words.
        self.key_label = _text_layer(theme.TYPE["small"], "ink", Quartz.kCAAlignmentCenter)
        self.key_label.setFont_(theme.font(theme.TYPE["small"], 600))
        self.key.addSublayer_(self.key_label)
        self.key_how = _text_layer(theme.TYPE["small"], "ink_3", Quartz.kCAAlignmentLeft)
        self.not_placed = _text_layer(theme.TYPE["small"], "ink_3", Quartz.kCAAlignmentRight)
        for layer in (self.this, self.this_label, self.notch, self.key, self.key_how, self.not_placed):
            root.addSublayer_(layer)
        # Made as machines and their marks first appear, by the machine's key, and dropped with it.
        self.screens = {}
        self.marks = {}
        self.machines = []
        self.state = None
        self.lit = {}
        self.setAccessibilityElement_(True)
        self.setAccessibilityRole_(AppKit.NSAccessibilityImageRole)
        return self

    def layout(self):
        objc.super(ArrangementDiagram, self).layout()
        if self.state is not None:
            self._draw(False)

    @objc.python_method
    def show(self, machines, key_text, has_notch, description, key_how="", shortcut=False):
        """Draws these machines, moving from the last drawing when on screen. Each machine is a dict
        with `key`, `label`, `side` ("" when not placed), its ways (`methods`, `parts`, `corner`) and
        whether it is `chosen`. `key_text` is the shortcut's key as its cap reads, `key_how` how it
        is pressed, `shortcut` whether it is on."""
        state = (tuple((m["key"], m["label"], m["side"], tuple(m["methods"]), tuple(m["parts"]), m["corner"], m["chosen"])
                       for m in machines), key_text, has_notch, key_how, shortcut)
        if state == self.state:
            return
        first = self.state is None
        self.state = state
        self.machines = [dict(machine) for machine in machines]
        self.setAccessibilityLabel_(description)
        self._draw(not first and motion.live(self))

    @objc.python_method
    def _screen(self, key):
        if key not in self.screens:
            screen = _box("panel", "rule", 4)
            label = _text_layer(theme.TYPE["small"], "ink_3")
            # Under this Mac's marks, which may sit at its edge beside a screen.
            for layer in (screen, label):
                self.layer().insertSublayer_below_(layer, self.this)
            self.screens[key] = (screen, label)
        return self.screens[key]

    @objc.python_method
    def _mark_layer(self, key):
        if key not in self.marks:
            layer = _box("signal", None, 2 if key[1] == "corner" else 1.5)
            self.layer().insertSublayer_above_(layer, self.notch)
            self.marks[key] = layer
        return self.marks[key]

    @objc.python_method
    def _draw(self, animate):
        _machines, key_text, has_notch, key_how, shortcut = self.state
        machines = self.machines
        bounds = self.bounds().size
        if bounds.width <= 0:
            return
        font = theme.font(theme.TYPE["small"], 500)
        width = min(bounds.width - 24, 24 + _text_width(key_text, theme.font(theme.TYPE["small"], 600)))
        how = _text_width(key_how, font) + 4
        stacked = bool(shortcut and width + 8 + how > bounds.width - 24)
        legend = self.LEGEND + (20 if stacked else 0)
        this, frames = pages.diagram_layout(bounds.width, bounds.height - legend, [pages.placement(m) for m in machines])
        # With one machine nothing is chosen between, and it looks as the one-machine drawing did.
        several = len(machines) > 1
        motion.transaction(animate)
        self.this.setFrame_(_rect(this))
        line = theme.TYPE["small"] + 4
        x, y, w, h = this
        _fit_text(self.this_label, tr("This Mac"), (x + 6, y + (4 if has_notch else 0), w - 12, h))
        keys = {machine["key"] for machine in machines}
        for key in [key for key in self.screens if key not in keys]:
            for layer in self.screens.pop(key):
                layer.removeFromSuperlayer()
        for machine, frame in zip(machines, frames):
            screen, label = self._screen(machine["key"])
            strong = several and machine["chosen"]
            screen.setHidden_(frame is None)
            label.setHidden_(frame is None)
            if frame is None:
                continue
            theme.tint(screen, border="ink_3" if strong else "rule")
            theme.tint(label, foreground="ink" if strong else "ink_3")
            label.setFont_(theme.font(theme.TYPE["small"], 600 if strong else 500))
            screen.setFrame_(_rect(frame))
            fx, fy, fw, fh = frame
            _fit_text(label, machine["label"], (fx + 4, fy, fw - 8, fh))
        marks, notch = pages.diagram_marks(this, machines)
        for key in [key for key in self.marks if key not in marks]:
            self.marks.pop(key).removeFromSuperlayer()
            self.lit.pop(key, None)
        sides = {machine["key"]: machine["side"] for machine in machines}
        for key, (frame, tone) in marks.items():
            layer = self._mark_layer(key)
            if tone is not None:
                theme.tint(layer, background=self.TONES[tone])
            # The chosen machine's ways on top where two machines' marks meet.
            layer.setZPosition_(1.0 if tone in ("chosen", "track") else 0.0)
            self._mark(key, layer, frame, tone, sides[key[0]] if key[1] != "corner" else None, animate)
        self.notch.setFrame_(_rect(pages.notch_mark(this)))
        self.notch.setHidden_(not has_notch)
        lit_notch = has_notch and notch is not None
        self.notch.setBorderWidth_(1.5 if lit_notch else 0)
        theme.tint(self.notch, border=self.TONES[notch] if lit_notch else "signal",
                   background=("signal", 0.25 if notch == "chosen" else 0.12) if lit_notch else "ground")
        shortcut = shortcut and bool(key_text)
        self.key_label.setString_(key_text)
        self.key_how.setString_(key_how)
        top = bounds.height - legend
        self.key.setFrame_(((12, top), (width, 24)))
        self.key_label.setFrame_(((0, (24 - line) / 2), (width, line)))
        how_left = 12 if stacked else 12 + width + 8
        how_top = top + 26 if stacked else top + (24 - line) / 2
        self.key_how.setFrame_(((how_left, how_top), (min(how, bounds.width - 12 - how_left), line)))
        for layer in (self.key, self.key_how):
            layer.setOpacity_(1.0 if shortcut else 0.0)
        # The machines with no side, on the key cap's line, from wherever the legend ends.
        start = how_left + how + 16 if shortcut else 12
        self.not_placed.setString_(pages.not_placed(machines))
        self.not_placed.setFrame_(((min(start, bounds.width - 12), how_top), (max(0, bounds.width - 12 - start), line)))
        Quartz.CATransaction.commit()

    @objc.python_method
    def _mark(self, key, layer, frame, tone, side, animate):
        """Places a mark; switched on or newly lit while moving, it grows out from its middle as
        it fades in. A mark with no frame (its machine has no side) is not drawn."""
        on = tone is not None and frame is not None
        # The side is part of the state: moved to another side, a mark grows in afresh there, which
        # reads far better than a strip stretching across the screen from one side to the other.
        state = (on, tone, side)
        was = self.lit.get(key)
        self.lit[key] = state
        if frame is None:
            layer.setOpacity_(0.0)
            return
        if not (on and was != state and animate):
            layer.setFrame_(_rect(frame))
            layer.setOpacity_(1.0 if on else 0.0)
            return
        # An implicit animation would start from the prior frame, not the collapsed mark.
        start = _collapsed(frame, side) if side else (frame[0] + frame[2] / 2, frame[1] + frame[3] / 2, 0, 0)
        motion.transaction(False)
        layer.setFrame_(_rect(frame))
        layer.setOpacity_(1.0)
        Quartz.CATransaction.commit()
        x, y, w, h = frame
        sx, sy, sw, sh = start
        for key, old, new in (
            ("bounds", AppKit.NSValue.valueWithRect_(((0, 0), (sw, sh))), AppKit.NSValue.valueWithRect_(((0, 0), (w, h)))),
            ("position", AppKit.NSValue.valueWithPoint_((sx + sw / 2, sy + sh / 2)), AppKit.NSValue.valueWithPoint_((x + w / 2, y + h / 2))),
            ("opacity", 0.0, 1.0),
        ):
            grow = Quartz.CABasicAnimation.animationWithKeyPath_(key)
            grow.setFromValue_(old)
            grow.setToValue_(new)
            grow.setDuration_(motion.DURATION)
            grow.setTimingFunction_(motion.timing())
            layer.addAnimation_forKey_(grow, key)
        if not (on and was != state and animate):
            layer.setFrame_(_rect(frame))
        layer.setOpacity_(1.0 if on else 0.0)


def outcome_for_tile(state, crosses, is_own=False):
    if is_own:
        return ""
    if state == "overlap":
        return tr("Overlaps")
    if state in ("gap", "between", "set-elsewhere", "set_elsewhere"):
        return tr("Drag to connect")
    if not crosses:
        return tr("Edge off")
    return tr("Crosses")


class AtlasAccessibleElement(AppKit.NSObject):
    def initWithCanvas_key_kind_(self, canvas, key, kind):
        self = objc.super(AtlasAccessibleElement, self).init()
        if self is None:
            return None
        self.canvas, self.key, self.kind = canvas, key, kind
        return self

    def accessibilityRole(self):
        return AppKit.NSAccessibilityCheckBoxRole if self.kind == "cycle" else AppKit.NSAccessibilityButtonRole

    def accessibilityLabel(self):
        if self.kind == "recentre":
            name = self.canvas.labels.get(self.canvas.selected, tr("machine"))
            return tr("Recentre {name}").format(name=name)
        name = self.canvas.labels.get(self.key, tr("Machine · {id}").format(id=self.key[-6:]))
        if self.kind == "cycle":
            return tr("{name}, In the shortcut's cycle").format(name=name)
        word = self.canvas.tile_words.get(self.key)
        return tr("{name}, {status}").format(name=name, status=word) if word else name

    def accessibilityValue(self):
        if self.kind == "cycle":
            return self.canvas.cycle.get(self.key, True) is True
        return tr("Selected") if self.canvas.selected == self.key else ""

    def accessibilityHelp(self):
        if self.kind == "tile" and self.canvas._can_resize(self.key):
            return tr("Drag a corner to resize, or use Option and the arrow keys.")
        return ""

    def accessibilityParent(self):
        return self.canvas

    def accessibilityFrame(self):
        if self.kind == "recentre":
            return self.canvas._recentre_screen_frame()
        frames, _scale = self.canvas._map()
        frame = frames.get(self.key)
        if frame is None:
            return AppKit.NSZeroRect
        if self.kind == "cycle":
            frame = self.canvas._cycle_hit_rect(frame)
        rect = AppKit.NSMakeRect(*frame)
        window = self.canvas.window()
        if window is None:
            return rect
        return window.convertRectToScreen_(self.canvas.convertRect_toView_(rect, None))

    def accessibilityPerformPress(self):
        if self.kind == "recentre":
            self.canvas.recentre_selected()
            return True
        if self.kind == "cycle":
            self.canvas.toggle_cycle(self.key)
        else:
            self.canvas.select_tile(self.key)
        return True


class AtlasCanvas(AppKit.NSView):
    """Interactive map of machine tiles; geometry stays in the shared core layout units."""

    HEIGHT = 260.0
    INSET = 14.0

    @staticmethod
    def snap_radius_for_scale(scale):
        return 12 / max(float(scale), 0.01)

    def isFlipped(self):
        return True

    @objc.python_method
    def setup(self):
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.heightAnchor().constraintEqualToConstant_(self.HEIGHT).setActive_(True)
        self.setWantsLayer_(True)
        theme.tint(self.layer(), background="well", border="edge")
        self.layer().setBorderWidth_(1)
        self.layer().setCornerRadius_(4)
        self.tiles = {}
        self.states = {}
        self.labels = {}
        self.tile_words = {}
        self.machine_picker = False
        self.read_only = False
        self.fixed_machine_id = ""
        self.older = set()
        self.selected = None
        self.own_id = ""
        self.cycle = {}
        self.cycle_enabled = False
        self.keyboard_focus = False
        self.hovered = None
        self.focus_layer = _box(None, "ink_2", 4)
        self.focus_layer.setBorderWidth_(2)
        self.focus_layer.setHidden_(True)
        self.layer().addSublayer_(self.focus_layer)
        self.layer_for_tile = {}
        self.display_layers = {}
        self.display_number_layers = {}
        self.label_for_tile = {}
        self.state_for_tile = {}
        self.outcome_for_tile = {}
        self.dot_for_tile = {}
        self.cycle_for_tile = {}
        self.resize_layers = {}
        self.accessible_elements = {}
        self.contact_layers = {}
        self.seam_segments = []
        self.mark_layers = {}
        self.corner_halo_layers = {}
        self.wrap_layers = {}
        self.recentre_layer = _box("panel", None, 4)
        self.recentre_label = _text_layer(theme.TYPE["note"], "ink_3")
        self.recentre_layer.addSublayer_(self.recentre_label)
        self.recentre_layer.setHidden_(True)
        self.recentre_layer.setZPosition_(22)
        self.layer().addSublayer_(self.recentre_layer)
        self.recentre_hovered = False
        self.snap_ghost = Quartz.CAShapeLayer.layer()
        self.snap_ghost.setFillColor_(AppKit.NSColor.clearColor().CGColor())
        self.snap_ghost.setStrokeColor_(theme.cg("signal"))
        self.snap_ghost.setLineWidth_(1)
        self.snap_ghost.setLineDashPattern_([4, 3])
        self.snap_ghost.setOpacity_(0.4)
        self.snap_ghost.setLineCap_(Quartz.kCALineCapRound)
        self.snap_ghost.setHidden_(True)
        self.layer().addSublayer_(self.snap_ghost)
        self.snap_seam = Quartz.CAShapeLayer.layer()
        self.snap_seam.setFillColor_(AppKit.NSColor.clearColor().CGColor())
        self.snap_seam.setStrokeColor_(theme.cg("signal"))
        self.snap_seam.setLineWidth_(4)
        self.snap_seam.setLineCap_(Quartz.kCALineCapRound)
        self.snap_seam.setOpacity_(0.5)
        self.snap_seam.setHidden_(True)
        self.layer().addSublayer_(self.snap_seam)
        self.guide_layers = []
        self.grid_layers = []
        self.dragging = None
        self.third_pressed = None
        self.third_layers = {}
        self.on_layout_change = None
        self.on_select = None
        self.on_third = None
        self.on_rename = None
        self.on_cycle_change = None
        self.on_leave_out = None
        self.reduced_motion = False
        self.last_announcement = ""
        self.setAccessibilityElement_(True)
        self.setAccessibilityRole_(AppKit.NSAccessibilityGroupRole)
        self.setAccessibilityLabel_(tr("Whole desk layout"))
        return self

    def acceptsFirstResponder(self):
        return True

    def resignFirstResponder(self):
        self.keyboard_focus = False
        self._draw(False)
        return True

    @objc.python_method
    def refresh_keyboard_focus(self):
        from widgets import keyboard_focus_visible

        self.keyboard_focus = keyboard_focus_visible(self)
        self._draw(False)

    @objc.python_method
    def show_layout(self, tiles, states, labels, own_id, selected, cycle=None, cycle_enabled=False,
                    older=(), animate=False, tile_words=None, machine_picker=False, read_only=False,
                    fixed_machine_id=None):
        """`own_id` anchors the viewed machine's seams; picker mode includes it in selection."""
        cycle = dict(cycle or {})
        older = set(older)
        tile_words = dict(tile_words or {})
        fixed_machine_id = own_id if fixed_machine_id is None else fixed_machine_id
        if (tiles == self.tiles and states == self.states and labels == self.labels
                and own_id == self.own_id and selected == self.selected
                and cycle == self.cycle and bool(cycle_enabled) == self.cycle_enabled
                and older == self.older and tile_words == self.tile_words
                and bool(machine_picker) == self.machine_picker and bool(read_only) == self.read_only
                and fixed_machine_id == self.fixed_machine_id):
            return
        self.tiles = copy.deepcopy(tiles)
        self.states = copy.deepcopy(states)
        self.labels = dict(labels)
        self.own_id = own_id
        self.selected = selected
        self.cycle = cycle
        self.cycle_enabled = bool(cycle_enabled)
        self.older = older
        self.tile_words = tile_words
        self.machine_picker = bool(machine_picker)
        self.read_only = bool(read_only)
        self.fixed_machine_id = fixed_machine_id
        if self.read_only:
            self.dragging = self.third_pressed = None
        for key in list(self.layer_for_tile):
            if key not in self.tiles:
                for layer in self.display_layers.pop(key, []):
                    layer.removeFromSuperlayer()
                for layer in self.display_number_layers.pop(key, []):
                    layer.removeFromSuperlayer()
                for layer in (self.layer_for_tile.pop(key), self.label_for_tile.pop(key),
                              self.state_for_tile.pop(key), self.outcome_for_tile.pop(key),
                              self.dot_for_tile.pop(key), self.cycle_for_tile.pop(key),
                              self.resize_layers.pop(key)):
                    layer.removeFromSuperlayer()
        self.setAccessibilityLabel_(self.last_announcement or self._description())
        self.setNeedsLayout_(True)
        self.setNeedsDisplay_(True)
        if self.superview() is not None:
            self.layoutSubtreeIfNeeded()
        self._draw(animate and not self.reduced_motion)

    @objc.python_method
    def _description(self):
        parts = []
        for key, tile in self.tiles.items():
            label = self.labels.get(key, tr("Machine · {id}").format(id=key[-6:]))
            state = self.states.get(key, {}).get("state")
            sentence = self.states.get(key, {}).get("sentence", "")
            if sentence:
                parts.append(sentence)
            elif self.machine_picker and key == self.own_id:
                parts.append(label)
            elif key == self.own_id:
                name = self.states.get(key, {}).get("machine_name")
                parts.append(tr("{label}, {name}, this Mac.").format(label=label, name=name) if name else
                             tr("{label}, this Mac.").format(label=label))
            else:
                status = self.states.get(key, {})
                outcome = (tr("Offline") if not status.get("linked") else
                           tr("Crosses") if status.get("crosses") else
                           tr("Edge off") if status.get("edge_off") else
                           outcome_for_tile(state, False))
                parts.append(tr("{label}, {outcome}.").format(label=label, outcome=outcome))
            if self.tile_words.get(key):
                parts.append(self.tile_words[key])
            if self._can_resize(key):
                parts.append(tr("{name}: drag a corner to resize, or use Option and the arrow keys.").format(name=label))
            if self.cycle_enabled and key != self.own_id:
                sentence = (tr("{name} is in the shortcut's cycle.") if self.cycle.get(key, True) else
                            tr("{name} is out of the shortcut's cycle."))
                parts.append(sentence.format(name=label))
        return " ".join(parts) or tr("No machines paired.")

    def accessibilityChildren(self):
        children = []
        for key in self.tiles:
            children.append(self.accessible_elements.setdefault(
                key, AtlasAccessibleElement.alloc().initWithCanvas_key_kind_(self, key, "tile")))
            if self.cycle_enabled and key != self.own_id:
                children.append(self.accessible_elements.setdefault(
                    (key, "cycle"), AtlasAccessibleElement.alloc().initWithCanvas_key_kind_(self, key, "cycle")))
        if self._show_recentre():
            children.append(self.accessible_elements.setdefault(
                "recentre", AtlasAccessibleElement.alloc().initWithCanvas_key_kind_(self, self.selected, "recentre")))
        return children

    @objc.python_method
    def select_tile(self, key):
        if key not in self.tiles or (key == self.own_id and not self.machine_picker):
            return
        self.selected = key
        if self.on_select:
            self.on_select(key)
        self._draw(False)

    @objc.python_method
    def reading_order(self):
        placed = sorted(((tile["y"] + tile["h"] / 2, tile["x"] + tile["w"] / 2,
                          tile["h"], key) for key, tile in self.tiles.items()
                         if self.machine_picker or key != self.own_id))
        rows = []
        for item in placed:
            if not rows or item[0] - rows[-1][0][0] > min(item[2], rows[-1][0][2]) / 2:
                rows.append([item])
            else:
                rows[-1].append(item)
        return [item[3] for row in rows for item in sorted(row, key=lambda entry: entry[1])]

    @objc.python_method
    def announce(self, sentence):
        self.last_announcement = sentence
        self.setAccessibilityLabel_(sentence)
        AppKit.NSAccessibilityPostNotificationWithUserInfo(
            self, AppKit.NSAccessibilityAnnouncementRequestedNotification,
            {AppKit.NSAccessibilityAnnouncementKey: sentence,
             AppKit.NSAccessibilityPriorityKey: AppKit.NSAccessibilityPriorityMedium})

    @objc.python_method
    def announce_move(self, peer, tile, state):
        name = self.labels.get(peer, f"Machine · {peer[-6:]}")
        own = self.tiles.get(self.own_id, {})
        side = "right of" if tile["x"] >= own.get("x", 0) + own.get("w", 0) else \
            "left of" if tile["x"] + tile["w"] <= own.get("x", 0) else \
            "below" if tile["y"] >= own.get("y", 0) + own.get("h", 0) else "above"
        thirds = state.get("thirds", [])
        map_state = state.get("state")
        if not state.get("crosses") and core_ways._rect_contact(own, tile) is None:
            map_state = "gap"
        outcome = outcome_for_tile(map_state, state.get("crosses"))
        if len(thirds) == 1:
            # Translators: {third} is one of the three sections along an edge.
            sentence = tr("{name}, {side} This Mac, {third} third. {outcome}.")
            position = tr(POSITION_WORDS[side])
            third = tr(THIRD_WORDS[thirds[0]])
            self.announce(sentence.format(name=name, side=position, third=third, outcome=outcome))
        else:
            sentence = tr("{name}, {side} This Mac. {outcome}.")
            position = tr(POSITION_WORDS[side])
            self.announce(sentence.format(name=name, side=position, outcome=outcome))

    @objc.python_method
    def announce_resize(self, key):
        name = self.labels.get(key, tr("Machine · {id}").format(id=key[-6:]))
        self.announce(tr("{name} resized.").format(name=name))

    @objc.python_method
    def _commit_resize(self, key):
        if self.on_layout_change:
            self.on_layout_change(self.tiles, key, snap=False)
        else:
            self.announce_resize(key)

    @objc.python_method
    def _layer(self, key):
        if key not in self.layer_for_tile:
            tile = _box("panel", "rule", 4)
            title = _text_layer(theme.TYPE["body"], "ink")
            title.setFont_(theme.font(theme.TYPE["body"], 600))
            state = _text_layer(theme.TYPE["eyebrow"], "ink_3")
            outcome = _text_layer(theme.TYPE["note"], "ink_2")
            dot = _box("signal", None, 3)
            cycle = _box("well", "edge", 3)
            check = Quartz.CAShapeLayer.layer()
            # The sublayer inherits the view's flipped geometry, so the tick is authored y-down.
            path = Quartz.CGPathCreateMutable()
            Quartz.CGPathMoveToPoint(path, None, 4.5, 8.5)
            Quartz.CGPathAddLineToPoint(path, None, 7, 11)
            Quartz.CGPathAddLineToPoint(path, None, 11.5, 5)
            check.setPath_(path)
            check.setFillColor_(AppKit.NSColor.clearColor().CGColor())
            theme.tint(check, stroke="ground")
            check.setLineWidth_(1.8)
            check.setLineCap_(Quartz.kCALineCapRound)
            check.setLineJoin_(Quartz.kCALineJoinRound)
            check.setFrame_(((0, 0), (CYCLE_BOX, CYCLE_BOX)))
            check.setAnchorPoint_((0, 0))
            check.setPosition_((0, 0))
            check.setHidden_(True)
            cycle.addSublayer_(check)
            grip = Quartz.CAShapeLayer.layer()
            grip.setFillColor_(AppKit.NSColor.clearColor().CGColor())
            grip.setLineWidth_(1.5)
            grip.setLineCap_(Quartz.kCALineCapRound)
            grip.setZPosition_(23)
            grip.setHidden_(True)
            self.layer().addSublayer_(tile)
            self.layer().addSublayer_(title)
            self.layer().addSublayer_(state)
            self.layer().addSublayer_(outcome)
            self.layer().addSublayer_(dot)
            self.layer().addSublayer_(cycle)
            self.layer().addSublayer_(grip)
            self.layer_for_tile[key] = tile
            self.label_for_tile[key] = title
            self.state_for_tile[key] = state
            self.outcome_for_tile[key] = outcome
            self.dot_for_tile[key] = dot
            self.cycle_for_tile[key] = cycle
            self.resize_layers[key] = grip
        return (self.layer_for_tile[key], self.label_for_tile[key], self.state_for_tile[key],
                self.outcome_for_tile[key], self.dot_for_tile[key], self.cycle_for_tile[key])

    @staticmethod
    def _title_top(frame):
        if frame[3] < 32:
            return frame[1] + 11
        if frame[3] < 44:
            return frame[1] + max(12, frame[3] - 21)
        return frame[1] + (4 if frame[3] < 56 else 10)

    @objc.python_method
    def _shows_cycle(self, key):
        return self.cycle_enabled and key != self.own_id

    @objc.python_method
    def _title_rect(self, key, frame):
        return _beside_display_numbers(self._title_without_numbers(key, frame),
                                       self.display_number_layers.get(key, ()))

    @objc.python_method
    def _title_without_numbers(self, key, frame):
        """The name's text rect. Beside the shortcut-cycle tick the name stays centred on the tile while
        it fits the room left symmetric by the tick, else takes the room left of the tick; a tile 44 to
        56pt tall puts the name on its own row above the tick."""
        x, y, width, height = frame
        if height < 32:
            return x + 5, self._title_top(frame), max(1, width - 10), max(1, height - 12)
        if not self._shows_cycle(key):
            return x + 8, self._title_top(frame), max(1, width - 16), 20
        if 44 <= height < 56:
            return x + 8, y + 4, max(1, width - 16), 20
        room = 8 + CYCLE_BOX + 6
        label = self.labels.get(key, f"Machine · {key[-6:]}")
        if height >= 56 and _text_width(label, theme.font(theme.TYPE["body"], 600)) <= width - 2 * room:
            return x + room, self._title_top(frame), width - 2 * room, 20
        return x + 8, self._title_top(frame), max(1, self._cycle_box(frame)[0] - 6 - x - 8), 20

    @staticmethod
    def _cycle_box(frame):
        """The shortcut-cycle tick: its own column at the right end of the name's line, or in the
        bottom-right corner of a tile 44 to 56pt tall, under the name."""
        x, y, width, height = frame
        if height < 32:
            return x + width - 15, y + 1, 10, 10
        if 44 <= height < 56:
            return x + width - 8 - CYCLE_BOX, y + height - 4 - CYCLE_BOX, CYCLE_BOX, CYCLE_BOX
        return x + width - 8 - CYCLE_BOX, AtlasCanvas._title_top(frame) + 10 - CYCLE_BOX / 2, CYCLE_BOX, CYCLE_BOX

    @staticmethod
    def _cycle_hit_rect(frame):
        x, y, width, height = AtlasCanvas._cycle_box(frame)
        return x - 4, y - 4, width + 8, height + 8

    @objc.python_method
    def _can_resize(self, key):
        tile = self.tiles.get(key)
        state = self.states.get(key, {})
        return (tile is not None and not self.read_only
                and not any(tile.get(flag) or state.get(flag) for flag in ("left_out", "shelved", "dormant"))
                and tile.get("in_use") is not False and state.get("in_use") is not False
                and (key == self.fixed_machine_id or not tile.get("display_offsets")))

    @staticmethod
    def _resize_rect(frame):
        x, y, width, height = frame
        size = min(RESIZE_GRIP, width, height)
        return x + width - size, y + height - size, size, size

    @objc.python_method
    def _resize_hit(self, point):
        # An edge third's 5 px band wins over a grip it overlaps, as on the PC map.
        if self._third_hit(point):
            return None
        frames, _scale = self._map()
        for key, frame in reversed(list(frames.items())):
            if not self._can_resize(key):
                continue
            x, y, width, height = self._resize_rect(frame)
            # A tile drawn smaller than the grip has its whole grip inside the band of the side it
            # shares; the hit area keeps the full grip size, reaching out past the corner.
            if x <= point.x <= x + max(width, RESIZE_GRIP) and y <= point.y <= y + max(height, RESIZE_GRIP):
                return key
        return None

    @staticmethod
    def _resize_cursor():
        cursor = getattr(AppKit.NSCursor, "resizeNorthWestSouthEastCursor", None)
        return cursor() if cursor is not None else AppKit.NSCursor.crosshairCursor()

    @objc.python_method
    def _map(self):
        width, height = self.bounds().size.width, self.bounds().size.height
        if not self.tiles or width <= 0 or height <= 0:
            return {}, 1.0
        if self.dragging is not None and all(key in self.dragging for key in ("scale", "map_x", "map_y")):
            scale = self.dragging["scale"]
            offset_x, offset_y = self.dragging["map_x"], self.dragging["map_y"]
            return {key: (tile["x"] * scale + offset_x, tile["y"] * scale + offset_y,
                          tile["w"] * scale, tile["h"] * scale)
                    for key, tile in self.tiles.items()}, scale
        panels = [frame for tile in self.tiles.values() for frame in core_ways._display_frames(tile)]
        horizontal = [(tile["x"], tile["x"] + tile["w"]) for tile in self.tiles.values()]
        vertical = [(tile["y"], tile["y"] + tile["h"]) for tile in self.tiles.values()]
        horizontal.extend((frame["x"], frame["x"] + frame["w"]) for frame in panels)
        vertical.extend((frame["y"], frame["y"] + frame["h"]) for frame in panels)
        left = min(edge for pair in horizontal for edge in pair)
        right = max(edge for pair in horizontal for edge in pair)
        top = min(edge for pair in vertical for edge in pair)
        bottom = max(edge for pair in vertical for edge in pair)
        cx, cy = (left+right)/2, (top+bottom)/2
        scale = min((width-2*self.INSET) / max(1, right-left),
                    (height-2*self.INSET) / max(1, bottom-top), 1.15)
        offset_x = width / 2 - cx * scale
        offset_y = height / 2 - cy * scale
        frames = {key: (tile["x"] * scale + offset_x, tile["y"] * scale + offset_y,
                        tile["w"] * scale, tile["h"] * scale)
                  for key, tile in self.tiles.items()}
        return frames, scale

    def layout(self):
        objc.super(AtlasCanvas, self).layout()
        if self.tiles:
            self._draw(False)

    @objc.python_method
    def _draw(self, animate):
        frames, _scale = self._map()
        if not frames:
            return
        motion.transaction(animate and motion.live(self))
        for key, frame in frames.items():
            tile, title, state_layer, outcome_layer, dot, cycle_layer = self._layer(key)
            state = self.states.get(key, {}).get("state", "")
            is_own = key == self.own_id
            inactive = self.states.get(key, {}).get("in_use") is False
            selected = key == self.selected
            elsewhere = (self.machine_picker and self.own_id != self.fixed_machine_id
                         and state in ("gap", "between", "set-elsewhere", "set_elsewhere"))
            gap = not elsewhere and state in ("gap", "set-elsewhere", "set_elsewhere", "between", "overlap")
            hover = key == self.hovered and not self.keyboard_focus
            _set_layer_frame_if_changed(tile, _rect(frame))
            tile.setBorderWidth_(2 if selected and self.machine_picker else 1 if is_own else 2 if selected or gap else 1)
            theme.tint(tile, background="well" if is_own else "panel",
                       border="ink_2" if selected and self.machine_picker else "edge" if is_own else "ink_2" if selected else
                       "ink_3" if hover else "amber" if gap else "edge")
            tile.setOpacity_(0.42 if inactive else 1.0)
            screens = core_ways._display_frames(self.tiles[key]) if self.tiles[key].get("displays") else []
            layers = self.display_layers.setdefault(key, [])
            while len(layers) < len(screens):
                screen_layer = Quartz.CALayer.layer()
                screen_layer.setBorderWidth_(1)
                screen_layer.setCornerRadius_(2)
                # Directly above the tile, under its labels: a snapshot (cacheDisplayInRect) draws
                # sublayers in array order and ignores zPosition, so appended they hid the name.
                self.layer().insertSublayer_above_(screen_layer, layers[-1] if layers else tile)
                layers.append(screen_layer)
            for index, layer in enumerate(layers):
                if index >= len(screens):
                    layer.setHidden_(True)
                    continue
                screen = screens[index]
                scale_x = frame[2] / self.tiles[key]["w"]
                scale_y = frame[3] / self.tiles[key]["h"]
                _set_layer_frame_if_changed(
                    layer, ((screen["x"] * scale_x + frame[0] - self.tiles[key]["x"] * scale_x,
                             screen["y"] * scale_y + frame[1] - self.tiles[key]["y"] * scale_y),
                            (screen["w"] * scale_x, screen["h"] * scale_y)))
                layer.setHidden_(False)
                theme.tint(layer, background="well", border="edge")
            numbered_frames = core_ways._display_frames(self.tiles[key])
            numbers = identify_screens.display_numbers(self.tiles[key])
            badges = self.display_number_layers.setdefault(key, [])
            while len(badges) < len(numbered_frames):
                badge = _text_layer(theme.TYPE["note"], "ink_2")
                badge.setCornerRadius_(theme.RADIUS["field"])
                theme.tint(badge, background="panel", foreground="ink_2")
                self.layer().insertSublayer_below_(badge, title)
                badges.append(badge)
            for index, badge in enumerate(badges):
                badge.setHidden_(index >= len(numbered_frames))
                if index >= len(numbered_frames):
                    continue
                screen = numbered_frames[index]
                display = screen.get("display") or "0"
                badge.setString_(str(numbers[display]))
                scale_x = frame[2] / self.tiles[key]["w"]
                scale_y = frame[3] / self.tiles[key]["h"]
                left = frame[0] + (screen["x"] - self.tiles[key]["x"]) * scale_x
                top = frame[1] + (screen["y"] - self.tiles[key]["y"]) * scale_y
                bottom = frame[1] + (screen["y"] + screen["h"] - self.tiles[key]["y"]) * scale_y
                if frame[3] < 32:
                    badge.setFontSize_(7)
                    badge_frame = ((left + 5, top + 1), (12, 8))
                elif frame[3] < 44:
                    badge.setFontSize_(8)
                    badge_frame = ((left + 5, top + 1), (14, 10))
                elif frame[3] < 56:
                    badge.setFontSize_(10)
                    badge_frame = ((left + 5, bottom - 15), (16, 14))
                else:
                    badge.setFontSize_(theme.TYPE["note"])
                    badge_frame = ((left + 5, bottom - 23), (20, 18))
                _set_layer_frame_if_changed(badge, badge_frame)
            title.setString_(self.labels.get(key, tr("Machine · {id}").format(id=key[-6:])))
            title_size = 10 if frame[3] < 32 else theme.TYPE["body"]
            if title.fontSize() != title_size:
                title.setFont_(theme.font(title_size, 600))
                title.setFontSize_(title_size)
            title.setWrapped_(False)
            title.setTruncationMode_(Quartz.kCATruncationEnd)
            _set_layer_frame_if_changed(title, _rect(self._title_rect(key, frame)))
            status_info = self.states.get(key, {})
            status = (tr("{name} · macOS").format(name=status_info.get('machine_name') or platform.node().split('.')[0] or tr("This Mac"))
                      if is_own else
                      tr("{platform} · {status}").format(platform=status_info.get('platform', tr("Computer")),
                                                          status=tr("Linked") if status_info.get('linked') else tr("Offline")))
            if key in self.older:
                status = tr("Beamer {version} · only sees which side").format(version=status_info.get('version') or '1.5.2')
            if inactive:
                status = tr("Not in use")
            status = self.tile_words.get(key) or status
            state_layer.setString_(status)
            state_layer.setAlignmentMode_(Quartz.kCAAlignmentLeft)
            _set_layer_frame_if_changed(state_layer, _rect(_beside_display_numbers(
                (frame[0] + 24, frame[1] + 35, max(1, frame[2] - 32), 16), badges)))
            state_layer.setHidden_(frame[3] < 72)
            theme.tint(state_layer, foreground="amber" if gap else "ink_3")
            crosses = status_info.get("crosses", is_own)
            outcome_kind = ("offline" if not is_own and not status_info.get("linked") else
                            "crosses" if crosses and (status_info.get("corner_ways") or status_info.get("wrap_sides")
                                                       or status_info.get("notch_way")) else
                            "edge_off" if status_info.get("edge_off") or status_info.get("unpicked") else
                            "tile")
            outcome = {"offline": tr("Offline"), "crosses": tr("Crosses"), "edge_off": tr("Edge off")}.get(
                outcome_kind, outcome_for_tile(state, crosses, is_own))
            if is_own:
                outcome = ""
            outcome_layer.setString_(tr("Set elsewhere") if elsewhere and not is_own else outcome)
            _set_layer_frame_if_changed(
                outcome_layer, _rect(_beside_display_numbers(
                    (frame[0] + 8, frame[1] + frame[3] - 22, max(1, frame[2] - 16), 14), badges)))
            outcome_layer.setHidden_(frame[3] < 56)
            theme.tint(outcome_layer, foreground="ink_3" if elsewhere else "signal" if outcome_kind == "crosses" else
                       "amber" if state in ("gap", "between", "set-elsewhere", "set_elsewhere", "overlap") else "ink_3")
            _set_layer_frame_if_changed(dot, ((frame[0] + 12, frame[1] + 40), (6, 6)))
            dot.setCornerRadius_(3)
            dot.setHidden_(frame[3] < 72 or any(
                AppKit.NSIntersectsRect(dot.frame(), badge.frame()) for badge in badges if not badge.isHidden()))
            word = self.tile_words.get(key)
            theme.tint(dot, background=("signal" if word in (tr("Desk"), tr("Own")) else "ink_3") if word else
                       "ink_3" if key in self.older else "signal" if status_info.get("linked") else "ink_3")
            cycle_layer.setHidden_(not self._shows_cycle(key))
            _set_layer_frame_if_changed(cycle_layer, _rect(self._cycle_box(frame)))
            ticked = self.cycle.get(key, True) is True
            cycle_layer.setBorderWidth_(0 if ticked else 1)
            theme.tint(cycle_layer, background="ink_2" if ticked else "well", border="edge")
            check = cycle_layer.sublayers()[0]
            check.setHidden_(not ticked)
            tick_scale = self._cycle_box(frame)[2] / CYCLE_BOX
            check.setAffineTransform_(Quartz.CGAffineTransformMakeScale(tick_scale, tick_scale))
            if self.display_layers.get(key):
                z_position = max(layer.zPosition() for layer in self.display_layers[key]) + 1.0
                for layer in (title, state_layer, outcome_layer, dot, cycle_layer):
                    layer.setZPosition_(z_position)
            grip = self.resize_layers[key]
            grip.setHidden_(not (self._can_resize(key) and (selected or key == self.hovered)))
            gx, gy, gw, gh = self._resize_rect(frame)
            path = Quartz.CGPathCreateMutable()
            for inset in (4, 8):
                Quartz.CGPathMoveToPoint(path, None, gx + gw - inset, gy + gh - 3)
                Quartz.CGPathAddLineToPoint(path, None, gx + gw - 3, gy + gh - inset)
            grip.setPath_(path)
            theme.tint(grip, stroke="ink_2" if selected else "ink_3")
        focus = frames.get(self.selected) if self.keyboard_focus else None
        self.focus_layer.setHidden_(focus is None)
        if focus is not None:
            _set_layer_frame_if_changed(
                self.focus_layer, _rect((focus[0] - 3, focus[1] - 3, focus[2] + 6, focus[3] + 6)))
        self._draw_guides(frames.get(self.own_id))
        self._draw_contacts(frames)
        self._draw_marks(frames)
        self._draw_snap_preview(frames, _scale)
        show_recentre = self._show_recentre()
        self.recentre_layer.setHidden_(not show_recentre)
        if show_recentre:
            button = self._recentre_frame()
            _set_layer_frame_if_changed(self.recentre_layer, _rect(button))
            self.recentre_layer.setBorderWidth_(1 if self.keyboard_focus or self.recentre_hovered else 0)
            theme.tint(self.recentre_layer, background="panel",
                       border="ink_2" if self.keyboard_focus or self.recentre_hovered else "rule")
            self.recentre_label.setString_(tr("Recentre"))
            self.recentre_label.setAlignmentMode_(Quartz.kCAAlignmentCenter)
            _set_layer_frame_if_changed(self.recentre_label, ((4, 2), (button[2] - 8, button[3] - 4)))
            theme.tint(self.recentre_label, foreground="ink_2" if self.recentre_hovered or self.keyboard_focus else "ink_3")
        self.removeAllToolTips()
        for _peer, _third, _side, start, end in self._third_segments(frames):
            if self.read_only:
                continue
            rect = (AppKit.NSMakeRect(start[0] - 5, start[1], 10, end[1] - start[1])
                    if start[0] == end[0] else
                    AppKit.NSMakeRect(start[0], start[1] - 5, end[0] - start[0], 10))
            self.addToolTipRect_owner_userData_(rect, self, 1)
        for key, frame in frames.items():
            title_rect = self._title_rect(key, frame)
            name = self.labels.get(key, tr("Machine · {id}").format(id=key[-6:]))
            if _text_width(name, self.label_for_tile[key].font()) > title_rect[2]:
                self.addToolTipRect_owner_userData_(AppKit.NSMakeRect(*title_rect), self, 2)
            if self._shows_cycle(key):
                self.addToolTipRect_owner_userData_(AppKit.NSMakeRect(*self._cycle_hit_rect(frame)), self, 0)
        Quartz.CATransaction.commit()

    @objc.python_method
    def _draw_contacts(self, frames):
        self.seam_segments = []
        contacts = {}
        own = self.own_id
        for peer, state in self.states.items():
            if (peer != own and own in frames and peer in frames
                    and state.get("state") in ("edge", "part", "corner") and state.get("crosses")
                    and state.get("picked_thirds", ["start"])):
                found = [(screen, stretch, self._third_breaks(contact, screen) if self.machine_picker or peer == self.selected else set())
                         for screen, contact in self._side_contacts(peer)
                         for stretch in core_ways.crossing_stretches(contact, screen, state.get("lit_thirds", []))]
                if found:
                    contacts[(own, peer, "signal")] = found
        peers = [key for key, state in self.states.items()
                 if key in frames and state.get("state") in ("between", "set-elsewhere")]
        for index, one in enumerate(peers):
            for two in peers[index + 1:]:
                if core_ways._rect_contact(self.tiles[one], self.tiles[two]):
                    pair = tuple(sorted((one, two)))
                    contacts[(pair[0], pair[1], "amber")] = [(None, core_ways._rect_contact(
                        self.tiles[pair[0]], self.tiles[pair[1]]), set())]
        for key in list(self.contact_layers):
            if key not in contacts:
                self.contact_layers.pop(key).removeFromSuperlayer()
        for key, stretches in contacts.items():
            one, two, kind = key
            a, b = frames[one], frames[two]
            layer = self.contact_layers.get(key)
            if layer is None:
                layer = Quartz.CAShapeLayer.layer()
                layer.setFillColor_(AppKit.NSColor.clearColor().CGColor())
                layer.setStrokeColor_(theme.cg("signal" if kind == "signal" else "amber"))
            for _screen, (side, start, end), _breaks in stretches:
                if kind == "signal":
                    layer.setFillColor_(theme.cg("signal"))
                layer.setLineWidth_(4 if kind == "signal" else 2)
                if kind == "amber":
                    layer.setLineDashPattern_([4, 3])
                layer.setLineCap_(Quartz.kCALineCapRound)
                layer.setZPosition_(20)
                self.layer().addSublayer_(layer)
                self.contact_layers[key] = layer
            path = Quartz.CGPathCreateMutable()
            scale = a[2] / self.tiles[one]["w"]
            layer.setStrokeColor_(theme.cg("signal" if kind == "signal" else "amber"))
            layer.setOpacity_(0.55 if kind == "signal" and not self.states.get(two, {}).get("linked", True) else 1.0)
            for screen, (side, start, end), breaks in stretches:
                if side == "corner":
                    corner = start
                    x = a[0] if corner.endswith("left") else a[0] + a[2]
                    y = a[1] if corner.startswith("top") else a[1] + a[3]
                    Quartz.CGPathMoveToPoint(path, None, x - 2, y - 2)
                    Quartz.CGPathAddLineToPoint(path, None, x + 2, y + 2)
                elif kind == "signal":
                    for piece in _split_at_breaks((side, start, end), breaks):
                        ends = self._seam_ends(frames, screen, *piece, breaks, 4.0)
                        if ends is not None:
                            Quartz.CGPathMoveToPoint(path, None, *ends[0])
                            Quartz.CGPathAddLineToPoint(path, None, *ends[1])
                            self.seam_segments.append({"peer": two, "kind": "seam", "side": side,
                                                       "start": ends[0], "end": ends[1], "width": 4.0})
                elif side in ("left", "right"):
                    x = (a[0] + a[2]) if side == "right" else a[0]
                    inset = min(2.0, max(0.0, (end - start) * scale / 2))
                    y1 = a[1] + (start - self.tiles[one]["y"]) * scale + inset
                    y2 = a[1] + (end - self.tiles[one]["y"]) * scale - inset
                    if y2 > y1:
                        Quartz.CGPathMoveToPoint(path, None, x, y1)
                        Quartz.CGPathAddLineToPoint(path, None, x, y2)
                else:
                    y = (a[1] + a[3]) if side == "bottom" else a[1]
                    inset = min(2.0, max(0.0, (end - start) * scale / 2))
                    x1 = a[0] + (start - self.tiles[one]["x"]) * scale + inset
                    x2 = a[0] + (end - self.tiles[one]["x"]) * scale - inset
                    if x2 > x1:
                        Quartz.CGPathMoveToPoint(path, None, x1, y)
                        Quartz.CGPathAddLineToPoint(path, None, x2, y)
            layer.setPath_(path)
        self._draw_thirds(frames)
        self._draw_wraps(frames)

    @objc.python_method
    def _draw_wraps(self, frames):
        """A wrap-around edge: a seam along This Mac's own side, with a label inside the tile naming
        where it leads and, on a left or right side, an arrow pointing out of it. A side kept while
        Edge is off is a faint dashed seam and says so."""
        wanted = {}
        own = frames.get(self.own_id)
        if own is not None:
            pads = self._pad_corners(frames)
            for peer, state in self.states.items():
                if peer == self.own_id or peer not in frames or state.get("in_use") is False:
                    continue
                opacity = (1.0 if state.get("linked", True) else 0.55) * (1.0 if self.machine_picker or peer == self.selected else 0.7)
                for kept, sides in ((False, state.get("wrap_sides", [])), (True, state.get("kept_sides", []))):
                    for side in sides:
                        wanted[(peer, side)] = (kept, opacity)
        for key in list(self.wrap_layers):
            if key not in wanted:
                for layer in self.wrap_layers.pop(key):
                    layer.removeFromSuperlayer()
        for (peer, side), (kept, opacity) in wanted.items():
            if (peer, side) not in self.wrap_layers:
                seam = Quartz.CAShapeLayer.layer()
                seam.setFillColor_(AppKit.NSColor.clearColor().CGColor())
                seam.setLineCap_(Quartz.kCALineCapRound)
                arrow = Quartz.CAShapeLayer.layer()
                arrow.setFillColor_(AppKit.NSColor.clearColor().CGColor())
                arrow.setLineWidth_(1.6)
                arrow.setLineCap_(Quartz.kCALineCapRound)
                arrow.setLineJoin_(Quartz.kCALineJoinRound)
                text = _text_layer(theme.TYPE["eyebrow"], "signal", Quartz.kCAAlignmentLeft)
                for layer in (seam, arrow, text):
                    layer.setZPosition_(21)
                    self.layer().addSublayer_(layer)
                self.wrap_layers[(peer, side)] = (seam, arrow, text)
            seam, arrow, text = self.wrap_layers[(peer, side)]
            x, y, width, height = own
            horizontal = side in ("top", "bottom")
            first, last = {"left": ("top_left", "bottom_left"), "right": ("top_right", "bottom_right"),
                           "top": ("top_left", "top_right"), "bottom": ("bottom_left", "bottom_right")}[side]
            start = (x if horizontal else y) + (13 if first in pads else 2)
            end = (x + width if horizontal else y + height) - (13 if last in pads else 2)
            fixed = {"left": x, "right": x + width, "top": y, "bottom": y + height}[side]
            path = Quartz.CGPathCreateMutable()
            if end > start:
                Quartz.CGPathMoveToPoint(path, None, *((start, fixed) if horizontal else (fixed, start)))
                Quartz.CGPathAddLineToPoint(path, None, *((end, fixed) if horizontal else (fixed, end)))
            seam.setPath_(path)
            seam.setLineWidth_(2 if kept else 4)
            seam.setLineDashPattern_([3, 5] if kept else None)
            theme.tint(seam, stroke="signal")
            seam.setOpacity_(0.35 if kept else opacity)
            ink = "ink_3" if kept else "signal"
            label = "wrap kept, Edge off" if kept else f"wraps to {self.labels.get(peer, 'another machine')}"
            text.setString_(label)
            theme.tint(text, foreground=ink)
            text.setOpacity_(0.8 if kept else opacity)
            theme.tint(arrow, stroke=ink)
            arrow.setOpacity_(0.7 if kept else opacity)
        if own is not None:
            self._place_wrap_labels(own, [(side, *self.wrap_layers[(peer, side)][1:]) for peer, side in wanted])

    @objc.python_method
    def _place_wrap_labels(self, own, marks):
        """Each wrap label gets its own row below This Mac's name and status line, so none ever lands on
        them or on another label: a top side's row nearest the name, a bottom side's nearest the
        bottom edge, left and right in between, sharing one row when both fit. Sides that say the
        same thing, as when one machine wraps on several, share one label naming them. Rows that do
        not fit the tile are hidden; the seam still shows the side."""
        x, y, width, height = own
        header = y + (51 if height >= 72 else 30)
        first, last = header + 4, y + height - 22
        order = {"merged": 0, "top": 0, "left": 1, "right": 2, "bottom": 3}
        marks = sorted(marks, key=lambda mark: order[mark[0]])
        said = {}
        for side, arrow, text in marks:
            said.setdefault(text.string(), []).append((side, arrow, text))
        marks = []
        for words, same in said.items():
            marks.append(same[0])
            if len(same) > 1:
                side, arrow, text = same[0]
                # The sides named, else counted, else the plain words: the seams still show which.
                merged = next((option for option in (f"{words} · {', '.join(mark[0] for mark in same)}",
                                                     f"{words} · {len(same)} sides")
                               if _text_width(option, text.font()) + 2 <= width - 24), words)
                text.setString_(merged)
                marks[-1] = ("merged", arrow, text)
                for _side, other_arrow, other_text in same[1:]:
                    other_text.setHidden_(True)
                    other_arrow.setHidden_(True)
        rows = []
        for side, arrow, text in sorted(marks, key=lambda mark: order[mark[0]]):
            label_width = min(math.ceil(_text_width(text.string(), text.font())) + 2,
                              max(1, width - (52 if side in ("left", "right") else 24)))
            mark = (side, arrow, text, label_width)
            if (side == "right" and rows and len(rows[-1]) == 1 and rows[-1][0][0] == "left"
                    and 26 + rows[-1][0][3] + 8 + label_width + 26 <= width):
                rows[-1].append(mark)
            else:
                rows.append([mark])
        room = 0 if height < 56 or last < first else int((last - first) // 18) + 1
        wanted = {"top": first, "merged": first, "left": y + height / 2 + 10, "right": y + height / 2 + 10,
                  "bottom": last}
        tops = []
        for row in rows[:room]:
            tops.append(max(min(last, wanted[row[0][0]]), tops[-1] + 18 if tops else first))
        for index in range(len(tops) - 1, -1, -1):
            tops[index] = min(tops[index], last if index == len(tops) - 1 else tops[index + 1] - 18)
        for index, row in enumerate(rows):
            for side, arrow, text, label_width in row:
                shown = index < len(tops)
                text.setHidden_(not shown)
                arrow.setHidden_(not shown or side not in ("left", "right"))
                if not shown:
                    continue
                label_y = tops[index]
                label_x = (x + 26 if side == "left" else x + width - 26 - label_width if side == "right"
                           else x + width - 12 - label_width)
                _set_layer_frame_if_changed(text, ((label_x, label_y), (label_width, 16)))
                arrow_path = Quartz.CGPathCreateMutable()
                if side in ("left", "right"):
                    direction = -1 if side == "left" else 1
                    tip, centre = (x + 16 if side == "left" else x + width - 16), label_y + 8
                    Quartz.CGPathMoveToPoint(arrow_path, None, tip - direction * 9, centre)
                    Quartz.CGPathAddLineToPoint(arrow_path, None, tip, centre)
                    Quartz.CGPathMoveToPoint(arrow_path, None, tip - direction * 4, centre - 4)
                    Quartz.CGPathAddLineToPoint(arrow_path, None, tip, centre)
                    Quartz.CGPathAddLineToPoint(arrow_path, None, tip - direction * 4, centre + 4)
                arrow.setPath_(arrow_path)

    @objc.python_method
    def _pad_corners(self, frames):
        """Every corner of This Mac's tile that carries a pad, whichever machine's it is."""
        return {corner for peer, state in self.states.items()
                if peer != self.own_id and peer in frames and state.get("in_use") is not False
                for corner in state.get("corner_ways", [])}

    @objc.python_method
    def _side_contacts(self, peer):
        """[(screen, contact)]: this Mac's display frames that meet `peer`'s along a side, as the engine
        finds them; thirds are measured along `screen`, the pointer's own display."""
        return core_ways.side_contacts(self.tiles[self.own_id], self.tiles[peer])

    @objc.python_method
    def _third_breaks(self, contact, screen):
        """The third boundaries of `screen`'s side inside `contact`, where the selected machine's
        seam and tracks break."""
        if not contact or contact[0] == "corner":
            return set()
        stretches = core_ways.third_stretches(contact, screen)
        return {before[3] for before, after in zip(stretches, stretches[1:])
                if before[1] == after[1] and abs(before[3] - after[2]) < 1e-6}

    @objc.python_method
    def _seam_ends(self, frames, screen, side, low, high, breaks, width):
        """The path ends, in view points, of the stretch `low`..`high` of `screen`'s `side`: 2pt in
        from an outer end and stopped at a corner halo; at a third boundary, far enough short that
        the round cap leaves half of THIRD_GAP clear. None when nothing is left to draw."""
        tile, frame = self.tiles[self.own_id], frames[self.own_id]
        scale = frame[2] / tile["w"]
        vertical = side in ("left", "right")
        origin, base = (frame[1], tile["y"]) if vertical else (frame[0], tile["x"])
        first, last = origin + (low - base) * scale, origin + (high - base) * scale
        outer = min(2.0, max(0.0, (last - first) / 2))
        first += THIRD_GAP / 2 + width / 2 if _at_break(low, breaks) else outer
        last -= THIRD_GAP / 2 + width / 2 if _at_break(high, breaks) else outer
        if vertical:
            fixed = frame[0] + (screen["x"] + (screen["w"] if side == "right" else 0) - tile["x"]) * scale
            start, end = self._stop_at_corner_halos(frames, side, (fixed, first), (fixed, last))
        else:
            fixed = frame[1] + (screen["y"] + (screen["h"] if side == "bottom" else 0) - tile["y"]) * scale
            start, end = self._stop_at_corner_halos(frames, side, (first, fixed), (last, fixed))
        along = 1 if vertical else 0
        return (start, end) if end[along] > start[along] else None

    @objc.python_method
    def _stop_at_corner_halos(self, frames, side, start, end):
        x, y, width, height = frames[self.own_id]
        corners = self._pad_corners(frames)
        if side in ("left", "right"):
            low = y + (11 if f"top_{side}" in corners else 0)
            high = y + height - (11 if f"bottom_{side}" in corners else 0)
            return (start[0], max(start[1], low)), (end[0], min(end[1], high))
        low = x + (11 if f"{side}_left" in corners else 0)
        high = x + width - (11 if f"{side}_right" in corners else 0)
        return (max(start[0], low), start[1]), (min(end[0], high), end[1])

    @objc.python_method
    def _third_segments(self, frames, peer=None):
        if peer is None and self.machine_picker:
            return [segment for key in self.tiles if key != self.own_id
                    for segment in self._third_segments(frames, key)]
        peer, own = self.selected if peer is None else peer, self.own_id
        state = self.states.get(peer, {})
        if (peer == own or peer not in frames or own not in frames
                or state.get("in_use") is False):
            return []
        tile, frame = self.tiles[own], frames[own]
        scale = frame[2] / tile["w"]
        segments = []
        for screen, contact in self._side_contacts(peer):
            for third, side, low, high in core_ways.third_stretches(contact, screen):
                if side in ("left", "right"):
                    x = frame[0] + (screen["x"] + (screen["w"] if side == "right" else 0) - tile["x"]) * scale
                    start, end = (x, frame[1] + (low - tile["y"]) * scale), (x, frame[1] + (high - tile["y"]) * scale)
                else:
                    y = frame[1] + (screen["y"] + (screen["h"] if side == "bottom" else 0) - tile["y"]) * scale
                    start, end = (frame[0] + (low - tile["x"]) * scale, y), (frame[0] + (high - tile["x"]) * scale, y)
                segments.append((peer, third, side, start, end))
        return segments

    @objc.python_method
    def _draw_thirds(self, frames):
        marks = {}
        segments = self._third_segments(frames)
        for peer in dict.fromkeys(segment[0] for segment in segments):
            state = self.states.get(peer, {})
            if state.get("edge_off") or not state.get("picked_thirds"):
                continue
            contacts = self._side_contacts(peer)
            for index, (screen, contact) in enumerate(contacts):
                breaks = self._third_breaks(contact, screen)
                for third, side, low, high in core_ways.third_stretches(contact, screen):
                    if third in (state.get("lit_thirds") or state["picked_thirds"]):
                        continue
                    ends = self._seam_ends(frames, screen, side, low, high, breaks, 2.0)
                    if ends is not None:
                        marks[(peer, third, "track") if len(contacts) == 1 else (peer, index, third, "track")] = ends
                        self.seam_segments.append({"peer": peer, "kind": "track", "side": side,
                                                   "start": ends[0], "end": ends[1], "width": 2.0})
        for key in list(self.third_layers):
            if key not in marks:
                self.third_layers.pop(key).removeFromSuperlayer()
        for key, (start, end) in marks.items():
            layer = self.third_layers.get(key)
            if layer is None:
                layer = Quartz.CAShapeLayer.layer()
                layer.setFillColor_(AppKit.NSColor.clearColor().CGColor())
                layer.setLineCap_(Quartz.kCALineCapRound)
                layer.setZPosition_(21)
                self.layer().addSublayer_(layer)
                self.third_layers[key] = layer
            layer.setStrokeColor_(theme.cg("signal"))
            layer.setLineWidth_(2)
            layer.setOpacity_(0.3)
            path = Quartz.CGPathCreateMutable()
            Quartz.CGPathMoveToPoint(path, None, *start)
            Quartz.CGPathAddLineToPoint(path, None, *end)
            layer.setPath_(path)

    @objc.python_method
    def _third_hit_segment(self, point):
        frames, _scale = self._map()
        for peer, third, side, start, end in self._third_segments(frames):
            if side in ("left", "right"):
                hit = abs(point.x - start[0]) <= 5 and start[1] <= point.y <= end[1]
            else:
                hit = abs(point.y - start[1]) <= 5 and start[0] <= point.x <= end[0]
            if hit:
                return peer, third, side
        return None

    @objc.python_method
    def _third_hit(self, point):
        if self.read_only:
            return None
        hit = self._third_hit_segment(point)
        return hit[:2] if hit else None

    @objc.python_method
    def _third_tooltip(self, peer, third, side=None):
        side = side or core_ways._rect_contact(self.tiles[self.own_id], self.tiles[peer])[0]
        name = core_ways.part_names(side)[third]
        picked = self.states.get(peer, {}).get("picked_thirds", [])
        # Translators: third names are one of the three sections of a screen edge.
        detail = tr("click to turn it off") if third in picked else tr("click to turn it on")
        return tr("{name} third: {detail}").format(name=name, detail=detail)

    @objc.python_method
    def _draw_marks(self, frames):
        marks = {}
        own_frame = frames.get(self.own_id)
        if own_frame is not None:
            x, y, width, height = own_frame
            for peer, state in self.states.items():
                if peer == self.own_id or peer not in frames or state.get("in_use") is False:
                    continue
                for corner in state.get("corner_ways", []):
                    px = x if corner.endswith("left") else x + width
                    py = y if corner.startswith("top") else y + height
                    path = Quartz.CGPathCreateMutable()
                    Quartz.CGPathAddRoundedRect(path, None, Quartz.CGRectMake(px - 8, py - 8, 16, 16), 4, 4)
                    marks[(peer, "corner", corner)] = (path, (px, py))
                if state.get("notch_way"):
                    path = Quartz.CGPathCreateMutable()
                    Quartz.CGPathAddRoundedRect(path, None, Quartz.CGRectMake(*self._notch_rect(own_frame)), 2, 2)
                    marks[(peer, "notch")] = (path, None)
        for key in list(self.mark_layers):
            if key not in marks:
                self.mark_layers.pop(key).removeFromSuperlayer()
                if key in self.corner_halo_layers:
                    self.corner_halo_layers.pop(key).removeFromSuperlayer()
        for key, (path, vertex) in marks.items():
            layer = self.mark_layers.get(key)
            if layer is None:
                layer = Quartz.CAShapeLayer.layer()
                layer.setLineWidth_(4)
                layer.setLineCap_(Quartz.kCALineCapRound)
                layer.setLineJoin_(Quartz.kCALineJoinRound)
                if vertex is not None:
                    halo = Quartz.CAShapeLayer.layer()
                    halo.setFillColor_(theme.cg("ground"))
                    self.layer().addSublayer_(halo)
                    self.corner_halo_layers[key] = halo
                    chevron = Quartz.CAShapeLayer.layer()
                    chevron.setFillColor_(AppKit.NSColor.clearColor().CGColor())
                    chevron.setStrokeColor_(theme.cg("ground"))
                    chevron.setLineWidth_(1.8)
                    chevron.setLineCap_(Quartz.kCALineCapRound)
                    chevron.setLineJoin_(Quartz.kCALineJoinRound)
                    layer.addSublayer_(chevron)
                self.layer().addSublayer_(layer)
                self.mark_layers[key] = layer
            clear = AppKit.NSColor.clearColor().CGColor()
            layer.setFillColor_(theme.cg("signal"))
            layer.setStrokeColor_(clear)
            if vertex is not None:
                px, py = vertex
                halo = self.corner_halo_layers[key]
                chevron = layer.sublayers()[0]
                halo_path = Quartz.CGPathCreateMutable()
                Quartz.CGPathAddRoundedRect(halo_path, None, Quartz.CGRectMake(px - 11, py - 11, 22, 22), 5, 5)
                halo.setPath_(halo_path)
                dx = 1 if key[2].endswith("left") else -1
                dy = 1 if key[2].startswith("top") else -1
                chevron_path = Quartz.CGPathCreateMutable()
                # The 7 pt bracket centred on the vertex, as in the 16 pt pad around it.
                Quartz.CGPathMoveToPoint(chevron_path, None, px - 3.5 * dx, py + 3.5 * dy)
                Quartz.CGPathAddLineToPoint(chevron_path, None, px - 3.5 * dx, py - 3.5 * dy)
                Quartz.CGPathAddLineToPoint(chevron_path, None, px + 3.5 * dx, py - 3.5 * dy)
                chevron.setPath_(chevron_path)
            selected = self.machine_picker or key[0] == self.selected
            layer.setOpacity_(1.0 if selected else 0.55)
            layer.setZPosition_(21 if selected else 20)
            if vertex is not None:
                halo.setZPosition_(19.5)
            layer.setPath_(path)

    @objc.python_method
    def _notch_rect(self, frame):
        """The notch dash, 4pt tall across the top edge: over the notch on the built-in display when
        this Mac's tile has its displays and `notch_span`, else a 14pt dash at the tile's top centre."""
        x, y, width, _height = frame
        own = self.tiles[self.own_id]
        span = self.states.get(self.own_id, {}).get("notch_span")
        screen = next((screen for screen in core_ways._display_frames(own)
                       if span and own.get("displays") and screen["display"] == span[0]), None)
        if screen is None:
            return x + width / 2 - 7, y - 2, 14, 4
        scale = width / own["w"]
        left = x + (screen["x"] - own["x"]) * scale
        top = y + (screen["y"] - own["y"]) * scale
        span_width = screen["w"] * scale
        return left + span[1] * span_width, top - 2, (span[2] - span[1]) * span_width, 4

    @objc.python_method
    def snap_preview(self):
        if self.dragging is None or self.dragging.get("display") is not None or self.dragging.get("resize"):
            return None
        key = self.dragging.get("key")
        if key in (None, self.own_id) or key not in self.tiles or self.own_id not in self.tiles:
            return None
        _frames, scale = self._map()
        radius = self.snap_radius_for_scale(scale)
        tile = self.tiles[key]
        layout = {ident: value for ident, value in self.tiles.items() if ident != key}
        snapped = core_ways.snap_position(tile, self.own_id, layout, radius=radius)
        if ((snapped["x"], snapped["y"]) == (tile["x"], tile["y"])
                or not core_ways.within_snap_radius(tile, snapped, self.own_id, layout, radius)):
            return None
        return {**tile, **snapped}

    @objc.python_method
    def _draw_snap_preview(self, frames, scale):
        snapped = self.snap_preview()
        if snapped is None:
            self.snap_ghost.setHidden_(True)
            self.snap_seam.setHidden_(True)
            return
        key = self.dragging["key"]
        own = self.tiles[self.own_id]
        own_frame = frames[self.own_id]
        offset_x = own_frame[0] - own["x"] * scale
        offset_y = own_frame[1] - own["y"] * scale
        peer_frame = (snapped["x"] * scale + offset_x, snapped["y"] * scale + offset_y,
                      snapped["w"] * scale, snapped["h"] * scale)
        path = Quartz.CGPathCreateMutable()
        Quartz.CGPathAddRect(path, None, Quartz.CGRectMake(*peer_frame))
        self.snap_ghost.setPath_(path)
        self.snap_ghost.setZPosition_(19)
        self.snap_ghost.setHidden_(False)
        contact = core_ways._rect_contact(own, snapped)
        self.snap_seam.setHidden_(contact is None)
        if contact is None:
            return
        side, start, end = contact
        x, y, width, height = frames[self.own_id]
        seam = Quartz.CGPathCreateMutable()
        if side == "corner":
            px = x if start.endswith("left") else x + width
            py = y if start.startswith("top") else y + height
            Quartz.CGPathAddEllipseInRect(seam, None, Quartz.CGRectMake(px - 3.5, py - 3.5, 7, 7))
            self.snap_seam.setStrokeColor_(AppKit.NSColor.clearColor().CGColor())
        elif side in ("left", "right"):
            px = x if side == "left" else x + width
            inset = min(2.0, max(0.0, (end - start) * scale / 2))
            self.snap_seam.setStrokeColor_(theme.cg("signal"))
            Quartz.CGPathMoveToPoint(seam, None, px, y + (start - own["y"]) * scale + inset)
            Quartz.CGPathAddLineToPoint(seam, None, px, y + (end - own["y"]) * scale - inset)
        else:
            py = y if side == "top" else y + height
            inset = min(2.0, max(0.0, (end - start) * scale / 2))
            self.snap_seam.setStrokeColor_(theme.cg("signal"))
            Quartz.CGPathMoveToPoint(seam, None, x + (start - own["x"]) * scale + inset, py)
            Quartz.CGPathAddLineToPoint(seam, None, x + (end - own["x"]) * scale - inset, py)
        self.snap_seam.setPath_(seam)
        self.snap_seam.setFillColor_(theme.cg("signal"))
        self.snap_seam.setZPosition_(21)

    @objc.python_method
    def _draw_guides(self, frame):
        own_frame = frame
        while len(self.grid_layers) < 4:
            grid = _box(("edge", 0.24), None)
            self.layer().insertSublayer_atIndex_(grid, 0)
            self.grid_layers.append(grid)
        for layer in self.grid_layers:
            layer.setHidden_(own_frame is None)
        if own_frame is not None:
            x, y, width, height = own_frame
            for index, layer in enumerate(self.grid_layers):
                if index < 2:
                    gx = x + width * (index + 1) / 3
                    layer.setFrame_(_rect((gx, 0, 1, self.bounds().size.height)))
                else:
                    gy = y + height * (index - 1) / 3
                    layer.setFrame_(_rect((0, gy, self.bounds().size.width, 1)))
        visible = self.dragging is not None and frame is not None
        while len(self.guide_layers) < 16:
            guide = _box(("ink_3", 0.36), None)
            self.layer().addSublayer_(guide)
            self.guide_layers.append(guide)
        for guide in self.guide_layers:
            guide.setHidden_(not visible)
        if not visible:
            return
        x, y, width, height = frame
        marks = []
        for part in range(4):
            cx, cy = x + width * part / 3, y + height * part / 3
            marks.extend(((cx - 0.8, y - 7, 1.6, 6), (cx - 0.8, y + height + 1, 1.6, 6),
                          (x - 7, cy - 0.8, 6, 1.6), (x + width + 1, cy - 0.8, 6, 1.6)))
        for layer, rect in zip(self.guide_layers, marks):
            layer.setFrame_(_rect(rect))

    @objc.python_method
    def _recentre_frame(self):
        width = self.bounds().size.width
        return width - 84, 7, 74, 24

    @objc.python_method
    def _recentre_screen_frame(self):
        rect = AppKit.NSMakeRect(*self._recentre_frame())
        window = self.window()
        if window is None:
            return rect
        return window.convertRectToScreen_(self.convertRect_toView_(rect, None))

    @objc.python_method
    def _nearest_side(self, own, peer):
        targets = {side: core_ways.place_beside({self.own_id: own, self.selected: peer},
                                                self.own_id, self.selected, side)
                   for side in ("left", "right", "top", "bottom")}
        return min(targets, key=lambda side: (targets[side]["x"] - peer["x"]) ** 2
                   + (targets[side]["y"] - peer["y"]) ** 2)

    @objc.python_method
    def _recentre_side(self, peer):
        own = self.tiles.get(self.own_id)
        tile = self.tiles.get(peer)
        if own is None or tile is None:
            return None
        contact = core_ways._rect_contact(own, tile)
        if contact and contact[0] != "corner":
            return contact[0]
        return self._nearest_side(own, tile)

    @objc.python_method
    def _is_centred(self, peer):
        if peer not in self.tiles or self.own_id not in self.tiles:
            return False
        own, tile = self.tiles[self.own_id], self.tiles[peer]
        for side in ("left", "right", "top", "bottom"):
            target = core_ways.place_beside({self.own_id: own, peer: tile}, self.own_id, peer, side)
            if target["x"] == tile["x"] and target["y"] == tile["y"]:
                return True
        return False

    @objc.python_method
    def _show_recentre(self):
        return (not self.read_only and self.selected not in (None, self.own_id, self.fixed_machine_id) and self.selected in self.tiles
                and self.own_id in self.tiles and not self._is_centred(self.selected))

    @objc.python_method
    def recentre_selected(self):
        if self.read_only or self.selected == self.fixed_machine_id or self.dragging is not None:
            return False
        peer = self.selected
        side = self._recentre_side(peer) if peer not in (None, self.own_id) else None
        if side is None:
            return False
        self.tiles[peer] = core_ways.place_beside(self.tiles, self.own_id, peer, side)
        self._draw(True)
        if self.on_layout_change:
            self.on_layout_change(self.tiles, peer, snap=False)
        return True

    @objc.python_method
    def _recentre_hit(self, point):
        if not self._show_recentre():
            return False
        x, y, width, height = self._recentre_frame()
        return x <= point.x <= x + width and y <= point.y <= y + height

    @objc.python_method
    def tile_at(self, point):
        frames, _scale = self._map()
        return next((key for key, frame in reversed(list(frames.items()))
                     if frame[0] <= point.x <= frame[0] + frame[2]
                     and frame[1] <= point.y <= frame[1] + frame[3]), None)

    @objc.python_method
    def display_at(self, point):
        frames, scale = self._map()
        for key in reversed(list(self.tiles)):
            if not self.tiles[key].get("displays"):
                continue
            frame = frames[key]
            map_x, map_y = frame[0] - self.tiles[key]["x"] * scale, frame[1] - self.tiles[key]["y"] * scale
            screens = reversed(core_ways._display_frames(self.tiles[key]))
            for screen in screens:
                rect = (screen["x"] * scale + map_x, screen["y"] * scale + map_y,
                        screen["w"] * scale, screen["h"] * scale)
                if rect[0] <= point.x <= rect[0] + rect[2] and rect[1] <= point.y <= rect[1] + rect[3]:
                    return key, screen["display"]
        return None, None

    @objc.python_method
    def _cycle_hit(self, key, point):
        frames, _scale = self._map()
        x, y, width, height = self._cycle_hit_rect(frames[key])
        return self._shows_cycle(key) and x <= point.x <= x + width and y <= point.y <= y + height

    @objc.python_method
    def _stop_tile_animations(self, key):
        layers = [self.layer_for_tile.get(key), self.label_for_tile.get(key),
                  self.state_for_tile.get(key), self.outcome_for_tile.get(key),
                  self.dot_for_tile.get(key), self.cycle_for_tile.get(key),
                  self.resize_layers.get(key), self.focus_layer]
        layers.extend(self.display_layers.get(key, ()))
        layers.extend(self.display_number_layers.get(key, ()))
        for layer in layers:
            if layer is not None:
                layer.removeAllAnimations()

    @objc.python_method
    def toggle_cycle(self, key):
        if self.read_only or not self.cycle_enabled or key == self.own_id or key not in self.tiles:
            return
        self.cycle[key] = not self.cycle.get(key, True)
        if self.on_cycle_change:
            self.on_cycle_change(key, self.cycle[key])
        self._draw(False)

    def updateTrackingAreas(self):
        for area in self.trackingAreas():
            self.removeTrackingArea_(area)
        area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingMouseMoved |
            AppKit.NSTrackingActiveInKeyWindow,
            self, None)
        self.addTrackingArea_(area)
        objc.super(AtlasCanvas, self).updateTrackingAreas()

    def mouseEntered_(self, event):
        self.mouseMoved_(event)

    def mouseMoved_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        recentre_hovered = self._recentre_hit(point)
        hovered = self.tile_at(point)
        if self._resize_hit(point) is not None:
            self._resize_cursor().set()
        elif self._third_hit(point):
            AppKit.NSCursor.pointingHandCursor().set()
        elif hovered is not None and self.machine_picker:
            AppKit.NSCursor.pointingHandCursor().set()
        elif hovered is not None and hovered != self.fixed_machine_id and not self.read_only:
            AppKit.NSCursor.openHandCursor().set()
        else:
            AppKit.NSCursor.arrowCursor().set()
        if hovered != self.hovered or recentre_hovered != self.recentre_hovered:
            self.hovered = hovered
            self.recentre_hovered = recentre_hovered
            self._draw(False)

    def mouseExited_(self, _event):
        self.hovered = None
        self.recentre_hovered = False
        if self.dragging is None:
            AppKit.NSCursor.arrowCursor().set()
        self._draw(False)

    def view_stringForToolTip_point_userData_(self, _view, _tag, point, _data):
        if _data == 2:
            key = self.tile_at(point)
            return self.labels.get(key, tr("Machine · {id}").format(id=(key or "")[-6:]))
        hit = self._third_hit_segment(point)
        if hit:
            return self._third_tooltip(*hit)
        return tr("In the shortcut's cycle")

    def accessibilityPerformPress(self):
        if self.machine_picker and self.selected in self.tiles:
            self.select_tile(self.selected)
            return True
        if self.selected in self.tiles and self.selected != self.own_id:
            self.toggle_cycle(self.selected)
            return True
        return False

    def mouseDown_(self, event):
        self.window().makeFirstResponder_(self)
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        resize_key = self._resize_hit(point)
        if resize_key is not None:
            frames, scale = self._map()
            original = copy.deepcopy(self.tiles[resize_key])
            frame = frames[resize_key]
            self._stop_tile_animations(resize_key)
            self.keyboard_focus = False
            self.selected = resize_key
            self.last_announcement = ""
            self.third_pressed = None
            self.dragging = {"key": resize_key, "resize": True, "original": original,
                             "pointer_x": point.x, "pointer_y": point.y, "scale": scale,
                             "map_x": frame[0] - original["x"] * scale,
                             "map_y": frame[1] - original["y"] * scale, "moved": False}
            # Picking a machine in profiles mode switches the viewed layout. A grip edits the
            # current picture instead; This Mac's move-selection handler also resets selection.
            if self.on_select and not self.machine_picker and resize_key != self.fixed_machine_id:
                self.on_select(resize_key)
            self._resize_cursor().set()
            self._draw(False)
            return
        hit_key = self.tile_at(point)
        picking = self.machine_picker and hit_key is not None and hit_key != self.own_id
        self.third_pressed = None if self.read_only or picking else self._third_hit(point)
        if self.third_pressed:
            return
        if self._recentre_hit(point):
            self.recentre_selected()
            return
        key, display = self.display_at(point)
        if key is None:
            key = self.tile_at(point)
        if key is None:
            return
        if event.clickCount() > 1 and not self.machine_picker and not self.read_only and key != self.own_id and self.on_rename:
            self.selected = key
            self.on_rename(key)
            return
        if event.clickCount() > 1 and not self.machine_picker and key == self.own_id:
            self.announce(tr("Rename this Mac on Overview."))
            return
        if self._cycle_hit(key, point):
            self.toggle_cycle(key)
            return
        if key == self.fixed_machine_id:
            if self.machine_picker:
                self.select_tile(key)
            return
        self._stop_tile_animations(key)
        self.keyboard_focus = False
        self.selected = key
        self.last_announcement = ""
        frames, scale = self._map()
        origin_id = self.own_id if self.own_id in frames else key
        origin = self.tiles[origin_id]
        map_x = frames[origin_id][0] - origin["x"] * scale
        map_y = frames[origin_id][1] - origin["y"] * scale
        if self.on_select:
            self.on_select(key)
        if self.read_only:
            self._draw(False)
            return
        offsets = self.tiles[key].get("display_offsets", {})
        split = display is not None and display in offsets
        option = bool(event.modifierFlags() & AppKit.NSEventModifierFlagOption)
        if display is not None and (split or option):
            self.dragging = {"key": key, "display": display, "display_offset": dict(offsets.get(display, {"x": 0, "y": 0})),
                             "pointer_x": point.x, "pointer_y": point.y,
                             "scale": scale,
                             "map_x": map_x, "map_y": map_y,
                             "moved": False,
                             "split_toggle": option and not split, "group_toggle": option and split}
            AppKit.NSCursor.closedHandCursor().set()
            self._draw(False)
            return
        frame = frames[key]
        self.dragging = {"key": key, "pointer_x": point.x - frame[0], "pointer_y": point.y - frame[1],
                         "map_x": map_x, "map_y": map_y, "scale": scale,
                         "moved": False}
        AppKit.NSCursor.closedHandCursor().set()
        self._draw(False)

    def mouseDragged_(self, event):
        if self.dragging is None:
            return
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        scale = self.dragging["scale"]
        key = self.dragging["key"]
        if self.dragging.get("resize"):
            original = self.dragging["original"]
            self.tiles[key] = core_ways.resize_from_drag(
                original, (point.x - self.dragging["pointer_x"]) / scale,
                (point.y - self.dragging["pointer_y"]) / scale)
            self.dragging["moved"] = self.tiles[key] != original
            self._draw(False)
            return
        if self.dragging.get("display") is not None:
            display = self.dragging["display"]
            dx = round((point.x - self.dragging["pointer_x"]) / scale)
            dy = round((point.y - self.dragging["pointer_y"]) / scale)
            tile = dict(self.tiles[key])
            offsets = dict(tile.get("display_offsets", {}))
            start = self.dragging["display_offset"]
            offsets[display] = {"x": start["x"] + dx, "y": start["y"] + dy}
            tile["display_offsets"] = offsets
            self.tiles[key] = tile
            self.dragging["moved"] = self.dragging["moved"] or bool(dx or dy)
            self._draw(False)
            return
        tile = dict(self.tiles[key])
        tile["x"] = round((point.x - self.dragging["pointer_x"] - self.dragging["map_x"]) / scale)
        tile["y"] = round((point.y - self.dragging["pointer_y"] - self.dragging["map_y"]) / scale)
        self.dragging["moved"] = self.dragging["moved"] or tile["x"] != self.tiles[key]["x"] or tile["y"] != self.tiles[key]["y"]
        self.tiles[key] = tile
        self._draw(False)

    def mouseUp_(self, event):
        if self.third_pressed:
            pressed, self.third_pressed = self.third_pressed, None
            point = self.convertPoint_fromView_(event.locationInWindow(), None)
            if self._third_hit(point) == pressed and self.on_third:
                self.on_third(*pressed)
            return
        if self.dragging is None:
            return
        key, moved = self.dragging["key"], self.dragging["moved"]
        if self.dragging.get("resize"):
            self.dragging = None
            AppKit.NSCursor.arrowCursor().set()
            self._draw(False)
            if moved:
                self._commit_resize(key)
            return
        display = self.dragging.get("display")
        split_toggle = self.dragging.get("split_toggle", False)
        group_toggle = self.dragging.get("group_toggle", False)
        preview = self.snap_preview() if moved and display is None else None
        if preview is not None:
            self.tiles[key] = preview
        self.dragging = None
        if preview is not None:
            self._draw(True)
        AppKit.NSCursor.arrowCursor().set()
        if key != self.fixed_machine_id and self.on_layout_change and (moved or (display is not None and (split_toggle or group_toggle))):
            point = self.convertPoint_fromView_(event.locationInWindow(), None)
            if display is not None and not moved and (split_toggle or group_toggle):
                tile = dict(self.tiles[key])
                offsets = dict(tile.get("display_offsets", {}))
                if split_toggle:
                    offsets[display] = {"x": 0, "y": 0}
                else:
                    offsets.pop(display, None)
                updated = {**tile, "display_offsets": offsets}
                if not offsets:
                    updated.pop("display_offsets", None)
                self.tiles[key] = updated
                self.on_layout_change(self.tiles, key, snap=False)
                return
            if display is None and point.y > self.bounds().size.height and self.on_leave_out:
                self.on_leave_out(key)
                return
            # The preview already decided any snap; a second snap in the app, on the scale after the
            # drop, could move a tile the preview said would stay.
            self.on_layout_change(self.tiles, key, snap=False)

    def keyDown_(self, event):
        self.keyboard_focus = True
        if self.machine_picker and event.keyCode() in (36, 49):
            self.select_tile(self.selected)
            return
        if event.keyCode() == 8 and self._show_recentre():
            self.recentre_selected()
            return
        vectors = {123: (-1, 0), 124: (1, 0), 125: (0, 1), 126: (0, -1)}
        vector = vectors.get(event.keyCode())
        if (not self.read_only and not self.machine_picker and event.keyCode() in (36, 120)
                and self.selected not in (None, self.own_id) and self.on_rename):
            self.on_rename(self.selected)
            return
        if event.keyCode() == 49 and self.cycle_enabled and self.selected not in (None, self.own_id):
            self.toggle_cycle(self.selected)
            return
        if event.keyCode() == 48 and self.selected is not None:
            ordered = self.reading_order()
            backwards = bool(event.modifierFlags() & AppKit.NSEventModifierFlagShift)
            next_index = ordered.index(self.selected) + (-1 if backwards else 1) if self.selected in ordered else 0
            if 0 <= next_index < len(ordered):
                if self.machine_picker:
                    self.selected = ordered[next_index]
                    self._draw(False)
                else:
                    self.select_tile(ordered[next_index])
                return
            self.keyboard_focus = False
            self._draw(False)
        if vector is not None and self.selected not in self.tiles:
            return
        if self.read_only and vector is not None:
            return
        if vector is not None and event.modifierFlags() & AppKit.NSEventModifierFlagOption:
            if not self._can_resize(self.selected):
                return
            tile = self.tiles[self.selected]
            long_side = max(tile["w"], tile["h"])
            resized = core_ways.resize_tile(tile, long_side + (vector[0] + vector[1]) * long_side / 8)
            if resized != tile:
                self.tiles[self.selected] = resized
                self._draw(True)
                self._commit_resize(self.selected)
            return
        if vector is None or self.selected is None or self.selected == self.fixed_machine_id:
            objc.super(AtlasCanvas, self).keyDown_(event)
            return
        tile = dict(self.tiles[self.selected])
        tile["x"] += round(tile["w"] / 3) * vector[0]
        tile["y"] += round(tile["h"] / 3) * vector[1]
        self.tiles[self.selected] = tile
        sentence = f"{self.labels.get(self.selected, 'Machine')} moved one third."
        self._draw(True)
        if self.on_layout_change:
            self.on_layout_change(self.tiles, self.selected, snap=False)
        else:
            self.announce(sentence)


class PushStrip(AppKit.NSView):
    """A single accessible resistance slider with live pressure on the same 0–500 track."""

    HEIGHT = 72.0
    MAXIMUM = MAX_RESISTANCE
    INSET = 18.0
    TRACK_TOP = 20.0
    TRACK_HEIGHT = 12.0
    drawn_focus = False

    def isFlipped(self):
        return True

    @objc.python_method
    def setup(self):
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.minimum_height = self.heightAnchor().constraintEqualToConstant_(self.HEIGHT)
        self.minimum_height.setActive_(True)
        self.setWantsLayer_(True)
        root = self.layer()
        root.setCornerRadius_(3)
        root.setMasksToBounds_(True)
        theme.tint(root, background="ground", border="rule")
        root.setBorderWidth_(1)
        self._value = 120
        self._pressure = 0.0
        self._active_edge = "right"
        self._crossing = False
        self._flash_timer = None
        self.on_change = None
        self.other_name = tr("Other machine")
        self.setAccessibilityElement_(True)
        self.setAccessibilityRole_(AppKit.NSAccessibilitySliderRole)
        self.setAccessibilityLabel_(tr("Resistance"))
        self.setAccessibilityValue_(self._value)
        self._update_accessibility_description()
        return self

    def acceptsFirstResponder(self):
        return True

    def becomeFirstResponder(self):
        self.setNeedsDisplay_(True)
        return True

    def resignFirstResponder(self):
        self.setNeedsDisplay_(True)
        return True

    @objc.python_method
    def refresh_keyboard_focus(self):
        from widgets import keyboard_focus_visible

        if keyboard_focus_visible(self) != self.drawn_focus:
            self.setNeedsDisplay_(True)

    @objc.python_method
    def set_value(self, value, notify=False):
        value = max(0, min(self.MAXIMUM, int(round(float(value)))))
        if value == self._value:
            return
        self._value = value
        self.setAccessibilityValue_(value)
        self._update_accessibility_description()
        self.setNeedsDisplay_(True)
        if notify and self.on_change is not None:
            self.on_change(value)

    @objc.python_method
    def set_other_name(self, name):
        name = name or tr("Other machine")
        if name != self.other_name:
            self.other_name = name
            self.setNeedsDisplay_(True)

    @objc.python_method
    def peer_label_text(self, width=None):
        width = self.bounds().size.width if width is None else width
        font = theme.font(theme.TYPE["eyebrow"], 600)
        attributes = {AppKit.NSFontAttributeName: font,
                      AppKit.NSForegroundColorAttributeName: theme.colour("ink_3"),
                      AppKit.NSKernAttributeName: 1.45}
        text = AppKit.NSString.stringWithString_(self.other_name.upper())
        available = max(1.0, width / 2 - 16)
        value = self.other_name.upper()
        while value and AppKit.NSString.stringWithString_(value + "…").sizeWithAttributes_(attributes).width > available:
            value = value[:-1]
        return value + "…" if value != self.other_name.upper() else self.other_name.upper()

    @objc.python_method
    def show(self, fraction, other_name=None):
        other_name = other_name or tr("Other machine")
        self.set_value(float(fraction) * self.MAXIMUM)
        self.set_other_name(other_name)

    @objc.python_method
    def set_pressure(self, pressure, edge=None, crossed=False):
        if self._flash_timer is not None:
            self._flash_timer.invalidate()
            self._flash_timer = None
        self._crossing = bool(crossed)
        if edge:
            self._active_edge = edge
        self._pressure = max(0.0, min(float(pressure), self.MAXIMUM))
        if self._crossing:
            timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                0.12, self, "clearCrossingFlash:", None, False
            )
            AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, AppKit.NSRunLoopCommonModes)
            self._flash_timer = timer
        self._update_accessibility_description()
        self.setNeedsDisplay_(True)

    def clearCrossingFlash_(self, _timer):
        self._flash_timer = None
        self._crossing = False
        self._pressure = 0.0
        self._update_accessibility_description()
        self.setNeedsDisplay_(True)

    def accessibilityHelp(self):
        return self._accessibility_description

    def accessibilityPerformIncrement(self):
        self.nudge(1)
        return True

    def accessibilityPerformDecrement(self):
        self.nudge(-1)
        return True

    @objc.python_method
    def _update_accessibility_description(self):
        pressure = int(round(self._pressure))
        if pressure > 0:
            sentence = tr("Current pressure {pressure} of {value} pointer units at the {edge} edge.")
            self._accessibility_description = sentence.format(
                pressure=pressure, value=self._value, edge=tr(core_ways.EDGE_NAMES[self._active_edge]))
        else:
            self._accessibility_description = tr("No push. Current pressure is 0 of {value} pointer units.").format(
                value=self._value)
        self.setAccessibilityHelp_(self._accessibility_description)

    @objc.python_method
    def move_to(self, fraction):
        self.set_value(max(0, min(1, float(fraction))) * self.MAXIMUM, notify=True)

    @objc.python_method
    def nudge(self, direction, fine=False):
        self.set_value(self._value + direction * (1 if fine else 10), notify=True)

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self.set_value(value)

    def mouseDown_(self, event):
        self._drag(event)

    def mouseDragged_(self, event):
        self._drag(event)

    @objc.python_method
    def _drag(self, event):
        self.window().makeFirstResponder_(self)
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        width = self.bounds().size.width
        self.move_to((point.x - self.INSET) / max(1.0, width - 2 * self.INSET))

    def keyDown_(self, event):
        keys = event.charactersIgnoringModifiers()
        steps = {AppKit.NSLeftArrowFunctionKey: -1, AppKit.NSDownArrowFunctionKey: -1,
                 AppKit.NSRightArrowFunctionKey: 1, AppKit.NSUpArrowFunctionKey: 1}
        direction = steps.get(keys[0]) if keys else None
        if direction is None:
            objc.super(PushStrip, self).keyDown_(event)
            return
        self.nudge(direction, fine=bool(event.modifierFlags() & AppKit.NSEventModifierFlagOption))

    def drawRect_(self, _rect):
        from Foundation import NSMakeRect, NSString

        width = self.bounds().size.width
        if width <= 0:
            return
        left = resistance_x(width, 0)
        right = resistance_x(width, self.MAXIMUM)
        span = max(0.0, right - left)

        def color(role):
            theme.colour(role).set()

        def text(value, x, y, w, h, font, role, align="center", kern=None):
            value = str(value)
            attributes = {AppKit.NSFontAttributeName: font,
                          AppKit.NSForegroundColorAttributeName: theme.colour(role)}
            if kern is not None:
                attributes[AppKit.NSKernAttributeName] = kern
            drawing = NSString.stringWithString_(value)
            if align in ("left", "right") and drawing.sizeWithAttributes_(attributes).width > w:
                while value and NSString.stringWithString_(value + "…").sizeWithAttributes_(attributes).width > w:
                    value = value[:-1]
                drawing = NSString.stringWithString_(value + "…")
            size = drawing.sizeWithAttributes_(attributes)
            if align == "right":
                x += max(0, w - size.width)
                w = size.width
            elif align == "center":
                x += max(0, (w - size.width) / 2)
                w = size.width
            drawing.drawInRect_withAttributes_(NSMakeRect(x, y, w, h), attributes)

        track = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(left, self.TRACK_TOP, span, self.TRACK_HEIGHT), 3, 3
        )
        color("well")
        track.fill()
        color("ink_3")
        track.setLineWidth_(1.0)
        track.stroke()

        head = resistance_x(width, self._pressure)
        if self._pressure > 0:
            fill = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(left, self.TRACK_TOP + 0.5, max(0.0, head - left), self.TRACK_HEIGHT - 1), 2.5, 2.5
            )
            color("signal")
            fill.fill()

        thumb_x = resistance_x(width, self._value)
        path = AppKit.NSBezierPath.bezierPath()
        path.moveToPoint_((thumb_x, 17.5))
        path.lineToPoint_((thumb_x, 38.0))
        from widgets import keyboard_focus_visible
        focused = keyboard_focus_visible(self)
        self.drawn_focus = focused
        color("signal" if focused else "ink")
        path.setLineWidth_(2.0)
        path.stroke()

        path = AppKit.NSBezierPath.bezierPath()
        path.moveToPoint_((left, 17.0))
        path.lineToPoint_((left, 41.0))
        color("resistance_flash" if self._crossing else "signal" if self._pressure > 0 else "edge")
        path.setLineWidth_(3.0 if self._crossing else 2.0)
        path.stroke()

        top_font = theme.font(theme.TYPE["eyebrow"], 600)
        local_label = tr("This Mac").upper()
        peer_label = self.peer_label_text(width)
        text(local_label, 8, 2, width / 2 - 8, 15, top_font, "ink_3", align="left", kern=1.45)
        text(peer_label, width / 2, 2, width / 2 - 8, 15, top_font, "ink_3", align="right", kern=1.45)

        scale_font = theme.mono_font(theme.TYPE["eyebrow"])
        for value in range(0, self.MAXIMUM + 1, 25):
            x = resistance_x(width, value)
            major = value % 100 == 0
            tick = AppKit.NSBezierPath.bezierPath()
            tick.moveToPoint_((x, self.TRACK_TOP + self.TRACK_HEIGHT))
            tick.lineToPoint_((x, self.TRACK_TOP + self.TRACK_HEIGHT + (12 if major else 4)))
            color("ink_3" if major else "edge")
            tick.setLineWidth_(1.0)
            tick.stroke()
            if major:
                text(value, x - 22, 52, 44, 17, scale_font, "ink_2")

    @property
    def fraction(self):
        return self._value / self.MAXIMUM
