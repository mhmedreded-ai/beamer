"""The ordinary GUI never changes the firewall, even if a parent launched it elevated."""

import sys
import unittest
from types import SimpleNamespace
from unittest import mock


@unittest.skipUnless(sys.platform == "win32", "the app module needs Windows")
class InstallerOwnedFirewallTests(unittest.TestCase):
    def test_start_and_recheck_never_repair_rules_or_networks(self):
        import kvm_bridge_win as module

        for frozen in (False, True):
            for elevated in (False, True):
                events = []
                owner = SimpleNamespace(
                    update_checker=SimpleNamespace(start=lambda: events.append("updates")),
                    _listen=lambda: events.append("listen"),
                    _check_firewall=lambda: events.append("check"),
                    _firewall_advice=SimpleNamespace(action="repair", rule_profiles=("Private",)),
                )
                with self.subTest(frozen=frozen, elevated=elevated), \
                        mock.patch.object(module.sys, "frozen", frozen, create=True), \
                        mock.patch.object(module.firewall_win, "is_elevated", return_value=elevated), \
                        mock.patch.object(module.firewall_win, "repair") as repair, \
                        mock.patch.object(module.firewall_win, "trust_network") as trust:
                    module.WindowsApplication.start(owner)
                    module.WindowsApplication._firewall_action(owner)
                self.assertEqual(events, ["updates", "listen", "check"])
                repair.assert_not_called()
                trust.assert_not_called()


if __name__ == "__main__":
    unittest.main()
