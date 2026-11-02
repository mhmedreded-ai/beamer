"""The mounted Shortcut tile follows its destination choice and saved value."""

from test_crossing_page_machines import BEE, SEA, Page, token
from settings_store import config_to_raw


class ShortcutSubtitle(Page):
    def test_destination_choices_refresh_the_mounted_tile_and_reload(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"}),
                             (SEA, token(150), "Sea", {"side": "left"})])
        window.key_recorder.value = "alt_r"
        window.style_select.value = "double_tap"
        tile = window.method_boxes["shortcut"]
        for mode, ending in (("next", "next in order"), ("ticked", "next among ticked"),
                             ("last", "to where input last went")):
            with self.subTest(mode=mode):
                window.shortcut_goes_to._choose(mode)
                self.assertEqual(tile.detail.view.stringValue(), f"Right Option, {ending}")
                window._apply_settings()
                self.assertEqual(self.store.current()["crossing"]["shortcut_goes_to"], mode)
                self.assertIsNotNone(tile.view.superview())
                self.assertFalse(tile.detail.view.isHidden())
                window._load_shared(config_to_raw(self.store.load()))
                self.assertEqual(tile.detail.view.stringValue(), f"Right Option, {ending}")
