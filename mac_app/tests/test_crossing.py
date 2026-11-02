import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crossing import DECAY_S, CrossingEngine, Ways

BOUNDS = (0, 0, 1728, 1117)
RIGHT_X = 1727.0


def engine(**overrides):
    settings = dict(methods=("edge",), edge="right", resistance_px=120)
    settings.update(overrides)
    return CrossingEngine(**settings)


def drive(machine, x, y, dx, dy, times, bounds=BOUNDS, notch_range=None, dragging=False):
    """The pointer is already against an edge, so the OS keeps reporting it there while the
    deltas keep coming, 10ms apart. Returns the step that crossed, or the last one."""
    result = None
    for index in range(times):
        result = machine.feed(x, y, dx, dy, bounds, 1.0 + index * 0.01, notch_range, dragging)
        if result.crossed:
            break
    return result


def push_right(machine, times, dx=20, y=500.0, dragging=False):
    return drive(machine, RIGHT_X, y, dx, 0, times, dragging=dragging)


class ArrivalDirectionTests(unittest.TestCase):
    def test_left_wrap_departure_preserves_offset_and_lands_on_the_right(self):
        machine = CrossingEngine(resistance_px=0, ways=[
            Ways("peer", frozenset({"edge"}), "left", frozenset(), "top_left", "0", "0", "right")])
        step = machine.feed(0, 335, -20, 0, BOUNDS, 1.0, displays=[BOUNDS])
        self.assertTrue(step.crossed)
        self.assertEqual((step.edge, step.display, step.side), ("right", "0", "right"))
        point = CrossingEngine.arrival_point(step.edge, step.offset, BOUNDS,
                                              displays=[BOUNDS], display=step.display, side=step.side)
        self.assertEqual(point[0], RIGHT_X - 2)
        self.assertAlmostEqual(point[1], 335, delta=1)


class ContactSpanTests(unittest.TestCase):
    def test_single_screen_contact_span_does_not_block_crossing(self):
        machine = CrossingEngine(resistance_px=0, ways=[
            Ways("peer", frozenset({"edge"}), "right", frozenset(), "top_right",
                 contact_span=(0.0, 1.0), target_span=(0.0, 1.0)),
        ])

        step = machine.feed(1919, 540, 10, 0, (0, 0, 1920, 1080), 1.0)

        self.assertTrue(step.crossed)
        self.assertAlmostEqual(step.offset, 0.5, places=3)

    def test_single_screen_contact_span_still_gates_crossings(self):
        ways = [Ways("peer", frozenset({"edge"}), "right", frozenset(), "top_right",
                     contact_span=(0.25, 0.75), target_span=(0.0, 1.0))]

        inside = CrossingEngine(resistance_px=0, ways=ways).feed(
            1919, 500, 10, 0, (0, 0, 1920, 1080), 1.0)
        outside = CrossingEngine(resistance_px=0, ways=ways).feed(
            1919, 100, 10, 0, (0, 0, 1920, 1080), 1.0)

        self.assertTrue(inside.crossed)
        self.assertFalse(outside.crossed)


class TickStepsTests(unittest.TestCase):
    def test_which_quarter_ticks_are_felt(self):
        from crossing import tick_fires

        quarters = (0.25, 0.5, 0.75)
        self.assertTrue(all(tick_fires("quarters", p) for p in quarters))
        self.assertEqual([tick_fires("halves", p) for p in quarters], [False, True, False])
        self.assertFalse(any(tick_fires("breakthrough", p) for p in quarters))


class TouchingTests(unittest.TestCase):
    def test_touching_survives_pressure_draining_away_but_not_leaving(self):
        machine = engine()
        machine.feed(RIGHT_X, 500.0, 2, 0, BOUNDS, 1.0)
        # Half a second later with no outward travel: pressure has drained and released.
        step = machine.feed(RIGHT_X, 500.0, 0, 0, BOUNDS, 1.5)
        self.assertEqual(step.pressure, 0.0)
        self.assertIsNone(machine.pin)
        self.assertTrue(machine.touching)
        machine.feed(800.0, 500.0, -20, 0, BOUNDS, 1.6)
        self.assertFalse(machine.touching)


