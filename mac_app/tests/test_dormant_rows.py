import base64
import copy
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mac_app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import machines
import machines_panel
import pages
from core import pairing, protocol, settings_sync
from test_crossing_page_machines import Page, SEA, token


TOKEN = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
PEER = protocol.id_text(bytes(range(20, 36)))


def entry(**fields):
    return {"id": PEER, "name": "Desk", "token": TOKEN, "host": "192.0.2.10", "port": 24820,
            "platform": "windows", "send": True, "allow_drive": True, "linked": True, **fields}


class DormantRowTests(unittest.TestCase):
    def test_row_shows_needs_repair_and_the_saved_reason_even_when_offline(self):
        row = machines.row(entry(dormant=True, dormant_reason="forgotten"), "Desk", False, "unreachable", "offline",
                           False, False, False, True)
        self.assertEqual((row.state.key, row.state.word, row.state.detail),
                         ("dormant", "Needs re-pair", "The other machine no longer has this pairing."))

    def test_wrong_id_has_a_distinct_reason(self):
        row = machines.row(entry(dormant=True, dormant_reason="wrong_id"), "Desk", False, "wrong_id", "refused",
                           False, False, False, True)
        self.assertEqual(row.state.detail, "The saved pairing now identifies a different machine.")

    def test_dormant_machines_mark_overview_and_crossing_for_attention(self):
        self.assertEqual(pages.dots(True, True, "", dormant=True),
                         {"overview": "amber", "crossing": "amber"})

    def test_re_pair_opens_the_existing_code_sheet_for_the_saved_address(self):
        panel = machines_panel.MachinesPanel.__new__(machines_panel.MachinesPanel)
        panel.controller = mock.Mock()
        panel.controller.book.peers.return_value = [entry(dormant=True, dormant_reason="forgotten")]
        panel.controller.cfg.hide_addresses = False
        panel.owner = mock.Mock()
        panel.find_field = mock.Mock()
        panel.find_secret = mock.Mock()
        panel.code_boxes = mock.Mock()
        panel.refresh = mock.Mock()
        panel.open = False
        panel.chosen = None
        panel._heard_key = None
        panel.service = None
        panel._refresh_nearby = mock.Mock()

        panel.re_pair(TOKEN)

        self.assertTrue(panel.open)
        self.assertEqual(panel.find_field.setStringValue_.call_args_list,
                         [mock.call(""), mock.call("192.0.2.10")])
        panel.refresh.assert_called_once_with()
        panel.code_boxes.focus.assert_called_once_with(panel.owner.window)


