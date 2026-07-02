# SPDX-License-Identifier: Apache-2.0
"""Unit tests for imap/_parse.py — no IMAP connection needed."""
from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from mail_mcp.imap._parse import (
    EmailAddressRecord,
    FolderRecord,
    _decode_header,
    _parse_address,
    _parse_address_list,
    address_to_dict,
    extract_uid,
    folder_to_dict,
    message_to_dict,
    paginate_body,
    parse_list_item,
    parse_message,
)

# ---------------------------------------------------------------------------
# _decode_header
# ---------------------------------------------------------------------------


def test_decode_header_plain() -> None:
    """Plain ASCII passes through unchanged."""
    assert _decode_header("Hello World") == "Hello World"


def test_decode_header_none() -> None:
    """None returns empty string."""
    assert _decode_header(None) == ""


def test_decode_header_rfc2047() -> None:
    """RFC 2047 base64 header is decoded."""
    assert _decode_header("=?utf-8?b?SGVsbG8gV29ybGQ=?=") == "Hello World"


# ---------------------------------------------------------------------------
# _parse_address
# ---------------------------------------------------------------------------


def test_parse_address_full() -> None:
    """Full RFC 5322 address is split into name and email."""
    rec = _parse_address('"Alice Smith" <alice@example.com>')
    assert rec.name == "Alice Smith"
    assert rec.email == "alice@example.com"


def test_parse_address_bare() -> None:
    """Bare email address has empty name."""
    rec = _parse_address("bob@example.com")
    assert rec.name == ""
    assert rec.email == "bob@example.com"


def test_parse_address_none() -> None:
    """None returns empty record."""
    rec = _parse_address(None)
    assert rec.name == ""
    assert rec.email == ""


def test_parse_address_list_multiple() -> None:
    """Comma-separated addresses are split correctly."""
    recs = _parse_address_list(
        "Alice <a@x.com>, Bob <b@x.com>"
    )
    assert len(recs) == 2
    assert recs[0].email == "a@x.com"
    assert recs[1].email == "b@x.com"


# ---------------------------------------------------------------------------
# extract_uid
# ---------------------------------------------------------------------------


def test_extract_uid_present() -> None:
    """UID is extracted from a FETCH header line."""
    assert extract_uid(b"1 (UID 42 RFC822 {512})") == "42"


def test_extract_uid_missing() -> None:
    """Missing UID returns '?'."""
    assert extract_uid(b"1 (RFC822 {512})") == "?"


# ---------------------------------------------------------------------------
# parse_list_item
# ---------------------------------------------------------------------------


def test_parse_list_item_simple() -> None:
    """Simple ASCII folder name is parsed correctly."""
    rec = parse_list_item(b'(\\HasNoChildren) "/" INBOX')
    assert rec is not None
    assert rec.path == "INBOX"
    assert rec.name == "INBOX"
    assert rec.parent is None
    assert rec.delim == "/"
    assert "\\HasNoChildren" in rec.flags


def test_parse_list_item_quoted() -> None:
    """Quoted folder name with spaces is parsed correctly."""
    rec = parse_list_item(b'(\\HasNoChildren) "/" "Gesendete Objekte"')
    assert rec is not None
    assert rec.path == "Gesendete Objekte"


def test_parse_list_item_utf7() -> None:
    """Modified UTF-7 folder name is decoded to Unicode."""
    rec = parse_list_item(b'(\\HasNoChildren) "/" Entw&APw-rfe')
    assert rec is not None
    assert rec.path == "Entwürfe"


def test_parse_list_item_subfolder() -> None:
    """Sub-folder path has correct name, path, parent."""
    rec = parse_list_item(b'(\\HasNoChildren) "/" INBOX/Projects')
    assert rec is not None
    assert rec.path == "INBOX/Projects"
    assert rec.name == "Projects"
    assert rec.parent == "INBOX"


def test_parse_list_item_invalid() -> None:
    """Malformed line returns None."""
    assert parse_list_item(b"GARBAGE") is None


# ---------------------------------------------------------------------------
# parse_message (headers only)
# ---------------------------------------------------------------------------


def _make_raw(subject: str, body: str) -> bytes:
    msg = MIMEMultipart()
    msg["From"] = "sender@example.com"
    msg["To"] = "user@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg.as_bytes()


def test_parse_message_headers_only() -> None:
    """Headers-only parse fills envelope fields, body is empty."""
    raw = _make_raw("Test Subject", "Body text")
    rec = parse_message("42", raw, "INBOX", headers_only=True)
    assert rec.uid == "42"
    assert rec.folder == "INBOX"
    assert rec.subject == "Test Subject"
    assert rec.from_.email == "sender@example.com"
    assert rec.body == ""
    assert rec.attachments == []


def test_parse_message_full_body() -> None:
    """Full parse extracts body text."""
    raw = _make_raw("Subject", "Hello from plain text")
    rec = parse_message("1", raw, "INBOX")
    assert "Hello from plain text" in rec.body


