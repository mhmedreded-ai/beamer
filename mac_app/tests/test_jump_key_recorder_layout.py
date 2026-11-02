import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import AppKit

AppKit.NSApplication.sharedApplication()

import theme
import widgets


class JumpKeyRecorderLayoutTests(unittest.TestCase):
    def test_input_method_toggles_are_not_shortcut_triggers(self):
        self.assertTrue({"hangul", "hanja"} <= widgets.UNRECORDABLE_TRIGGERS)

    def test_empty_key_uses_body_type_and_the_hint_sits_below(self):
        recorder = widgets.JumpKeyRecorder()
        font = recorder.key.view.attributedStringValue().attribute_atIndex_effectiveRange_(
            AppKit.NSFontAttributeName, 0, None
        )[0]

        self.assertEqual(font.pointSize(), theme.TYPE["body"])
        self.assertNotIn("Mono", font.fontName())
        self.assertEqual(recorder.line.orientation(), AppKit.NSUserInterfaceLayoutOrientationVertical)


if __name__ == "__main__":
    unittest.main()
