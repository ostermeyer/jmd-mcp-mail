# SPDX-License-Identifier: Apache-2.0
"""Tests for mail_mcp._sealing: unsealing broker-sealed access tokens."""
from __future__ import annotations

import base64

from nacl.public import PublicKey, SealedBox

from mail_mcp import _sealing


def _seal_for(pubkey_b64: str, plaintext: bytes) -> str:
    """Seal *plaintext* against a base64 public key (the broker's role)."""
    box = SealedBox(PublicKey(base64.b64decode(pubkey_b64)))
    return base64.b64encode(box.encrypt(plaintext)).decode("ascii")


def test_public_key_is_stable(mem_keyring: dict[str, str]) -> None:
    """The public key is generated once and stays stable."""
    assert _sealing.public_key() == _sealing.public_key()


def test_unseal_roundtrip(mem_keyring: dict[str, str]) -> None:
    """A token sealed to our public key unseals back to plaintext."""
    pub = _sealing.public_key()
    ciphertext = _seal_for(pub, b"the-access-token")
    assert _sealing.unseal(ciphertext) == "the-access-token"


def test_unseal_large_token(mem_keyring: dict[str, str]) -> None:
    """A 1.4 KB token (real Microsoft size) unseals correctly."""
    pub = _sealing.public_key()
    token = "x" * 1420
    ciphertext = _seal_for(pub, token.encode())
    assert _sealing.unseal(ciphertext) == token
