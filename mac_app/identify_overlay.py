"""Brief, click-through display labels. All AppKit work stays on the main thread."""

import time

import AppKit
from PyObjCTools import AppHelper

import desktop_mac
import theme
from core.identify_screens import OVERLAY_SECONDS, display_numbers


class IdentifyPanel(AppKit.NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class IdentifyOverlay:
    DURATION = OVERLAY_SECONDS
    SIZE = 220.0

    def __init__(self, logger):
        self.logger = logger
        self.panels = []
        self._generation = 0
        self._last_request_at = None

    def show(self, labels):
        now = time.monotonic()
        if self._last_request_at is not None and now - self._last_request_at < OVERLAY_SECONDS:
            return
        self._last_request_at = now
        self.hide()
        generation = self._generation
        try:
            displays = desktop_mac.display_map()
            if set(labels) != set(displays):
                labels = display_numbers({"x": 0, "y": 0, "w": 1, "h": 1, "displays": {
                    key: {"x": rect.x, "y": rect.y, "w": rect.width, "h": rect.height}
                    for key, rect in displays.items()}})
            primary_height = AppKit.NSScreen.screens()[0].frame().size.height
            for key, number in labels.items():
                rect = displays.get(key)
                if rect is None:
                    continue
                size = min(self.SIZE, rect.width, rect.height)
                frame = ((rect.x + (rect.width - size) / 2,
                          primary_height - rect.y - (rect.height + size) / 2), (size, size))
                panel = self._panel(frame, number)
                self.panels.append(panel)
                panel.orderFrontRegardless()
            AppHelper.callLater(self.DURATION, self._expire, generation)
        except Exception:
            self.hide()
            self.logger.exception("could not identify this Mac's displays")

    def _panel(self, frame, number):
        panel = IdentifyPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        panel.setReleasedWhenClosed_(False)
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
            | AppKit.NSWindowCollectionBehaviorStationary)
        view = panel.contentView()
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(theme.RADIUS["keycap"])
        view.layer().setBorderWidth_(1)
        theme.tint(view.layer(), background="panel", border="edge")
        label = AppKit.NSTextField.labelWithString_(str(number))
        label.setFont_(theme.mono_font(min(128, frame[1][0] * 0.58), 700))
        label.setTextColor_(theme.colour("ink"))
        label.setAlignment_(AppKit.NSTextAlignmentCenter)
        label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        view.addSubview_(label)
        AppKit.NSLayoutConstraint.activateConstraints_([
            label.centerXAnchor().constraintEqualToAnchor_(view.centerXAnchor()),
            label.centerYAnchor().constraintEqualToAnchor_(view.centerYAnchor()),
            label.widthAnchor().constraintLessThanOrEqualToAnchor_(view.widthAnchor()),
        ])
        panel.identify_number = number
        return panel

    def _expire(self, generation):
        if generation == self._generation:
            self.hide()

    def hide(self):
        self._generation += 1
        for panel in self.panels:
            panel.orderOut_(None)
            panel.close()
        self.panels.clear()