def test_parse_message_threading_headers() -> None:
    """Message-ID, In-Reply-To and References are captured."""
    msg = MIMEMultipart()
    msg["From"] = "sender@example.com"
    msg["To"] = "user@example.com"
    msg["Subject"] = "Re: Thread"
    msg["Message-ID"] = "<reply@example.com>"
    msg["In-Reply-To"] = "<orig@example.com>"
    msg["References"] = "<root@example.com>\r\n <orig@example.com>"
    msg.attach(MIMEText("x", "plain", "utf-8"))
    rec = parse_message("7", msg.as_bytes(), "INBOX")
    assert rec.message_id == "<reply@example.com>"
    assert rec.in_reply_to == "<orig@example.com>"
    # Folded header whitespace is normalized to single spaces.
    assert rec.references == "<root@example.com> <orig@example.com>"


def test_parse_message_no_threading_headers() -> None:
    """Absent threading headers yield empty strings."""
    rec = parse_message("8", _make_raw("S", "b"), "INBOX")
    assert rec.message_id == ""
    assert rec.in_reply_to == ""
    assert rec.references == ""


def test_extract_body_not_capped() -> None:
    """Long bodies survive parsing in full (no silent 4000 cap)."""
    long_body = "Zeile mit Inhalt.\n" * 500  # ~9000 chars
    rec = parse_message("10", _make_raw("S", long_body), "INBOX")
    assert len(rec.body) > 8000


def test_paginate_body_single_page() -> None:
    """Short bodies are one page, no matter the page size."""
    text, pages, total = paginate_body("short", 1, 4000)
    assert (text, pages, total) == ("short", 1, 5)


def test_paginate_body_cuts_at_line_boundary() -> None:
    """Pages end at a newline, not mid-line."""
    body = "\n".join(f"line {i:04d}" for i in range(1000))
    page1, pages, total = paginate_body(body, 1, 4000)
    assert pages > 1
    assert total == len(body)
    assert page1.endswith("\n")
    # Page 2 starts exactly where page 1 ended.
    page2, _, _ = paginate_body(body, 2, 4000)
    assert body.startswith(page1 + page2[: len(page2)])


def test_paginate_body_page_clamped() -> None:
    """Out-of-range pages clamp to the last page."""
    body = "x\n" * 5000
    last, pages, _ = paginate_body(body, 999, 4000)
    expected, _, _ = paginate_body(body, pages, 4000)
    assert last == expected


def test_paginate_body_zero_disables() -> None:
    """page_size 0 returns the full text as one page."""
    body = "y" * 10000
    text, pages, total = paginate_body(body, 1, 0)
    assert text == body
    assert pages == 1
    assert total == 10000


def test_message_to_dict_paginates_long_body() -> None:
    """Long bodies emit page 1 plus pagination metadata."""
    long_body = "\n".join(f"Zeile {i:04d} mit Inhalt." for i in range(400))
    rec = parse_message("11", _make_raw("S", long_body), "INBOX")
    d = message_to_dict(rec)
    assert d["body-pages"] == 3
    assert d["body-page"] == 1
    assert d["body-chars"] == len(rec.body)
    assert len(str(d["body"])) <= 4000
    d2 = message_to_dict(rec, body_page=2)
    assert d2["body-page"] == 2
    assert d2["body"] != d["body"]


def test_message_to_dict_short_body_no_meta() -> None:
    """Single-page bodies carry no pagination noise."""
    rec = parse_message("12", _make_raw("S", "kurz"), "INBOX")
    d = message_to_dict(rec)
    assert "body-pages" not in d
    assert "body-chars" not in d


def test_message_to_dict_threading_keys() -> None:
    """message_to_dict emits threading keys only when present."""
    rec = parse_message("9", _make_raw("S", "b"), "INBOX")
    assert "message-id" not in message_to_dict(rec)
    rec.message_id = "<m@example.com>"
    rec.in_reply_to = "<o@example.com>"
    rec.references = "<r@example.com>"
    d = message_to_dict(rec)
    assert d["message-id"] == "<m@example.com>"
    assert d["in-reply-to"] == "<o@example.com>"
    assert d["references"] == "<r@example.com>"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def test_address_to_dict_with_name() -> None:
    """address_to_dict includes name when present."""
    rec = EmailAddressRecord(name="Alice", email="a@x.com")
    d = address_to_dict(rec)
    assert d["name"] == "Alice"
    assert d["email"] == "a@x.com"


def test_address_to_dict_no_name() -> None:
    """address_to_dict omits name when empty."""
    rec = EmailAddressRecord(name="", email="a@x.com")
    d = address_to_dict(rec)
    assert "name" not in d


def test_folder_to_dict_root() -> None:
    """folder_to_dict for root folder has no parent key."""
    rec = FolderRecord(
        name="INBOX", path="INBOX", parent=None,
        delim="/", flags=["\\HasNoChildren"],
        messages=10, unseen=2,
    )
    d = folder_to_dict(rec)
    assert d["path"] == "INBOX"
    assert "parent" not in d
    assert d["messages"] == 10
    assert d["unseen"] == 2


def test_message_to_dict_omits_empty_fields() -> None:
    """message_to_dict omits optional fields when empty."""
    raw = _make_raw("S", "B")
    rec = parse_message("1", raw, "INBOX")
    d = message_to_dict(rec)
    # cc / bcc / reply-to absent if empty
    assert "cc" not in d or d["cc"] == []
