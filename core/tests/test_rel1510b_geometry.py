"""Queued motion must retain the crossing decisions made by individual samples."""

import unittest
from types import SimpleNamespace

from core import protocol, receiver, return_edge
from core.tests.responder_harness import B, HERE, FakeDesktop, Machine, entry


class ReceiverQueuedGeometryTests(unittest.TestCase):
    def deliver(self, moves, pointer, model, queued, sender=False, calls=None, display_map=None, input_scale=None, held_button=None):
        desktop = FakeDesktop(cursor=pointer)
        if display_map is not None:
            desktop.display_map = lambda: display_map
        machine = Machine([entry(B, "Driver")], desktop=desktop)
        link = receiver._Link(
            SimpleNamespace(shutdown=lambda _how: None), None, B, None,
            "127.0.0.1", "Driver", [], HERE, [], True,
        )
        responder = machine.responder
        responder.input_scale = input_scale
        responder._owner = link
        responder._armed = [(B, model)] if model is not None else []
        responder._zones_live = True
        if held_button is not None:
            responder._dispatch(link, {"type": protocol.MSG_MOUSEDOWN,
                                       "data": {"button": held_button, "seq": 1}})
            self.assertIn(("button", held_button), link.held)
            machine.injector.calls.clear()
        original = machine.injector.inject_mouse_move

        def move_pointer(dx, dy):
            original(dx, dy)
            x, y = desktop.cursor
            bounds = desktop.monitors()[0]
            desktop.cursor = (
                min(bounds.right, max(bounds.x, x + dx)),
                min(bounds.bottom, max(bounds.y, y + dy)),
            )

        machine.injector.inject_mouse_move = move_pointer
        messages = [protocol.mousemove_msg(dx, dy, seq)
                    for seq, (dx, dy) in enumerate(moves, 1)]
        if sender:
            pending = messages[0]
            for following in messages[1:]:
                pending = protocol.coalesce_mousemove(pending, following)
                self.assertIsNotNone(pending)
            messages = [pending]
        if queued:
            for message in messages:
                link.receive(message, len(protocol.dumps_message(message)))
            messages = [item[0] for item in link.inbox]
        for message in messages:
            responder._dispatch(link, message)
        switches = [message for message in link.outbox
                    if message.get("type") == protocol.MSG_SWITCH]
        if calls is not None:
            calls.extend(machine.injected())
        return desktop.cursor, len(switches)

    def test_interior_drag_keeps_each_native_path_segment(self):
        paths = [
            [(20, 0), (0, 20), (-20, 0), (0, -20)],
            [(20, 0), (-20, 0)],
            [(0, 20), (0, -20)],
            [(20, 0), (0, 20)],
            [(10, 10), (10, 5)],
        ]
        for moves, speed in ((path, speed) for path in paths for speed in (None, 1.0)):
            with self.subTest(moves=moves, speed=speed):
                direct_calls, batch_calls = [], []
                direct_scale = None if speed is None else receiver.InputScale(speed)
                batch_scale = None if speed is None else receiver.InputScale(speed)
                direct = self.deliver(moves, (500, 500), None, False, calls=direct_calls,
                                      input_scale=direct_scale, held_button="left")
                batch = self.deliver(moves, (500, 500), None, True, sender=True, calls=batch_calls,
                                     input_scale=batch_scale, held_button="left")
                self.assertEqual(direct_calls, [("move", dx, dy) for dx, dy in moves])
                self.assertEqual(batch, direct)
                self.assertEqual(batch_calls, direct_calls)

    def test_unarmed_wall_reversals_keep_each_native_clipping_step(self):
        for moves, pointer in [
            ([(5, 0), (-5, 0)], (1919, 500)),
            ([(-5, 0), (5, 0)], (0, 500)),
            ([(0, 5), (0, -5)], (500, 1079)),
            ([(0, -5), (0, 5)], (500, 0)),
        ]:
            with self.subTest(pointer=pointer):
                self.assertEqual(self.deliver(moves, pointer, None, True, sender=True),
                                 self.deliver(moves, pointer, None, False))

    def test_a_long_bent_drag_collapses_to_one_native_move(self):
        # GH15c: replaying every bend of a 1000 Hz drag let 1.5.10 fall seconds behind.
        moves = [(2, 0), (0, 2), (-2, 0), (0, -1)] * 10
        for speed in (None, 1.4):
            with self.subTest(speed=speed):
                direct_scale = None if speed is None else receiver.InputScale(speed)
                batch_scale = None if speed is None else receiver.InputScale(speed)
                direct = self.deliver(moves, (500, 500), None, False,
                                      input_scale=direct_scale, held_button="left")
                calls = []
                batch = self.deliver(moves, (500, 500), None, True, sender=True, calls=calls,
                                     input_scale=batch_scale, held_button="left")
                self.assertEqual(batch, direct)
                self.assertEqual(len(calls), 1)
                if speed is not None:
                    self.assertEqual(batch_scale._carry, direct_scale._carry)

    def test_a_long_path_near_a_wall_collapses_to_one_clipped_native_move(self):
        moves = [(3, 1), (-1, 2), (4, -1), (-2, -2), (3, 0)] * 8
        calls = []
        cursor, switches = self.deliver(moves, (1900, 500), None, True, sender=True, calls=calls)
        self.assertEqual((cursor, switches), ((1919, 500), 0))
        self.assertEqual(calls, [("move", 20, 2), ("move", 36, -2)])

    def test_a_long_path_at_an_armed_edge_still_crosses(self):
        moves = [(1, 1), (1, -1)] * 20
        calls = []
        result = self.deliver(moves, (1915, 500),
                              return_edge.ReturnEdge("right", resistance_px=10, clock=lambda: 0),
                              True, sender=True, calls=calls)
        self.assertEqual(result, ((1919, 500), 1))
        self.assertEqual(calls, [("move", 4, 0)])

    def test_unarmed_interior_and_parallel_wall_paths_still_batch_native_calls(self):
        for pointer in ((500, 500), (1919, 500)):
            with self.subTest(pointer=pointer):
                calls = []
                result = self.deliver([(0, 1), (0, -1)] * 32, pointer, None,
                                      True, sender=True, calls=calls)
                self.assertEqual(result, (pointer, 0))
                self.assertLessEqual(len(calls), 1)

    def test_interior_batch_preserves_per_sample_scaling_and_fractional_carry(self):
        for speed in (.6, 1.4):
            with self.subTest(speed=speed):
                direct_scale, batch_scale = receiver.InputScale(speed), receiver.InputScale(speed)
                direct = self.deliver([(2, 0), (-1, 0)], (500, 500), None, False, input_scale=direct_scale)
                calls = []
                batched = self.deliver([(2, 0), (-1, 0)], (500, 500), None, True,
                                       sender=True, calls=calls, input_scale=batch_scale)
                self.assertEqual(batched, direct)
                self.assertEqual(batch_scale._carry, direct_scale._carry)
                self.assertEqual(len(calls), 1)

    def test_scaled_carry_and_display_edge_tolerance_do_not_hide_a_crossing(self):
        mapping = {"inner": return_edge.Rect(0, 0, 1000, 1080)}
        def run(queued):
            scale = receiver.InputScale(1.4)
            scale._carry = [.8, 0]
            return self.deliver([(1, 0), (1, 0)], (996, 500),
                                return_edge.ReturnEdge("right", resistance_px=1, clock=lambda: 0, display="inner"),
                                queued, sender=queued, display_map=mapping, input_scale=scale)
        self.assertEqual(run(False), ((998, 500), 1))
        self.assertEqual(run(True), run(False))

    def test_bounded_near_edge_path_sweep_matches_original_sample_geometry(self):
        paths = [
            [(1, 0), (1, 0)], [(2, 0), (-1, 0)], [(0, 1), (1, 0)],
            [(1, 1), (1, -1)], [(-1, 1), (2, 0)], [(1, 2), (2, 1)],
            [(0, 2), (0, -1)], [(2, 1), (1, 2)],
        ]
        mapping = {"inner": return_edge.Rect(0, 0, 1000, 1080)}
        models = [
            lambda: return_edge.ReturnEdge("right", resistance_px=3, clock=lambda: 0, display="inner"),
            lambda: return_edge.PartEdge("right", ["start"], resistance_px=3, clock=lambda: 0, display="inner"),
            lambda: return_edge.CornerPush("bottom_right", "right", resistance_px=3, clock=lambda: 0, display="inner"),
        ]
        for model_index, model in enumerate(models):
            for x in (996, 998, 999):
                for y in (358, 360, 1078):
                    for speed in (.6, 1.0, 1.4, 3.7):
                        for carry in (-.8, .8):
                            for moves in paths:
                                with self.subTest(model=model_index, pointer=(x, y), speed=speed, carry=carry, moves=moves):
                                    direct_scale, batch_scale = receiver.InputScale(speed), receiver.InputScale(speed)
                                    direct_scale._carry = [carry, -carry]
                                    batch_scale._carry = [carry, -carry]
                                    direct = self.deliver(moves, (x, y), model(), False,
                                                          display_map=mapping, input_scale=direct_scale)
                                    batch = self.deliver(moves, (x, y), model(), True, sender=True,
                                                         display_map=mapping, input_scale=batch_scale)
                                    self.assertEqual(batch, direct)
                                    self.assertEqual(batch_scale._carry, direct_scale._carry)

    def test_parallel_wall_jitter_outside_selected_part_batches_native_injection(self):
        calls = []
        result = self.deliver([(0, 1), (0, -1)] * 32, (1919, 500),
                              return_edge.PartEdge("right", ["start"], resistance_px=120, clock=lambda: 0),
                              True, sender=True, calls=calls)
        self.assertEqual(result, ((1919, 500), 0))
        self.assertLessEqual(len(calls), 2)

    def test_first_cross_discards_the_rest_of_its_batched_path(self):
        calls = []
        self.assertEqual(self.deliver([(1, 0), (-500, -300)], (1919, 500),
                                     return_edge.ReturnEdge("right", resistance_px=1, clock=lambda: 0),
                                     True, sender=True, calls=calls), ((1919, 500), 1))
        self.assertEqual(calls, [])

    def test_tangent_motion_at_an_active_part_keeps_each_actual_pointer_position(self):
        class TrackedPart(return_edge.PartEdge):
            def feed(self, monitors, pointer, dx, dy, display_map=None):
                seen.append(pointer)
                return super().feed(monitors, pointer, dx, dy, display_map=display_map)
        seen = []
        model = TrackedPart("right", ["start"], resistance_px=120, clock=lambda: 0)
        model._travel = 100
        self.deliver([(0, 20), (0, -20)], (1919, 350), model, True, sender=True)
        self.assertEqual(seen, [(1919, 350), (1919, 370)])

    def test_larger_overlapping_display_does_not_hide_an_armed_display_edge(self):
        mapping = {"inner": return_edge.Rect(0, 0, 1000, 1080)}
        def model():
            return return_edge.PartEdge("right", ["start"], resistance_px=120, clock=lambda: 0, display="inner")
        direct = self.deliver([(1, 4), (200, 1)], (999, 358), model(), False, display_map=mapping)
        self.assertEqual(self.deliver([(1, 4), (200, 1)], (999, 358), model(), True,
                                     sender=True, display_map=mapping), direct)

    def test_queued_diagonal_motion_does_not_cross_after_leaving_selected_part(self):
        def run(queued):
            return self.deliver(
                [(1, 4), (200, 1)], (1919, 358),
                return_edge.PartEdge("right", ["start"], resistance_px=120, clock=lambda: 0),
                queued,
            )

        direct = run(False)
        self.assertEqual(direct, ((1919, 363), 0))
        self.assertEqual(run(True), direct)

    def test_sender_coalesced_path_keeps_selected_part_and_corner_geometry(self):
        cases = [
            ([(1, 4), (200, 1)], (1919, 358),
             lambda: return_edge.PartEdge("right", ["start"], resistance_px=120, clock=lambda: 0)),
            ([(119, 1), (1, 119)], (1919, 1079),
             lambda: return_edge.CornerPush("bottom_right", "right", resistance_px=200, clock=lambda: 0)),
            ([(120, 0), (0, 120)], (1919, 1079),
             lambda: return_edge.CornerPush("bottom_right", "right", resistance_px=120, clock=lambda: 0)),
        ]
        for moves, pointer, model in cases:
            with self.subTest(moves=moves):
                self.assertEqual(self.deliver(moves, pointer, model(), True, sender=True),
                                 self.deliver(moves, pointer, model(), False))

    def test_queued_diagonal_motion_keeps_corner_push_pressure(self):
        def run(queued):
            return self.deliver(
                [(119, 1), (1, 119)], (1919, 1079),
                return_edge.CornerPush("bottom_right", "right", resistance_px=200, clock=lambda: 0),
                queued,
            )

        direct = run(False)
        self.assertEqual(direct, ((1919, 1079), 1))
        self.assertEqual(run(True), direct)


if __name__ == "__main__":
    unittest.main()