class DormantCrossingTests(Page):
    def build_dormant(self, **fields):
        window = self.build([(PEER, TOKEN, "Desk", {"side": "right", "dormant": True,
                                                 "dormant_reason": "forgotten", **fields}),
                           (SEA, token(150), "Other", {"side": "left"})],
                          [{"peer": PEER, "kind": "edge"}, {"peer": SEA, "kind": "edge"}])
        window._live = lambda peer: peer != PEER
        window._show_arrangement()
        return window

    def test_persisted_dormant_peer_keeps_an_offline_ledger_row(self):
        window = self.build_dormant()
        self.assertNotIn(PEER, window.atlas_canvas.tiles)
        row = window.crossing_ledger.rows[PEER]
        self.assertEqual(row.status_text, "Windows · Offline, ways kept")
        self.assertTrue(row.dimmed)
        window._machine_picked(PEER)
        self.assertEqual(window.chosen_peer, PEER)
        self.assertTrue(window.crossing_ledger.rows[PEER].selected)
        self.assertNotIn(PEER, window.atlas_canvas.tiles)

    def test_dormant_layout_and_intent_survive_another_machine_moving(self):
        window = self.build_dormant()
        settings = self.store.current()
        own = settings["machine_id"]
        settings["layout"] = {own: {"x": 0, "y": 0, "w": 160, "h": 100},
                              PEER: {"x": 160, "y": 0, "w": 160, "h": 100},
                              SEA: {"x": -160, "y": 0, "w": 160, "h": 100}}
        settings["layout_set_at"] = 10
        self.store.save_settings(settings)
        window._show_arrangement([])
        tiles = copy.deepcopy(window.atlas_canvas.tiles)
        tiles[SEA]["y"] = 33
        window._atlas_layout_changed(tiles, SEA, snap=False)
        current = self.store.current()
        self.assertEqual(current["layout"][PEER], settings["layout"][PEER])
        self.assertEqual(next(peer for peer in current["peers"] if peer["id"] == PEER)["ways"],
                         next(peer for peer in settings["peers"] if peer["id"] == PEER)["ways"])
        self.assertFalse(any(zone["peer"] == PEER for zone in current["zones"]))

    def test_incoming_layout_accepts_dormant_position_and_keeps_intent(self):
        window = self.build_dormant()
        settings = self.store.current()
        own = settings["machine_id"]
        settings["layout"] = {own: {"x": 0, "y": 0, "w": 160, "h": 100},
                              PEER: {"x": 160, "y": 0, "w": 160, "h": 100},
                              SEA: {"x": 320, "y": 0, "w": 160, "h": 100}}
        self.store.save_settings(settings)
        settings = self.store.current()
        original = copy.deepcopy(settings)
        incoming = copy.deepcopy(settings["layout"])
        incoming[PEER]["x"] += 160
        state = {"tiles": incoming, "set_at": settings.get("layout_set_at", 0) + 1, "by": SEA}
        data = settings_sync.message_data(False, 0, by=settings["machine_id"], layout_state=state)
        window._design_peer_caps = lambda: {SEA: {"settings", "layout"}}

        with mock.patch.object(window, "refresh"):
            self.assertTrue(window._receive_layout(data, SEA))

        current = self.store.current()
        self.assertEqual(current["layout"][PEER], incoming[PEER])
        self.assertEqual(next(peer for peer in current["peers"] if peer["id"] == PEER)["ways"],
                         next(peer for peer in original["peers"] if peer["id"] == PEER)["ways"])
        self.assertFalse(any(zone["peer"] == PEER for zone in current["zones"]))

    def test_a_sleeping_peer_is_kept_on_the_canvas(self):
        window = self.build([(PEER, TOKEN, "Desk", {"side": "right"})])
        self.assertIn(PEER, window.atlas_canvas.tiles)
        self.assertIn(PEER, window.crossing_ledger.rows)

    def test_existing_repair_path_opens_overview_and_prefills_saved_machine(self):
        window = self.build_dormant()
        window._re_pair_peer(PEER)
        self.assertEqual(window.page, "overview")
        self.assertTrue(window.panel.open)
        self.assertEqual(window.panel._address_text(), "192.0.2.30")

    def test_dormant_ledger_and_rename_fit_the_narrow_page(self):
        from mac_app.tests.design_offscreen import settle
        import theme

        theme.init_fonts()
        window = self.build_dormant(label="Office studio PC")
        window._machine_picked(PEER)
        window._select_page("crossing")
        window.window.setFrame_display_(((100000, 100000), (640, 900)), False)
        settle(window.window)
        window._begin_atlas_rename(PEER)
        settle(window.window)
        row = window.crossing_ledger.rows[PEER]
        self.assertTrue(row.renaming)
        self.assertFalse(row.name_field.isHidden())
        self.assertGreater(row.name_field.frame().size.width, 0)
        self.assertFalse(row.rename_hint.view.isHidden())
        body = window.pages["crossing"].documentView().subviews()[0]
        self.assertFalse(window.window.isVisible())
        self.assertGreater(body.frame().size.width, 0)
        self.assertLess(body.frame().size.width, window.pages["crossing"].frame().size.width)
        self.assertLessEqual(row.name_field.frame().origin.x + row.name_field.frame().size.width,
                             row.cells["machine"].bounds().size.width)
        for cell in row.cells.values():
            self.assertLessEqual(cell.frame().origin.x + cell.frame().size.width,
                                 window.crossing_ledger.bounds().size.width)

    def test_repair_keeps_label_layout_zones_and_left_out_after_reloading(self):
        self.build_dormant(label="Office", left_out=True, left_out_ways=["edge"])
        settings = copy.deepcopy(self.store.current())
        settings["layout"] = {settings["machine_id"]: {"x": 0, "y": 0, "w": 160, "h": 100},
                              PEER: {"x": 160, "y": 0, "w": 160, "h": 100}}
        self.store.save_settings(settings)
        old = copy.deepcopy(settings["peers"][0])
        repaired = pairing.peer_entry(protocol.read_id(PEER), "Desk", "macos", 24820,
                                      token(5), "192.0.2.20", 100)
        self.store.add_peer(repaired, old)
        saved = type(self.store)(self.store.path).current()
        peer = saved["peers"][0]
        self.assertEqual(peer["label"], "Office")
        self.assertTrue(peer["left_out"])
        self.assertNotIn("left_out_ways", peer)
        self.assertEqual(peer["ways"], old["ways"])
        self.assertEqual(saved["layout"], settings["layout"])
        self.assertEqual(saved["zones"], settings["zones"])
        self.assertNotIn("dormant", peer)


if __name__ == "__main__":
    unittest.main()
