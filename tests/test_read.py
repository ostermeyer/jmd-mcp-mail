# SPDX-License-Identifier: Apache-2.0
"""Tests for IMAP read helpers (UID extraction across server quirks)."""
from __future__ import annotations

from mail_mcp.imap.read import _uid_at


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
