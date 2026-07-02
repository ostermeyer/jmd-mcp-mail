# SPDX-License-Identifier: Apache-2.0
"""Unit tests for imap/_thread.py — reply threading helpers."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from mail_mcp.imap import _thread
from mail_mcp.imap._thread import (
    OriginalHeaders,
    apply_reply_defaults,
    fetch_original,
    reply_headers,
    reply_subject,
    reply_to_default,
)


def _orig(**overrides: str) -> OriginalHeaders:
    base = {
        "message_id": "<orig@example.com>",
        "references": "",
        "subject": "Numbers",
        "from_addr": "Alice <alice@example.com>",
        "reply_to": "",
    }
    base.update(overrides)
    return OriginalHeaders(**base)  # type: ignore[arg-type]


def test_reply_subject_adds_prefix() -> None:
    """A plain subject gets a Re: prefix."""
    assert reply_subject("Numbers") == "Re: Numbers"


def test_reply_subject_keeps_existing_prefix() -> None:
    """re:/AW: prefixed subjects stay untouched."""
    assert reply_subject("RE: Numbers") == "RE: Numbers"
    assert reply_subject("Aw: Zahlen") == "Aw: Zahlen"


def test_reply_headers_builds_references_chain() -> None:
    """References = old chain + the original's Message-ID."""
    orig = _orig(references="<root@example.com>")
    headers = reply_headers(orig)
    assert headers["In-Reply-To"] == "<orig@example.com>"
    assert headers["References"] == (
        "<root@example.com> <orig@example.com>"
    )


def test_reply_headers_first_reply() -> None:
    """Without a prior chain, References is just the Message-ID."""
    assert reply_headers(_orig())["References"] == "<orig@example.com>"


def test_reply_headers_empty_without_message_id() -> None:
    """No Message-ID on the original → no threading headers."""
    assert reply_headers(_orig(message_id="")) == {}


def test_reply_to_default_prefers_reply_to() -> None:
    """Reply-To wins over From."""
    orig = _orig(reply_to="List <list@example.com>")
    assert reply_to_default(orig) == "list@example.com"


def test_reply_to_default_falls_back_to_from() -> None:
    """Without Reply-To, the bare From address is used."""
    assert reply_to_default(_orig()) == "alice@example.com"


def test_apply_reply_defaults_fills_missing_only() -> None:
    """User-supplied to/subject always win over the defaults."""
    fields: dict[str, object] = {"to": "bob@example.com"}
    apply_reply_defaults(fields, _orig())
    assert fields["to"] == "bob@example.com"
    assert fields["subject"] == "Re: Numbers"


async def test_fetch_original_parses_headers() -> None:
    """The header FETCH is parsed into OriginalHeaders."""
    raw = (
        b"Message-ID: <orig@example.com>\r\n"
        b"References: <root@example.com>\r\n"
        b"Subject: Numbers\r\n"
        b"From: Alice <alice@example.com>\r\n"
        b"Reply-To: list@example.com\r\n\r\n"
    )
    replies = [
        ("OK", [b"5 EXISTS"]),                # select (readonly)
        ("OK", [(b"1 (UID 42 ...)", raw)]),   # uid FETCH
    ]
    with patch.object(
        _thread, "imap_call", new=AsyncMock(side_effect=replies),
    ):
        orig = await fetch_original(object(), "INBOX", "42")  # type: ignore[arg-type]
    assert orig is not None
    assert orig.message_id == "<orig@example.com>"
    assert orig.references == "<root@example.com>"
    assert orig.subject == "Numbers"
    assert orig.reply_to == "list@example.com"


async def test_fetch_original_missing_returns_none() -> None:
    """A miss (no tuple in the FETCH data) yields None."""
    replies = [("OK", []), ("OK", [None])]
    with patch.object(
        _thread, "imap_call", new=AsyncMock(side_effect=replies),
    ):
        orig = await fetch_original(object(), "INBOX", "99")  # type: ignore[arg-type]
    assert orig is None
