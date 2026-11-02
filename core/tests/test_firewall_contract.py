"""The shared shell accepts the Linux firewall stand-in's complete status contract."""

from types import SimpleNamespace
import unittest

from core.tests.app_methods import load_methods
from win_app.linux_stand_ins import FirewallStatus


class LinuxFirewallContractTests(unittest.TestCase):
    def test_a_covered_linux_status_can_update_the_shared_shell(self):
        roles = []
        shell = load_methods('win_app/kvm_bridge_win.py', 'WindowsApplication', ('_on_firewall',), {
            'portable_runtime': SimpleNamespace(enabled=lambda: False),
            'firewall_win': SimpleNamespace(advise=lambda _: SimpleNamespace(
                sentence='Synthetic firewall status', button='Check again', action=None)),
            'widgets': SimpleNamespace(set_role=lambda _widget, role: roles.append(role)),
        })()
        shell._closing = False
        shell._firewall_busy = True
        shell._firewall_again = False
        shell.firewall_note = SimpleNamespace(setText=lambda _: None)
        shell.firewall_button = SimpleNamespace(setText=lambda _: None, setEnabled=lambda _: None)
        status = FirewallStatus(24820, allowed=True, covered=True)
        shell._on_firewall(status)
        self.assertIs(shell._firewall_status, status)
        self.assertEqual(roles, ['note'])
        self.assertFalse(shell._firewall_busy)