class ResistanceTests(unittest.TestCase):
    def test_outward_travel_accumulates_and_holds_the_pointer(self):
        machine = engine()
        first = machine.feed(RIGHT_X, 500.0, 20, 0, BOUNDS, 1.0)
        self.assertFalse(first.hold, "the first touch passes so the pointer visibly arrives")
        second = machine.feed(RIGHT_X, 500.0, 20, 0, BOUNDS, 1.01)
        self.assertTrue(second.hold)
        self.assertEqual(second.pin, (RIGHT_X, 500.0))
        self.assertEqual(second.mac_edge, "right")
        self.assertGreater(second.pressure, first.pressure)
        self.assertFalse(second.crossed)

    def test_the_crossing_step_says_where_it_gave(self):
        crossed = engine(resistance_px=0).feed(RIGHT_X, 420.0, 20, 0, BOUNDS, 1.0)
        self.assertTrue(crossed.crossed)
        self.assertEqual(crossed.pin, (RIGHT_X, 420.0))

    def test_arriving_at_the_edge_counts_only_the_overshoot(self):
        machine = engine()
        machine.feed(1700.0, 500.0, 0, 0, BOUNDS, 1.0)
        step = machine.feed(RIGHT_X, 500.0, 40, 0, BOUNDS, 1.01)
        self.assertAlmostEqual(machine.pressure, 13.0, places=3)
        self.assertFalse(step.hold)

    def test_pressure_decays_over_time(self):
        machine = engine()
        push_right(machine, 4)
        full = machine.pressure
        self.assertGreater(full, 0)
        self.assertLess(machine.pressure_at(1.03 + DECAY_S / 2), full / 120)
        self.assertEqual(machine.pressure_at(1.03 + DECAY_S * 2), 0.0)
        step = machine.feed(RIGHT_X, 500.0, 0, 0, BOUNDS, 1.03 + DECAY_S * 2)
        self.assertEqual(machine.pressure, 0.0)
        self.assertFalse(step.hold)

    def test_decay_removes_the_same_pointer_travel_at_120_and_500(self):
        for resistance in (120, 500):
            machine = engine(resistance_px=resistance)
            machine.feed(RIGHT_X, 500.0, 100, 0, BOUNDS, 1.0)
            fraction = machine.pressure_at(1.2)
            with self.subTest(resistance=resistance):
                self.assertAlmostEqual(fraction * resistance, 40.0)

    def test_sustained_push_examples_use_a_fixed_decay_rate(self):
        for resistance, speed, expected_time, expected_travel in (
                (120, 500, 0.6, 300), (500, 500, 2.5, 1250), (500, 1000, 0.71, 714)):
            machine = engine(resistance_px=resistance)
            step = None
            interval = 0.01
            for index in range(400):
                step = machine.feed(RIGHT_X, 500.0, speed * interval, 0, BOUNDS,
                                    1.0 + index * interval)
                if step.crossed:
                    break
            with self.subTest(resistance=resistance, speed=speed):
                self.assertTrue(step.crossed)
                self.assertAlmostEqual(index * interval, expected_time, delta=0.025)
                self.assertAlmostEqual((index + 1) * speed * interval, expected_travel, delta=15)


    def test_track_endpoints_and_fill_head_share_the_same_coordinate_scale(self):
        from core.resistance_strip import resistance_x

        self.assertEqual(resistance_x(720, 0), 18.0)
        self.assertEqual(resistance_x(720, 500), 702.0)
        self.assertEqual(resistance_x(720, 250), 360.0)
        self.assertEqual(resistance_x(720, 700), resistance_x(720, 500))

    def test_a_slow_lean_never_builds(self):
        machine = engine()
        step = push_right(machine, 200, dx=1)
        self.assertFalse(step.crossed)
        self.assertFalse(step.hold)

    def test_inward_movement_subtracts(self):
        machine = engine()
        push_right(machine, 4)
        before = machine.pressure
        step = machine.feed(RIGHT_X, 500.0, -30, 0, BOUNDS, 1.04)
        self.assertLess(machine.pressure, before)
        self.assertTrue(step.hold)
        released = machine.feed(RIGHT_X, 500.0, -200, 0, BOUNDS, 1.05)
        self.assertEqual(machine.pressure, 0.0)
        self.assertFalse(released.hold)
        self.assertIsNone(machine.pin)

    def test_movement_along_the_edge_does_not_count(self):
        machine = engine()
        push_right(machine, 3)
        before = machine.pressure
        machine.feed(RIGHT_X, 600.0, 0, 40, BOUNDS, 1.03)
        self.assertLess(machine.pressure, before, "only decay applied")

    def test_quarter_ticks_fire_once_each(self):
        machine = engine()
        ticks = 0
        for index in range(12):
            step = machine.feed(RIGHT_X, 500.0, 12, 0, BOUNDS, 1.0 + index * 0.005)
            ticks += step.tick
            if step.crossed:
                break
        self.assertTrue(step.crossed)
        self.assertEqual(ticks, 3)

    def test_a_quarter_boundary_does_not_tick_twice_when_decay_dips_under_it(self):
        machine = engine()
        machine.feed(RIGHT_X, 500.0, 30, 0, BOUNDS, 1.0)
        step = machine.feed(RIGHT_X, 500.0, 2, 0, BOUNDS, 1.02)
        self.assertLess(machine.pressure, 30.0)
        self.assertFalse(step.tick)
        step = machine.feed(RIGHT_X, 500.0, 10, 0, BOUNDS, 1.03)
        self.assertGreater(machine.pressure, 30.0)
        self.assertFalse(step.tick)


