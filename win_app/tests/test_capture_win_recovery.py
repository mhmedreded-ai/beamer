"""Secure desktop returns repair translation state without inventing wire events."""

import ctypes
import unittest
from unittest.mock import Mock, patch

import capture_win
import desktop_win


class ModifierRecoveryTests(unittest.TestCase):
    class HookData(ctypes.Structure):
        _fields_ = [
            ("vkCode", ctypes.c_ulong),
            ("scanCode", ctypes.c_ulong),
            ("flags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    def setUp(self):
        self.events = []
        self.hooks = capture_win.Hooks(lambda *event: self.events.append(event) or True, Mock(), Mock())
        self.user32 = Mock()
        self.user32.CallNextHookEx.return_value = 99
        self.user32.GetKeyState.return_value = 0
        self.user32.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if vk in self.held else 0
        self.held = set()
        self.default_desktop = True
        self.enterContext(patch.object(capture_win, "user32", self.user32))
        self.enterContext(patch.object(capture_win, "KBDLLHOOKSTRUCT", self.HookData))
        self.enterContext(patch.object(desktop_win, "_on_default_desktop", side_effect=lambda: self.default_desktop))

    def tick(self, default):
        self.default_desktop = default
        self.hooks._window_proc(10, 0x8001, 0, 0)

    def key(self, vk, scan, down=True, flags=0):
        data = self.HookData(vk, scan, flags, 0, 0)
        return self.hooks._keyboard_proc(
            capture_win.HC_ACTION, capture_win.WM_KEYDOWN if down else capture_win.WM_KEYUP, ctypes.byref(data),
        )

    def run_hooks(self):
        with patch.object(capture_win, "kernel32", Mock()), \
                patch.object(capture_win, "_LL_HOOK_PROC", side_effect=lambda callback: callback), \
                patch.object(capture_win, "_WNDPROC", side_effect=lambda callback: callback), \
                patch.object(capture_win, "_WINEVENTPROC", side_effect=lambda callback: callback, create=True), \
                patch.object(capture_win, "MSG", ctypes.c_long), \
                patch.object(capture_win, "_create_message_window", return_value=10), \
                patch.object(capture_win, "_register_raw_mouse"), \
                patch.object(capture_win, "_unregister_window_class"):
            self.hooks._run()

    def test_ctrl_sides_with_and_without_extended_flag_stay_independent(self):
        for flags in (0, capture_win.LLKHF_EXTENDED):
            with self.subTest(flags=flags):
                self.events.clear()
                self.key(0xA2, 0x1D)
                self.key(0xA3, 0x1D, flags=flags)
                self.key(0xA3, 0x1D, False, flags)
                self.key(0xA2, 0x1D, False)
                self.assertEqual([(e[0], e[1]) for e in self.events], [
                    ("cmd", True), ("cmd_r", True), ("cmd_r", False), ("cmd", False),
                ])
                self.assertEqual(self.hooks._key_names, {})

    def test_numlock_vk_change_releases_the_held_keypad_press(self):
        for press, release in ((0x61, 0x23), (0x23, 0x61)):
            with self.subTest(press=press):
                self.events.clear()
                self.key(press, 0x4F)
                self.key(release, 0x4F, False)
                self.assertEqual([(e[0], e[1]) for e in self.events], [
                    ("numpad_1", True), ("numpad_1", False),
                ])
                self.assertEqual(self.hooks._key_names, {})

    def test_main_and_keypad_enter_can_be_held_together(self):
        self.key(0x0D, 0x1C)
        self.key(0x0D, 0x1C, flags=capture_win.LLKHF_EXTENDED)
        self.key(0x0D, 0x1C, False)
        self.key(0x0D, 0x1C, False, capture_win.LLKHF_EXTENDED)
        self.assertEqual([(e[0], e[1]) for e in self.events], [
            ("enter", True), ("numpad_enter", True), ("enter", False), ("numpad_enter", False),
        ])
        self.assertEqual(self.hooks._key_names, {})

    def test_altgr_without_e0_does_not_reuse_held_left_alt(self):
        self.key(0xA4, 0x38)
        self.key(0xA2, 0x21D)
        self.key(0xA5, 0x38)
        self.key(0xA2, 0x21D, False)
        self.key(0xA5, 0x38, False)
        self.key(0xA4, 0x38, False)
        self.assertEqual([(e[0], e[1]) for e in self.events], [
            ("alt", True), ("alt_r", True), ("alt_r", False), ("alt", False),
        ])
        self.assertFalse(self.hooks._state.altgr_down)
        self.assertEqual(self.hooks._state.ctrl_down, set())
        self.assertEqual(self.hooks._key_names, {})

    def test_return_repairs_lost_ctrl_and_altgr_releases_before_another_key(self):
        self.key(0xA2, 0x1D)
        self.key(0xA5, 0x38)
        before = list(self.events)

        self.tick(False)
        self.tick(True)

        self.assertEqual(self.hooks._state.ctrl_down, set())
        self.assertFalse(self.hooks._state.altgr_down)
        self.assertEqual(self.events, before)

    def test_secure_desktop_zeroes_do_not_release_genuinely_held_modifiers(self):
        self.key(0xA2, 0x1D)
        self.key(0xA3, 0x1D)
        self.key(0xA5, 0x38)
        before = list(self.events)

        self.tick(False)
        self.tick(False)

        self.assertEqual(self.hooks._state.ctrl_down, {0xA2, 0xA3})
        self.assertTrue(self.hooks._state.altgr_down)
        self.assertEqual(self.events, before)
        self.user32.GetAsyncKeyState.assert_not_called()

    def test_return_preserves_held_ctrl_side_and_its_eventual_real_release(self):
        self.key(0xA2, 0x1D)
        self.key(0xA3, 0x1D)
        self.tick(False)
        self.held = {0xA3}

        self.tick(True)
        self.assertEqual(self.hooks._state.ctrl_down, {0xA3})
        self.key(0xA3, 0x1D, False)

        self.assertEqual(self.events, [
            ("cmd", True, 0xA2, None), ("cmd_r", True, 0xA3, None), ("cmd_r", False, 0xA3, None),
        ])
        self.assertEqual(self.hooks._state.ctrl_down, set())

    def test_return_with_held_altgr_does_not_promote_its_synthetic_ctrl(self):
        self.key(0xA2, 0x21D)
        self.key(0xA5, 0x38)
        self.tick(False)
        self.held = {0xA2, 0xA5}

        self.tick(True)
        self.assertEqual(self.hooks._state.ctrl_down, set())
        self.assertTrue(self.hooks._state.altgr_down)
        self.key(0xA5, 0x38, False)

        self.assertEqual(self.events, [("alt_r", True, 0xA5, None), ("alt_r", False, 0xA5, None)])

    def test_return_restores_dead_key_composition_after_a_lost_ctrl_release(self):
        self.key(0xA2, 0x1D)
        self.tick(False)
        self.tick(True)
        self.events.clear()

        def french(vk, _scan, _state):
            return capture_win.DeadKey("^") if vk == 0xDD else "e"

        with patch.object(capture_win, "_to_unicode", side_effect=french):
            self.key(0xDD, 0x1A)
            self.key(0xDD, 0x1A, False)
            self.key(0x45, 0x12)
            self.key(0x45, 0x12, False)

        self.assertIsInstance(self.events[0][0], capture_win.DeadKey)
        self.assertEqual(self.events[2:], [("ê", True, 0x45, "e"), ("ê", False, 0x45, "e")])

    def test_return_restores_plain_text_after_a_lost_altgr_release(self):
        self.key(0xA5, 0x38)
        self.tick(False)
        self.tick(True)
        self.events.clear()

        def layout(_vk, _scan, state):
            return "@" if state.get(0xA5) else "q"

        with patch.object(capture_win, "_to_unicode", side_effect=layout):
            self.key(0x51, 0x10)
            self.key(0x51, 0x10, False)

        self.assertEqual(self.events, [("q", True, 0x51, "q"), ("q", False, 0x51, "q")])

    def test_unrelated_window_message_does_not_resample_suppressed_modifier_presses(self):
        self.key(0xA2, 0x1D)
        self.key(0xA5, 0x38)

        self.hooks._window_proc(10, 0x0113, 1, 0)

        self.assertEqual(self.hooks._state.ctrl_down, {0xA2})
        self.assertTrue(self.hooks._state.altgr_down)
        self.user32.GetAsyncKeyState.assert_not_called()

    def test_hook_lifecycle_listens_for_desktop_return_and_releases_the_listener(self):
        notifications = []
        lifetime = []

        def listen(first, last, module, callback, process, thread, flags):
            self.assertEqual((first, last, module, process, thread, flags), (0x20, 0x20, None, 0, 0, 0))
            notifications.append(callback)
            return 30

        def dispatch(_message, _window, _first, _last):
            if notifications:
                self.key(0xA2, 0x1D)
                self.key(0xA5, 0x38)
                self.default_desktop = False
                notifications[0](30, 0x20, None, 0, 0, 0, 0)
                self.default_desktop = True
                notifications[0](30, 0x20, None, 0, 0, 0, 0)
            return 0

        self.user32.SetWindowsHookExW.side_effect = (20, 21)
        self.user32.SetWinEventHook.side_effect = listen
        self.user32.GetMessageW.side_effect = dispatch
        self.user32.PostMessageW.side_effect = lambda *args: self.hooks._window_proc(*args) or 1
        self.user32.UnhookWinEvent.side_effect = lambda handle: lifetime.append(
            ("unhook", handle, notifications[0] in self.hooks._callbacks),
        )
        self.user32.DestroyWindow.side_effect = lambda handle: lifetime.append(("destroy", handle))
        self.run_hooks()

        self.assertEqual(len(notifications), 1)
        self.assertIsNone(self.hooks._failure)
        self.assertEqual(self.hooks._state.ctrl_down, set())
        self.assertFalse(self.hooks._state.altgr_down)
        self.assertEqual(self.events, [("cmd", True, 0xA2, None), ("alt_r", True, 0xA5, None)])
        self.assertEqual(lifetime, [("unhook", 30, True), ("destroy", 10)])
        self.assertEqual(self.hooks._callbacks, [])

    def test_failed_desktop_listener_registration_removes_input_hooks(self):
        self.user32.SetWindowsHookExW.side_effect = (20, 21)
        self.user32.SetWinEventHook.return_value = 0
        self.user32.GetMessageW.return_value = 0

        with self.assertLogs(capture_win.LOGGER, level="ERROR"):
            self.run_hooks()

        self.assertIsInstance(self.hooks._failure, RuntimeError)
        self.assertTrue(self.hooks._ready.is_set())
        self.assertEqual([call.args for call in self.user32.UnhookWindowsHookEx.call_args_list], [(20,), (21,)])
        self.user32.UnhookWinEvent.assert_not_called()
        self.user32.DestroyWindow.assert_called_once_with(10)
        self.assertEqual(self.hooks._callbacks, [])

    def test_unavailable_desktop_listener_api_removes_input_hooks(self):
        self.user32.SetWindowsHookExW.side_effect = (20, 21)
        del self.user32.SetWinEventHook
        self.user32.GetMessageW.return_value = 0

        with self.assertLogs(capture_win.LOGGER, level="ERROR"):
            self.run_hooks()

        self.assertIsInstance(self.hooks._failure, AttributeError)
        self.assertEqual([call.args for call in self.user32.UnhookWindowsHookEx.call_args_list], [(20,), (21,)])
        self.user32.DestroyWindow.assert_called_once_with(10)
        self.assertEqual(self.hooks._callbacks, [])

    def test_message_loop_failure_releases_the_desktop_listener(self):
        self.user32.SetWindowsHookExW.side_effect = (20, 21)
        self.user32.SetWinEventHook.return_value = 30
        self.user32.GetMessageW.side_effect = RuntimeError("message pump failed")

        with self.assertLogs(capture_win.LOGGER, level="ERROR"):
            self.run_hooks()

        self.assertEqual(str(self.hooks._failure), "message pump failed")
        self.user32.UnhookWinEvent.assert_called_once_with(30)
        self.user32.DestroyWindow.assert_called_once_with(10)
        self.assertEqual(self.hooks._callbacks, [])


if __name__ == "__main__":
    unittest.main()
