import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import previews


class PreviewFrameTest(unittest.TestCase):
    def test_page_clock_stays_stopped_until_a_tile_is_hovered(self):
        import types

        loop = previews.PreviewLoop(types.SimpleNamespace(cfg=types.SimpleNamespace(crossing={"notch_after_ms": 1200})),
                                    host=object())
        loop.start()
        self.assertFalse(loop.clock.is_alive())

    def test_effect_hover_holds_until_intent_then_starts_with_approach(self):
        from types import SimpleNamespace
        from unittest import mock

        view = SimpleNamespace(playing_since=10.1)
        with mock.patch.object(previews.time, "monotonic", return_value=10.0), \
                mock.patch.object(previews.effects_overlay, "reduce_motion", return_value=False):
            self.assertEqual(previews._played(view, 2.2), 2.2)
            self.assertFalse(previews._live(view))
        with mock.patch.object(previews.time, "monotonic", return_value=10.3), \
                mock.patch.object(previews.effects_overlay, "reduce_motion", return_value=False):
            self.assertAlmostEqual(previews._played(view, 2.2), 0.2)
            self.assertAlmostEqual(previews._played(view, 2.2, switch=True), 2.4)
            self.assertTrue(previews._live(view))
        with mock.patch.object(previews.time, "monotonic", return_value=11.0), \
                mock.patch.object(previews.effects_overlay, "reduce_motion", return_value=True):
            self.assertEqual(previews._played(view, 2.2), 2.2)
            self.assertFalse(previews._live(view))

    def test_a_push_builds_goes_through_plays_and_rests(self):
        self.assertEqual(previews.preview_frame(0.0, 1.2), (0.0, "push"))
        self.assertLess(previews.preview_frame(0.5, 1.2)[0], previews.preview_frame(1.5, 1.2)[0])
        self.assertEqual(previews.preview_frame(previews.PUSH_S + 0.1, 1.2), (0.0, "after"))
        self.assertEqual(previews.preview_frame(previews.PUSH_S + 1.3, 1.2), (0.0, "rest"))

    def test_loops_and_plays_for_as_long_as_keep_animating_says(self):
        cycle = previews.PUSH_S + 3.0 + previews.REST_S
        self.assertEqual(previews.preview_frame(previews.PUSH_S + 2.5, 3.0)[1], "after")
        self.assertEqual(previews.preview_frame(cycle + 0.1, 3.0)[1], "push")



class HostedGlowTest(unittest.TestCase):
    def test_the_design_pages_glow_preview_survives_a_push(self):
        # A preview's feed has no desktop; the glow once asked it for one and switched itself off.
        import logging
        import types

        import config
        import crossing
        import kvm_bridge_app

        feed = previews.PreviewFeed(types.SimpleNamespace(cfg=config.Config(
            host="192.0.2.10", port=24820, auth_token="synthetic-token", crossing=dict(config.DEFAULT_CROSSING))))
        glow = kvm_bridge_app.EdgeGlow(feed, logging.getLogger("test-previews"))
        glow._draw = lambda: None
        step = crossing.Step(pressure=0.4, pin=(799.0, 200.0), mac_edge="right", region=(799, 0, 1, 450), via="edge")
        glow.update("pressure", step)
        self.assertFalse(glow.disabled)
        self.assertEqual(glow.region, (799, 0, 1, 450))


