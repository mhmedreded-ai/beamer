import unittest
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kvm_bridge_app
from core import effects
import config as mac_config


class LengthSizeSliderTests(unittest.TestCase):
    def test_native_slider_exposes_accessibility_and_arrow_step(self):
        calls = []
        control = kvm_bridge_app._DesignSliderChoice(
            "Length", 25, 250, 5, 100, (0.5, 1.0, 2.0), effects.LENGTHS,
            lambda owner, sender: calls.append(sender.doubleValue()), "Sets playback time.")
        slider = control.slider
        self.assertIsInstance(slider, kvm_bridge_app.AppKit.NSSlider)
        self.assertEqual(slider.accessibilityLabel(), "Length")
        self.assertIn("playback time", slider.accessibilityHelp())
        control.set_accessible_value("1.0 s")
        self.assertEqual(slider.accessibilityValueDescription(), "1.0 s")
        slider.keyDown_(mock.Mock(keyCode=lambda: 124))
        self.assertEqual(slider.doubleValue(), 105)
        self.assertEqual(calls, [105.0])

    def test_slider_keyboard_steps_cover_arrows_page_and_range_ends(self):
        key_value = kvm_bridge_app.design_slider_key_value
        self.assertEqual(key_value(123, 100, 25, 250, 5, 25), 95)
        self.assertEqual(key_value(124, 100, 25, 250, 5, 25), 105)
        self.assertEqual(key_value(125, 100, 25, 250, 5, 25), 95)
        self.assertEqual(key_value(126, 100, 25, 250, 5, 25), 105)
        self.assertEqual(key_value(116, 100, 25, 250, 5, 25), 125)
        self.assertEqual(key_value(121, 100, 25, 250, 5, 25), 75)
        self.assertEqual(key_value(115, 100, 25, 250, 5, 25), 25)
        self.assertEqual(key_value(119, 100, 25, 250, 5, 25), 250)

    def test_drag_detent_and_fixed_width_readouts(self):
        self.assertEqual(effects.snap_detent(54, 25, 250, (50, 100, 200)), 50)
        length, size = effects.design_slider_readouts("beam", 2.0, 0.06, 1920, 1080)
        self.assertEqual(length, "1.2 s")
        self.assertTrue(size.endswith(" pt"))
        constraint = mock.Mock()
        anchor = mock.Mock()
        anchor.constraintEqualToConstant_.return_value = constraint
        view = mock.Mock()
        view.widthAnchor.return_value = anchor
        label = mock.Mock(view=view)
        with mock.patch.object(kvm_bridge_app.widgets, "Label", return_value=label):
            readout = kvm_bridge_app.design_readout()
        self.assertIs(readout, label)
        anchor.constraintEqualToConstant_.assert_called_once_with(effects.DESIGN_READOUT_WIDTH)
        constraint.setActive_.assert_called_once_with(True)
        self.assertEqual(effects.play_through_seconds("beam", 2.0), 1.2)

    def test_preset_only_mac_settings_migrate_to_both_forms(self):
        config = mac_config.parse_config({
            "host": "192.0.2.1", "port": 51820, "auth_token": "synthetic",
            "crossing": {"effect_length": "long", "effect_size": "small"},
        })
        crossing = config.crossing
        self.assertEqual((crossing["effect_pace"], crossing["effect_size_fraction"],
                          crossing["effect_length"], crossing["effect_size"]),
                         (2.0, 0.03, "long", "small"))


if __name__ == "__main__":
    unittest.main()
