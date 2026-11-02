"""Optional system-GTK helper: layer-shell is not part of Qt's Python bindings.

    https://wmww.github.io/gtk-layer-shell/
    A missing binding, compositor protocol or exact monitor match leaves Qt to fall back.
"""

import json
import sys


def run():
    import cairo
    import gi

    gi.require_foreign("cairo")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango, PangoCairo

    if not Gtk.init_check([])[0]:
        print("GTK could not open the Wayland display", file=sys.stderr, flush=True)
        return 1
    if not GtkLayerShell.is_supported():
        print("The Wayland compositor does not offer layer-shell", file=sys.stderr, flush=True)
        return 1
    assignments, palette, face = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
    display = Gdk.Display.get_default()
    monitors = [display.get_monitor(index) for index in range(display.get_n_monitors())]
    matched = []
    for assignment in assignments:
        monitor = next((monitor for monitor in monitors if
                        [monitor.get_geometry().x, monitor.get_geometry().y,
                         monitor.get_geometry().width, monitor.get_geometry().height] == assignment["geometry"]), None)
        if monitor is None:
            geometries = [[m.get_geometry().x, m.get_geometry().y,
                           m.get_geometry().width, m.get_geometry().height] for m in monitors]
            print(f"GTK monitor geometry mismatch: wanted {assignment['geometry']}, available {geometries}",
                  file=sys.stderr, flush=True)
            return 1
        monitors.remove(monitor)
        matched.append((monitor, assignment["number"]))

    def rgb(value):
        return tuple(int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))

    drawn = set()
    ready = False

    def draw(widget, context, number):
        nonlocal ready
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.set_source_rgba(0, 0, 0, 0)
        context.paint()
        context.set_operator(cairo.OPERATOR_OVER)
        size = widget.get_allocated_width()
        radius = 12
        for x, y, start in ((size-radius, radius, -90), (size-radius, size-radius, 0),
                            (radius, size-radius, 90), (radius, radius, 180)):
            import math
            context.arc(x, y, radius - 0.5, math.radians(start), math.radians(start + 90))
        context.close_path()
        context.set_source_rgb(*rgb(palette["panel"]))
        context.fill_preserve()
        context.set_source_rgb(*rgb(palette["edge"]))
        context.set_line_width(1)
        context.stroke()
        layout = PangoCairo.create_layout(context)
        font = Pango.FontDescription.from_string(face)
        font.set_absolute_size(112 * Pango.SCALE)
        font.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(font)
        layout.set_text(str(number), -1)
        _, ink = layout.get_pixel_extents()
        context.move_to((size - ink.width) / 2 - ink.x, (size - ink.height) / 2 - ink.y)
        context.set_source_rgb(*rgb(palette["ink"]))
        PangoCairo.show_layout(context, layout)
        drawn.add(widget)
        if not ready and len(drawn) == len(windows):
            ready = True
            focus = {"focus_window": any(window.has_toplevel_focus() for window in windows),
                     "active_window": any(window.is_active() for window in windows),
                     "active_overlay": any(window.has_focus() for window in windows)}
            print("focus " + json.dumps(focus), flush=True)
            if any(focus.values()):
                Gtk.main_quit()
                return False
            print("ready", flush=True)
            GLib.timeout_add(3000, lambda: (Gtk.main_quit(), False)[1])
        return False

    windows = []
    for monitor, number in matched:
        # Layer-shell supplies the role; Gtk toplevels wait for a separate WM configure.
        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_accept_focus(False)
        window.set_focus_on_map(False)
        window.set_decorated(False)
        window.set_app_paintable(True)
        visual = window.get_screen().get_rgba_visual()
        if visual is not None:
            window.set_visual(visual)
        window.set_default_size(208, 208)
        GtkLayerShell.init_for_window(window)
        GtkLayerShell.set_namespace(window, "beamer-identify")
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_monitor(window, monitor)
        GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(window, -1)
        window.connect("draw", draw, number)
        window.connect("realize", lambda widget: widget.input_shape_combine_region(cairo.Region()))
        windows.append(window)
    for window in windows:
        window.show_all()

    Gtk.main()
    for window in windows:
        window.destroy()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (ImportError, ValueError, RuntimeError, AttributeError) as exc:
        print(f"Layer-shell helper {type(exc).__name__}: {str(exc)[:512]}", file=sys.stderr, flush=True)
        raise SystemExit(1)
