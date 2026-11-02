"""The Crossing ledger: saved intent, with stable native controls for each paired desktop."""

import math

import AppKit
import objc
import Quartz

import theme
import widgets
from core import peerlist, ways
from core.i18n import N_, tr, trc

# The columns' narrowest widths sum to this plus the gaps; under it the table stacks.
CHIP_HEIGHT = 26
CHIP_GAP = 6
CORNER_SIZE = 28
CORNER_GAP = 4
CORNERS_COLUMN = 70
# Wide enough for the "Notch" checkbox in the body face.
NOTCH_COLUMN = 64


class _ActionButton(AppKit.NSButton):
    def acceptsFirstResponder(self):
        return self.isEnabled()

    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), AppKit.NSCursor.pointingHandCursor()
                                  if self.isEnabled() else AppKit.NSCursor.arrowCursor())


class _CellView(AppKit.NSView):
    def isFlipped(self):
        return True


def quiet_button(title, callback):
    target = widgets.callback_target(callback)
    view = _ActionButton.alloc().initWithFrame_(((0, 0), (40, 24)))
    view.setTitle_(title)
    view.setTarget_(target)
    view.setAction_("fire:")
    view.setAccessibilityElement_(True)
    view.setAccessibilityRole_(AppKit.NSAccessibilityButtonRole)
    view.setBordered_(False)
    view.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
    view.cell().setUsesSingleLineMode_(True)
    view.cell().setWraps_(False)
    view.setAttributedTitle_(widgets.attributed(title, 12.5, 500, "ink_2"))
    view.setAccessibilityLabel_(title)
    view._ledger_target = target
    return view


def _stroke(path, name, alpha=1.0, width=1.5, dash=None):
    colour = theme.colour(name)
    (colour.colorWithAlphaComponent_(alpha) if alpha < 1 else colour).setStroke()
    path.setLineWidth_(width)
    path.setLineCapStyle_(AppKit.NSRoundLineCapStyle)
    path.setLineJoinStyle_(AppKit.NSRoundLineJoinStyle)
    if dash:
        path.setLineDash_count_phase_(dash, len(dash), 0)
    path.stroke()


def _icon(kind, name, x, y):
    """The 14 px drawn icons of the chips, on the 14 px grid of the render, at (x, y)."""
    def at(px, py):
        return (x + px, y + py)
    path = AppKit.NSBezierPath.bezierPath()
    if kind == "plus":
        path.moveToPoint_(at(7, 3)); path.lineToPoint_(at(7, 11))
        path.moveToPoint_(at(3, 7)); path.lineToPoint_(at(11, 7))
    elif kind == "wrap":
        path.moveToPoint_(at(10.5, 4.5)); path.lineToPoint_(at(5, 4.5))
        path.curveToPoint_controlPoint1_controlPoint2_(at(5, 9.5), at(1.67, 4.5), at(1.67, 9.5))
        path.lineToPoint_(at(11, 9.5))
        path.moveToPoint_(at(9, 7.5)); path.lineToPoint_(at(11, 9.5)); path.lineToPoint_(at(9, 11.5))
    else:
        path.moveToPoint_(at(2, 2.5)); path.lineToPoint_(at(2, 11.5))
        path.moveToPoint_(at(5, 7)); path.lineToPoint_(at(12, 7))
        path.moveToPoint_(at(9.5, 4.5)); path.lineToPoint_(at(12, 7)); path.lineToPoint_(at(9.5, 9.5))
    _stroke(path, name)


class _Control(widgets.Pressable):
    """A drawn button of the ledger: the pointing hand, a hover and a pressed look, Space and Return,
    and the signal focus ring Pressable already draws. A control that is not `enabled` takes none."""

    hovered = False
    pressed = False

    def isFlipped(self):
        return True

    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), AppKit.NSCursor.pointingHandCursor()
                                  if self.enabled else AppKit.NSCursor.arrowCursor())

    def updateTrackingAreas(self):
        objc.super(_Control, self).updateTrackingAreas()
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveInKeyWindow
            | AppKit.NSTrackingInVisibleRect, self, None))

    def mouseEntered_(self, _event):
        self.hovered = True
        self.setNeedsDisplay_(True)

    def mouseExited_(self, _event):
        self.hovered = False
        self.pressed = False
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        objc.super(_Control, self).mouseDown_(event)
        self.pressed = True
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        self.pressed = False
        self.setNeedsDisplay_(True)
        objc.super(_Control, self).mouseUp_(event)

    @objc.python_method
    def paint_box(self, fill, border, border_alpha, dashed, radius, bounds):
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSInsetRect(bounds, .5, .5), radius, radius)
        for name, alpha in fill or ():
            theme.colour(name).colorWithAlphaComponent_(alpha).setFill()
            path.fill()
        _stroke(path, border, border_alpha, 1, [3, 3] if dashed else None)

    @objc.python_method
    def refresh_cursor(self):
        if self.window() is not None:
            self.window().invalidateCursorRectsForView_(self)