class BreakthroughTests(unittest.TestCase):
    def scoped_corner_step(self):
        machine = CrossingEngine(resistance_px=120, ways=[
            Ways("peer", frozenset({"corner"}), "bottom", frozenset(), "bottom_left", "1", "B1", "right")
        ])
        displays = [(0, 0, 100, 100), (100, 0, 300, 100)]
        return machine.feed(100.0, 99.0, -30.0, 30.0, (0, 0, 400, 100), 1.0,
                            displays=displays, ready=lambda _peer: True)

    def seam_overshoot_step(self):
        machine = CrossingEngine(resistance_px=120, ways=[
            Ways("peer", frozenset({"edge"}), "right", frozenset(), "top_right", "0", "B0", "left")
        ])
        displays = [(0, 0, 100, 100), (100, 0, 300, 100)]
        return machine.feed(105.0, 50.0, 10.0, 0.0, (0, 0, 400, 100), 1.0,
                            displays=displays, ready=lambda _peer: True)

    def test_per_screen_mac_gap_probes_return_steps(self):
        self.assertIsNotNone(self.scoped_corner_step())
        self.assertIsNotNone(self.seam_overshoot_step())

    def test_scoped_corner_on_second_display_is_selected_at_that_displays_corner(self):
        step = self.scoped_corner_step()
        self.assertGreater(step.pressure, 0,
                        "Mac scoped corners gap: the B1 route must be evaluated at A1's nominated corner")

    def test_per_screen_seam_captures_a_single_event_overshoot(self):
        step = self.seam_overshoot_step()
        self.assertGreater(step.pressure, 0, "Mac seam capture gap: one event crossed the display edge uncaptured")

    def test_per_screen_landing_offset_uses_the_departing_display(self):
        machine = CrossingEngine(resistance_px=0, ways=[
            Ways("peer", frozenset({"edge"}), "bottom", frozenset(), "top_right", "1", "B1", "top")
        ])
        displays = [(0, 0, 100, 100), (100, 0, 300, 100)]

        step = machine.feed(200.0, 99.0, 0.0, 1.0, (0, 0, 300, 100), 1.0,
                            displays=displays, ready=lambda _peer: True)

        self.assertTrue(step.crossed)
        self.assertAlmostEqual(step.offset, 0.5, places=2,
                               msg="Mac landing offset gap: fraction must be relative to source display A1")

    def test_breakthrough_carries_the_windows_edge_and_offset(self):
        machine = engine()
        step = push_right(machine, 8, dx=30, y=558.0)
        self.assertTrue(step.crossed)
        self.assertEqual(step.edge, "left")
        self.assertEqual(step.mac_edge, "right")
        self.assertAlmostEqual(step.offset, 0.5, places=3)
        self.assertEqual(machine.pressure, 0.0)
        self.assertIsNone(machine.pin)

    def test_offset_is_measured_from_where_the_push_started(self):
        machine = engine()
        machine.feed(RIGHT_X, 0.0, 20, 0, BOUNDS, 1.0)
        machine.feed(RIGHT_X, 0.0, 20, 0, BOUNDS, 1.01)
        step = machine.feed(RIGHT_X, 900.0, 200, 0, BOUNDS, 1.02)
        self.assertTrue(step.crossed)
        self.assertEqual(step.offset, 0.0)

    def test_zero_resistance_switches_on_contact(self):
        machine = engine(resistance_px=0)
        step = machine.feed(RIGHT_X, 100.0, 1, 0, BOUNDS, 1.0)
        self.assertTrue(step.crossed)
        self.assertEqual(step.edge, "left")

    def test_decayed_pressure_cannot_cross_without_a_push(self):
        machine = engine(resistance_px=10)
        machine.feed(RIGHT_X, 100.0, 9, 0, BOUNDS, 1.0)
        step = machine.feed(RIGHT_X, 100.0, 0, 0, BOUNDS, 1.001)
        self.assertFalse(step.crossed)

    def test_each_edge_maps_to_its_opposite_with_the_right_offset(self):
        cases = {
            "left": ((0.0, 279.0), (-30, 0), "right", 0.25),
            "top": ((431.75, 0.0), (0, -30), "bottom", 0.25),
            "bottom": ((1295.25, 1116.0), (0, 30), "top", 0.75),
        }
        for edge, ((x, y), (dx, dy), windows_edge, offset) in cases.items():
            with self.subTest(edge=edge):
                machine = engine(edge=edge)
                step = drive(machine, x, y, dx, dy, 8)
                self.assertTrue(step.crossed)
                self.assertEqual(step.edge, windows_edge)
                self.assertAlmostEqual(step.offset, offset, places=3)

    def test_arrival_point_sits_just_inside_the_edge(self):
        self.assertEqual(CrossingEngine.arrival_point("left", 0.5, BOUNDS), (2.0, 558.0))
        # An end of an edge lands LANDING_CLEAR_PX in, clear of the edge and corner beside it.
        self.assertEqual(CrossingEngine.arrival_point("right", 0.0, BOUNDS), (1725.0, 10.0))
        self.assertEqual(CrossingEngine.arrival_point("top", 1.0, BOUNDS), (1717.0, 2.0))
        self.assertEqual(CrossingEngine.arrival_point("bottom", 0.5, BOUNDS), (863.5, 1114.0))
        self.assertEqual(CrossingEngine.arrival_point("left", 7.0, BOUNDS)[1], 1106.0)


