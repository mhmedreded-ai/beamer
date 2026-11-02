"""Text, control grouping and preview geometry in unordered settings windows."""
import copy
import unittest
from types import SimpleNamespace

from mac_app.tests.design_offscreen import AppKit, kvm_bridge_app, offscreen_control_window, settle
from mac_app.tests.test_settings_layout_acceptance import frame_in, walk

widgets = kvm_bridge_app.widgets
theme = kvm_bridge_app.theme


def luminance(colour):
    colour = colour.colorUsingColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())
    values = [colour.redComponent(), colour.greenComponent(), colour.blueComponent()]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
    return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))


class SettingsPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        theme.init_fonts()
        cls.control = offscreen_control_window()

    def _add_machine_row(self):
        state = SimpleNamespace(key="linked", word="Connected", led="signal", blink=False,
                                tone="signal", detail="")
        row = SimpleNamespace(label="Windows PC", state=state, token="synthetic", platform="Windows",
                              address="192.0.2.20", in_use=True, drives=True, driven=True,
                              phone=False, left_out=False)
        self.control.panel._build_rows([row])

    def test_all_segment_labels_fit_one_line_in_both_appearances(self):
        control = self.control
        for dark in (False, True):
            theme.set_dark(dark)
            for width in (320, 640, 900, 1440, 1920):
                for page in control.pages:
                    control._select_page(page)
                    control.window.setFrame_display_(((100000, 100000), (width, 900)), False)
                    settle(control.window)
                    for view in walk(control.pages[page]):
                        selector = getattr(view, 'segmented_owner', None)
                        if selector is None:
                            continue
                        for value, _cell, words in selector.cells:
                            selector.value = value
                            settle(control.window)
                            with self.subTest(page=page, width=width, label=words.text, dark=dark):
                                text = words.view.attributedStringValue()
                                self.assertGreaterEqual(words.view.bounds().size.width + .5, text.size().width)
                                if not selector.enabled:
                                    # A dimmed picker (Corner while Corner is off) drops the ink fill
                                    # on purpose; the contrast bar is for live choices only.
                                    continue
                                foreground = text.attribute_atIndex_effectiveRange_(AppKit.NSForegroundColorAttributeName, 0, None)[0]
                                a, b = sorted((luminance(foreground), luminance(theme.colour('ink'))))
                                self.assertGreaterEqual((b + .05) / (a + .05), 4.5)

    def test_field_controls_start_immediately_after_the_label_track(self):
        control = self.control
        for page in control.pages:
            control._select_page(page)
            control.window.setFrame_display_(((100000, 100000), (1440, 900)), False)
            settle(control.window)
            for view in walk(control.pages[page]):
                field = getattr(view, 'field_owner', None)
                if field is not None and field.caption and not field.stacked:
                    with self.subTest(page=page, caption=field.caption):
                        self.assertAlmostEqual(frame_in(field.control, view).origin.x, 196, delta=1)

    def test_preview_hint_has_no_disclosure_triangle(self):
        self.assertEqual(self.control.style_preview_hint.text, 'Hover to preview')

    def test_time_between_taps_value_fits_in_both_appearances_at_every_width(self):
        control = self.control
        control._select_page('crossing')
        figure = control.double_tap_numeral
        original = figure.text
        try:
            for dark in (False, True):
                theme.set_dark(dark)
                for width in (320, 640, 900, 1440, 1920):
                    control.window.setFrame_display_(((100000, 100000), (width, 900)), False)
                    for value in ('300', '2000'):
                        figure.set(value)
                        settle(control.window)
                        with self.subTest(dark=dark, width=width, value=value):
                            self.assertGreaterEqual(figure.view.bounds().size.width + .5,
                                                    figure.view.attributedStringValue().size().width)
                            self.assertTrue(AppKit.NSContainsRect(control.double_tap_head.bounds(),
                                                                 frame_in(figure.view, control.double_tap_head)))
        finally:
            figure.set(original)

    def test_overview_machine_controls_form_one_group_at_the_right_edge(self):
        control = self.control
        self._add_machine_row()
        control._select_page('overview')
        control.window.setFrame_display_(((100000, 100000), (900, 900)), False)
        settle(control.window)
        row = control.panel.list.arrangedSubviews()[0]
        controls = [v for v in walk(row) if isinstance(v, widgets.Pressable) and v.accessibilityLabel()]
        in_use = next(v for v in controls if v.accessibilityLabel() == 'In use')
        directions = next(v for v in controls if str(v.accessibilityLabel()).startswith('Show directions'))
        left_out = next(v for v in controls if str(v.accessibilityLabel()).startswith('Leave out '))
        remove = next(v for v in controls if str(v.accessibilityLabel()).startswith('Remove '))
        a, b, c, d = (frame_in(v, row) for v in (in_use, left_out, directions, remove))
        self.assertAlmostEqual(b.origin.x - a.origin.x - a.size.width, 12, delta=1)
        self.assertAlmostEqual(c.origin.x - b.origin.x - b.size.width, 12, delta=1)
        self.assertAlmostEqual(d.origin.x - c.origin.x - c.size.width, 12, delta=1)
        self.assertAlmostEqual(d.origin.x + d.size.width, row.bounds().size.width - 12, delta=1)

    def test_machine_names_fit_both_crossing_drawings_at_minimum_width(self):
        control = self.control
        self._add_machine_row()
        control._select_page('crossing')
        for width in (320, 640, 900, 1440, 1920):
            control.window.setFrame_display_(((100000, 100000), (width, 900)), False)
            settle(control.window)
            control.push_strip.set_other_name("Alex’s Windows gaming machine")
            canvas = control.atlas_canvas
            own = control.settings_store.current()["machine_id"]
            canvas.show_layout(
                {own: {"x": 0, "y": 0, "w": 160, "h": 100},
                 "synthetic": {"x": 160, "y": 0, "w": 160, "h": 100}},
                {}, {own: "This Mac", "synthetic": "Alex’s Windows gaming machine"}, own, "synthetic")
            title = canvas.label_for_tile["synthetic"]
            with self.subTest(width=width):
                self.assertEqual(title.string(), "Alex’s Windows gaming machine")
                self.assertGreater(title.frame().size.width, 0)
            label = control.push_strip.peer_label_text(width)
            attributes = {AppKit.NSFontAttributeName: theme.font(theme.TYPE['eyebrow'], 600),
                          AppKit.NSKernAttributeName: 1.45}
            ink = AppKit.NSString.stringWithString_(label).sizeWithAttributes_(attributes).width
            with self.subTest(width=width, label=label):
                self.assertLessEqual(ink, width / 2 - 16)
        self.assertGreaterEqual(control.push_strip.bounds().size.height, 72)

    def test_narrow_resistance_push_always_moves_towards_the_other_machine(self):
        control = self.control
        control._select_page('crossing')
        control.window.setFrame_display_(((100000, 100000), (320, 900)), False)
        settle(control.window)
        strip = control.push_strip
        configured = strip.value
        strip.set_pressure(500)
        from core.resistance_strip import resistance_x
        self.assertEqual(resistance_x(strip.bounds().size.width, strip._pressure),
                         resistance_x(strip.bounds().size.width, 500))
        self.assertEqual(strip.value, configured)

    def test_decay_refresh_keeps_the_active_edge_in_its_accessible_description(self):
        strip = self.control.push_strip
        strip.set_pressure(60, "top")
        strip.set_pressure(30)
        self.assertEqual(strip._active_edge, "top")
        self.assertIn("top edge", strip._accessibility_description)

    def test_narrow_machine_card_preserves_its_name_and_address(self):
        control = self.control
        control._select_page('overview')
        control.window.setFrame_display_(((100000, 100000), (320, 900)), False)
        settle(control.window)
        row = control.panel.list.arrangedSubviews()[0]
        names = [v for v in walk(row) if isinstance(v, AppKit.NSTextField) and
                 v.stringValue() in ('Windows PC', 'Windows  192.0.2.20 · port 24820')]
        self.assertEqual(len(names), 2)
        for label in names:
            text = label.attributedStringValue()
            width = label.cell().drawingRectForBounds_(label.bounds()).size.width
            height = text.boundingRectWithSize_options_((width, 10000), AppKit.NSStringDrawingUsesLineFragmentOrigin).size.height
            self.assertTrue(label.cell().wraps())
            self.assertGreaterEqual(label.bounds().size.height + 1, height)

    def test_crossing_ledger_elides_machine_names_with_complete_tooltips(self):
        control = self.control
        control._select_page('crossing')
        previous = control.settings_store._cache
        settings = copy.deepcopy(previous)
        peer = '0123456789abcdef0123456789abcdef'
        text = 'Alex’s Windows gaming machine with two monitors'
        settings['peers'] = [{'id': peer, 'name': text, 'token': 'synthetic-render-only',
                              'platform': 'windows', 'port': 24820, 'in_use': True,
                              'send': True, 'recv': True, 'side': 'right'}]
        control.settings_store._cache = settings
        try:
            control.crossing_ledger.refresh(settings, peer)
            for width in (640, 900, 1440, 1920):
                control.window.setFrame_display_(((100000, 100000), (width, 900)), False)
                settle(control.window)
                control.crossing_ledger.reflow()
                row = control.crossing_ledger.rows[peer]
                with self.subTest(width=width):
                    self.assertEqual(row.name.view.stringValue(), text)
                    self.assertEqual(row.name.view.toolTip(), text)
                    self.assertEqual(row.name.view.accessibilityLabel(), text)
                    self.assertEqual(row.name.view.lineBreakMode(), AppKit.NSLineBreakByTruncatingTail)
                    self.assertTrue(AppKit.NSContainsRect(row.cells['machine'].bounds(), row.name.view.frame()))
        finally:
            control.settings_store._cache = previous
            control.crossing_ledger.refresh(previous, control.chosen_peer)

    def test_preview_captions_fit_without_overlapping_the_picture_or_each_other(self):
        control = self.control
        control._select_page('design')
        control.has_notch = True
        control.place = 'notch'
        control._reflect()
        choices = [control.notch_style_select, *control.glow_style_select.rows,
                   *control.switch_style_select.rows]
        for width in (320, 640, 900, 1440, 1920):
            control.window.setFrame_display_(((100000, 100000), (width, 900)), False)
            settle(control.window)
            for family in choices:
                for value, tile, name in family.tiles:
                    content = tile.tile_content
                    with self.subTest(width=width, style=value):
                        for label in (content.name, content.detail):
                            bounds = label.view.bounds()
                            required = label.view.attributedStringValue().boundingRectWithSize_options_(
                                (bounds.size.width, 10000), AppKit.NSStringDrawingUsesLineFragmentOrigin).size.height
                            self.assertGreaterEqual(bounds.size.height + .5, required)
                            self.assertTrue(AppKit.NSContainsRect(tile.bounds(), label.view.frame()))
                            self.assertEqual(label.view.accessibilityLabel(), label.view.attributedStringValue().string())
                        self.assertGreaterEqual(content.preview.frame().origin.y,
                                                content.name.view.frame().origin.y + content.name.view.frame().size.height + 5)
                        self.assertGreaterEqual(content.name.view.frame().origin.y,
                                                content.detail.view.frame().origin.y + content.detail.view.frame().size.height + 3)
                        self.assertGreaterEqual(content.preview.bounds().size.height, 10)
            for family in control.glow_colour_select.rows:
                for value, cell, chip, name in family.cells:
                    with self.subTest(width=width, colour=value):
                        required = name.view.attributedStringValue().boundingRectWithSize_options_(
                            (name.view.bounds().size.width, 10000), AppKit.NSStringDrawingUsesLineFragmentOrigin).size.height
                        self.assertGreaterEqual(name.view.bounds().size.height + .5, required)
                        self.assertTrue(AppKit.NSContainsRect(cell.bounds(), name.view.frame()))
                        self.assertGreaterEqual(chip.frame().origin.y,
                                                name.view.frame().origin.y + name.view.frame().size.height + 5)

    def test_only_the_active_page_is_attached_and_each_document_scrolls_to_its_end(self):
        control = self.control
        for width in (320, 1920):
            control.window.setFrame_display_(((100000, 100000), (width, 900)), False)
            for key, scroll in control.pages.items():
                control._select_page(key)
                settle(control.window)
                self.assertEqual(list(control.page_host.arrangedSubviews()), [scroll])
                page, clip = scroll.documentView(), scroll.contentView()
                self.assertTrue(page.isFlipped())
                maximum = max(0, page.frame().size.height - clip.bounds().size.height)
                clip.scrollToPoint_((0, maximum))
                scroll.reflectScrolledClipView_(clip)
                settle(control.window)
                self.assertAlmostEqual(clip.bounds().origin.y, maximum, delta=1)
                clip.scrollToPoint_((0, 0))

    def test_hidden_stacked_preview_children_wait_until_they_are_shown(self):
        from previews import TileStack
        children = [AppKit.NSView.alloc().init() for _ in range(3)]
        view = TileStack.alloc().init().setup(children)
        view.setFrame_(((0, 0), (200, 100)))
        view.layout()
        self.assertEqual(children[0].frame().size.width, 200)
        self.assertEqual(children[1].frame().size.width, 0)
        view.show(children[1])
        view.layout()
        self.assertEqual(children[1].frame().size.width, 200)

    def test_narrow_overview_controls_fit_as_a_trailing_group(self):
        control = self.control
        control._select_page('overview')
        control.window.setFrame_display_(((100000, 100000), (320, 900)), False)
        settle(control.window)
        row = control.panel.list.arrangedSubviews()[0]
        controls = [v for v in walk(row) if isinstance(v, widgets.Pressable) and v.accessibilityLabel()]
        chosen = [next(v for v in controls if v.accessibilityLabel() == 'In use'),
                  next(v for v in controls if str(v.accessibilityLabel()).startswith('Leave out ')),
                  next(v for v in controls if str(v.accessibilityLabel()).startswith('Show directions')),
                  next(v for v in controls if str(v.accessibilityLabel()).startswith('Remove '))]
        frames = [frame_in(v, row) for v in chosen]
        for frame in frames:
            self.assertTrue(AppKit.NSContainsRect(row.bounds(), frame))
            self.assertAlmostEqual(frame.origin.x + frame.size.width, row.bounds().size.width - 12, delta=1)
        for first, second in zip(frames, frames[1:]):
            self.assertFalse(AppKit.NSIntersectsRect(first, second))

    def test_long_machine_names_stay_inside_the_crossing_visual_slots(self):
        control = self.control
        control._select_page('crossing')
        control.window.setFrame_display_(((100000, 100000), (320, 900)), False)
        settle(control.window)
        canvas = control.atlas_canvas
        own = control.settings_store.current()["machine_id"]
        canvas.show_layout(
            {own: {"x": 0, "y": 0, "w": 160, "h": 100},
             "synthetic": {"x": 160, "y": 0, "w": 160, "h": 100}},
            {}, {own: "This Mac", "synthetic": "Alex’s Windows gaming machine"}, own, "synthetic")
        control.push_strip.set_other_name('Alex’s Windows gaming machine')
        settle(control.window)
        self.assertEqual(canvas.label_for_tile["synthetic"].string(), 'Alex’s Windows gaming machine')
        self.assertGreater(canvas.label_for_tile["synthetic"].frame().size.width, 0)
        peer_label = control.push_strip.peer_label_text()
        attributes = {AppKit.NSFontAttributeName: theme.font(theme.TYPE['eyebrow'], 600),
                      AppKit.NSKernAttributeName: 1.45}
        width = AppKit.NSString.stringWithString_(peer_label).sizeWithAttributes_(attributes).width
        self.assertLessEqual(width, control.push_strip.bounds().size.width / 2 - 16)
        self.assertTrue(peer_label.endswith('…'))


if __name__ == '__main__':
    unittest.main()
