"""Whether a newer Beamer has been released, for both apps.

One HTTPS request to GitHub's public releases API at startup and once a day while automatic
checks are on, or on an explicit check. All newer releases appear on the Updates page. Only
important patches and newer minor/major versions raise a notice; nothing downloads or installs.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from typing import NamedTuple

LATEST_URL = "https://api.github.com/repos/kalkman-code/beamer/releases/latest"
RELEASES_PAGE = "https://github.com/kalkman-code/beamer/releases/latest"
CHECK_EVERY_SECONDS = 24 * 60 * 60
TIMEOUT_SECONDS = 10.0
MAX_BYTES = 256 * 1024

LOGGER = logging.getLogger(__name__)
_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-beta\.(\d+))?$")


class Release(NamedTuple):
    version: str
    url: str
    notify: bool = True


def _parse(text):
    """((major, minor, patch), is_beta) or None."""
    match = _VERSION.match(str(text or "").strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups()[:3]), match.group(4) is not None


def parse_version(text):
    """(major, minor, patch) for "1.4", "1.4.0", "v1.4.0" or a beta such as "1.5.0-beta.1" (which
    reads as its release), else None: a local "dev" build or a tag in any other shape never reads as
    older or newer than anything."""
    parsed = _parse(text)
    return parsed[0] if parsed else None


def newer_release(current, payload):
    """Release(version, url, notify) when the API's JSON names a release worth offering over
    `current`, else None. Every newer stable version counts, including patch releases. Drafts and
    prereleases never count, nor does a tag that names a beta. A beta install is offered its own
    release (1.5.0-beta.1 to 1.5.0) and anything newer, never an older one."""
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        return None
    tag = payload.get("tag_name")
    theirs, ours = _parse(tag), _parse(current)
    if theirs is None or ours is None or theirs[1]:
        return None
    theirs, (ours, beta) = theirs[0], ours
    if theirs < ours or (theirs == ours and not beta):
        return None
    url = payload.get("html_url")
    if not isinstance(url, str) or not url.startswith("https://github.com/kalkman-code/beamer/"):
        url = RELEASES_PAGE
    body = payload.get("body")
    important = isinstance(body, str) and any(
        line.strip() == "<!-- beamer-notify: important -->" for line in body.splitlines())
    return Release(str(tag).lstrip("v"), url, important or theirs[:2] > ours[:2])


def visible_notice(found, enabled, dismissed, requested=False):
    """Quiet releases stay on the Updates page, including after an explicit request.

    A requested alerting release resurfaces until dismissed again. Dismissal is per version,
    so skipping or dismissing a quiet patch cannot suppress a later important release.
    """
    return found if (enabled or requested) and found and found.notify and (requested or found[0] != dismissed) else None


def _portable_download(version, payload):
    name = f"Beamer-Portable-{version}-windows-x64.zip"
    url = f"https://github.com/kalkman-code/beamer/releases/download/v{version}/{name}"
    assets = payload.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if (isinstance(asset, dict) and asset.get("name") == name
                    and asset.get("browser_download_url") == url):
                return url
    return None


def fetch(url=LATEST_URL):
    """The response body as bytes. The Mac goes through Foundation, which trusts the system's
    certificates; a bundled Python has no certificate store of its own there."""
    if sys.platform == "darwin":
        import Foundation

        request = Foundation.NSMutableURLRequest.requestWithURL_cachePolicy_timeoutInterval_(
            Foundation.NSURL.URLWithString_(url), Foundation.NSURLRequestReloadIgnoringLocalCacheData, TIMEOUT_SECONDS)
        request.setValue_forHTTPHeaderField_("application/vnd.github+json", "Accept")
        data, response, error = Foundation.NSURLConnection.sendSynchronousRequest_returningResponse_error_(
            request, None, None)
        if data is None or error is not None or response is None or response.statusCode() != 200:
            raise OSError(f"update check failed: {error or (response.statusCode() if response else 'no response')}")
        return bytes(data)[:MAX_BYTES]
    import urllib.request

    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read(MAX_BYTES)


class Checker:
    """Checks now and every CHECK_EVERY_SECONDS on its own thread while `enabled()` says so, and
    calls `on_result(Release or None)` on that thread after each check that got an answer;
    the owner marshals it. A failed check keeps the last answer and logs once. Explicit checks use
    `on_check(state, found)` instead, also on the worker thread, so the UI can show progress/errors
    and display the requested answer even while automatic checks are off."""

    def __init__(self, current, enabled, on_result, fetcher=fetch, logger=None, portable=False,
                 on_check=None):
        self.current = current
        self.enabled = enabled
        self.on_result = on_result
        self.fetcher = fetcher
        self.logger = logger or LOGGER
        self.portable = portable
        self.on_check = on_check
        self.latest = None
        self.download_url = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._manual = threading.Event()
        self._thread = None
        self._failed = False

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="Beamer-updates", daemon=True)
            self._thread.start()

    def check_now(self, manual=False):
        """Wake the worker; an explicit check also works with automatic checks off."""
        if manual:
            self._manual.set()
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _checked(self, state, found=None):
        if self.on_check is not None:
            try:
                self.on_check(state, found)
            except Exception:
                self.logger.exception("manual update callback failed")

    def check_once(self, manual=False):
        if self._stop.is_set():
            return
        if parse_version(self.current) is None:
            if manual:
                self._checked("failed")
            return
        if not manual and not self.enabled():
            return
        if manual:
            self._checked("checking")
        try:
            payload = json.loads(self.fetcher().decode("utf-8"))
            if not isinstance(payload, dict) or _parse(payload.get("tag_name")) is None:
                raise ValueError("release response has no recognised version")
        except Exception as exc:
            if not self._failed:
                self.logger.info("could not check for a newer Beamer: %s", exc)
            self._failed = True
            if manual and not self._stop.is_set():
                self._checked("failed")
            return
        self._failed = False
        if (not manual and not self.enabled()) or self._stop.is_set():
            return
        self.latest = newer_release(self.current, payload)
        self.download_url = (_portable_download(self.latest[0], payload)
                             if self.portable and self.latest else None)
        if self.latest:
            self.logger.info("Beamer %s is available", self.latest[0])
        if manual:
            self._checked("available" if self.latest else "current", self.latest)
            return
        try:
            self.on_result(self.latest)
        except Exception:
            self.logger.exception("update result callback failed")

    def _run(self):
        self.check_once()
        while not self._stop.is_set():
            self._wake.wait(CHECK_EVERY_SECONDS)
            self._wake.clear()
            manual = self._manual.is_set()
            if manual:
                self._manual.clear()
            self.check_once(manual=manual)