class GuardTests(unittest.TestCase):
    def test_no_crossing_while_a_button_is_held(self):
        machine = engine()
        step = push_right(machine, 10, dx=40, dragging=True)
        self.assertFalse(step.crossed)
        self.assertFalse(step.hold)
        self.assertEqual(machine.pressure, 0.0)

    def test_dragging_is_allowed_when_the_block_is_off(self):
        machine = engine(block_while_dragging=False)
        step = push_right(machine, 10, dx=40, dragging=True)
        self.assertTrue(step.crossed)

    def test_an_internal_edge_between_two_mac_displays_never_triggers(self):
        wide = (0, 0, 1728 + 1920, 1117)
        machine = engine()
        step = drive(machine, RIGHT_X, 500.0, 40, 0, 10, bounds=wide)
        self.assertFalse(step.crossed)
        self.assertFalse(step.hold)
        self.assertEqual(machine.pressure, 0.0)

    def test_other_edges_are_inert(self):
        machine = engine(edge="right")
        step = drive(machine, 0.0, 500.0, -40, 0, 10)
        self.assertFalse(step.crossed)
        self.assertEqual(machine.pressure, 0.0)

    def test_shortcut_only_is_not_armed(self):
        self.assertFalse(engine(methods=("shortcut",)).armed)
        self.assertTrue(engine(methods=("shortcut", "notch")).armed)

    def test_reset_clears_the_push(self):
        machine = engine()
        push_right(machine, 4)
        machine.reset()
        self.assertEqual(machine.pressure, 0.0)
        self.assertEqual(machine.pressure_at(5.0), 0.0)


class CornerTests(unittest.TestCase):
    def test_diagonal_push_in_the_corner_box_crosses(self):
        machine = engine(methods=("corner",), corner="top_right")
        step = drive(machine, RIGHT_X, 0.0, 30, -30, 10)
        self.assertTrue(step.crossed)
        self.assertEqual(step.mac_edge, "right")
        self.assertEqual(step.edge, "left")
        self.assertEqual(step.offset, 0.0)
        self.assertEqual(step.via, "corner")

    def test_straight_push_in_the_corner_box_does_not_count(self):
        machine = engine(methods=("corner",), corner="top_right")
        step = drive(machine, RIGHT_X, 3.0, 40, 0, 10)
        self.assertFalse(step.crossed)
        self.assertEqual(machine.pressure, 0.0)

    def test_diagonal_push_outside_the_box_does_not_count(self):
        machine = engine(methods=("corner",), corner="top_right")
        step = drive(machine, RIGHT_X, 20.0, 30, -30, 10)
        self.assertFalse(step.crossed)
        self.assertEqual(machine.pressure, 0.0)

    def test_bottom_left_corner(self):
        machine = engine(methods=("corner",), corner="bottom_left")
        step = drive(machine, 0.0, 1116.0, -30, 30, 10)
        self.assertTrue(step.crossed)
        self.assertEqual(step.edge, "right")
        self.assertEqual(step.offset, 1.0)
        self.assertEqual(step.region, (0, 1109.0, 8.0, 8.0))


class NotchTests(unittest.TestCase):
    NOTCH = (771.0, 956.0)

    def test_push_up_within_the_notch_crosses(self):
        machine = engine(methods=("notch",))
        step = drive(machine, 860.0, 0.0, 0, -30, 10, notch_range=self.NOTCH)
        self.assertTrue(step.crossed)
        self.assertEqual(step.mac_edge, "top")
        self.assertEqual(step.edge, "bottom")
        self.assertEqual(step.region, (771.0, 0, 185.0, 1))
        self.assertEqual(step.via, "notch")

    def test_push_up_beside_the_notch_does_nothing(self):
        machine = engine(methods=("notch",))
        step = drive(machine, 400.0, 0.0, 0, -30, 10, notch_range=self.NOTCH)
        self.assertFalse(step.crossed)
        self.assertEqual(machine.pressure, 0.0)

    def test_no_notch_means_no_target(self):
        machine = engine(methods=("notch",))
        step = drive(machine, 860.0, 0.0, 0, -30, 10)
        self.assertFalse(step.crossed)



