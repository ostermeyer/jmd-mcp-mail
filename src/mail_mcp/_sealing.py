# SPDX-License-Identifier: Apache-2.0
"""Open sealed OAuth2 access tokens handed to us by the token broker.

jmd-mcp-oauth2 seals a freshly-minted access token against this
server's X25519 **public** key (a libsodium sealed box). Only this
server, holding the matching **private** key, can open it. The
private key is generated once and kept in the OS keyring (32 bytes —
no size limit); the public key is handed to the broker and is not
sensitive.
"""
from __future__ import annotations

import base64

import keyring
from nacl.public import PrivateKey, SealedBox

_KEYRING_SERVICE = "jmd-mcp-mail"
_PRIVATE_KEY_USER = "_x25519_private_key"


def _load_or_create_private_key() -> PrivateKey:
    """Return this server's X25519 private key, generating it once."""
    stored = keyring.get_password(_KEYRING_SERVICE, _PRIVATE_KEY_USER)
    if stored:
        return PrivateKey(base64.b64decode(stored))
    sk = PrivateKey.generate()
    keyring.set_password(
        _KEYRING_SERVICE,
        _PRIVATE_KEY_USER,
        base64.b64encode(bytes(sk)).decode("ascii"),
    )
    return sk


def public_key() -> str:
    """Return this server's base64 X25519 public key for the broker."""
    sk = _load_or_create_private_key()
    return base64.b64encode(bytes(sk.public_key)).decode("ascii")


def unseal(ciphertext_b64: str) -> str:
    """Open a sealed access token and return it as text.

    Args:
        ciphertext_b64: Base64 sealed-box ciphertext from the broker.

    Returns:
        The recovered access-token string.

    Raises:
        nacl.exceptions.CryptoError: On a wrong key or tampering.
    """
    sk = _load_or_create_private_key()
    plaintext = SealedBox(sk).decrypt(base64.b64decode(ciphertext_b64))
    return bytes(plaintext).decode("utf-8")