class Chip(_Control):
    """One side of This Mac's screen as a chip: the side the machine touches (with its thirds bar), a
    side that wraps round to it, one kept while Edge is off, a free one to add, or one another
    machine holds."""

    kind = "free"
    label = ""
    bar = None
    segments = None

    @objc.python_method
    def setup(self, callback):
        self.callback = callback
        self.enabled = True
        self.segments = {}
        self.setAccessibilityRole_(AppKit.NSAccessibilityButtonRole)
        return self

    @objc.python_method
    def natural_width(self):
        label = widgets.attributed(self.label, 12).size().width
        return math.ceil(label) + 40 + (28 if self.bar is not None else 0)

    @objc.python_method
    def apply(self, kind, label, bar, enabled):
        if (kind, label, bar, enabled) != (self.kind, self.label, self.bar, self.enabled):
            self.kind, self.label, self.bar, self.enabled = kind, label, bar, enabled
            self.setNeedsDisplay_(True)
            self.refresh_cursor()

    @objc.python_method
    def bar_x(self):
        return self.bounds().size.width - 11 - 22

    @objc.python_method
    def place_segments(self):
        for index, button in enumerate(self.segments.values()):
            button.setFrame_(((self.bar_x() - 1 + index * 22 / 3, 0), (9, CHIP_HEIGHT)))

    def setFrameSize_(self, size):
        objc.super(Chip, self).setFrameSize_(size)
        if self.segments:
            self.place_segments()

    def drawRect_(self, _rect):
        bounds = self.bounds()
        width = bounds.size.width
        context = AppKit.NSGraphicsContext.currentContext()
        context.saveGraphicsState()
        held = self.kind == "held"
        if held:
            Quartz.CGContextSetAlpha(context.CGContext(), .45)
        hover = self.hovered and self.enabled and not held
        pressed = self.pressed and self.enabled and not held
        fill, border, alpha, dashed = None, "edge", 1.0, False
        text, icon = "ink_2", "ink_2"
        if self.kind in ("contact_on", "on"):
            fill, border, text, icon = [("signal_dim", 1)], "signal", "ink", "signal"
        elif self.kind == "contact_off":
            text, icon = "ink_3", "ink_3"
        elif self.kind == "kept":
            border, alpha, dashed, text = "signal", .45, True, "ink_3"
        elif self.kind == "free":
            dashed = True
        else:
            border, text, icon = "rule", "ink_3", "ink_3"
        if hover:
            fill, border, alpha, dashed, text = [("well", 1)], "ink_2", 1.0, False, "ink"
        if pressed:
            fill, border, alpha, dashed, text = [("well", 1), ("signal", .35)], "signal", 1.0, False, "ink"
        if not self.enabled:
            fill, border, alpha, dashed, text, icon = None, "rule", 1.0, False, "ink_3", "ink_3"
        self.paint_box(fill, border, alpha, dashed, 5, bounds)
        _icon({"free": "plus", "on": "wrap", "kept": "wrap"}.get(self.kind, "touch"), icon, 9, 6)
        bar_room = 28 if self.bar is not None else 0
        title = widgets.attributed(self.label, 12, ink=text)
        height = title.size().height
        title.drawInRect_(((29, (CHIP_HEIGHT - height) / 2), (max(1, width - 29 - 11 - bar_room), height)))
        if self.bar is not None:
            for index, part in enumerate(ways.PARTS):
                on = part in self.bar
                path = AppKit.NSBezierPath.bezierPath()
                path.moveToPoint_((self.bar_x() + 1 + index * 7.3, 13))
                path.lineToPoint_((self.bar_x() + 6 + index * 7.3, 13))
                _stroke(path, "ink_3" if not self.enabled else "signal" if on else "edge", 1, 3 if on else 1.5)
        context.restoreGraphicsState()


