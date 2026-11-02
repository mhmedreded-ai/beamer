import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import notch_island


class BreakthroughPhaseTest(unittest.TestCase):
    def test_full_size_from_the_first_frame_even_after_a_flick(self):
        level, flash = notch_island.breakthrough_phase(0.0)
        self.assertEqual(level, 1.0)
        self.assertEqual(flash, 1.0)

    def test_flash_fades_while_the_island_holds_full_size(self):
        level, flash = notch_island.breakthrough_phase(notch_island.BREAKTHROUGH_GROW_S / 2)
        self.assertEqual(level, 1.0)
        self.assertAlmostEqual(flash, 0.5)
        level, flash = notch_island.breakthrough_phase(notch_island.BREAKTHROUGH_GROW_S + 0.01)
        self.assertEqual(level, 1.0)
        self.assertEqual(flash, 0.0)

    def test_the_sequence_lasts_long_enough_to_see(self):
        total = notch_island.BREAKTHROUGH_GROW_S + notch_island.BREAKTHROUGH_HOLD_S
        self.assertGreaterEqual(total, 0.5)
        self.assertIsNotNone(notch_island.breakthrough_phase(total - 0.001))
        self.assertIsNone(notch_island.breakthrough_phase(total))
        self.assertIsNone(notch_island.breakthrough_phase(-0.01))

    def test_plays_for_as_long_as_the_setting_says(self):
        self.assertEqual(notch_island.breakthrough_phase(2.5, 3.0)[0], 1.0)
        self.assertIsNone(notch_island.breakthrough_phase(3.0, 3.0))


class HeldTest(unittest.TestCase):
    class Controller:
        class Crossing:
            touching = True

        crossing = Crossing()

    def setUp(self):
        self.controller = self.Controller()
        self.island = notch_island.NotchIsland(self.controller, logger=None)

    def test_repositioning_at_the_notch_keeps_the_last_pressure_showing(self):
        self.assertEqual(self.island._held(0.6, 10.0), 0.6)
        self.assertEqual(self.island._held(0.0, 10.5), 0.6)
        self.assertEqual(self.island._held(0.3, 10.8), 0.6)
        self.assertEqual(self.island._held(0.8, 10.85), 0.8)

    def test_a_slow_push_that_drains_between_events_keeps_showing(self):
        for frame in range(30):
            now = 10.0 + frame / 60
            level = 0.04 if frame % 3 == 0 else 0.0
            self.assertGreater(self.island._held(level, now), 0.0, f"dropped at frame {frame}")

    def test_lets_go_after_the_grace_period(self):
        self.island._held(0.6, 10.0)
        self.assertEqual(self.island._held(0.0, 10.0 + notch_island.GRACE_S + 0.01), 0.0)

    def test_leaving_the_notch_lets_go_at_once(self):
        self.island._held(0.6, 10.0)
        self.controller.crossing = None
        self.assertEqual(self.island._held(0.0, 10.1), 0.0)


class TraceTest(unittest.TestCase):
    def test_finishes_off_the_end_of_the_rim_and_stays_there(self):
        end = 1.0 + notch_island.TRACE_LENGTH
        for after in (0.6, 1.2, 2.0, 3.0):
            with self.subTest(after=after):
                self.assertEqual(notch_island.trace_head(0.0, after), 0.0)
                self.assertAlmostEqual(notch_island.trace_head(0.8, after), end)
                self.assertAlmostEqual(notch_island.trace_head(0.95, after), end)

    def test_runs_whole_passes_that_fit_the_duration(self):
        # 3s makes three passes of 0.8s; half way through the first it is part way along the rim.
        middle = notch_island.trace_head(0.8 / 6, 3.0)
        self.assertGreater(middle, 0.0)
        self.assertLess(middle, 1.0 + notch_island.TRACE_LENGTH)


if __name__ == "__main__":
    unittest.main()
