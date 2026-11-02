"""Passphrase-protected portable settings; no platform credential store is used."""

import json
import os
import stat
import tempfile
import threading
from pathlib import Path

from nacl.exceptions import CryptoError
from nacl.pwhash import argon2id
from nacl.secret import SecretBox
from nacl.utils import random


_MAGIC = b"BEAMER-PORTABLE\x00\x01"
KDF_OPSLIMIT = argon2id.OPSLIMIT_INTERACTIVE
KDF_MEMLIMIT = argon2id.MEMLIMIT_INTERACTIVE
MAX_STATE_BYTES = 1024 * 1024
MAX_ENVELOPE_BYTES = (len(_MAGIC) + argon2id.SALTBYTES + SecretBox.NONCE_SIZE
                      + SecretBox.MACBYTES + MAX_STATE_BYTES)
_MAX_DEPTH = 32


class PortableStoreError(ValueError):
    """Portable state cannot be used safely."""


class PortableStoreLockedError(PortableStoreError):
    """An unlock is required before reading or saving settings."""


class PortableStoreUnlockError(PortableStoreError):
    """The passphrase or the existing encrypted state is invalid."""


def _validate(value, depth=0):
    if depth > _MAX_DEPTH:
        raise ValueError("Portable settings are nested too deeply")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Portable settings keys must be strings")
            _validate(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate(item, depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("Portable settings must contain JSON values")


def _encode(value):
    if not isinstance(value, dict):
        raise ValueError("Portable settings must be an object")
    _validate(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError("Portable settings exceed the size limit")
    return encoded


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate setting")
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError("Invalid JSON number")


def _decode(encoded):
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError("Portable settings exceed the size limit")
    value = json.loads(encoded, object_pairs_hook=_unique_object,
                       parse_constant=_invalid_constant)
    _encode(value)
    return value


class PortableStore:
    def __init__(self, path):
        self.path = Path(path)
        self._key = None
        self._salt = None
        self._data = None
        self._mutex = threading.RLock()

    @property
    def exists(self):
        # A dangling link is still existing state and must never trigger a reset.
        return os.path.lexists(self.path)

    @property
    def is_unlocked(self):
        return self._key is not None

    def unlock(self, passphrase):
        with self._mutex:
            self.lock()
            if not isinstance(passphrase, str):
                raise ValueError("Enter a passphrase")
            try:
                password = passphrase.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("Invalid passphrase") from exc
            if not 1 <= len(password) <= 1024:
                raise ValueError("Passphrase must contain 1 to 1024 UTF-8 bytes")
            if self.exists:
                envelope = self._read_envelope()
                salt = envelope[len(_MAGIC):len(_MAGIC) + argon2id.SALTBYTES]
                key = self._derive(password, salt)
                try:
                    encrypted = envelope[len(_MAGIC) + argon2id.SALTBYTES:]
                    data = _decode(SecretBox(bytes(key)).decrypt(encrypted))
                except (CryptoError, ValueError, UnicodeError, RecursionError) as exc:
                    key[:] = b"\x00" * len(key)
                    raise PortableStoreUnlockError(
                        "Unable to unlock portable settings; check the passphrase and vault") from exc
            else:
                salt = random(argon2id.SALTBYTES)
                key = self._derive(password, salt)
                data = {}
                try:
                    self._save(_encode(data), key, salt, create=True)
                except Exception:
                    key[:] = b"\x00" * len(key)
                    raise
            self._key, self._salt, self._data = key, salt, data

    @staticmethod
    def _derive(password, salt):
        # The envelope carries no attacker-controlled KDF cost parameters.
        return bytearray(argon2id.kdf(SecretBox.KEY_SIZE, password, salt,
                                    opslimit=KDF_OPSLIMIT, memlimit=KDF_MEMLIMIT))

    def _read_envelope(self):
        try:
            if self.path.is_symlink():
                raise ValueError("Linked vaults are not supported")
            if not stat.S_ISREG(self.path.stat().st_mode):
                raise ValueError("Invalid vault type")
            with self.path.open("rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ENVELOPE_BYTES:
                    raise ValueError("Invalid vault size or type")
                envelope = stream.read(MAX_ENVELOPE_BYTES + 1)
            minimum = (len(_MAGIC) + argon2id.SALTBYTES + SecretBox.NONCE_SIZE
                       + SecretBox.MACBYTES + 2)
            if not minimum <= len(envelope) <= MAX_ENVELOPE_BYTES or not envelope.startswith(_MAGIC):
                raise ValueError("Invalid or unsupported vault")
            return envelope
        except (OSError, ValueError) as exc:
            raise PortableStoreUnlockError("Portable settings are unreadable or unsupported") from exc

    def read(self):
        with self._mutex:
            self._require_unlocked()
            return _decode(_encode(self._data))

    def write(self, value):
        with self._mutex:
            self._require_unlocked()
            encoded = _encode(value)
            self._save(encoded, self._key, self._salt)
            self._data = _decode(encoded)

    def _save(self, encoded, key, salt, create=False):
        envelope = _MAGIC + salt + bytes(SecretBox(bytes(key)).encrypt(encoded))
        descriptor, temporary = tempfile.mkstemp(prefix=".beamer-vault-", suffix=".tmp",
                                                 dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(envelope)
                stream.flush()
                os.fsync(stream.fileno())
            if create:
                # Windows rename refuses an existing destination; POSIX rename replaces it.
                if os.name == "nt":
                    os.rename(temporary, self.path)
                else:
                    os.link(temporary, self.path)
            else:
                os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _require_unlocked(self):
        if self._key is None:
            raise PortableStoreLockedError("Unlock portable settings before reading or saving")

    def lock(self):
        with self._mutex:
            if self._key is not None:
                self._key[:] = b"\x00" * len(self._key)
            self._key = self._salt = self._data = None
