"""Draws the DMG window's background, background.png and background@2x.png beside this file, from
Beamer's own palette and type. Run it again after changing the palette or the layout in
dmg_settings.py; the build only reads the PNGs.

Finder writes the icon names itself, white in Dark Mode and black in Light Mode, so the icons sit
on a shelf whose tone reads against both: `edge` has a contrast of about 5:1 with white and 4:1 with
black. Everything else is Beamer's dark ground."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

import AppKit  # noqa: E402

import theme  # noqa: E402
import tokens  # noqa: E402

# The window's content, in points; dmg_settings.py places the icons on these centres.
WIDTH, HEIGHT = 640, 400
APP_AT, APPLICATIONS_AT = (170, 240), (470, 240)
SHELF = (40, 150, 560, 210)


def colour(hex_value, alpha=1.0):
    r, g, b = theme.rgb(hex_value)
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alpha)


def text(words, size, weight, ink, centre_y):
    style = AppKit.NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(AppKit.NSTextAlignmentCenter)
    attributes = {
        AppKit.NSFontAttributeName: theme.font(size, weight),
        AppKit.NSForegroundColorAttributeName: colour(ink),
        AppKit.NSParagraphStyleAttributeName: style,
    }
    string = AppKit.NSAttributedString.alloc().initWithString_attributes_(words, attributes)
    height = string.size().height
    string.drawInRect_(AppKit.NSMakeRect(0, HEIGHT - centre_y - height / 2, WIDTH, height))


def draw(scale):
    palette = tokens.PALETTE
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, WIDTH * scale, HEIGHT * scale, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0
    )
    rep.setSize_((WIDTH, HEIGHT))
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))

    colour(palette["ground"]).setFill()
    AppKit.NSRectFill(AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT))

    text("Drag Beamer to Applications", 24, 600, palette["ink"], 58)
    text("Then open it from Applications. The first time, it asks for two permissions.",
         13, 400, palette["ink_2"], 92)

    x, y, w, h = SHELF
    shelf = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        AppKit.NSMakeRect(x, HEIGHT - y - h, w, h), 14, 14
    )
    gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
        colour("#76776e"), colour("#65665e")
    )
    gradient.drawInBezierPath_angle_(shelf, -90)
    # A lit top edge, so the shelf reads as a tray the icons sit in rather than a flat block.
    rim = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        AppKit.NSMakeRect(x + 0.5, HEIGHT - y - h + 0.5, w - 1, h - 1), 13.5, 13.5
    )
    colour("#8c8d83").set()
    rim.setLineWidth_(1)
    rim.stroke()

    # The arrow runs between the two icons at their centre height, dark on the shelf.
    start, end = APP_AT[0] + 84, APPLICATIONS_AT[0] - 84
    mid = HEIGHT - APP_AT[1]
    colour(palette["ground"]).set()
    line = AppKit.NSBezierPath.bezierPath()
    line.setLineWidth_(5)
    line.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    line.moveToPoint_((start, mid))
    line.lineToPoint_((end - 6, mid))
    line.stroke()
    head = AppKit.NSBezierPath.bezierPath()
    head.moveToPoint_((end + 6, mid))
    head.lineToPoint_((end - 14, mid + 13))
    head.lineToPoint_((end - 14, mid - 13))
    head.closePath()
    head.fill()

    AppKit.NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    name = "background.png" if scale == 1 else f"background@{scale}x.png"
    data.writeToFile_atomically_(str(HERE / name), True)
    return HERE / name


if __name__ == "__main__":
    AppKit.NSApplication.sharedApplication()
    theme.init_fonts()
    for scale in (1, 2):
        print(draw(scale))