class CornerButton(_Control):
    """One corner of This Mac's screen: an L bracket in a square, on or free to add, held by another
    machine, or not offered until the machine sits beside this one."""

    corner = ""
    kind = "free"

    @objc.python_method
    def setup(self, corner, callback):
        self.corner, self.callback, self.enabled = corner, callback, True
        self.setAccessibilityRole_(AppKit.NSAccessibilityButtonRole)
        return self

    @objc.python_method
    def apply(self, kind, enabled):
        if (kind, enabled) != (self.kind, self.enabled):
            self.kind, self.enabled = kind, enabled
            self.setNeedsDisplay_(True)
            self.refresh_cursor()

    def drawRect_(self, _rect):
        bounds = self.bounds()
        context = AppKit.NSGraphicsContext.currentContext()
        context.saveGraphicsState()
        if self.kind == "unavailable":
            Quartz.CGContextSetAlpha(context.CGContext(), .4)
        hover = self.hovered and self.enabled
        pressed = self.pressed and self.enabled
        fill, border, alpha, dashed, bracket, bracket_alpha = None, "edge", 1.0, True, "ink_2", 1.0
        if self.kind == "on":
            fill, border, dashed, bracket = [("signal", 1)], "signal", False, "ground"
        elif self.kind == "held":
            border, alpha, dashed, bracket, bracket_alpha = "signal", .35, False, "signal", .5
        if hover:
            fill, border, alpha, dashed = ([("well", 1)] if self.kind != "on" else fill), "ink_2", 1.0, False
        if pressed:
            fill, border, alpha, dashed = [("well", 1), ("signal", .35)], "signal", 1.0, False
        if not self.enabled:
            fill, border, alpha, dashed, bracket, bracket_alpha = None, "rule", 1.0, False, "ink_3", 1.0
        self.paint_box(fill, border, alpha, dashed, 5, bounds)
        vertical, horizontal = self.corner.split("_")
        # The 7 pt bracket sits centred in the square, its corner towards the square's corner.
        inset_x, inset_y = (bounds.size.width - 7) / 2, (bounds.size.height - 7) / 2
        sx = inset_x if horizontal == "left" else bounds.size.width - inset_x
        sy = inset_y if vertical == "top" else bounds.size.height - inset_y
        dx, dy = (1 if horizontal == "left" else -1), (1 if vertical == "top" else -1)
        path = AppKit.NSBezierPath.bezierPath()
        path.moveToPoint_((sx, sy + dy * 7))
        path.lineToPoint_((sx, sy))
        path.lineToPoint_((sx + dx * 7, sy))
        _stroke(path, bracket, bracket_alpha, 1.8)
        context.restoreGraphicsState()


