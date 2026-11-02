"""Physical screen labels shared by the Crossing map and temporary overlays."""

from core import ways

OVERLAY_SECONDS = 3.0


def display_numbers(tile):
    """Number a machine's displays in the map's top-to-bottom, left-to-right order."""
    if not tile or not tile.get("displays"):
        return {"0": 1}
    return {frame["display"]: index + 1
            for index, frame in enumerate(ways._display_frames(tile))}
