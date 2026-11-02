import logging
import unittest

from kvm_bridge_app import TrayApp
from settings_store import config_to_raw, editable_default_config


class _FakeControlWindow:
    def __init__(self):
        self.loaded_raw = []

    def _load(self, raw):
        self.loaded_raw.append(raw)


class _FakeController:
    def __init__(self):
        self.cfg = None

    def update_config(self, cfg):
        self.cfg = cfg


class _FakeSettingsStore:
    def __init__(self, cfg):
        self._cfg = cfg

    def load(self):
        return self._cfg


class _FakeTrayApp:
    """Stand-in with just what TrayApp.reload_config reads from self."""

    reload_config = TrayApp.reload_config

    def __init__(self, cfg):
        self.logger = logging.getLogger("test-reload-config")
        self.settings_store = _FakeSettingsStore(cfg)
        self.control_window = _FakeControlWindow()
        self.controller = _FakeController()
        self.notified = []

    def notify_user(self, title, detail):
        self.notified.append((title, detail))


class ReloadConfigRefreshesWindowTest(unittest.TestCase):
    def test_reload_config_repopulates_the_open_window(self):
        """reload_config once only pushed the new config into the controller,
        so a page already open (Crossing's ways-in ticks, among others) kept
        showing whatever was on screen when the window was built, not what
        config.json now says."""
        cfg = editable_default_config()
        cfg.crossing["methods"] = ["edge"]
        app = _FakeTrayApp(cfg)

        app.reload_config(None)

        self.assertEqual(app.control_window.loaded_raw, [config_to_raw(cfg)])
        self.assertIs(app.controller.cfg, cfg)


if __name__ == "__main__":
    unittest.main()