class PartOfTheEdgeTests(unittest.TestCase):
    """Part of the edge: the chosen thirds of the edge cross, the rest is a plain wall."""

    def test_only_the_chosen_thirds_cross(self):
        # 1117 tall: the thirds break at about 372 and 745.
        for y, crosses in ((100.0, False), (560.0, True), (1000.0, True)):
            with self.subTest(y=y):
                machine = engine(methods=("part",), parts=("middle", "end"))
                self.assertEqual(push_right(machine, 20, y=y).crossed, crosses)

    def test_the_glow_region_is_the_third_being_pushed(self):
        machine = engine(methods=("part",), parts=("middle",))
        step = drive(machine, RIGHT_X, 560.0, 20, 0, 2)
        x, y, w, h = step.region
        self.assertAlmostEqual((y, h), (1117 / 3, 1117 / 3), places=3)

    def test_thirds_are_the_pointers_own_displays(self):
        # A 1117-tall MacBook as the rightmost display, top-aligned beside a 1440-tall external:
        # on the 1440 box its end third would start at 960, 157 points from its bottom.
        displays = [(-2560, 0, 0, 1440), (0, 0, 1728, 1117)]
        bounds = (-2560, 0, 1728, 1440)
        machine = engine(methods=("part",), parts=("end",))
        result = drive(machine, RIGHT_X, 800.0, 20, 0, 20, bounds=bounds)
        self.assertFalse(result.crossed, "measured on the desktop box, 800 is the middle third")
        machine = engine(methods=("part",), parts=("end",))
        for index in range(20):
            result = machine.feed(RIGHT_X, 800.0, 20, 0, bounds, 1.0 + index * 0.01, displays=displays)
            if result.crossed:
                break
        self.assertTrue(result.crossed)

    def test_part_of_the_edge_arms_the_engine(self):
        self.assertTrue(engine(methods=("part",)).armed)


