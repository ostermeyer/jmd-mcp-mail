# SPDX-License-Identifier: Apache-2.0
"""Tests for the OAuth2 path: config auth field, sealing, resolution."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from nacl.public import PublicKey, SealedBox

from mail_mcp import _config, _credentials, _sealing, server
from mail_mcp._endpoint import ConnectionInfo

_OAUTH = """\
# Account[]
- label: outlook
  imap: outlook.office365.com:993
  smtp: smtp-mail.outlook.com:587
  username: me@live.de
  auth: oauth2
  broker-client: outlook
"""

_BASIC = """\
# Account[]
- label: ionos
  imap: imap.ionos.de:993
  smtp: smtp.ionos.de:587
  username: me@ionos.de
"""


def _write_config(text: str) -> Path:
    """Write *text* to the (isolated) config.jmd and return its path."""
    path = _config.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _seal(pubkey_b64: str, plaintext: bytes) -> str:
    """Seal *plaintext* against a base64 public key (the broker's role)."""
    box = SealedBox(PublicKey(base64.b64decode(pubkey_b64)))
    return base64.b64encode(box.encrypt(plaintext)).decode("ascii")


def test_oauth_account_config_roundtrip() -> None:
    """An oauth2 account loads its auth type and broker-client."""
    _write_config(_OAUTH)
    acc = _config.resolve("outlook")
    assert acc is not None
    assert acc.auth == "oauth2"
    assert acc.broker_client == "outlook"


def test_resolve_unknown_account() -> None:
    """An unconfigured label yields an unknown_account error."""
    info = server._resolve_info("nope", "# Folder[]")
    assert isinstance(info, str)
    assert "unknown_account" in info


def test_resolve_oauth_via_sealed_token(
    mem_keyring: dict[str, str],
) -> None:
    """A sealed-token frontmatter yields an XOAUTH2 connection."""
    _write_config(_OAUTH)
    ciphertext = _seal(_sealing.public_key(), b"ACCESS-TOKEN")
    doc = f"access-token-sealed: {ciphertext}\n\n# Folder[]"
    info = server._resolve_info("outlook", doc)
    assert isinstance(info, ConnectionInfo)
    assert info.access_token == "ACCESS-TOKEN"
    assert info.password == ""


def test_resolve_bad_sealed_token(mem_keyring: dict[str, str]) -> None:
    """An unopenable sealed token returns a structured error."""
    _write_config(_OAUTH)
    doc = "access-token-sealed: not-a-valid-ciphertext\n\n# Folder[]"
    info = server._resolve_info("outlook", doc)
    assert isinstance(info, str)
    assert "bad_sealed_token" in info


def test_resolve_oauth_account_without_token_hints() -> None:
    """A configured oauth2 account with no token gets an OAuth hint."""
    _write_config(_OAUTH)
    info = server._resolve_info("outlook", "# Folder[]")
    assert isinstance(info, str)
    assert "oauth_token_required" in info
    assert "outlook" in info  # broker-client hint in the message


def test_resolve_basic_credential_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A basic account with no keystore item yields credential_missing."""
    _write_config(_BASIC)
    monkeypatch.setattr(
        _credentials, "_read_from_keystore", lambda s, u: None,
    )
    _credentials._reset_cache_for_tests()
    info = server._resolve_info("ionos", "# Folder[]")
    assert isinstance(info, str)
    assert "credential_missing" in info
