import unittest

from core import pairing


class PortableDiscoveryTests(unittest.TestCase):
    def test_routed_find_answers_keep_one_machine_and_its_live_pairing_port(self):
        service = pairing.PairingService("Peer", b"b" * 16, "windows", lambda: 41000,
                                         lambda: [], lambda *args: None, bind_port=53000)
        discovery = pairing.Discovery()
        beacon = pairing.decode(service._beacon())
        for port in (53000, 51001, 51002):
            discovery._note_beacon(beacon, ("192.0.2.20", port))
        self.assertEqual(len(discovery.pcs()), 1)
        self.assertEqual(discovery.pcs()[0]["reply_port"], 53000)

    def test_legacy_routed_beacons_are_deduplicated_by_advertised_app_endpoint(self):
        discovery = pairing.Discovery()
        for port in (24821, 51001, 51002):
            discovery._note_beacon(pairing.beacon_msg("Legacy", 41000), ("192.0.2.20", port))
        self.assertEqual(len(discovery.pcs()), 1)

    def test_invalid_advertised_pairing_ports_are_rejected(self):
        discovery = pairing.Discovery()
        for port in (True, 0, 65536, "53000"):
            beacon = pairing.beacon_msg("Peer", 41000)
            beacon["pair_port"] = port
            discovery._note_beacon(beacon, ("192.0.2.20", 51000))
        self.assertEqual(discovery.pcs(), [])

    def test_two_apps_on_one_ip_remain_selectable_and_update_independently(self):
        discovery = pairing.PairingService("Observer", b"a" * 16, "windows", lambda: 24820,
                                           lambda: [], lambda *args: None)
        for port, name, listener in ((24821, "Installed", 24820), (53000, "Portable", 53001)):
            discovery._note_beacon(pairing.beacon_msg(name, listener), ("127.0.0.1", port))
        self.assertEqual({(pc["name"], pc["reply_port"], pc["port"]) for pc in discovery.pcs()},
                         {("Installed", 24821, 24820), ("Portable", 53000, 53001)})
        discovery._note_beacon(pairing.beacon_msg("Portable", 53002, "new-code"), ("127.0.0.1", 53000))
        self.assertEqual(len(discovery.pcs()), 2)
        portable = next(pc for pc in discovery.pcs() if pc["name"] == "Portable")
        self.assertEqual((portable["port"], portable["pair_id"]), (53002, "new-code"))

    def test_many_endpoints_on_typed_host_remain_bounded(self):
        discovery = pairing.Discovery()
        discovery._finding = ("127.0.0.1", 24821)
        for port in range(10000, 10000 + pairing.MAX_SEEN_HOSTS + 4):
            discovery._note_beacon(pairing.beacon_msg("Synthetic", port), ("127.0.0.1", port))
        self.assertEqual(len(discovery._seen), pairing.MAX_SEEN_HOSTS)
