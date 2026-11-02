"""Spec section 6: a click never leaves a focus ring; Tab and the arrows show one. Offscreen only."""
import unittest

from mac_app.tests.design_offscreen import AppKit, kvm_bridge_app, offscreen_control_window, settle

widgets = kvm_bridge_app.widgets


def mouse(window, view, kind):
    bounds = view.bounds()
    point = view.convertPoint_toView_((bounds.size.width / 2, bounds.size.height / 2), None)
    return AppKit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
        kind, point, 0, 0, window.windowNumber(), None, 0, 1, 1.0)


def key(window, characters, code):
    return AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        AppKit.NSEventTypeKeyDown, (0, 0), 0, 0, window.windowNumber(), None, characters, characters, False, code)


def ring_shown(view):
    while view is not None and not isinstance(view, (widgets.Pressable, widgets._RulerView,
                                                       kvm_bridge_app._DesignSlider)):
        view = view.superview()
    if isinstance(view, widgets._RulerView):
        return widgets.keyboard_focus_visible(view)
    if isinstance(view, kvm_bridge_app._DesignSlider):
        window = view.window()
        return (window is not None and window.keyboard_navigation
                and window.firstResponder() == view)
    return view.ring is not None and not view.ring.isHidden()


def visible_pressables(view):
    if view.isHidden():
        return
    if isinstance(view, widgets.Pressable):
        yield view
    for child in view.subviews():
        yield from visible_pressables(child)


class ClickFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        kvm_bridge_app.theme.init_fonts()
        cls.control = offscreen_control_window()
        cls.window = cls.control.window
        cls.window.setFrame_display_(((100000, 100000), (1100, 900)), False)

    def show(self, page):
        self.control._select_page(page)
        settle(self.window)

    def click(self, view):
        window = self.window
        window.keyboard_navigation = True
        view.scrollRectToVisible_(view.bounds())
        settle(window)
        down = mouse(window, view, AppKit.NSEventTypeLeftMouseDown)
        up = mouse(window, view, AppKit.NSEventTypeLeftMouseUp)
        hit = window.contentView().superview().hitTest_(down.locationInWindow())
        self.assertIsNotNone(hit)
        window.sendEvent_(down)
        # An offscreen window is never key, so AppKit stops short of what it does for a real
        # click: the hit view takes first responder if it accepts it, then gets the click.
        if hit.acceptsFirstResponder():
            window.makeFirstResponder_(hit)
        hit.mouseDown_(down)
        window.sendEvent_(up)
        hit.mouseUp_(up)
        settle(window)

    def press_key(self, characters, code):
        window = self.window
        window.sendEvent_(key(window, characters, code))
        # Unkeyed, the window never runs its own Tab handling; this is what Tab calls.
        if characters == '\t':
            window.selectNextKeyView_(None)
        settle(window)

    def assert_no_ring_after_click(self, view, name):
        with self.subTest(control=name):
            self.click(view)
            self.assertFalse(self.window.keyboard_navigation)
            self.assertFalse(ring_shown(view), name)
            responder = self.window.firstResponder()
            self.assertNotIsInstance(responder, AppKit.NSTextView, f"{name}: a click opened the field editor")
            if isinstance(responder, widgets.Pressable):
                self.assertFalse(ring_shown(responder), name)

    def test_wrapping_labels_are_not_selectable(self):
        label = widgets.Label("Corner", wrap=True)
        self.assertFalse(label.view.isSelectable())
        self.assertFalse(label.view.acceptsFirstResponder())
        self.assertFalse(widgets.note("A note").view.isSelectable())

    def test_click_on_each_control_type_draws_no_ring(self):
        control = self.control
        self.show('design')
        slider = control.length_select.slider
        self.assert_no_ring_after_click(slider, 'design slider')
        self.assert_no_ring_after_click(control.glow_box.view, 'switch')
        self.assert_no_ring_after_click(control.glow_box.words.view, 'switch label')
        tiles = control.notch_style_select.tiles
        tile = tiles[0][1]
        if not tile.isHiddenOrHasHiddenAncestor():
            self.assert_no_ring_after_click(tile, 'tile')
        chips = [view for view in visible_pressables(control.glow_colour_select.rows[0].view)]
        self.assertTrue(chips)
        self.assert_no_ring_after_click(chips[0], 'swatch')
        for view in visible_pressables(control.pages['design'].documentView()):
            if view.accessibilityRole() == AppKit.NSAccessibilityRadioButtonRole:
                self.assert_no_ring_after_click(view, view.accessibilityLabel())
        self.assert_no_ring_after_click(control.sidebar.rows['design'][1], 'sidebar row')

    def test_click_on_ruler_does_not_light_its_thumb(self):
        control = self.control
        for page in control.pages:
            self.show(page)
            views = [v for v in self._rulers(control.pages[page].documentView())]
            if views:
                self.assert_no_ring_after_click(views[0], 'ruler')
                self.assertIs(self.window.firstResponder(), views[0])
                self.press_key(AppKit.NSRightArrowFunctionKey, 124)
                self.assertTrue(ring_shown(views[0]))
                return
        self.fail('no ruler found')

    def test_keyboard_focus_invalidates_the_resistance_thumb(self):
        from unittest import mock

        control = self.control
        self.show('crossing')
        strip = control.push_strip
        self.window.makeFirstResponder_(strip)
        self.window.keyboard_navigation = True
        strip.drawn_focus = False

        with mock.patch.object(strip, 'setNeedsDisplay_', wraps=strip.setNeedsDisplay_) as redraw:
            self.window.refresh_input_focus()

        redraw.assert_called_once_with(True)

    def test_resistance_thumb_redraws_on_responder_transitions(self):
        from unittest import mock

        self.show('crossing')
        strip = self.control.push_strip
        with mock.patch.object(strip, 'setNeedsDisplay_', wraps=strip.setNeedsDisplay_) as redraw:
            self.assertTrue(strip.becomeFirstResponder())
            redraw.assert_called_once_with(True)
            redraw.reset_mock()
            self.assertTrue(strip.resignFirstResponder())
            redraw.assert_called_once_with(True)

    def test_mouse_focus_does_not_highlight_the_resistance_thumb(self):
        self.show('crossing')
        strip = self.control.push_strip
        self.click(strip)
        self.assertFalse(widgets.keyboard_focus_visible(strip))

    def _rulers(self, view):
        if view.isHidden():
            return
        if isinstance(view, widgets._RulerView):
            yield view
        for child in view.subviews():
            yield from self._rulers(child)

    def test_other_keys_after_a_click_leave_the_ring_off(self):
        self.show('design')
        slider = self.control.length_select.slider
        self.click(slider)
        self.press_key('a', 0)
        self.assertFalse(self.window.keyboard_navigation)
        self.assertFalse(ring_shown(slider))

    def test_tab_shows_a_ring_and_the_next_click_clears_it(self):
        self.show('design')
        slider = self.control.length_select.slider
        self.click(slider)
        self.press_key('\t', 48)
        self.assertTrue(self.window.keyboard_navigation)
        responder = self.window.firstResponder()
        self.assertIsInstance(responder, kvm_bridge_app._DesignSlider)
        self.assertTrue(ring_shown(responder))
        self.click(slider)
        self.assertFalse(ring_shown(responder))
        self.assertFalse(ring_shown(slider))


if __name__ == '__main__':
    unittest.main()
