"""Minimal AES-256-GCM for Chromium cookie import on Windows.

Chromium on Windows protects the cookie key inside ``Local State`` with DPAPI
and then stores each ``v10`` cookie as AES-256-GCM: a 12-byte nonce, the
ciphertext, and a 16-byte tag. macOS/Linux use AES-128-CBC through ``openssl``
instead, but ``openssl enc`` has no AEAD mode, so this module keeps the Windows
path dependency-free.

Only what decryption needs is implemented: AES encryption (CTR mode never needs
the inverse cipher) and GHASH. Both are checked against published GCM test
vectors in ``tests/cli/test_cookies_windows.py``.
"""

from __future__ import annotations

import hmac
from functools import lru_cache

BLOCK_BYTES = 16
TAG_BYTES = 16
NONCE_BYTES = 12


def _rotl8(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


@lru_cache(maxsize=1)
def _sbox() -> tuple[int, ...]:
    table = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        table[p] = (
            q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3) ^ _rotl8(q, 4) ^ 0x63
        )
        if p == 1:
            break
    table[0] = 0x63
    return tuple(table)


@lru_cache(maxsize=1)
def _rcon() -> tuple[int, ...]:
    values = []
    value = 1
    for _ in range(15):
        values.append(value)
        value = ((value << 1) ^ (0x1B if value & 0x80 else 0)) & 0xFF
    return tuple(values)


def _expand_key(key: bytes) -> list[list[int]]:
    if len(key) not in (16, 24, 32):
        raise ValueError("AES keys must be 16, 24 or 32 bytes")
    words = [list(key[index: index + 4]) for index in range(0, len(key), 4)]
    total = 4 * (len(key) // 4 + 7)
    sbox = _sbox()
    rcon = _rcon()
    for index in range(len(words), total):
        word = list(words[index - 1])
        if index % (len(key) // 4) == 0:
            word = word[1:] + word[:1]
            word = [sbox[byte] for byte in word]
            word[0] ^= rcon[index // (len(key) // 4) - 1]
        elif len(key) == 32 and index % 8 == 4:
            word = [sbox[byte] for byte in word]
        words.append([words[index - len(key) // 4][i] ^ word[i] for i in range(4)])
    rounds = len(key) // 4 + 6
    return [sum(words[4 * r: 4 * r + 4], []) for r in range(rounds + 1)]


def _mix_column(column: list[int]) -> list[int]:
    a, b, c, d = column
    return [
        (mul2(a) ^ mul3(b) ^ c ^ d),
        (a ^ mul2(b) ^ mul3(c) ^ d),
        (a ^ b ^ mul2(c) ^ mul3(d)),
        (mul3(a) ^ b ^ c ^ mul2(d)),
    ]


def _xtime(value: int) -> int:
    return ((value << 1) ^ 0x1B) & 0xFF if value & 0x80 else (value << 1) & 0xFF


def mul2(value: int) -> int:
    return _xtime(value)


def mul3(value: int) -> int:
    return _xtime(value) ^ value


def _encrypt_block(round_keys: list[list[int]], block: bytes) -> bytes:
    sbox = _sbox()
    state = [block[i] ^ round_keys[0][i] for i in range(16)]
    for round_index in range(1, len(round_keys)):
        state = [sbox[byte] for byte in state]
        shifted = [0] * 16
        for column in range(4):
            for row in range(4):
                shifted[4 * column + row] = state[4 * ((column + row) % 4) + row]
        state = shifted
        if round_index != len(round_keys) - 1:
            columns = []
            for column in range(4):
                columns.extend(_mix_column(state[4 * column: 4 * column + 4]))
            state = columns
        state = [state[i] ^ round_keys[round_index][i] for i in range(16)]
    return bytes(state)


def _ghash_multiply(x: int, y: int) -> int:
    result = 0
    value = y
    for bit in range(127, -1, -1):
        if (x >> bit) & 1:
            result ^= value
        value = (value >> 1) ^ (0xE1 << 120) if value & 1 else value >> 1
    return result


def _ghash(hash_key: int, data: bytes) -> int:
    state = 0
    for offset in range(0, len(data), BLOCK_BYTES):
        chunk = data[offset: offset + BLOCK_BYTES].ljust(BLOCK_BYTES, b"\x00")
        state = _ghash_multiply(state ^ int.from_bytes(chunk, "big"), hash_key)
    return state


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(left ^ right for left, right in zip(a, b))


def _ctr_keystream(round_keys: list[list[int]], counter: int, length: int) -> bytes:
    stream = bytearray()
    while len(stream) < length:
        counter = (counter & ~0xFFFFFFFF) | (((counter & 0xFFFFFFFF) + 1) & 0xFFFFFFFF)
        stream.extend(_encrypt_block(round_keys, counter.to_bytes(16, "big")))
    return bytes(stream[:length])


def _initial_counter(nonce: bytes) -> int:
    return int.from_bytes(nonce + b"\x00\x00\x00\x01", "big")


def _tag(round_keys: list[list[int]], hash_key: int, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    lengths = (len(aad) * 8).to_bytes(8, "big") + (len(ciphertext) * 8).to_bytes(8, "big")
    padded_aad = aad + b"\x00" * (-len(aad) % BLOCK_BYTES)
    padded_cipher = ciphertext + b"\x00" * (-len(ciphertext) % BLOCK_BYTES)
    digest = _ghash(hash_key, padded_aad + padded_cipher + lengths)
    mask = _encrypt_block(round_keys, _initial_counter(nonce).to_bytes(16, "big"))
    return _xor(digest.to_bytes(BLOCK_BYTES, "big"), mask)


def decrypt(key: bytes, payload: bytes, aad: bytes = b"") -> bytes:
    """Decrypt one AES-GCM payload laid out as nonce | ciphertext | tag."""

    if len(payload) < NONCE_BYTES + TAG_BYTES:
        raise ValueError("AES-GCM payload is too short")
    nonce = payload[:NONCE_BYTES]
    ciphertext = payload[NONCE_BYTES: -TAG_BYTES]
    tag = payload[-TAG_BYTES:]
    round_keys = _expand_key(key)
    hash_key = int.from_bytes(_encrypt_block(round_keys, bytes(BLOCK_BYTES)), "big")
    expected = _tag(round_keys, hash_key, nonce, ciphertext, aad)
    if not hmac.compare_digest(expected, tag):
        raise ValueError("AES-GCM authentication failed")
    return _xor(ciphertext, _ctr_keystream(round_keys, _initial_counter(nonce), len(ciphertext)))


def encrypt(key: bytes, plaintext: bytes, nonce: bytes, aad: bytes = b"") -> bytes:
    """Encrypt with AES-GCM; used to build test fixtures and never in the import path."""

    if len(nonce) != NONCE_BYTES:
        raise ValueError("AES-GCM nonces must be 12 bytes")
    round_keys = _expand_key(key)
    hash_key = int.from_bytes(_encrypt_block(round_keys, bytes(BLOCK_BYTES)), "big")
    ciphertext = _xor(plaintext, _ctr_keystream(round_keys, _initial_counter(nonce), len(plaintext)))
    return nonce + ciphertext + _tag(round_keys, hash_key, nonce, ciphertext, aad)
