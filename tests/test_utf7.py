# SPDX-License-Identifier: Apache-2.0
"""Unit tests for Modified UTF-7 codec (RFC 3501)."""
from __future__ import annotations

import pytest

from mail_mcp.utf7 import decode, encode


@pytest.mark.parametrize("name,expected_bytes", [
    ("INBOX", b"INBOX"),
    ("Sent", b"Sent"),
    ("Archive", b"Archive"),
    # '&' is encoded as '&-'
    ("A&B", b"A&-B"),
    # German umlauts
    ("Entwürfe", b"Entw&APw-rfe"),
    ("Gesendete Elemente", b"Gesendete Elemente"),
    ("Papierkorb", b"Papierkorb"),
    # Empty string
    ("", b""),
])
def test_encode(name: str, expected_bytes: bytes) -> None:
    """encode() produces correct Modified UTF-7 bytes."""
    assert encode(name) == expected_bytes


@pytest.mark.parametrize("raw,expected_name", [
    (b"INBOX", "INBOX"),
    (b"Sent", "Sent"),
    (b"Archive", "Archive"),
    (b"A&-B", "A&B"),
    (b"Entw&APw-rfe", "Entwürfe"),
    (b"Gesendete Elemente", "Gesendete Elemente"),
    (b"Papierkorb", "Papierkorb"),
    (b"", ""),
])
def test_decode(raw: bytes, expected_name: str) -> None:
    """decode() reconstructs the original Unicode name."""
    assert decode(raw) == expected_name


@pytest.mark.parametrize("name", [
    "INBOX",
    "Entwürfe",
    "Gesendete Elemente",
    "A&B",
    "日本語",
    "Ünits & Über",
])
def test_roundtrip(name: str) -> None:
    """Encode → decode round-trip is lossless."""
    assert decode(encode(name)) == name
