# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the read-only ``accounts`` tool (projection + errors)."""
from __future__ import annotations

from pathlib import Path

from mail_mcp import _config, accounts

_OAUTH = """\
# Account[]
- label: outlook
  imap: outlook.office365.com:993
  smtp: smtp-mail.outlook.com:587
  username: me@live.de
  auth: oauth2
  broker-client: outlook
"""


def _write_config(text: str) -> Path:
    """Write *text* to the (isolated) config.jmd and return its path."""
    path = _config.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_handle_schema_returns_account_schema() -> None:
    """`#! Account` returns the Account schema verbatim."""
    from mail_mcp.schemas import ACCOUNT

    assert accounts.handle("#! Account\n") == ACCOUNT


def test_handle_list_empty() -> None:
    """`# Account[]` against an empty config returns an empty array."""
    assert "# Account[]" in accounts.handle("# Account[]\n")


def test_handle_list_projection_hides_pii() -> None:
    """Listing exposes label/auth/broker — never username or endpoints."""
    _write_config(_OAUTH)
    out = accounts.handle("# Account[]\n")
    assert "outlook" in out          # label + broker-client
    assert "oauth2" in out
    assert "me@live.de" not in out   # username stays out
    assert "office365" not in out    # endpoints stay out


def test_upsert_attempt_is_readonly() -> None:
    """A single-object # Account (upsert) is refused as read-only."""
    doc = (
        "# Account\n"
        "label: x\n"
        "imap: imap.x:993\n"
        "smtp: smtp.x:587\n"
        "username: a@b\n"
    )
    out = accounts.handle(doc)
    assert "status: 405" in out
    assert "config_readonly" in out


def test_delete_is_readonly() -> None:
    """`#- Account` is refused as read-only."""
    out = accounts.handle("#- Account\nlabel: x\n")
    assert "config_readonly" in out


def test_query_unsupported() -> None:
    """Query mode is rejected with a hint."""
    out = accounts.handle("#? Account\n")
    assert "unsupported_mode" in out


def test_unparseable_document_is_error() -> None:
    """Garbage in → a well-formed error document, no exception."""
    assert "# Error" in accounts.handle("this is not jmd at all")


def test_public_key_shape(mem_keyring: dict[str, str]) -> None:
    """`# PublicKey` returns this server's public key."""
    out = accounts.handle("# PublicKey")
    assert "# PublicKey" in out
    assert "key:" in out