class TileHoverTest(unittest.TestCase):
    """The page's tiles hold stills; the one the pointer is over plays, alone, from its still."""

    def setUp(self):
        from unittest import mock

        self.patches = [mock.patch.object(previews, "on_screen", lambda view: True),
                        mock.patch.object(previews.effects_overlay, "reduce_motion", lambda: False)]
        for patch in self.patches:
            patch.start()
        controller = mock.Mock()
        controller.cfg.crossing = {"notch_after_ms": 1200}
        self.loop = previews.PreviewLoop(controller)
        self.screens = [mock.Mock(**{"window.return_value": None}) for _ in range(2)]
        for screen in self.screens:
            feed = mock.Mock(level=0.0)
            renderer = mock.Mock(spec=["_draw", "_hide", "_tick", "timer"])
            self.loop.add(feed, renderer, mock.Mock(), screen)
        self.loop.clock.start = mock.Mock()
        self.loop.clock.stop = mock.Mock()
        self.loop.running = True

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def playing(self):
        return [player.started_at is not None for player in self.loop.players]

    def test_only_the_resting_tile_plays_until_one_is_hovered(self):
        self.loop.set_resting([self.screens[0]])
        self.loop._tick()
        self.assertEqual(self.playing(), [True, False])
        self.loop.hover(self.screens[1])
        self.loop.hovered_at -= previews.HOVER_INTENT_S
        self.loop._tick()
        self.assertEqual(self.playing(), [False, True])

    def test_a_pointer_passing_over_starts_nothing(self):
        self.loop.hover(self.screens[1])
        self.loop._tick()
        self.assertEqual(self.playing(), [False, False])

    def test_hover_starts_the_page_clock(self):
        self.loop.hover(self.screens[1])
        self.loop.clock.start.assert_called_once_with()

    def test_hovered_offscreen_tile_does_not_keep_page_clock_running(self):
        from unittest import mock
        self.loop.hover(self.screens[0])
        with mock.patch.object(previews, "on_screen", return_value=False):
            self.loop._tick()
        self.loop.clock.stop.assert_called()

    def test_reduce_motion_suspends_renderer_and_page_clocks_then_resumes(self):
        from unittest import mock
        self.loop.hover(self.screens[0])
        self.loop.hovered_at -= previews.HOVER_INTENT_S
        self.loop._tick()
        player = self.loop.players[0]
        self.assertIsNotNone(player.started_at)
        player.renderer.timer.stop = mock.Mock(wraps=player.renderer.timer.stop)
        with mock.patch.object(previews.effects_overlay, 'reduce_motion', return_value=True):
            self.loop.start()
        self.assertIsNone(player.started_at)
        player.renderer.timer.stop.assert_called_once_with()
        self.loop.clock.stop.assert_called()
        self.loop.clock.start.reset_mock()
        self.loop.start()
        self.loop.clock.start.assert_called_once_with()
        self.loop._tick()
        self.assertIsNotNone(player.started_at)

    def test_hover_under_reduce_motion_leaves_the_page_clock_stopped(self):
        from unittest import mock
        with mock.patch.object(previews.effects_overlay, "reduce_motion", return_value=True):
            self.loop.hover(self.screens[1])
        self.loop.clock.start.assert_not_called()

    def test_a_tile_plays_on_from_its_held_push(self):
        self.loop.set_resting([self.screens[0]])
        self.loop._tick()
        self.assertAlmostEqual(self.loop.players[0].feed.level, previews.PreviewLoop.HOLD, places=2)

    def test_a_tile_left_mid_breakthrough_holds_a_clean_still(self):
        renderer = self.loop.players[0].renderer
        renderer.cross_at, renderer.flare = 12.0, 0.8
        renderer.mock_add_spec(["_draw", "_hide", "_tick", "timer", "cross_at", "flare"])
        renderer.cross_at, renderer.flare = 12.0, 0.8
        self.loop._hold(self.loop.players[0])
        self.assertEqual((renderer.cross_at, renderer.flare), (None, 0.0))

    def test_a_pointer_passing_over_leaves_the_resting_tile_playing(self):
        self.loop.set_resting([self.screens[0]])
        self.loop._tick()
        self.loop.hover(self.screens[1])
        self.loop._tick()
        self.assertEqual(self.playing(), [True, False])

    def test_reduce_motion_keeps_the_hovered_tile_still(self):
        with __import__("unittest").mock.patch.object(previews.effects_overlay, "reduce_motion", lambda: True):
            self.loop.hover(self.screens[1])
            self.loop.hovered_at -= previews.HOVER_INTENT_S
            self.loop._tick()
        self.assertEqual(self.playing(), [False, False])


if __name__ == "__main__":
    unittest.main()
