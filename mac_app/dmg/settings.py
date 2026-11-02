# dmgbuild settings for Beamer's disk image: the app, a link to Applications and a background
# that says what to do. build_dmg.sh passes -D app=<Beamer.app> -D background=<background.png>;
# dmgbuild finds background@2x.png beside it for Retina screens.

import os.path

application = defines["app"]  # noqa: F821 - dmgbuild provides `defines`
background = defines["background"]  # noqa: F821

format = "UDZO"
files = [application]
symlinks = {"Applications": "/Applications"}

# The same centres draw_background.py draws the shelf and the arrow for, in a 640 x 400 window.
icon_locations = {os.path.basename(application): (170, 240), "Applications": (470, 240)}
window_rect = ((200, 160), (640, 400))
default_view = "icon-view"
icon_size = 128
text_size = 13
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