class MonitorLayoutTests(unittest.TestCase):
    """Displays as (left, top, right, bottom) in Quartz points, the way the tap sees them."""

    # A MacBook under a wider external: the MacBook's right edge stops short of the desktop's.
    UNDER = [(0, 0, 2560, 1440), (416, 1440, 2144, 2557)]
    UNDER_BOUNDS = (0, 0, 2560, 2557)

    def feed_at(self, machine, x, y, dx, dy, displays, bounds, times=20):
        result = None
        for index in range(times):
            result = machine.feed(x, y, dx, dy, bounds, 1.0 + index * 0.01, displays=displays)
            if result.crossed:
                break
        return result

    def test_a_shorter_displays_own_edge_is_a_wall_when_nothing_lies_beyond(self):
        step = self.feed_at(engine(), 2143.0, 2000.0, 20, 0, self.UNDER, self.UNDER_BOUNDS)
        self.assertTrue(step.crossed)

    def test_an_edge_with_another_display_beyond_it_is_not(self):
        side = [(0, 0, 1728, 1117), (1728, 0, 3648, 1080)]
        step = self.feed_at(engine(), 1727.0, 500.0, 20, 0, side, (0, 0, 3648, 1117))
        self.assertFalse(step.crossed)

    def test_a_zone_on_one_display_holds_and_crosses_at_its_internal_seam(self):
        machine = CrossingEngine(
            resistance_px=20,
            ways=[Ways("peer", frozenset({"edge"}), "right", frozenset(), "top_right",
                       "0", "display-a", "left")],
        )
        displays = [(0, 0, 100, 100), (100, 0, 200, 100)]
        result = None
        for index in range(20):
            result = machine.feed(99, 50, 5, 0, (0, 0, 200, 100), 1 + index * 0.01,
                                  displays=displays)
            if result.crossed:
                break
        self.assertTrue(result.crossed)
        self.assertEqual((result.display, result.side), ("display-a", "left"))

    def test_one_event_can_cross_an_armed_internal_seam_in_any_direction(self):
        cases = (
            ("right", [(0, 0, 100, 100), (100, 0, 200, 100)], (0, 0, 200, 100),
             "0", 105, 50, 10, 0),
            ("left", [(0, 0, 100, 100), (100, 0, 200, 100)], (0, 0, 200, 100),
             "1", 95, 50, -10, 0),
            ("bottom", [(0, 0, 100, 100), (0, 100, 100, 200)], (0, 0, 100, 200),
             "0", 50, 105, 0, 10),
            ("top", [(0, 0, 100, 100), (0, 100, 100, 200)], (0, 0, 100, 200),
             "1", 50, 95, 0, -10),
        )
        for edge, displays, bounds, display, x, y, dx, dy in cases:
            with self.subTest(edge=edge):
                machine = CrossingEngine(
                    resistance_px=10,
                    ways=[Ways("peer", frozenset({"edge"}), edge, frozenset(), "top_right",
                               display, "peer-display", "left")],
                )
                machine.feed(x - dx, y - dy, 0, 0, bounds, 0.99, displays=displays)
                step = machine.feed(x, y, dx, dy, bounds, 1.0, displays=displays)
                self.assertTrue(step.hold)
                self.assertFalse(step.crossed)
                self.assertGreater(machine.pressure, 0)
                pinned_x, pinned_y = step.pin
                step = machine.feed(pinned_x, pinned_y, dx, dy, bounds, 1.01, displays=displays)
                self.assertTrue(step.crossed)
                self.assertEqual((step.display, step.side), ("peer-display", "left"))

    def test_consecutive_outward_overshoots_keep_building_from_a_pinned_seam(self):
        cases = (
            ("right", (0, 0, 100, 100), (95, 50), (105, 50), (110, 50)),
            ("left", (100, 0, 200, 100), (105, 50), (95, 50), (90, 50)),
            ("bottom", (0, 0, 100, 100), (50, 95), (50, 105), (50, 110)),
            ("top", (0, 100, 100, 200), (50, 105), (50, 95), (50, 90)),
        )
        for edge, display, start, first, second in cases:
            with self.subTest(edge=edge):
                displays = [display, (200, 200, 300, 300)]
                bounds = (0, 0, 300, 300)
                machine = CrossingEngine(
                    resistance_px=40,
                    ways=[Ways("peer", frozenset({"edge"}), edge, frozenset(), "top_right",
                               "0", "peer-display", "left")],
                )

                def feed(point, previous, now):
                    dx, dy = point[0] - previous[0], point[1] - previous[1]
                    return machine.feed(*point, dx, dy, bounds, now, displays=displays)

                first_step = feed(first, start, 1.0)
                self.assertTrue(first_step.hold)
                self.assertEqual(first_step.pin, (display[0] if edge == "left" else
                                                  display[2] - 1 if edge == "right" else first[0],
                                                  display[1] if edge == "top" else
                                                  display[3] - 1 if edge == "bottom" else first[1]))
                first_pressure = machine.pressure

                second_step = feed(second, first, 1.01)
                self.assertTrue(second_step.hold)
                self.assertGreater(machine.pressure, first_pressure)


    def test_first_scoped_event_uses_its_delta_to_locate_the_previous_point(self):
        machine = CrossingEngine(
            resistance_px=20,
            ways=[Ways("peer", frozenset({"edge"}), "right", frozenset(), "top_right",
                       "0", "peer-display", "left")],
        )
        step = machine.feed(105, 50, 10, 0, (0, 0, 200, 100), 1.0,
                            displays=[(0, 0, 100, 100), (100, 0, 200, 100)])
        self.assertTrue(step.hold)
        self.assertEqual(step.pin, (99, 50))
        self.assertAlmostEqual(machine.pressure, 6.0)

    def test_scoped_motion_misses_do_not_hold_and_first_wall_event_keeps_legacy_threshold(self):
        displays = [(0, 0, 100, 100), (100, 0, 200, 100)]
        machine = CrossingEngine(
            resistance_px=20,
            ways=[Ways("peer", frozenset({"edge"}), "right", frozenset(), "top_right", "0")],
        )
        machine.feed(50, 50, 0, 0, (0, 0, 200, 100), 0.99, displays=displays)
        ordinary = machine.feed(60, 50, 10, 0, (0, 0, 200, 100), 1.0, displays=displays)
        self.assertFalse(ordinary.hold)
        self.assertFalse(ordinary.crossed)
        self.assertEqual(machine.pressure, 0.0)

        machine.reset()
        first_wall = machine.feed(99, 50, 10, 0, (0, 0, 200, 100), 1.1, displays=displays)
        self.assertFalse(first_wall.hold)
        self.assertFalse(first_wall.crossed)
        self.assertEqual(machine.pressure, 10.0)
        self.assertEqual(first_wall.pin, (99, 50))

    def test_diagonal_overshoot_that_misses_the_display_corner_does_not_hold(self):
        displays = [(0, 0, 100, 100), (100, 0, 200, 100)]
        machine = CrossingEngine(
            resistance_px=20,
            ways=[Ways("peer", frozenset({"edge"}), "right", frozenset(), "top_right", "0")],
        )
        machine.feed(95, 95, 0, 0, (0, 0, 200, 100), 0.99, displays=displays)
        miss = machine.feed(105, 105, 10, 10, (0, 0, 200, 100), 1.0, displays=displays)
        self.assertFalse(miss.hold)
        self.assertFalse(miss.crossed)
        self.assertEqual(machine.pressure, 0.0)

    def test_scoped_corner_one_wall_overshoot_uses_corner_pressure_and_first_hit_pin(self):
        displays = [
            (0, 0, 100, 100), (100, 0, 200, 100), (200, 0, 300, 100),
            (100, 100, 200, 200),
        ]
        machine = CrossingEngine(
            resistance_px=20,
            ways=[Ways("peer", frozenset({"corner"}), "right", frozenset(),
                       "bottom_right", "1")],
        )
        machine.feed(195, 95, 0, 0, (0, 0, 300, 200), 0.99, displays=displays)
        step = machine.feed(205, 99, 10, 4, (0, 0, 300, 200), 1.0, displays=displays)
        self.assertTrue(step.hold)
        self.assertEqual(step.pin, (199, 97))
        self.assertAlmostEqual(machine.pressure, (10 ** 2 + 4 ** 2) ** 0.5)

        machine.reset()
        at_wall = machine.feed(199, 97, 10, 4, (0, 0, 300, 200), 1.1, displays=displays)
        self.assertFalse(at_wall.crossed)
        self.assertAlmostEqual(machine.pressure, (10 ** 2 + 4 ** 2) ** 0.5)

    def test_one_event_overshoot_still_obeys_thirds_reversal_dragging_and_readiness(self):
        displays = [(0, 0, 100, 100), (100, 0, 200, 100)]
        bounds = (0, 0, 200, 100)
        way = Ways("peer", frozenset({"part"}), "right", frozenset({"middle"}), "top_right",
                   "0", "peer-display", "left")

        def new_engine():
            return CrossingEngine(resistance_px=20, ways=[way])

        machine = new_engine()
        machine.feed(95, 10, 0, 0, bounds, 0.99, displays=displays)
        outside_third = machine.feed(105, 10, 10, 0, bounds, 1.0, displays=displays)
        self.assertFalse(outside_third.hold)
        self.assertEqual(machine.pressure, 0.0)

        machine = new_engine()
        machine.feed(95, 50, 0, 0, bounds, 0.99, displays=displays)
        machine.feed(105, 50, 10, 0, bounds, 1.0, displays=displays)
        reverse = machine.feed(95, 50, -10, 0, bounds, 1.01, displays=displays)
        self.assertFalse(reverse.crossed)
        self.assertFalse(reverse.hold)
        self.assertEqual(machine.pressure, 0.0)

        machine = new_engine()
        machine.feed(95, 50, 0, 0, bounds, 0.99, displays=displays)
        dragging = machine.feed(105, 50, 10, 0, bounds, 1.0, dragging=True, displays=displays)
        self.assertFalse(dragging.hold)
        self.assertEqual(machine.pressure, 0.0)

        machine = CrossingEngine(resistance_px=10, block_while_dragging=False, ways=[way])
        machine.feed(95, 50, 0, 0, bounds, 0.99, displays=displays)
        first = machine.feed(105, 50, 10, 0, bounds, 1.0, dragging=True, displays=displays)
        self.assertTrue(first.hold)
        crossed = machine.feed(99, 50, 10, 0, bounds, 1.01, dragging=True, displays=displays)
        self.assertTrue(crossed.crossed)

        machine = new_engine()
        machine.feed(95, 50, 0, 0, bounds, 0.99, displays=displays)
        unready = machine.feed(105, 50, 10, 0, bounds, 1.0, displays=displays,
                               ready=lambda _peer: False)
        self.assertFalse(unready.hold)
        self.assertEqual(machine.pressure, 0.0)

    def test_diagonal_seam_overshoot_uses_the_seam_intersection_for_thirds(self):
        displays = [(0, 0, 100, 100), (100, 0, 200, 100)]
        machine = CrossingEngine(
            resistance_px=20,
            ways=[Ways("peer", frozenset({"part"}), "right", frozenset({"middle"}),
                       "top_right", "0", "peer-display", "left")],
        )
        machine.feed(95, 30, 0, 0, (0, 0, 200, 100), 0.99, displays=displays)
        step = machine.feed(105, 75, 10, 45, (0, 0, 200, 100), 1.0, displays=displays)
        self.assertTrue(step.hold)
        self.assertEqual(step.pin, (99, 52.5))

    def test_scoped_horizontal_and_vertical_departures_use_source_display_offsets(self):
        cases = (
            ("left", [(0, 0, 100, 100), (0, 100, 100, 200)],
             (0, 0, 100, 200), "1", (0, 149.5), (-10, 0)),
            ("bottom", [(0, 0, 100, 100), (100, 0, 200, 100)],
             (0, 0, 200, 100), "1", (149.5, 99), (0, 10)),
        )
        for edge, displays, bounds, source_display, point, delta in cases:
            with self.subTest(edge=edge):
                machine = CrossingEngine(
                    resistance_px=0,
                    ways=[Ways("peer", frozenset({"edge"}), edge, frozenset(), "top_right",
                               source_display, "peer-display", "left")],
                )
                x, y = point
                dx, dy = delta
                machine.feed(x, y, 0, 0, bounds, 0.99, displays=displays)
                step = machine.feed(x, y, dx, dy, bounds, 1.0, displays=displays)
                self.assertTrue(step.crossed)
                self.assertAlmostEqual(step.offset, 0.5, places=3)

    def test_scoped_corner_uses_its_display_when_both_corner_walls_are_internal(self):
        displays = [(0, 0, 100, 100), (100, 0, 200, 100),
                    (200, 0, 300, 100), (100, 100, 200, 200)]
        machine = CrossingEngine(
            resistance_px=15,
            ways=[Ways("peer", frozenset({"corner"}), "right", frozenset(), "bottom_right",
                       "1", "peer-display", "left")],
        )
        machine.feed(199, 99, 0, 0, (0, 0, 300, 200), 0.99, displays=displays)
        step = machine.feed(205, 105, 10, 10, (0, 0, 300, 200), 1.0, displays=displays)
        self.assertTrue(step.hold)
        self.assertEqual(step.pin, (199, 99))
        step = machine.feed(199, 99, 10, 10, (0, 0, 300, 200), 1.01, displays=displays)
        self.assertTrue(step.crossed)
        self.assertEqual((step.via, step.mac_edge, step.display, step.side),
                         ("corner", "right", "peer-display", "left"))

    def test_unknown_scoped_corner_does_not_fall_back_to_desktop_corner(self):
        displays = [(0, 0, 100, 100), (100, 0, 200, 100)]
        machine = CrossingEngine(
            ways=[Ways("peer", frozenset({"corner"}), "right", frozenset(), "bottom_right",
                       "missing", "peer-display", "left")],
        )
        step = machine.feed(199, 99, 10, 10, (0, 0, 200, 100), 1.0, displays=displays)
        self.assertFalse(step.crossed)
        self.assertEqual(machine.pressure, 0.0)

    def test_scoped_corner_box_without_wall_contact_does_not_hold(self):
        displays = [(0, 0, 100, 100), (100, 0, 200, 100),
                    (200, 0, 300, 100), (100, 100, 200, 200)]
        machine = CrossingEngine(
            ways=[Ways("peer", frozenset({"corner"}), "right", frozenset(), "bottom_right",
                       "1", "peer-display", "left")],
        )
        step = machine.feed(194, 94, 0, 0, (0, 0, 300, 200), 1.0, displays=displays)
        self.assertFalse(step.hold)
        self.assertEqual(machine.pressure, 0.0)

    def test_the_bottom_of_a_short_display_beside_a_tall_one_crosses(self):
        tall_short = [(0, 0, 100, 300), (100, 0, 200, 100)]
        step = self.feed_at(engine(edge="bottom"), 150.0, 99.0, 0, 20, tall_short, (0, 0, 200, 300))
        self.assertTrue(step.crossed)

    def test_part_of_the_edge_counts_its_thirds_on_the_pointers_display(self):
        machine = engine(methods=("part",), parts=("middle",))
        step = self.feed_at(machine, 2143.0, 1440 + 1117 / 2, 20, 0, self.UNDER, self.UNDER_BOUNDS)
        self.assertTrue(step.crossed)
        machine = engine(methods=("part",), parts=("middle",))
        step = self.feed_at(machine, 2143.0, 1450.0, 20, 0, self.UNDER, self.UNDER_BOUNDS)
        self.assertFalse(step.crossed)

    def test_an_arrival_never_lands_in_a_gap(self):
        staggered = [(0, 0, 1920, 1080), (1920, 0, 3200, 720)]
        x, y = CrossingEngine.arrival_point("right", 0.9, (0, 0, 3200, 1080), displays=staggered)
        self.assertTrue(any(d[0] <= x < d[2] and d[1] <= y < d[3] for d in staggered), (x, y))

    def test_an_arrival_lands_on_whichever_of_two_stacked_displays_holds_it(self):
        stacked = [(0, 0, 1512, 982), (0, 982, 1920, 2062)]
        x, y = CrossingEngine.arrival_point("left", 0.9, (0, 0, 1920, 2062), displays=stacked)
        self.assertEqual(x, 2.0)
        self.assertGreater(y, 982)

    def test_an_arrival_with_no_display_named_lands_where_it_crossed_as_on_the_pc(self):
        # rc16 bug hunt (11-10-2026): a switch naming no display (a 1.5.1 peer, or a picture without
        # this Mac's displays) laid the whole edge's fraction across one display, about a display's
        # width from where the pointer crossed. return_edge.arrival_position keeps the point.
        side_by_side = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]
        x, y = CrossingEngine.arrival_point("top", 0.49, (0, 0, 3840, 1080), displays=side_by_side)
        self.assertAlmostEqual(x, 0.49 * 3839, delta=1.0)
        self.assertEqual(y, 2.0)
        stacked = [(0, 0, 1920, 1080), (0, 1080, 1920, 2160)]
        x, y = CrossingEngine.arrival_point("left", 0.25, (0, 0, 1920, 2160), displays=stacked)
        self.assertAlmostEqual(y, 0.25 * 2159, delta=1.0)
        self.assertEqual(x, 2.0)



class CornerAndEdgeTests(unittest.TestCase):
    def test_a_straight_push_in_the_corners_box_crosses_by_the_edge(self):
        machine = engine(methods=("corner", "edge"), corner="top_right")
        step = drive(machine, RIGHT_X, 3.0, 40, 0, 20)
        self.assertTrue(step.crossed)
        self.assertEqual(step.via, "edge")

    def test_a_diagonal_push_there_is_still_the_corner(self):
        machine = engine(methods=("corner", "edge"), corner="top_right")
        step = drive(machine, RIGHT_X, 0.0, 30, -30, 20)
        self.assertTrue(step.crossed)
        self.assertEqual(step.via, "corner")


if __name__ == "__main__":
    unittest.main()
