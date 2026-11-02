"""Start at login, owned by the app through SMAppService: macOS keeps the record, so there is no
config field to disagree with it.

ServiceManagement is loaded from the system framework rather than through
pyobjc-framework-ServiceManagement, which would be a new dependency for four calls.
"""

from pathlib import Path

import objc
from core.i18n import N_, tr

objc.loadBundle(
    "ServiceManagement", {}, bundle_path="/System/Library/Frameworks/ServiceManagement.framework"
)
for _selector in (b"registerAndReturnError:", b"unregisterAndReturnError:"):
    objc.registerMetaDataForSelector(
        b"SMAppService", _selector, {"arguments": {2: {"type_modifier": objc._C_OUT}}}
    )
SMAppService = objc.lookUpClass("SMAppService")

NOT_REGISTERED, ENABLED, REQUIRES_APPROVAL, NOT_FOUND = 0, 1, 2, 3
STATUS_TEXT = {
    NOT_REGISTERED: N_("Not registered"),
    ENABLED: N_("Enabled"),
    REQUIRES_APPROVAL: N_("Requires approval"),
    NOT_FOUND: N_("Not found"),
}


def status():
    return SMAppService.mainAppService().status()


def status_text(value):
    return tr(STATUS_TEXT.get(value, N_("Unknown")))


def installed_in_applications(bundle_path):
    path = Path(bundle_path).resolve()
    if "/AppTranslocation/" in str(path):
        return False
    roots = (Path("/Applications"), Path.home() / "Applications")
    return any(path == root or root in path.parents for root in roots)


def can_toggle(status_value, bundle_path):
    return installed_in_applications(bundle_path) or status_value in (ENABLED, REQUIRES_APPROVAL)


def is_background_launch(is_default_launch):
    return is_default_launch is not None and not bool(is_default_launch)


def is_enabled():
    """Waiting for approval in Login Items counts as on: the choice was made here."""
    return status() in (ENABLED, REQUIRES_APPROVAL)


def set_enabled(enabled):
    """Raises OSError with macOS's own words when it refuses."""
    service = SMAppService.mainAppService()
    if enabled:
        ok, error = service.registerAndReturnError_(None)
    else:
        if service.status() not in (ENABLED, REQUIRES_APPROVAL):
            return
        ok, error = service.unregisterAndReturnError_(None)
    if not ok:
        raise OSError(error.localizedDescription() if error is not None else "macOS refused")


def open_system_settings():
    SMAppService.openSystemSettingsLoginItems()
