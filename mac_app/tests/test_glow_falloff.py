"""Inward alpha must fade before live and hosted glow layers meet their bounds."""

import sys
import math
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from beam_live_offscreen import live_glow, layer_profile, profile
import tokens
from core import effects


def assert_soft_end(values):
    assert values[0] > 10
    tail = values[int(len(values) * .8):] + [0]
    # A fractional band can leave the last sampled pixel more than half a pixel from zero;
    # Core Animation also dithers its 8-bit gradient by a few alpha levels.
    tolerance = math.ceil(2 * 255 / len(values)) + 2
    assert max(abs(a - b) for a, b in zip(tail, tail[1:])) <= tolerance
    assert values[-1] <= tolerance


@pytest.mark.parametrize("style", ["glow", "beam"])
@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
@pytest.mark.parametrize("scale", [1, 2])
@pytest.mark.parametrize("size", ["small", "medium", "large"])
def test_edges_fade_without_a_step_at_the_panel_boundary(style, edge, scale, size):
    assert_soft_end(profile(live_glow(edge, size, style=style), edge, scale))


@pytest.mark.parametrize("style", ["glow", "beam"])
@pytest.mark.parametrize("corner", ["top_left", "top_right", "bottom_left", "bottom_right"])
@pytest.mark.parametrize("scale", [1, 2])
def test_corner_walls_fade_without_a_step_at_the_layer_boundary(style, corner, scale):
    glow = live_glow("left", "medium", style=style, corner=corner)
    for wall, layer in zip(corner.split("_"), (glow.fill, glow.side)):
        assert_soft_end(layer_profile(layer, wall, scale))


@pytest.mark.parametrize("style", ["glow", "beam"])
def test_thirds_keep_the_inward_fade(style):
    assert_soft_end(profile(live_glow("left", "medium", style=style, third=True), "left", 2))


def test_style_changes_refresh_the_existing_inward_mask():
    import kvm_bridge_app

    glow = live_glow("left", "medium", style="beam")
    for style in ("glow", "beam", "glow"):
        glow.controller.cfg.crossing["glow_style"] = style
        with mock.patch.object(kvm_bridge_app.time, "monotonic", return_value=100):
            glow._draw()
        assert_soft_end(profile(glow, "left", 2))


@pytest.mark.parametrize("style", ["glow", "beam"])
def test_design_preview_uses_the_same_inward_fade(style):
    import AppKit
    import kvm_bridge_app
    import previews

    source = live_glow("right", "medium", style=style)
    screen = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 300, 180))
    glow = previews.hosted_edge(kvm_bridge_app.EdgeGlow)(source.controller, source.logger, screen)
    glow.timer = mock.Mock()
    glow.visible = True
    glow.mac_edge, glow.region, glow.centre = source.mac_edge, source.region, source.centre
    with mock.patch.object(kvm_bridge_app.time, "monotonic", return_value=100):
        glow._draw()
    assert_soft_end(layer_profile(glow.fill, "right", 2))


@pytest.mark.parametrize("style", ["glow", "beam"])
@pytest.mark.parametrize("colour", list(tokens.PALETTES) + list(effects.PACK_IDS))
def test_every_palette_keeps_the_inward_alpha_profile(style, colour):
    import kvm_bridge_app

    glow = live_glow("left", "medium", style=style)
    expected = profile(glow, "left", 2)
    glow.controller.cfg.crossing["glow_colour"] = colour
    with mock.patch.object(kvm_bridge_app.time, "monotonic", return_value=100):
        glow._draw()
    values = profile(glow, "left", 2)
    assert max(abs(a - b) for a, b in zip(expected, values)) <= 2
    assert_soft_end(values)


@pytest.mark.parametrize("style", ["glow", "beam"])
@pytest.mark.parametrize("pressure", [.1, .5, 1.0])
def test_pressure_and_menu_bar_depth_do_not_clip_the_fade(style, pressure):
    import kvm_bridge_app

    glow = live_glow("top", "medium", style=style, pressure=pressure)
    with mock.patch.object(kvm_bridge_app, "menu_bar_height", return_value=24), \
            mock.patch.object(kvm_bridge_app.time, "monotonic", return_value=100):
        glow._draw()
    assert_soft_end(profile(glow, "top", 2))


def test_screen_colour_space_does_not_change_the_alpha():
    import Quartz

    glow = live_glow("left", "medium", style="glow")
    screen = profile(glow, "left", 2)
    srgb = profile(glow, "left", 2, Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB))
    assert max(abs(a - b) for a, b in zip(screen, srgb)) <= 2