class Row(AppKit.NSView):
    def isFlipped(self):
        return True

    @objc.python_method
    def setup(self, ledger, peer):
        self.ledger, self.control, self.peer = ledger, ledger.control, peer
        self.renaming = False
        self.setAccessibilityElement_(True)
        self.setAccessibilityRole_(AppKit.NSAccessibilityRowRole)
        self.cells = {}
        for key in ("machine", "edge", "corner", "notch", "keys"):
            cell = _CellView.alloc().init()
            if hasattr(cell, "setClipsToBounds_"):
                cell.setClipsToBounds_(False)
            self.addSubview_(cell)
            self.cells[key] = cell
        self.name = widgets.Label("", theme.TYPE["body"], 600)
        self.status = widgets.Label("", theme.TYPE["small"], ink="ink_3")
        self.rename = quiet_button(tr("Rename"), self.begin_rename)
        self.leave = quiet_button(tr("Leave out"), lambda: self.control._set_atlas_left_out(
            self.peer, not (self.entry.get("left_out") is True or self.entry.get("in_use", True) is False)))
        self.name_field = AppKit.NSTextField.alloc().init()
        self.name_field.setDelegate_(self.control)
        self.name_field.setFont_(theme.font(theme.TYPE["body"]))
        self.name_field.setHidden_(True)
        self.rename_hint = widgets.Label(tr("Return saves, Escape cancels"), theme.TYPE["small"], ink="ink_3", wrap=True)
        self.scope = widgets.Label(tr("Only on this Mac"), theme.TYPE["small"], ink="ink_3")
        for item in (self.name.view, self.status.view, self.rename, self.leave, self.name_field,
                     self.rename_hint.view, self.scope.view):
            self._add("machine", item)
        self.chips, self.chip_order = {}, list(ways.EDGES)
        for side in ways.EDGES:
            chip = self._chip(side)
            self.chips[side] = chip
            self._add("edge", chip)
        self.edge_note = widgets.Label(tr("Place it on the map"), theme.TYPE["small"], ink="ink_3")
        self._add("edge", self.edge_note.view)
        self.corner_buttons = {}
        for corner in ways.CORNERS:
            button = widgets.box(None, None, 5, cls=CornerButton).setup(
                corner, lambda corner=corner: self.control._ledger_corner(self.peer, corner))
            button._ledger_target = widgets._Target.alloc().initWithCallback_(lambda _sender, b=button: b.press())
            self.corner_buttons[corner] = button
            self._add("corner", button)
        self.notch = self._check(lambda on: self.control._ledger_notch(self.peer, on))
        self.no_notch = widgets.Label(tr("No notch"), theme.TYPE["body"], ink="ink_3", wrap=True)
        self.cycle = self._check(lambda on: self.control._set_shortcut_cycle(self.peer, on))
        self.notch_return = self._check(lambda on: self.control._ledger_notch_return(self.peer, on))
        self.notch_return.setTitle_(tr("Return through the notch"))
        self._add("notch", self.notch)
        self._add("notch", self.no_notch.view)
        self._add("notch", self.notch_return)
        self._add("keys", self.cycle)
        self.jump_caption = widgets.Label(tr("Jump"), theme.TYPE["small"], ink="ink_3")
        self.jump = widgets.JumpKeyRecorder(on_change=lambda value: self.control._save_jump_key(value, self.peer),
                                          on_arm=self._arm_jump, compact=True,
                                          hint=tr(widgets.JumpKeyRecorder.HINT),
                                          recording_title=tr("Press a key combination…"),
                                          recording_hint=tr("Click again to cancel"))
        self.clear = quiet_button(tr("Clear"), lambda: self.control._save_jump_key("", self.peer))
        self.leave_state = None
        for item in (self.jump_caption.view, self.jump.view, self.clear):
            self._add("keys", item)
        return self

    @objc.python_method
    def _chip(self, side):
        chip = widgets.box(None, None, 5, cls=Chip).setup(lambda side=side: self.control._ledger_side(self.peer, side))
        chip._ledger_target = widgets._Target.alloc().initWithCallback_(lambda _sender: chip.press())
        for part in ways.PARTS:
            segment = quiet_button("", lambda part=part: self.control._ledger_edge(self.peer, part))
            segment.setTransparent_(True)
            chip.addSubview_(segment)
            chip.segments[part] = segment
        return chip

    @objc.python_method
    def _add(self, cell, view):
        view.setTranslatesAutoresizingMaskIntoConstraints_(True)
        self.cells[cell].addSubview_(view)

    @objc.python_method
    def _check(self, callback):
        target = widgets._Target.alloc().initWithCallback_(lambda sender: callback(sender.state() == AppKit.NSControlStateValueOn))
        view = _ActionButton.alloc().initWithFrame_(((0, 0), (100, 28)))
        view.setButtonType_(AppKit.NSSwitchButton)
        view.setTarget_(target)
        view.setAction_("fire:")
        view.setTitle_("")
        view.setFont_(theme.font(theme.TYPE["body"]))
        view.setAccessibilityElement_(True)
        view.setAccessibilityRole_(AppKit.NSAccessibilityCheckBoxRole)
        view._ledger_target = target
        return view

    @objc.python_method
    def _arm_jump(self, armed):
        if armed:
            for row in self.ledger.rows.values():
                if row is not self:
                    row.jump.cancel()
        self.control._jump_arming(any(row.jump.monitor is not None for row in self.ledger.rows.values()))
        self.jump.hint.view.setHidden_(not armed)
        self.ledger.reflow()

    @objc.python_method
    def begin_rename(self):
        if self.ledger.remote or self.ledger.read_only:
            return
        if self.control.page != "crossing":
            self.control._select_page("crossing")
        self.renaming = True
        self.name_field.setStringValue_(self.entry.get("label") or self.entry.get("name") or "")
        self.ledger.reflow()
        self.control.window.makeFirstResponder_(self.name_field)
        self.name_field.selectText_(None)

    @objc.python_method
    def finish_rename(self, save):
        self.renaming = False
        if save:
            self.control._rename_peer(self.entry["token"], self.name_field.stringValue())
        self.ledger.refresh(self.control.settings_store.current(), self.control.chosen_peer)
        self.control.window.makeFirstResponder_(self.control.atlas_canvas)

    @objc.python_method
    def _reach(self, settings, out):
        """What each side and corner of This Mac is to this machine. A left-out machine is answered as
        if put back, so its kept ways are drawn as they would cross; `out` makes them inert."""
        return self.control._reach(self.peer, settings)

    @objc.python_method
    def refresh(self, settings, entry, selected):
        self.settings, self.entry, self.selected = settings, entry, selected
        self.held = ways.ways(settings, self.peer)
        tiles, own = ways.derive_layout(settings, gap=0), settings.get("machine_id")
        contact = ways._rect_contact(tiles[own], tiles[self.peer]) if own in tiles and self.peer in tiles else None
        self.side = contact[0] if contact and contact[0] != "corner" else ""
        name = self.control._shown(peerlist.label_for(settings["peers"], peer_id=self.peer) or peerlist.UNNAMED)
        linked = self.control._live(self.peer)
        out = entry.get("left_out") is True or entry.get("in_use", True) is False
        inert = out or self.ledger.read_only
        self.dimmed = out or not linked
        platform = tr({"macos": N_("macOS"), "windows": N_("Windows"), "linux": N_("Linux")}.get(entry.get("platform"), N_("Machine")))
        caps = self.control._design_peer_caps()
        status = tr("Left out") if out else tr("Linked") if linked else tr("Offline, ways kept")
        self.status_text = tr("{platform} · {status}").format(platform=platform, status=status)
        self.old_peer = self.peer in caps and "layout" not in caps[self.peer]
        if self.old_peer:
            self.status_text = tr("Beamer {version} · only sees which side").format(version=entry.get("app") or "1.5.2")
        self.name.set(name, ink="ink_2" if self.dimmed else "ink")
        self.name.view.setToolTip_(name)
        self.name.view.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        self.status.set(self.status_text)
        leave_title = tr("Put back") if out else tr("Leave out")
        if self.leave_state != out:
            self.leave.setAttributedTitle_(widgets.attributed(leave_title, 12.5, 500, "ink_2"))
            self.leave_state = out
        self.leave.setAccessibilityLabel_(leave_title)
        self.leave.setToolTip_(tr("Left out machines stay paired and linked. The shortcut, menu and clipboard still reach them.") if out else tr("Leave this machine out of pointer crossings"))
        self.reach = self._reach(settings, out)
        self._refresh_chips(name, out)
        self._refresh_corners(name, out)
        has_notch = self.ledger.has_notch
        self.notch.setTitle_(tr("Notch") if has_notch else "")
        self.no_notch.view.setHidden_(has_notch or self.ledger.remote)
        self.notch.setAccessibilityLabel_(tr("Notch, no notch") if not has_notch else
                                         tr("Notch, on") if "notch" in self.held["methods"] else tr("Notch, off"))
        self.notch.setState_(int("notch" in self.held["methods"]))
        self.notch.setEnabled_(has_notch and not inert)
        self.notch.setToolTip_(tr("Push into the notch to reach {name}").format(name=name))
        notch_return = entry.get("notch_return") is True
        self.notch_return.setHidden_(not has_notch or "notch" not in self.held["methods"])
        self.notch_return.setState_(int(notch_return))
        self.notch_return.setEnabled_(not inert)
        self.notch_return.setAccessibilityLabel_(tr("Return through the notch, on") if notch_return else tr("Return through the notch, off"))
        self.notch_return.setToolTip_(tr("Coming back from {name}, arrive just below the notch").format(name=name))
        self.cycle.setTitle_(widgets.key_title(self.control.key_recorder.value))
        self.cycle.setState_(int(entry.get("shortcut_cycle") is not False))
        self.cycle.setToolTip_(tr("In the shortcut's cycle"))
        if self.jump.monitor is None:
            self.jump.set_value(entry.get("jump_key", ""))
        self.jump.hint.view.setHidden_(self.jump.monitor is None)
        # Translators: {key} is the recorded key combination; {name} is the machine name.
        self.jump.view.setToolTip_(tr("Press {key} to send input to {name}; press again to bring it home.").format(key=self.jump._title(), name=name))
        self.clear.setHidden_(not bool(self.jump.value))
        self.rename.setAccessibilityLabel_(tr("Rename {name}").format(name=name))
        self.leave.setAccessibilityLabel_(tr("Put back {name}").format(name=name) if out else tr("Leave out {name}").format(name=name))
        self.cells["keys"].setHidden_(self.ledger.remote)
        self.cells["notch"].setHidden_(self.ledger.remote and not has_notch)
        self.rename.setHidden_(self.ledger.remote)
        self.leave.setHidden_(self.ledger.remote)
        self.rename.setEnabled_(not self.ledger.read_only)
        self.leave.setEnabled_(not self.ledger.read_only)
        self.cycle.setEnabled_(not self.ledger.read_only)
        if self.ledger.remote:
            self.renaming = False
            self.jump.cancel()
        self.setAccessibilityLabel_(name)
        self.setNeedsDisplay_(True)

    @objc.python_method
    def contact(self):
        """The chip of the side this machine touches, None when it touches none."""
        return next((chip for chip in self.chips.values() if chip.kind in ("contact_on", "contact_off")), None)

    @objc.python_method
    def _holder(self, state):
        return self.control._shown(peerlist.label_for(self.settings["peers"], peer_id=state.get("by")) or peerlist.UNNAMED)

    @objc.python_method
    def _refresh_chips(self, name, out):
        sides = self.reach["sides"]
        contact = [side for side in ways.EDGES if sides[side]["state"] == "contact"]
        self.chip_order = contact + [side for side in ways.EDGES if side not in contact]
        picked = ways.picked_thirds(self.held["methods"], self.held["parts"])
        for side, chip in self.chips.items():
            state = sides[side]
            kind, bar = state["state"], None
            side_title = tr({"left": N_("Left"), "right": N_("Right"),
                             "top": N_("Top"), "bottom": N_("Bottom")}[side])
            title = side_title
            lead = tr("{side} edge").format(side=side_title)
            on_word, off_word = trc("switch state", "on"), trc("switch state", "off")
            if kind == "contact":
                kind, bar = "contact_on" if picked else "contact_off", picked
                tip = tr("{edge}: touches {name}; click to turn it {state}").format(
                    edge=lead, name=name, state=off_word if picked else on_word)
                label = tr("{edge}, touches {name}, {state}").format(
                    edge=lead, name=name, state=on_word if picked else off_word)
            elif kind == "on":
                title = tr("{side}, wraps").format(side=side_title)
                tip = tr("{edge} wraps round to {name}; click to remove").format(edge=lead, name=name)
                label = tr("{edge}, wraps round, {state}").format(edge=lead, state=on_word)
            elif kind == "kept":
                title = tr("{side}, wraps").format(side=side_title)
                tip = (tr("{edge} wraps round to {name} once Edge is on; click to turn Edge on").format(edge=lead, name=name) if not picked else
                       tr("{edge}: none of it is free just now; click to remove").format(edge=lead, name=name))
                label = tr("{edge}, wraps round, kept").format(edge=lead)
            elif kind == "free":
                tip = tr("{edge}: click to wrap round to {name}").format(edge=lead, name=name)
                label = tr("{edge}, wraps round, {state}").format(edge=lead, state=off_word)
            elif kind == "held":
                other = self._holder(state)
                title = tr("{side}, {name}").format(side=side_title, name=other)
                tip = tr("{edge}: {name}'s").format(edge=lead, name=other)
                label = tr("{edge}, held by {name}").format(edge=lead, name=other)
            else:
                tip = label = ""
            chip.setToolTip_(tr("Left out: Put back to change its ways") if out and kind != "unavailable" else tip)
            chip.setAccessibilityLabel_(label)
            chip.setAccessibilityValue_(on_word if kind in ("contact_on", "on") else off_word)
            chip.apply(kind, title, bar, kind not in ("held", "unavailable") and not out and not self.ledger.read_only)
            # A left-out row shows only the ways it keeps.
            chip.setHidden_(kind == "unavailable" or out and kind in ("free", "held"))
            for part, segment in chip.segments.items():
                segment.setHidden_(bar is None)
                if bar is not None:
                    third = tr({"Top": N_("Top"), "Middle": N_("Middle"), "Bottom": N_("Bottom"),
                                "Left": N_("Left"), "Right": N_("Right")}[ways.part_names(side)[part]])
                    segment.setEnabled_(not out and not self.ledger.read_only)
                    segment.setAccessibilityLabel_(tr("{edge}, {third} third, {state}").format(
                        edge=lead, third=third.lower(), state=on_word if part in picked else off_word))
                    segment.setAccessibilityValue_(on_word if part in picked else off_word)
                    segment.setToolTip_(tr("Left out: Put back to change its ways") if out else
                                        tr("{third} third: click to leave it out").format(third=third) if part in picked else
                                        tr("{third} third: click to turn it on").format(third=third))
            chip.place_segments()
        self.edge_note.view.setHidden_(any(not chip.isHidden() for chip in self.chips.values()))

    @objc.python_method
    def _refresh_corners(self, name, out):
        for corner, button in self.corner_buttons.items():
            state = self.reach["corners"][corner]
            kind = state["state"]
            if kind == "unavailable" and corner in self.held["corners"]:
                # A corner left on after its machine moved away must still be removable.
                kind = "on"
            title = tr({"top_left": N_("Top left"), "top_right": N_("Top right"),
                        "bottom_left": N_("Bottom left"), "bottom_right": N_("Bottom right")}[corner])
            holder = self._holder(state) if kind == "held" else ""
            tip = {"on": tr("{corner} corner: click to turn it off").format(corner=title),
                   "free": tr("{corner} corner: click to turn it on").format(corner=title),
                   "held": tr("{name}'s corner").format(name=holder),
                   "unavailable": tr("Place {name} beside this Mac first").format(name=name)}[kind]
            button.setToolTip_(tr("Left out: Put back to change its ways") if out else tip)
            on_word, off_word = trc("switch state", "on"), trc("switch state", "off")
            state_word = {"on": on_word, "free": off_word, "unavailable": tr("unavailable"),
                          "held": tr("held by {name}").format(name=holder)}[kind]
            button.setAccessibilityLabel_(tr("{corner} corner, {state}").format(corner=title, state=state_word))
            button.setAccessibilityValue_(on_word if kind == "on" else off_word)
            button.apply(kind, kind in ("on", "free") and not out and not self.ledger.read_only)

    def mouseDown_(self, event):
        self.control._machine_picked(self.peer)

    def accessibilityRowHeaderUIElements(self):
        return [self.name.view]

    @objc.python_method
    def _flow(self, width):
        """Lay the visible chips out in wrapping lines; the cell is as tall as the lines."""
        x = y = 0
        placed = False
        for side in self.chip_order:
            chip = self.chips[side]
            if chip.isHidden():
                continue
            chip_width = min(chip.natural_width(), max(1, width))
            if x and x + chip_width > width:
                x, y = 0, y + CHIP_HEIGHT + CHIP_GAP
            chip.setFrame_(((x, y), (chip_width, CHIP_HEIGHT)))
            x += chip_width + CHIP_GAP
            placed = True
        return y + CHIP_HEIGHT if placed else 22

    @objc.python_method
    def arrange(self, width, narrow):
        rename_space = 74 if self.renaming else 0
        positions = self.ledger.columns(width)
        if narrow:
            inner = width - 24
            keys_x = 12 + CORNERS_COLUMN + 16 + NOTCH_COLUMN + 16
            positions = [(0, width), (12, inner), (12, CORNERS_COLUMN), (12 + CORNERS_COLUMN + 16, NOTCH_COLUMN),
                         (keys_x, max(128, width - 12 - keys_x))]
            if self.ledger.remote and not self.ledger.has_notch:
                positions[3] = (width, 0)
        positions[0] = (12, positions[0][1] - 12)
        for key, (x, w) in zip(self.cells, positions):
            self.cells[key].setFrame_(((x, 12), (w, 72)))
            self.cells[key].setAlphaValue_(.5 if self.dimmed else 1)
        cell_heights = {"machine": 72 + rename_space, "notch": 28,
                        "corner": 2 * CORNER_SIZE + CORNER_GAP}
        if self.ledger.remote:
            cell_heights["machine"] = 48
            if not self.ledger.has_notch:
                cell_heights["notch"] = 0
        cell_heights["edge"] = self._flow(self.cells["edge"].bounds().size.width)
        self.edge_note.view.setFrame_(((0, 2), (self.cells["edge"].bounds().size.width, 20)))
        for index, corner in enumerate(ways.CORNERS):
            self.corner_buttons[corner].setFrame_((
                ((index % 2) * (CORNER_SIZE + CORNER_GAP), (index // 2) * (CORNER_SIZE + CORNER_GAP)),
                (CORNER_SIZE, CORNER_SIZE)))
        w = self.cells["keys"].bounds().size.width
        self.cycle.setFrame_(((0, 0), (w, 28)))
        self.jump_caption.view.setFrame_(((0, 34), (35, 24)))
        jump_w, jump_h = self.jump.arrange_compact(max(1, w-36))
        self.jump.view.setFrame_(((36, 32), (jump_w, jump_h)))
        clear_x, clear_y = 36 + jump_w + 4, 32
        clear_w = 38
        if clear_x + clear_w > w:
            clear_x, clear_y = 36, 32 + jump_h + 2
        self.clear.setFrame_(((clear_x, clear_y), (clear_w, 24)))
        self.clear.setHidden_(not self.jump.value or self.jump.monitor is not None)
        cell_heights["keys"] = max(60, 32 + jump_h,
                                   clear_y + 24 if not self.clear.isHidden() else 0)
        if self.ledger.remote:
            cell_heights["keys"] = 0
        # Return through the notch runs on under the notch, corners and keys, from the notch's column to
        # the row's end: no column is wide enough for it.
        return_y = max(cell_heights["corner"], cell_heights["notch"], cell_heights["keys"]) + 8
        if not self.notch_return.isHidden():
            cell_heights["notch"] = return_y + 28
        if narrow:
            edge_y = 12 + cell_heights["machine"] + 12
            bottom_y = edge_y + cell_heights["edge"] + 12
            bottom_h = max(cell_heights["corner"], cell_heights["notch"], cell_heights["keys"])
            places = [(12, cell_heights["machine"]), (edge_y, cell_heights["edge"]), (bottom_y, bottom_h),
                      (bottom_y, bottom_h), (bottom_y, bottom_h)]
            height = bottom_y + bottom_h + 12
        else:
            height = max(76, max(cell_heights.values()) + 24)
            places = [(12, height-24)] * 5
        for key, (y, h) in zip(self.cells, places):
            cell = self.cells[key]
            if self.ledger.remote and (key == "keys" or key == "notch" and not self.ledger.has_notch):
                h = 0
            cell.setFrame_(((cell.frame().origin.x, y), (cell.bounds().size.width, h)))
        machine = self.cells["machine"].bounds().size.width
        self.name.view.setFrame_(((0, 0), (machine, 22)))
        self.status.view.setFrame_(((12, 25), (machine-12, 19)))
        # Sized to the title, so a longer translation ("Rinomina") is not cut.
        rename_w = max(54, math.ceil(self.rename.fittingSize().width))
        self.rename.setFrame_(((0, 48), (rename_w, 24)))
        self.leave.setFrame_(((rename_w + 2, 48), (min(74, machine - rename_w - 2), 24)))
        self.name_field.setFrame_(((0, 0), (machine, 24)))
        self.name_field.setHidden_(not self.renaming)
        self.name.view.setHidden_(self.renaming)
        for label, y in ((self.rename_hint, 74), (self.scope, 110)):
            label.view.setFrame_(((0, y), (machine, 32)))
            label.view.setHidden_(not self.renaming or label is self.scope and self.control.controller.cfg.same_on_both)
        w = self.cells["notch"].bounds().size.width
        self.notch.setFrame_(((0, 0), (w if self.ledger.has_notch else 22, 28)))
        # Wraps inside its column: the keys column sits beside it when rows stack, so it cannot grow sideways.
        self.no_notch.view.setFrame_(((0, 32), (w, 40)))
        self.notch_return.setFrame_(((0, return_y), (max(w, width - 12 - self.cells["notch"].frame().origin.x), 28)))
        return height

    def drawRect_(self, rect):
        width = self.bounds().size.width
        theme.colour("rule").setFill()
        AppKit.NSRectFill(((0, 0), (width, 1)))
        if self.selected:
            theme.colour("signal").setFill()
            AppKit.NSRectFill(((0, 12), (3, self.bounds().size.height-24)))
        cell = self.cells["machine"].frame()
        theme.colour("amber" if self.old_peer else "signal" if self.control._live(self.peer) else "ink_3").setFill()
        AppKit.NSBezierPath.bezierPathWithOvalInRect_(((cell.origin.x+1, cell.origin.y+32), (6, 6))).fill()


class Ledger(AppKit.NSView):
    def isFlipped(self):
        return True

    @objc.python_method
    def setup(self, control):
        self.control, self.rows = control, {}
        self.remote = self.read_only = False
        self.has_notch = control.has_notch
        self.order = []
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.height = self.heightAnchor().constraintEqualToConstant_(0)
        self.height.setActive_(True)
        self.setAccessibilityElement_(True)
        self.setAccessibilityRole_(AppKit.NSAccessibilityTableRole)
        self.setAccessibilityLabel_(tr("Machines and crossing ways"))
        self.headers = []
        for title in (tr("Machine"), tr("Edges"), tr("Corners"), tr("Notch"), tr("Keys")):
            label = widgets.Label(title, theme.TYPE["eyebrow"], 700, "ink_3",
                                  tracking=theme.TRACKING["eyebrow"], upper=True)
            label.view.setTranslatesAutoresizingMaskIntoConstraints_(True)
            label.view.setAccessibilityRole_(AppKit.NSAccessibilityColumnRole)
            self.addSubview_(label.view)
            self.headers.append(label)
        return self

    @objc.python_method
    def narrow_below(self):
        """The narrowest table every column's least width fits: under it the rows stack."""
        if self.remote and not self.has_notch:
            return 132 + 220 + CORNERS_COLUMN + 2 * 16
        return 132 + 220 + CORNERS_COLUMN + NOTCH_COLUMN + (3 * 16 if self.remote else 132 + 4 * 16)

    @objc.python_method
    def columns(self, width):
        if self.remote:
            gaps = 48 if self.has_notch else 32
            notch = NOTCH_COLUMN if self.has_notch else 0
            remaining = max(352, width - gaps - CORNERS_COLUMN - notch)
            machine = max(132, remaining - max(220, remaining * 2 / 3.3))
            edge = remaining - machine
            return [(0, machine), (machine + 16, edge),
                    (machine + edge + 32, CORNERS_COLUMN),
                    (machine + edge + CORNERS_COLUMN + 48, NOTCH_COLUMN) if self.has_notch else (width, 0),
                    (width, 0)]
        weights = (1.3, 2, 0, 0, 1)
        widths = [132, 220, CORNERS_COLUMN, NOTCH_COLUMN, 132]
        remaining = max(480, width - 64 - CORNERS_COLUMN - NOTCH_COLUMN)
        flexible = {0, 1, 4}
        while flexible:
            unit = remaining / sum(weights[index] for index in flexible)
            fixed = {index for index in flexible if widths[index] > unit * weights[index]}
            if not fixed:
                for index in flexible:
                    widths[index] = unit * weights[index]
                break
            remaining -= sum(widths[index] for index in fixed)
            flexible -= fixed
        result, x = [], 0
        for w in widths:
            result.append((x, w))
            x += w+16
        return result

    @objc.python_method
    def refresh(self, settings, selected, *, remote=False, read_only=False, has_notch=None):
        self.remote, self.read_only = bool(remote), bool(read_only)
        self.has_notch = self.control.has_notch if has_notch is None else bool(has_notch)
        entries = peerlist.desktops(settings["peers"])
        tiles = ways.derive_layout(settings, gap=0)
        entries.sort(key=lambda entry: (entry["id"] not in tiles,
                     tiles.get(entry["id"], {}).get("x", 0), tiles.get(entry["id"], {}).get("y", 0)))
        ids = [entry["id"] for entry in entries]
        for peer in list(self.rows):
            if peer not in ids:
                self.rows[peer].jump.cancel()
                self.rows.pop(peer).removeFromSuperview()
        for entry in entries:
            peer = entry["id"]
            if peer not in self.rows:
                row = Row.alloc().init().setup(self, peer)
                self.rows[peer] = row
                self.addSubview_(row)
            self.rows[peer].refresh(settings, entry, peer == selected)
        self.order = ids
        self.reflow()

    @objc.python_method
    def reflow(self):
        width = self.bounds().size.width
        narrow = width < self.narrow_below()
        y = 0 if narrow or not self.rows else 34
        for index, (label, (x, w)) in enumerate(zip(self.headers, self.columns(width))):
            label.view.setHidden_(narrow or not self.rows or self.remote and
                                  (index == 4 or index == 3 and not self.has_notch))
            label.view.setFrame_(((x, 0), (w, 24)))
        for peer in self.order:
            row = self.rows[peer]
            h = row.arrange(width, narrow)
            row.setFrame_(((0, y), (width, h)))
            y += h
        self.height.setConstant_(y)
        self.setNeedsDisplay_(True)

    def setFrameSize_(self, size):
        objc.super(Ledger, self).setFrameSize_(size)
        if hasattr(self, "rows"):
            self.reflow()

    def accessibilityRows(self):
        return [self.rows[peer] for peer in self.order]

    def accessibilityColumns(self):
        count = (4 if self.has_notch else 3) if self.remote else 5
        return [header.view for header in self.headers[:count]]

    def accessibilityColumnHeaderUIElements(self):
        return self.accessibilityColumns()
