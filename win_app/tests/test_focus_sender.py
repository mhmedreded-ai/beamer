import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import protocol
from links_rig import B, Rig


class FocusSenderTests(unittest.TestCase):
    def test_send_preserves_the_mapped_display_and_side(self):
        rig = Rig()
        # The owner's internal form (core/owner.py _take): a raw machine id, which _send rebuilds for the wire.
        focus = {"type": protocol.MSG_FOCUS, "data": {"route": 1, "target": B, "edge": "right", "offset": 0.5,
                                                      "display": "macbook", "side": "left", "reach": []}}

        rig.sender._send(B, focus)

        sent = [message for peer, message in rig.links.sent if peer == B and message["type"] == protocol.MSG_FOCUS]
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0]["data"]["display"], sent[0]["data"]["side"]), ("macbook", "left"))


if __name__ == "__main__":
    unittest.main()
