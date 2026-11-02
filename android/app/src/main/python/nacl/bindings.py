"""RFC 7748 X25519 & RFC 8439 ChaCha20-Poly1305 IETF bindings compatible with PyNaCl's nacl.bindings."""

from __future__ import annotations

import hmac
import struct
from nacl.exceptions import CryptoError

crypto_aead_chacha20poly1305_ietf_ABYTES = 16
_P25519 = 2**255 - 19
_P1305 = (1 << 130) - 5
_BASE_POINT = b"\x09" + b"\x00" * 31


def _x25519_ladder(scalar: bytes, u: bytes) -> bytes:
    if len(scalar) != 32 or len(u) != 32:
        raise CryptoError("Invalid X25519 input length")
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    k_int = int.from_bytes(k, "little")
    x1 = (int.from_bytes(u, "little") & ((1 << 255) - 1)) % _P25519
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        bit = (k_int >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % _P25519, (x2 - z2) % _P25519
        aa, bb = a * a % _P25519, b * b % _P25519
        e = (aa - bb) % _P25519
        c, d = (x3 + z3) % _P25519, (x3 - z3) % _P25519
        da, cb = d * a % _P25519, c * b % _P25519
        x3, z3 = (da + cb) ** 2 % _P25519, x1 * (da - cb) ** 2 % _P25519
        x2, z2 = aa * bb % _P25519, e * (aa + 121665 * e) % _P25519
    if swap:
        x2, z2 = x3, z3
    out = (x2 * pow(z2, _P25519 - 2, _P25519) % _P25519).to_bytes(32, "little")
    if out == b"\x00" * 32:
        raise CryptoError("Low-order X25519 point rejected")
    return out


def crypto_scalarmult(n: bytes, p: bytes) -> bytes:
    return _x25519_ladder(n, p)


def crypto_scalarmult_base(n: bytes) -> bytes:
    return _x25519_ladder(n, _BASE_POINT)


def _rotl32(v: int, c: int) -> int:
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotl32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotl32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotl32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotl32(state[b] ^ state[c], 7)


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    constants = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
    key_words = struct.unpack("<8I", key)
    nonce_words = struct.unpack("<3I", nonce)
    initial = list(constants) + list(key_words) + [counter & 0xFFFFFFFF] + list(nonce_words)
    working = list(initial)
    for _ in range(10):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    return struct.pack("<16I", *((working[i] + initial[i]) & 0xFFFFFFFF for i in range(16)))


def _chacha20_xor(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for i in range(0, len(data), 64):
        block = _chacha20_block(key, counter + (i // 64), nonce)
        chunk = data[i : i + 64]
        for j, b in enumerate(chunk):
            out[i + j] = b ^ block[j]
    return bytes(out)


def _poly1305_mac(msg: bytes, key: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for i in range(0, len(msg), 16):
        chunk = msg[i : i + 16] + b"\x01"
        n = int.from_bytes(chunk, "little")
        acc = ((acc + n) * r) % _P1305
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(data: bytes) -> bytes:
    rem = len(data) % 16
    return b"" if rem == 0 else b"\x00" * (16 - rem)


def crypto_aead_chacha20poly1305_ietf_encrypt(
    message: bytes, aad: bytes | None, nonce: bytes, key: bytes
) -> bytes:
    if not isinstance(message, bytes):
        raise TypeError("message must be bytes")
    aad_bytes = aad or b""
    otk = _chacha20_block(key, 0, nonce)[:32]
    ciphertext = _chacha20_xor(key, 1, nonce, message)
    mac_data = (
        aad_bytes
        + _pad16(aad_bytes)
        + ciphertext
        + _pad16(ciphertext)
        + struct.pack("<QQ", len(aad_bytes), len(ciphertext))
    )
    tag = _poly1305_mac(mac_data, otk)
    return ciphertext + tag


def crypto_aead_chacha20poly1305_ietf_decrypt(
    ciphertext: bytes, aad: bytes | None, nonce: bytes, key: bytes
) -> bytes:
    if not isinstance(ciphertext, bytes):
        raise TypeError("ciphertext must be bytes")
    if len(ciphertext) < crypto_aead_chacha20poly1305_ietf_ABYTES:
        raise CryptoError("Ciphertext too short")
    aad_bytes = aad or b""
    ct = ciphertext[:-16]
    tag = ciphertext[-16:]
    otk = _chacha20_block(key, 0, nonce)[:32]
    mac_data = (
        aad_bytes
        + _pad16(aad_bytes)
        + ct
        + _pad16(ct)
        + struct.pack("<QQ", len(aad_bytes), len(ct))
    )
    expected_tag = _poly1305_mac(mac_data, otk)
    if not hmac.compare_digest(tag, expected_tag):
        raise CryptoError("Poly1305 MAC verification failed")
    return _chacha20_xor(key, 1, nonce, ct)

