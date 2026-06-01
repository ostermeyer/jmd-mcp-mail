# SPDX-License-Identifier: Apache-2.0
"""Tests for the OAuth2 tool surface (auth field, PublicKey, sealing)."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from nacl.public import PublicKey, SealedBox

from mail_mcp import _credentials, _sealing, accounts, server
from mail_mcp._endpoint import ConnectionInfo
from mail_mcp.accounts import Account


@pytest.fixture(autouse=True)
def _tmp_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point the account registry at a throwaway file per test."""
    monkeypatch.setenv(
        "JMD_MCP_MAIL_ACCOUNTS_PATH", str(tmp_path / "accounts.jmd"),
    )


def _seal(pubkey_b64: str, plaintext: bytes) -> str:
    """Seal *plaintext* against a base64 public key (the broker's role)."""
    box = SealedBox(PublicKey(base64.b64decode(pubkey_b64)))
    return base64.b64encode(box.encrypt(plaintext)).decode("ascii")


# --- account registry: auth field -----------------------------------------


def test_oauth2_account_roundtrip() -> None:
    """An oauth2 account persists its auth type and broker-client."""
    accounts.upsert(Account(
        "outlook", "outlook.office365.com:993",
        "smtp-mail.outlook.com:587", "me@live.de",
        auth="oauth2", broker_client="outlook",
    ))
    loaded = accounts.load()
    assert len(loaded) == 1
    assert loaded[0].auth == "oauth2"
    assert loaded[0].broker_client == "outlook"


def test_basic_account_serializes_without_auth() -> None:
    """A basic account keeps its original four-field shape."""
    acct = Account(
        "ionos", "imap.ionos.de:993", "smtp.ionos.de:587", "me@ionos.de",
    )
    d = acct.as_jmd_dict()
    assert "auth" not in d
    assert "broker-client" not in d


def test_oauth2_requires_broker_client() -> None:
    """Upserting an oauth2 account without a broker-client is rejected."""
    with pytest.raises(ValueError, match="broker-client"):
        accounts.upsert(Account(
            "x", "imap.x:993", "smtp.x:587", "me@x", auth="oauth2",
        ))


def test_invalid_auth_rejected() -> None:
    """An unknown auth value is rejected."""
    with pytest.raises(ValueError, match="basic"):
        accounts.upsert(Account(
            "x", "imap.x:993", "smtp.x:587", "me@x", auth="weird",
        ))


def test_find_by_endpoint() -> None:
    """find_by_endpoint matches on either IMAP or SMTP endpoint."""
    accounts.upsert(Account(
        "outlook", "outlook.office365.com:993",
        "smtp-mail.outlook.com:587", "me@live.de",
        auth="oauth2", broker_client="outlook",
    ))
    found = accounts.find_by_endpoint("smtp-mail.outlook.com:587", "me@live.de")
    assert found is not None
    assert found.label == "outlook"
    assert accounts.find_by_endpoint("imap.other:993", "me@live.de") is None


# --- public key shape ------------------------------------------------------


def test_public_key_shape(mem_keyring: dict[str, str]) -> None:
    """`accounts` with `# PublicKey` returns this server's public key."""
    out = accounts.handle("# PublicKey")
    assert "# PublicKey" in out
    assert "key:" in out


# --- sealed-token resolution -----------------------------------------------


def test_resolve_oauth_via_sealed_token(
    mem_keyring: dict[str, str],
) -> None:
    """A sealed-token frontmatter yields an XOAUTH2 connection."""
    pub = _sealing.public_key()
    ciphertext = _seal(pub, b"ACCESS-TOKEN")
    doc = f"access-token-sealed: {ciphertext}\n\n# Folder[]"
    info = server._resolve_info(
        "outlook.office365.com:993", "me@live.de", doc,
    )
    assert isinstance(info, ConnectionInfo)
    assert info.access_token == "ACCESS-TOKEN"
    assert info.password == ""


def test_resolve_bad_sealed_token(mem_keyring: dict[str, str]) -> None:
    """An unopenable sealed token returns a structured error."""
    doc = "access-token-sealed: not-a-valid-ciphertext\n\n# Folder[]"
    info = server._resolve_info("x:993", "me@x", doc)
    assert isinstance(info, str)
    assert "bad_sealed_token" in info


def test_resolve_oauth_account_without_token_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registered oauth2 account with no token gets an OAuth hint."""
    accounts.upsert(Account(
        "outlook", "outlook.office365.com:993",
        "smtp-mail.outlook.com:587", "me@live.de",
        auth="oauth2", broker_client="outlook",
    ))
    monkeypatch.setattr(
        _credentials, "_read_from_keystore", lambda s, u: None,
    )
    info = server._resolve_info(
        "outlook.office365.com:993", "me@live.de", "# Folder[]",
    )
    assert isinstance(info, str)
    assert "oauth_token_required" in info
    assert "outlook" in info  # broker-client hint in the message
