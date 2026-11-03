"""Persistent OAuth2 / Bearer Token Manager & Automatic HTTP 401 Retry Handler.

Addresses:
`Error: HTTP 401 Unauthorized` | `Www-Authenticate: Bearer realm="https://accounts.google.com/", error="invalid_token"`

Features:
- Persistent atomic caching of OAuth2 credentials (`access_token`, `refresh_token`, `expires_at`,
  `client_id`, `client_secret`, `token_uri`, `scope`) on disk.
- Proactive token refresh before expiration (`refresh_margin_seconds`, default 120s) so requests
  never use an expired or near-expired access token.
- Automatic HTTP 401 (`invalid_token` / `Unauthorized`) detection and transparent retry with forced
  token refresh under a re-entrant lock (`threading.RLock()`).
- Background daemon refresh loop for long-running tasks and workflows.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

LOGGER = logging.getLogger("Beamer.TokenManager")

DEFAULT_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_REFRESH_MARGIN_SECONDS = 120.0
DEFAULT_BACKGROUND_INTERVAL_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2

_WWW_AUTH_KV_RE = re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*(?:"([^"]*)"|([^\s,]+))')


class TokenRefreshError(RuntimeError):
    """Raised when an access token cannot be refreshed via any configured mechanism."""

    def __init__(self, message: str, status_code: int | None = None, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.cause = cause


def parse_www_authenticate(header_value: str | None) -> dict[str, str]:
    """Parses a `Www-Authenticate` header such as:
    `Bearer realm="https://accounts.google.com/", error="invalid_token", error_description="..."`
    """
    if not header_value or not isinstance(header_value, str):
        return {}
    result: dict[str, str] = {}
    stripped = header_value.strip()
    parts = stripped.split(None, 1)
    if parts:
        result["scheme"] = parts[0].lower()
    for match in _WWW_AUTH_KV_RE.finditer(stripped):
        key = match.group(1).lower()
        val = match.group(2) if match.group(2) is not None else (match.group(3) or "")
        result[key] = val
    return result


def is_unauthorized_error(exc: BaseException) -> bool:
    """Returns True if `exc` represents an HTTP 401 Unauthorized or `invalid_token` error."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 401:
            return True
        auth_hdr = exc.headers.get("Www-Authenticate") or exc.headers.get("WWW-Authenticate") if exc.headers else None
        parsed = parse_www_authenticate(auth_hdr)
        if parsed.get("error") == "invalid_token":
            return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None) or getattr(exc, "code", None)
    if status == 401:
        return True
    text = str(exc)
    if "401" in text and ("Unauthorized" in text or "invalid_token" in text):
        return True
    if 'error="invalid_token"' in text or "error='invalid_token'" in text:
        return True
    return False


