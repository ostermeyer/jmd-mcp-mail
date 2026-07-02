# SPDX-License-Identifier: Apache-2.0
"""Unit tests for imap/_connection.py — folder name encoding."""
from __future__ import annotations

from mail_mcp.imap._connection import encode_folder


def test_plain_name_unquoted() -> None:
    """Simple ASCII names stay bare atoms."""
    assert encode_folder("INBOX") == "INBOX"


def test_space_quoted() -> None:
    """Names with spaces are double-quoted."""
    assert encode_folder("Sent Items") == '"Sent Items"'


def test_empty_quoted() -> None:
    """The empty name is quoted."""
    assert encode_folder("") == '""'


def test_brackets_quoted() -> None:
    """Gmail-style bracket paths are quoted (atom-specials)."""
    assert encode_folder("[Gmail]/Drafts") == '"[Gmail]/Drafts"'


def test_inner_quote_escaped() -> None:
    """Literal quotes inside the name are backslash-escaped."""
    assert encode_folder('We"ird') == '"We\\"ird"'


def test_utf7_encoding_applied() -> None:
    """Non-ASCII names are Modified-UTF-7 encoded."""
    encoded = encode_folder("Entwürfe")
    assert "&" in encoded
    assert "ü" not in encoded
