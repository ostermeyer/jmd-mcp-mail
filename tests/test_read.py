# SPDX-License-Identifier: Apache-2.0
"""Tests for IMAP read helpers (UID extraction across server quirks)."""
from __future__ import annotations

import pytest

from mail_mcp._endpoint import ConnectionInfo, TlsMode
from mail_mcp.imap import read as imap_read
from mail_mcp.imap.read import _uid_at


def _info() -> ConnectionInfo:
    """Return inert connection data for dispatch-only tests."""
    return ConnectionInfo(
        host="mail.example",
        port=993,
        tls_mode=TlsMode.IMPLICIT,
        username="user@example",
        password="secret",
    )


@pytest.mark.asyncio
async def test_unknown_schema_does_not_fall_through_to_message() -> None:
    """Unknown resources fail before any IMAP operation is attempted."""
    result = await imap_read.read("#! Typo", _info())

    assert "unknown_label" in result


def test_uid_in_prefix() -> None:
    """Most servers put the UID in the FETCH prefix (item[0])."""
    data: list[object] = [(b"1 (UID 42 BODY[HEADER] {10}", b"hdr")]
    assert _uid_at(data, 0) == "42"


def test_uid_in_trailer_exchange() -> None:
    """Exchange/Outlook emit the UID after the body literal (trailer)."""
    data: list[object] = [
        (b"1879 (BODY[HEADER] {9584}", b"hdr"),
        b" UID 14479)",
    ]
    assert _uid_at(data, 0) == "14479"


def test_uid_missing_returns_placeholder() -> None:
    """No UID anywhere yields the '?' placeholder."""
    data: list[object] = [(b"1 (BODY[HEADER] {5}", b"hdr"), b")"]
    assert _uid_at(data, 0) == "?"
