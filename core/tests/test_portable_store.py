"""Synthetic portable-state persistence; no platform or GUI dependencies."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import portable_store


class PortableStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "pairing.vault"
        self.store = portable_store.PortableStore(self.path)

    def test_locked_state_cannot_be_read_or_written(self):
        self.assertFalse(self.store.exists)
        self.assertFalse(self.store.is_unlocked)
        for operation in (self.store.read, lambda: self.store.write({"token": "secret"})):
            with self.assertRaises(portable_store.PortableStoreLockedError):
                operation()
        self.assertFalse(self.path.exists())

    def test_create_round_trip_and_lock(self):
        self.store.unlock("synthetic passphrase")
        self.assertTrue(self.store.exists)
        self.assertEqual(self.store.read(), {})
        data = {"identity": "identity-secret", "peers": [{"token": "pairing-secret"}],
                "label": "Synthetic desk", "port": 48123}
        self.store.write(data)
        for secret in (b"identity-secret", b"pairing-secret", b"Synthetic desk"):
            self.assertNotIn(secret, self.path.read_bytes())
        self.store.lock()
        self.assertFalse(self.store.is_unlocked)
        with self.assertRaises(portable_store.PortableStoreLockedError):
            self.store.read()
        self.store.unlock("synthetic passphrase")
        self.assertEqual(self.store.read(), data)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_wrong_password_never_replaces_state_and_relocks(self):
        self.store.unlock("right password")
        self.store.write({"token": "synthetic-token"})
        before = self.path.read_bytes()
        with self.assertRaises(portable_store.PortableStoreUnlockError):
            self.store.unlock("wrong password")
        self.assertFalse(self.store.is_unlocked)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_and_unsupported_files_never_reset_or_run_kdf(self):
        for bad in (b"", b"{}", b"wrong header" * 100,
                    portable_store._MAGIC + b"\x00" * 4):
            with self.subTest(bad=bad[:20]):
                self.path.write_bytes(bad)
                with mock.patch.object(portable_store.argon2id, "kdf") as kdf:
                    with self.assertRaises(portable_store.PortableStoreUnlockError):
                        self.store.unlock("synthetic password")
                    kdf.assert_not_called()
                self.assertEqual(self.path.read_bytes(), bad)

    def test_authenticated_ciphertext_rejects_mutation(self):
        self.store.unlock("synthetic password")
        damaged = bytearray(self.path.read_bytes())
        damaged[-1] ^= 1
        self.path.write_bytes(damaged)
        with self.assertRaises(portable_store.PortableStoreUnlockError):
            self.store.unlock("synthetic password")
        self.assertEqual(self.path.read_bytes(), damaged)

    def test_fresh_salt_and_nonce_and_fresh_nonce_for_every_write(self):
        self.store.unlock("synthetic password")
        first = self.path.read_bytes()
        self.store.write({})
        second = self.path.read_bytes()
        self.assertNotEqual(first, second)
        other = portable_store.PortableStore(self.path.with_name("other.vault"))
        other.unlock("synthetic password")
        self.assertNotEqual(first, other.path.read_bytes())

    def test_atomic_failure_preserves_disk_and_memory_without_plaintext_temp(self):
        self.store.unlock("synthetic password")
        self.store.write({"token": "old synthetic token"})
        before = self.path.read_bytes()

        def reject_replace(source, destination):
            self.assertEqual(Path(destination), self.path)
            self.assertNotIn(b"new synthetic token", Path(source).read_bytes())
            raise OSError("synthetic interrupted save")

        with mock.patch.object(portable_store.os, "replace", side_effect=reject_replace):
            with self.assertRaises(OSError):
                self.store.write({"token": "new synthetic token"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.read(), {"token": "old synthetic token"})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_read_and_write_do_not_share_mutable_objects(self):
        self.store.unlock("synthetic password")
        value = {"nested": ["original"]}
        self.store.write(value)
        value["nested"].append("changed")
        read = self.store.read()
        read["nested"].append("changed again")
        self.assertEqual(self.store.read(), {"nested": ["original"]})

    def test_invalid_passphrases_and_values_do_not_create_a_file(self):
        for passphrase in ("", "a" * 1025, None, b"password"):
            with self.subTest(passphrase_type=type(passphrase)):
                with self.assertRaises(ValueError):
                    self.store.unlock(passphrase)
                self.assertFalse(self.path.exists())
        self.store.unlock("synthetic password")
        before = self.path.read_bytes()
        for value in ([], {1: "bad key"}, {"value": float("nan")}, {"value": object()},
                      {"huge": "x" * portable_store.MAX_STATE_BYTES}):
            with self.subTest(value_type=type(value)):
                with self.assertRaises(ValueError):
                    self.store.write(value)
                self.assertEqual(self.path.read_bytes(), before)

    def test_oversized_envelope_rejected_before_key_derivation(self):
        self.path.write_bytes(b"x" * (portable_store.MAX_ENVELOPE_BYTES + 1))
        with mock.patch.object(portable_store.argon2id, "kdf") as kdf:
            with self.assertRaises(portable_store.PortableStoreUnlockError):
                self.store.unlock("synthetic password")
            kdf.assert_not_called()

    def test_new_vault_never_overwrites_a_concurrently_created_file(self):
        derive = self.store._derive

        def create_competing_vault(password, salt):
            self.path.write_bytes(b"synthetic existing vault")
            return derive(password, salt)

        with mock.patch.object(self.store, "_derive", side_effect=create_competing_vault):
            with self.assertRaises(FileExistsError):
                self.store.unlock("synthetic password")
        self.assertFalse(self.store.is_unlocked)
        self.assertEqual(self.path.read_bytes(), b"synthetic existing vault")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_excessive_nesting_is_rejected_without_changing_state(self):
        self.store.unlock("synthetic password")
        before = self.path.read_bytes()
        value = []
        for _ in range(40):
            value = [value]
        with self.assertRaises(ValueError):
            self.store.write({"deep": value})
        self.assertEqual(self.path.read_bytes(), before)

    def test_authenticated_invalid_json_never_unlocked(self):
        from nacl.secret import SecretBox

        salt = b"s" * portable_store.argon2id.SALTBYTES
        key = portable_store.argon2id.kdf(
            SecretBox.KEY_SIZE, b"synthetic password", salt,
            opslimit=portable_store.KDF_OPSLIMIT, memlimit=portable_store.KDF_MEMLIMIT)
        for plaintext in (b"[]", b'{"duplicate": 1, "duplicate": 2}', b'{"n": NaN}',
                          b'{"broken"', json.dumps({"deep": [[[[[]]]]]}).encode()):
            # The last case is valid and proves the same envelope can unlock.
            self.path.write_bytes(portable_store._MAGIC + salt + bytes(SecretBox(key).encrypt(plaintext)))
            if b"deep" in plaintext:
                self.store.unlock("synthetic password")
                self.assertIn("deep", self.store.read())
            else:
                with self.assertRaises(portable_store.PortableStoreUnlockError):
                    self.store.unlock("synthetic password")
                self.assertFalse(self.store.is_unlocked)


if __name__ == "__main__":
    unittest.main()
