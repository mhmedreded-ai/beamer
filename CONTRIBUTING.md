# Contributing

Beamer is written and kept up by one person, in spare time. Issues are welcome and read, but a
reply is not guaranteed.

## Bug reports

Use the bug report form, and attach the logs from both machines where you can:
`~/Library/Logs/Beamer/Beamer.log` on the Mac and `%LOCALAPPDATA%\Beamer\Beamer.log` on Windows.
They hold your machines' names and local network addresses, so look them over first. On Linux, use
Open log folder from Beamer's menu.

## Feature requests

Use the feature request form, or add a reaction to an existing request rather than a "+1"
comment. There is no roadmap and no promise that anything gets built. Beamer 1.5.0 supports Intel
Macs, experimental Linux on GNOME Wayland or X11, Mac-to-Mac and Windows-to-Windows pairs, and three machines
at once. Test builds appear on the releases page as pre-releases.

## Pull requests

Pull requests are welcome. Sign off every commit with `git commit -s`. The sign-off is the
[Developer Certificate of Origin](https://developercertificate.org): it confirms the change is your
own work, or that you have the right to submit it, and that you license it under GPL-3.0-only, the
licence Beamer is released under. There is no separate contributor agreement. For anything bigger
than a small, obvious fix, open an issue first so we agree the approach before you write code.

## Building it

The README's "Building from source" and "Development checks" sections cover macOS and Windows.
For the Linux Flatpak build, see `docs/flatpak-build.md`.