class TokenManager:
    """Thread-safe persistent OAuth2 / Bearer token manager with proactive refresh and 401 retry."""

    def __init__(
        self,
        cache_path: str | Path | None = None,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        access_token: str | None = None,
        expires_at: float | None = None,
        token_uri: str = DEFAULT_GOOGLE_TOKEN_URI,
        scope: str | None = None,
        refresh_margin_seconds: float = DEFAULT_REFRESH_MARGIN_SECONDS,
        refresh_callback: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        http_sender: Callable[[urllib.request.Request, float], tuple[int, dict[str, str], bytes]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._cache_path = Path(cache_path) if cache_path is not None else None
        self._refresh_margin = max(5.0, float(refresh_margin_seconds))
        self._refresh_callback = refresh_callback
        self._http_sender = http_sender or self._default_http_sender

        self._state: dict[str, Any] = {
            "access_token": access_token or os.environ.get("BEAMER_ACCESS_TOKEN") or os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN") or "",
            "refresh_token": refresh_token or os.environ.get("BEAMER_REFRESH_TOKEN") or os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN") or "",
            "client_id": client_id or os.environ.get("BEAMER_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID") or "",
            "client_secret": client_secret or os.environ.get("BEAMER_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET") or "",
            "token_uri": token_uri or os.environ.get("BEAMER_TOKEN_URI") or DEFAULT_GOOGLE_TOKEN_URI,
            "scope": scope or os.environ.get("BEAMER_OAUTH_SCOPE") or "",
            "expires_at": float(expires_at) if expires_at is not None else 0.0,
            "updated_at": 0.0,
            "refresh_count": 0,
            "last_error": "",
        }

        if self._cache_path is not None:
            self._load_cache()
            if access_token is not None:
                self._state["access_token"] = access_token
            if refresh_token is not None:
                self._state["refresh_token"] = refresh_token
            if client_id is not None:
                self._state["client_id"] = client_id
            if client_secret is not None:
                self._state["client_secret"] = client_secret
            if expires_at is not None:
                self._state["expires_at"] = float(expires_at)
            self._save_cache()

        self._bg_stop = threading.Event()
        self._bg_thread: threading.Thread | None = None

    @staticmethod
    def _default_http_sender(req: urllib.request.Request, timeout: float = 15.0) -> tuple[int, dict[str, str], bytes]:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode() or 200
            headers = {k: v for k, v in resp.headers.items()} if resp.headers else {}
            body = resp.read()
            return int(status), headers, body

    def _load_cache(self) -> None:
        with self._lock:
            if self._cache_path is None or not self._cache_path.exists():
                return
            try:
                raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for key in (
                        "access_token",
                        "refresh_token",
                        "client_id",
                        "client_secret",
                        "token_uri",
                        "scope",
                        "expires_at",
                        "updated_at",
                        "refresh_count",
                    ):
                        if key in raw and raw[key] not in (None, ""):
                            self._state[key] = raw[key]
            except Exception as exc:
                LOGGER.warning("Failed to load token cache from %s: %s", self._cache_path, exc)

    def _save_cache(self) -> None:
        with self._lock:
            if self._cache_path is None:
                return
            try:
                self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(
                    prefix=".token_cache_",
                    suffix=".tmp",
                    dir=str(self._cache_path.parent),
                )
                try:
                    payload = json.dumps(self._state, indent=2)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp_name, self._cache_path)
                finally:
                    if os.path.exists(tmp_name):
                        try:
                            os.remove(tmp_name)
                        except OSError:
                            pass
            except Exception as exc:
                LOGGER.warning("Failed to persist token cache to %s: %s", self._cache_path, exc)

    def set_credentials(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_in: float | None = None,
        expires_at: float | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_uri: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """Updates stored credentials atomically and persists them to disk."""
        with self._lock:
            now = self._clock()
            if access_token is not None:
                self._state["access_token"] = str(access_token).strip()
            if refresh_token is not None:
                self._state["refresh_token"] = str(refresh_token).strip()
            if client_id is not None:
                self._state["client_id"] = str(client_id).strip()
            if client_secret is not None:
                self._state["client_secret"] = str(client_secret).strip()
            if token_uri is not None:
                self._state["token_uri"] = str(token_uri).strip()
            if scope is not None:
                self._state["scope"] = str(scope).strip()
            if expires_at is not None:
                self._state["expires_at"] = float(expires_at)
            elif expires_in is not None:
                self._state["expires_at"] = now + max(0.0, float(expires_in))
            self._state["updated_at"] = now
            self._state["last_error"] = ""
            self._save_cache()
            return self.status()

    def is_expired_or_expiring(self, now: float | None = None) -> bool:
        """Returns True if there is no access token or it will expire within `refresh_margin_seconds`."""
        with self._lock:
            token = self._state.get("access_token", "")
            if not token:
                return True
            expires_at = float(self._state.get("expires_at") or 0.0)
            if expires_at <= 0.0:
                return False
            current = self._clock() if now is None else now
            return (current + self._refresh_margin) >= expires_at

    def get_valid_token(self, *, force_refresh: bool = False) -> str:
        """Returns a valid access token, refreshing proactively if expired or near expiration."""
        with self._lock:
            if force_refresh or self.is_expired_or_expiring():
                if self.can_refresh():
                    return self.refresh_access_token()
            return str(self._state.get("access_token") or "")

    def authorization_header(self, *, force_refresh: bool = False) -> dict[str, str]:
        """Returns `{"Authorization": "Bearer <valid_token>"}`."""
        token = self.get_valid_token(force_refresh=force_refresh)
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def can_refresh(self) -> bool:
        with self._lock:
            if self._refresh_callback is not None:
                return True
            return bool(self._state.get("refresh_token"))

    def refresh_access_token(self) -> str:
        """Performs an immediate OAuth2 token refresh and persists the new token."""
        with self._lock:
            now = self._clock()
            try:
                if self._refresh_callback is not None:
                    response_data = self._refresh_callback(dict(self._state))
                else:
                    response_data = self._oauth2_refresh_request()

                new_access = str(response_data.get("access_token") or "").strip()
                if not new_access:
                    raise TokenRefreshError("Token endpoint response did not include 'access_token'")

                expires_in = float(response_data.get("expires_in", 3600.0))
                new_refresh = response_data.get("refresh_token")
                self._state["access_token"] = new_access
                if new_refresh:
                    self._state["refresh_token"] = str(new_refresh).strip()
                self._state["expires_at"] = now + max(1.0, expires_in)
                self._state["updated_at"] = now
                self._state["refresh_count"] = int(self._state.get("refresh_count", 0)) + 1
                self._state["last_error"] = ""
                self._save_cache()
                LOGGER.info(
                    "Refreshed OAuth2 access token successfully (refresh_count=%d, expires_in=%.0fs)",
                    self._state["refresh_count"],
                    expires_in,
                )
                return new_access
            except Exception as exc:
                self._state["last_error"] = str(exc)
                self._save_cache()
                if isinstance(exc, TokenRefreshError):
                    raise
                raise TokenRefreshError(f"Failed to refresh access token: {exc}", cause=exc) from exc

    def _oauth2_refresh_request(self) -> dict[str, Any]:
        refresh_tok = str(self._state.get("refresh_token") or "").strip()
        if not refresh_tok:
            raise TokenRefreshError("No refresh_token available to refresh expired access_token")

        token_uri = str(self._state.get("token_uri") or DEFAULT_GOOGLE_TOKEN_URI).strip()
        form: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
        }
        if self._state.get("client_id"):
            form["client_id"] = str(self._state["client_id"])
        if self._state.get("client_secret"):
            form["client_secret"] = str(self._state["client_secret"])
        if self._state.get("scope"):
            form["scope"] = str(self._state["scope"])

        encoded = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(
            token_uri,
            data=encoded,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            status, _headers, body = self._http_sender(req, 15.0)
        except urllib.error.HTTPError as http_err:
            err_body = ""
            try:
                err_body = http_err.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            raise TokenRefreshError(
                f"OAuth2 token refresh failed with HTTP {http_err.code}: {err_body or http_err.reason}",
                status_code=http_err.code,
                cause=http_err,
            ) from http_err

        if status < 200 or status >= 300:
            raise TokenRefreshError(f"OAuth2 token refresh returned HTTP {status}", status_code=status)

        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise TokenRefreshError("OAuth2 token endpoint returned non-JSON object")
        return parsed

    def execute_with_retry(
        self,
        operation: Callable[..., Any],
        *args: Any,
        max_retries: int = DEFAULT_MAX_RETRIES,
        inject_token_kwarg: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Executes `operation(*args, **kwargs)`.
        - Proactively refreshes the access token before calling `operation` if it is expired/expiring.
        - If `operation` raises HTTP 401 (`invalid_token` / `Unauthorized`), automatically forces a token
          refresh and retries up to `max_retries` times.
        """
        attempt = 0
        force_next = False
        while True:
            token = self.get_valid_token(force_refresh=force_next)
            force_next = False
            call_kwargs = dict(kwargs)
            if inject_token_kwarg:
                call_kwargs[inject_token_kwarg] = token
            try:
                return operation(*args, **call_kwargs)
            except Exception as exc:
                if attempt < max_retries and is_unauthorized_error(exc) and self.can_refresh():
                    attempt += 1
                    force_next = True
                    LOGGER.warning(
                        "Intercepted HTTP 401 / invalid_token (attempt %d/%d); refreshing token and retrying...",
                        attempt,
                        max_retries,
                    )
                    continue
                raise

    def urlopen_with_retry(
        self,
        url_or_request: str | urllib.request.Request,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        method: str | None = None,
        timeout: float = 15.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> tuple[int, dict[str, str], bytes]:
        """Executes an authenticated HTTP request with `Authorization: Bearer <token>`, automatically
        refreshing and retrying on HTTP 401 (`Www-Authenticate: Bearer ... error="invalid_token"`).
        """
        def _do_request(access_token: str = "") -> tuple[int, dict[str, str], bytes]:
            if isinstance(url_or_request, urllib.request.Request):
                req_url = url_or_request.full_url
                req_data = data if data is not None else url_or_request.data
                req_method = method or url_or_request.get_method()
                merged_headers = dict(url_or_request.header_items())
            else:
                req_url = str(url_or_request)
                req_data = data
                req_method = method or ("POST" if req_data is not None else "GET")
                merged_headers = {}

            if headers:
                merged_headers.update(headers)
            if access_token:
                merged_headers["Authorization"] = f"Bearer {access_token}"

            req = urllib.request.Request(req_url, data=req_data, headers=merged_headers, method=req_method)
            return self._http_sender(req, timeout)

        return self.execute_with_retry(
            _do_request,
            max_retries=max_retries,
            inject_token_kwarg="access_token",
        )

    def start_background_refresh(self, interval_seconds: float = DEFAULT_BACKGROUND_INTERVAL_SECONDS) -> None:
        """Starts a background daemon thread that checks token expiration periodically and refreshes
        transparently before expiration during long-running tasks."""
        with self._lock:
            if self._bg_thread is not None and self._bg_thread.is_alive():
                return
            self._bg_stop.clear()
            check_interval = max(1.0, float(interval_seconds))

            def _worker() -> None:
                while not self._bg_stop.wait(check_interval):
                    try:
                        if self.is_expired_or_expiring() and self.can_refresh():
                            self.refresh_access_token()
                    except Exception as exc:
                        LOGGER.warning("Background token refresh attempt failed: %s", exc)

            self._bg_thread = threading.Thread(target=_worker, name="Beamer-token-refresh", daemon=True)
            self._bg_thread.start()

    def stop_background_refresh(self) -> None:
        self._bg_stop.set()
        thread = self._bg_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._bg_thread = None

    def status(self) -> dict[str, Any]:
        """Returns a safe diagnostic snapshot of the token manager state (without leaking raw secrets)."""
        with self._lock:
            now = self._clock()
            expires_at = float(self._state.get("expires_at") or 0.0)
            seconds_remaining = max(0.0, expires_at - now) if expires_at > 0.0 else None
            return {
                "has_access_token": bool(self._state.get("access_token")),
                "has_refresh_token": bool(self._state.get("refresh_token")),
                "can_refresh": self.can_refresh(),
                "is_expired_or_expiring": self.is_expired_or_expiring(now),
                "expires_at": expires_at,
                "seconds_remaining": round(seconds_remaining, 1) if seconds_remaining is not None else None,
                "refresh_count": int(self._state.get("refresh_count", 0)),
                "token_uri": str(self._state.get("token_uri") or DEFAULT_GOOGLE_TOKEN_URI),
                "last_error": str(self._state.get("last_error") or ""),
            }
