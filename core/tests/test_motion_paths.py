"""A coalesced mouse frame retains the path needed by edge and corner geometry."""

import unittest

from core import protocol


class MotionPathTests(unittest.TestCase):
    def test_coalescing_keeps_original_mixed_axis_segments(self):
        merged = protocol.coalesce_mousemove(protocol.mousemove_msg(119, 1, 1),
                                             protocol.mousemove_msg(1, 119, 2))
        self.assertEqual(merged["data"].get("samples"), [[119, 1], [1, 119]])
        self.assertEqual(protocol.read_input(merged).get("samples"), [[119, 1], [1, 119]])

    def test_malformed_or_unbounded_paths_are_rejected(self):
        for samples in ([], [[1]], [[True, 1]], [[1, 1]] * 65, [[65536, 0]], [[2, 1]]):
            with self.subTest(samples=samples):
                message = protocol.mousemove_msg(1, 1, 1)
                message["data"]["samples"] = samples
                self.assertIsNone(protocol.read_input(message))

    def test_a_path_past_its_limit_collapses_to_the_latest_position(self):
        # GH15c: refusing the 65th sample is what let a 1000 Hz hand queue seconds of motion.
        merged = protocol.mousemove_msg(1, 1, 1)
        for seq in range(2, 65):
            merged = protocol.coalesce_mousemove(merged, protocol.mousemove_msg(1, 1, seq))
            self.assertIsNotNone(merged)
        merged = protocol.coalesce_mousemove(merged, protocol.mousemove_msg(-3, 2, 65))
        self.assertEqual((merged["data"]["dx"], merged["data"]["dy"], merged["data"]["seq"]), (61, 66, 65))
        self.assertEqual(merged["data"]["samples"], [[61, 66]])
        self.assertEqual(protocol.read_input(merged)["samples"], [[61, 66]])
        merged = protocol.coalesce_mousemove(merged, protocol.mousemove_msg(0, -1, 66))
        self.assertEqual(merged["data"]["samples"], [[61, 66], [0, -1]])

    def test_legacy_coalescing_does_not_erase_diagonal_path(self):
        self.assertIsNone(protocol.coalesce_mousemove(protocol.mousemove_msg(119, 1, 1),
                                                     protocol.mousemove_msg(1, 119, 2), preserve_path=False))


if __name__ == "__main__":
    unittest.main()
