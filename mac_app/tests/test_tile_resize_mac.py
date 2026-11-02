"""Mac resize interaction coverage; deliberately not run by the edits-only worker."""

import copy
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import AppKit
import Quartz

AppKit.NSApplication.sharedApplication()

import diagram
from core import crossing_state, ways
from test_crossing_page_machines import BEE, Page, token
from wake import WakingController


class TileResizeMac(Page):
    def setUp(self):
        super().setUp()
        capture = mock.patch.object(WakingController, "start_input_capture")
        capture.start()
        self.addCleanup(capture.stop)

    def canvas(self, *, selected=BEE, read_only=False):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})])
        canvas = window.atlas_canvas
        own = canvas.own_id
        tiles = {own: {"x": 0, "y": 0, "w": 160, "h": 100},
                 BEE: {"x": 160, "y": 0, "w": 160, "h": 100}}
        window._atlas_layout_changed(tiles, BEE, snap=False)
        canvas.setFrame_(((0, 0), (188, canvas.HEIGHT)))
        canvas.show_layout(tiles, canvas.states, {own: "This Mac", BEE: "Bee"}, own,
                           selected, read_only=read_only)
        return window, canvas

    def event(self, canvas, point, *, key=124, flags=0):
        canvas.window = mock.Mock()
        canvas.convertPoint_fromView_ = mock.Mock(return_value=AppKit.NSMakePoint(*point))
        event = mock.Mock()
        event.locationInWindow.return_value = AppKit.NSMakePoint(*point)
        event.clickCount.return_value = 1
        event.keyCode.return_value = key
        event.modifierFlags.return_value = flags
        return event

    def start_resize(self, canvas, key=BEE):
        frame = canvas._map()[0][key]
        # The middle of the drawn grip: its outer 5 px on a shared seam belong to that edge's third.
        point = (frame[0] + frame[2] - 7, frame[1] + frame[3] - 7)
        event = self.event(canvas, point)
        canvas.mouseDown_(event)
        return event, point

    def test_grip_drag_uses_frozen_scale_keeps_aspect_and_commits_one_drop(self):
        window, canvas = self.canvas()
        before = copy.deepcopy(canvas.tiles[BEE])
        scale = canvas._map()[1]
        with mock.patch.object(diagram.AppKit, "NSCursor"), \
                mock.patch.object(window.controller, "crossing_event", wraps=window.controller.crossing_event) as drop:
            event, point = self.start_resize(canvas)
            self.assertTrue(canvas.dragging["resize"])
            for dx in (40, 80):
                canvas.convertPoint_fromView_.return_value = AppKit.NSMakePoint(point[0] + dx * scale, point[1])
                canvas.mouseDragged_(event)
                drop.assert_not_called()
                self.assertEqual(canvas._map()[1], scale)
            self.assertEqual(canvas.tiles[BEE], {**before, "w": 240, "h": 150})
            canvas.mouseUp_(event)
            canvas.mouseUp_(event)
        drop.assert_called_once()
        self.assertIs(drop.call_args.args[0], crossing_state.drop)
        self.assertEqual(drop.call_args.kwargs["snap_radius"], 0)
        self.assertEqual(self.store.current()["layout"][BEE], {**before, "w": 240, "h": 150})
        self.assertEqual(canvas.last_announcement, "Bee resized.")

    def test_grip_drag_clamps_both_limits_without_shelving_or_snapping(self):
        for delta, target in ((10000, ways.RESIZE_MAX), (-10000, ways.RESIZE_MIN)):
            with self.subTest(target=target), mock.patch.object(diagram.AppKit, "NSCursor"):
                _window, canvas = self.canvas()
                canvas.on_layout_change = mock.Mock()
                canvas.on_leave_out = mock.Mock()
                event, point = self.start_resize(canvas)
                canvas.convertPoint_fromView_.return_value = AppKit.NSMakePoint(point[0] + delta, point[1] + delta)
                canvas.mouseDragged_(event)
                self.assertIsNone(canvas.snap_preview())
                canvas.mouseUp_(event)
                self.assertEqual(canvas.tiles[BEE]["w"], target)
                self.assertEqual(canvas.tiles[BEE]["h"], round(target * 100 / 160))
                self.assertEqual((canvas.tiles[BEE]["x"], canvas.tiles[BEE]["y"]), (160, 0))
                canvas.on_layout_change.assert_called_once_with(canvas.tiles, BEE, snap=False)
                canvas.on_leave_out.assert_not_called()

    def test_grip_visibility_on_selection_and_hover_and_read_only(self):
        _window, canvas = self.canvas(selected=None)
        self.assertTrue(canvas.resize_layers[BEE].isHidden())
        frame = canvas._map()[0][BEE]
        event = self.event(canvas, (frame[0] + frame[2] - 3, frame[1] + frame[3] - 3))
        with mock.patch.object(diagram.AppKit, "NSCursor"):
            canvas.mouseMoved_(event)
            self.assertFalse(canvas.resize_layers[BEE].isHidden())
            canvas._resize_cursor().set.assert_called()
            canvas.mouseExited_(event)
        self.assertTrue(canvas.resize_layers[BEE].isHidden())
        canvas.selected = BEE
        canvas._draw(False)
        self.assertFalse(canvas.resize_layers[BEE].isHidden())
        canvas.show_layout(canvas.tiles, canvas.states, canvas.labels, canvas.own_id, BEE, read_only=True)
        self.assertTrue(canvas.resize_layers[BEE].isHidden())
        self.assertIsNone(canvas._resize_hit(AppKit.NSMakePoint(frame[0] + frame[2] - 3, frame[1] + frame[3] - 3)))

    def test_a_tile_inside_the_band_keeps_its_end_third_and_its_grip(self):
        window, canvas = self.canvas()
        tiles = {canvas.own_id: {"x": 0, "y": 0, "w": 160, "h": 100},
                 BEE: {"x": 160, "y": 0, "w": 4, "h": 100}}
        window._atlas_layout_changed(tiles, BEE, snap=False)
        canvas.show_layout(tiles, canvas.states, canvas.labels, canvas.own_id, BEE)
        frames, _scale = canvas._map()
        x, y, width, height = canvas._resize_rect(frames[BEE])
        self.assertLessEqual(width, 5)
        _peer, _third, _side, _start, end = next(segment for segment in canvas._third_segments(frames)
                                                 if segment[1] == "end")
        band = AppKit.NSMakePoint(end[0], end[1] - 1)
        self.assertEqual(canvas._third_hit(band), (BEE, "end"))
        self.assertIsNone(canvas._resize_hit(band))
        outside = AppKit.NSMakePoint(x + width + 6, y + height + 3)
        self.assertIsNone(canvas._third_hit(outside))
        self.assertEqual(canvas._resize_hit(outside), BEE)

    def test_no_grip_for_shelved_or_other_machines_split_displays(self):
        window, canvas = self.canvas()
        canvas.tiles[BEE]["displays"] = {"main": {"x": 0, "y": 0, "w": 160, "h": 100}}
        canvas.tiles[BEE]["display_offsets"] = {"main": {"x": 30, "y": 20}}
        canvas._draw(False)
        self.assertTrue(canvas.resize_layers[BEE].isHidden())
        canvas.on_layout_change = mock.Mock()
        canvas.keyDown_(self.event(canvas, (0, 0), flags=AppKit.NSEventModifierFlagOption))
        canvas.on_layout_change.assert_not_called()
        window._set_atlas_left_out(BEE, True)
        self.assertNotIn(BEE, canvas.tiles)
        self.assertNotIn(BEE, canvas.resize_layers)

    def test_option_arrows_resize_one_eighth_and_keep_origin(self):
        for key, dimensions in ((124, (180, 112)), (125, (180, 112)),
                                (123, (140, 88)), (126, (140, 88))):
            with self.subTest(key=key):
                _window, canvas = self.canvas()
                event = self.event(canvas, (0, 0), key=key, flags=AppKit.NSEventModifierFlagOption)
                canvas.keyDown_(event)
                saved = self.store.current()["layout"][BEE]
                self.assertEqual((saved["w"], saved["h"]), dimensions)
                self.assertEqual((saved["x"], saved["y"]), (160, 0))
                self.assertEqual(canvas.last_announcement, "Bee resized.")

    def test_this_mac_can_resize_but_stays_fixed_in_position(self):
        _window, canvas = self.canvas()
        canvas.selected = canvas.own_id
        canvas._draw(False)
        canvas.keyDown_(self.event(canvas, (0, 0), key=123, flags=AppKit.NSEventModifierFlagOption))
        saved = self.store.current()["layout"][canvas.own_id]
        self.assertEqual(saved, {"x": 0, "y": 0, "w": 140, "h": 88})

    def test_read_only_keyboard_and_unchanged_drag_do_not_commit(self):
        _window, canvas = self.canvas(read_only=True)
        canvas.on_layout_change = mock.Mock()
        canvas.keyDown_(self.event(canvas, (0, 0), flags=AppKit.NSEventModifierFlagOption))
        canvas.on_layout_change.assert_not_called()
        _window, canvas = self.canvas()
        canvas.on_layout_change = mock.Mock()
        with mock.patch.object(diagram.AppKit, "NSCursor"):
            event, _point = self.start_resize(canvas)
            canvas.mouseUp_(event)
        canvas.on_layout_change.assert_not_called()

    def test_writable_profiles_grip_keeps_the_viewed_layout(self):
        _window, canvas = self.canvas()
        canvas.show_layout(canvas.tiles, canvas.states, canvas.labels, canvas.own_id,
                           BEE, machine_picker=True, read_only=False)
        canvas.on_select = mock.Mock()
        canvas.on_layout_change = mock.Mock()
        with mock.patch.object(diagram.AppKit, "NSCursor"):
            event, point = self.start_resize(canvas)
            scale = canvas.dragging["scale"]
            canvas.convertPoint_fromView_.return_value = AppKit.NSMakePoint(point[0] + 80 * scale, point[1])
            canvas.mouseDragged_(event)
            canvas.mouseUp_(event)
        canvas.on_select.assert_not_called()
        canvas.on_layout_change.assert_called_once_with(canvas.tiles, BEE, snap=False)
        self.assertEqual((canvas.tiles[BEE]["w"], canvas.tiles[BEE]["h"]), (240, 150))

    def test_local_split_tile_can_resize_and_preserves_scaled_offsets(self):
        _window, canvas = self.canvas()
        own = canvas.own_id
        canvas.tiles[own]["display_offsets"] = {"main": {"x": 16, "y": 8}}
        canvas.on_layout_change = mock.Mock()
        canvas.on_select = mock.Mock()
        with mock.patch.object(diagram.AppKit, "NSCursor"):
            event, point = self.start_resize(canvas, own)
            scale = canvas.dragging["scale"]
            canvas.convertPoint_fromView_.return_value = AppKit.NSMakePoint(point[0] + 80 * scale, point[1])
            canvas.mouseDragged_(event)
            canvas.mouseUp_(event)
        canvas.on_select.assert_not_called()
        self.assertEqual(canvas.selected, own)
        self.assertEqual(canvas.tiles[own]["display_offsets"], {"main": {"x": 24, "y": 12}})
        canvas.on_layout_change.assert_called_once_with(canvas.tiles, own, snap=False)

    def test_option_arrows_at_limits_do_not_commit_unchanged_size(self):
        for target, key in ((ways.RESIZE_MIN, 123), (ways.RESIZE_MAX, 124)):
            with self.subTest(target=target):
                _window, canvas = self.canvas()
                canvas.tiles[BEE] = ways.resize_tile(canvas.tiles[BEE], target)
                canvas.on_layout_change = mock.Mock()
                canvas.keyDown_(self.event(canvas, (0, 0), key=key, flags=AppKit.NSEventModifierFlagOption))
                self.assertEqual(canvas.tiles[BEE]["w"], target)
                canvas.on_layout_change.assert_not_called()

    def test_small_tile_elides_name_and_keeps_full_tooltip_and_accessibility(self):
        _window, canvas = self.canvas()
        name = "A very long machine name that cannot fit"
        canvas.labels[BEE] = name
        canvas.tiles[BEE] = ways.resize_tile(canvas.tiles[BEE], ways.RESIZE_MIN)
        canvas._draw(False)
        title = canvas.label_for_tile[BEE]
        self.assertEqual(title.string(), name)
        self.assertEqual(title.truncationMode(), Quartz.kCATruncationEnd)
        frame = canvas._title_rect(BEE, canvas._map()[0][BEE])
        point = AppKit.NSMakePoint(frame[0] + 1, frame[1] + 1)
        self.assertEqual(canvas.view_stringForToolTip_point_userData_(canvas, 0, point, 2), name)
        element = next(child for child in canvas.accessibilityChildren() if child.key == BEE)
        self.assertEqual(element.accessibilityLabel(), name)
        self.assertIn("Option", element.accessibilityHelp())
        self.assertIn("resize", canvas._description())

    def test_off_hint_mentions_corner_resize_and_profiles_on_hint_is_preserved(self):
        window, _canvas = self.canvas()
        self.assertTrue(window.atlas_canvas_hint.text.endswith(" · drag a corner to resize"))
        window.ways_switch._toggle()
        self.assertEqual(window.atlas_canvas_hint.text,
                         "Click a machine to set its ways · Tab and Return pick one from the keyboard")
