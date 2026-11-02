"""Mac settings decisions with in-memory configuration and no AppKit window."""

from unittest import mock

from core.tests import settings_behaviour
from core.tests.settings_behaviour import SettingsBehaviourTests as _SettingsBehaviourTests


class MacSettingsBehaviourTests(_SettingsBehaviourTests):
    platform = "mac"

    def setUp(self):
        super().setUp()
        self.app._methods = lambda: [name for name, tile in self.app.method_boxes.items() if tile.value]

    def test_shared_clash_note_stays_visible_when_shortcut_is_switched_off(self):
        load_methods = settings_behaviour.load_methods

        def load_reflect(path, class_name, names, namespace):
            # The shared fixture's unrelated-UI stop moved to the map refresh in the Mac ledger.
            self.app._show_arrangement = namespace["pages"].part_names
            return load_methods(path, class_name, names, namespace)

        with mock.patch.object(settings_behaviour, "load_methods", side_effect=load_reflect):
            super().test_shared_clash_note_stays_visible_when_shortcut_is_switched_off()


del _SettingsBehaviourTests
