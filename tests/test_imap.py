"""Unit tests for mail_mcp.imap.

All IMAP connections are mocked — no real server is contacted.
Email addresses and hostnames use example.com (RFC 2606).
"""
from __future__ import annotations

import email
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

import pytest

from mail_mcp import imap
from mail_mcp.config import MailConfig


@pytest.fixture
def cfg() -> MailConfig:
    """Return a MailConfig with example.com placeholders."""
    return MailConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        imap_host="imap.example.com",
        imap_port=993,
        username="user@example.com",
        password="test-password",
    )


def _plain_bytes(subject: str, body: str) -> bytes:
    """Build a minimal RFC 2822 plaintext message."""
    msg = MIMEMultipart()
    msg["From"] = "sender@example.com"
    msg["To"] = "user@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg.as_bytes()


def _html_bytes(subject: str, html: str) -> bytes:
    """Build a minimal RFC 2822 HTML-only message."""
    msg = MIMEMultipart()
    msg["From"] = "sender@example.com"
    msg["To"] = "user@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg.as_bytes()


_UID_FETCH_META = b"1 (UID 42 RFC822 {512})"
_HEADER_FETCH_META = b"1 (UID 42 BODY[HEADER] {256})"
_HEADER_BYTES = (
    b"From: sender@example.com\r\n"
    b"To: user@example.com\r\n"
    b"Subject: Test subject\r\n"
    b"Date: Mon, 01 Jan 2024 12:00:00 +0000\r\n\r\n"
)


# ---------------------------------------------------------------------------
# _decode_header
# ---------------------------------------------------------------------------

def test_decode_header_plain() -> None:
    """Plain ASCII header passes through unchanged."""
    assert imap._decode_header("Hello World") == "Hello World"


def test_decode_header_none() -> None:
    """None input returns empty string."""
    assert imap._decode_header(None) == ""


def test_decode_header_rfc2047() -> None:
    """RFC 2047 base64-encoded header is decoded to Unicode."""
    # "Hello World" encoded as UTF-8 base64
    encoded = "=?utf-8?b?SGVsbG8gV29ybGQ=?="
    assert imap._decode_header(encoded) == "Hello World"


# ---------------------------------------------------------------------------
# _extract_body
# ---------------------------------------------------------------------------

def test_extract_body_plain() -> None:
    """Plain-text body is extracted as-is."""
    raw = _plain_bytes("Test", "Hello from plain text")
    msg = email.message_from_bytes(raw)
    assert "Hello from plain text" in imap._extract_body(msg)


def test_extract_body_html_fallback() -> None:
    """HTML-only message body is converted to Markdown."""
    raw = _html_bytes("Test", "<h1>Title</h1><p>Some text.</p>")
    msg = email.message_from_bytes(raw)
    body = imap._extract_body(msg)
    assert "Title" in body
    assert "Some text" in body


def test_extract_body_prefers_plain_over_html() -> None:
    """Multipart/alternative message returns text/plain, not HTML."""
    msg = MIMEMultipart("alternative")
    msg["From"] = "sender@example.com"
    msg["Subject"] = "Alt"
    msg.attach(MIMEText("plain content", "plain", "utf-8"))
    msg.attach(MIMEText("<b>html content</b>", "html", "utf-8"))
    body = imap._extract_body(email.message_from_bytes(msg.as_bytes()))
    assert "plain content" in body
    assert "html content" not in body


# ---------------------------------------------------------------------------
# read — schema
# ---------------------------------------------------------------------------

def test_read_schema(cfg: MailConfig) -> None:
    """Schema document returns #! Message with expected fields."""
    result = imap.read("#! Message", cfg)
    assert result.startswith("#! Message")
    assert "id" in result
    assert "subject" in result
    assert "body" in result


# ---------------------------------------------------------------------------
# read — data (single message)
# ---------------------------------------------------------------------------

def test_read_data_missing_id(cfg: MailConfig) -> None:
    """Data read without id returns 400 error."""
    result = imap.read("# Message\nfolder: INBOX", cfg)
    assert "# Error" in result
    assert "400" in result


def test_read_data_fetches_message(cfg: MailConfig) -> None:
    """Data read with valid id fetches and returns the message."""
    raw = _plain_bytes("Hello", "Test body content")
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.uid.return_value = ("OK", [(_UID_FETCH_META, raw)])

    with patch("mail_mcp.imap.imaplib.IMAP4_SSL") as mock_cls:
        mock_cls.return_value = mock_conn
        result = imap.read("# Message\nid: 42\nfolder: INBOX", cfg)

    assert "# Message" in result
    assert "Test body content" in result


def test_read_data_not_found(cfg: MailConfig) -> None:
    """IMAP returning empty payload produces a 404 error."""
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [None])

    with patch("mail_mcp.imap.imaplib.IMAP4_SSL") as mock_cls:
        mock_cls.return_value = mock_conn
        result = imap.read("# Message\nid: 999\nfolder: INBOX", cfg)

    assert "# Error" in result
    assert "404" in result


def test_read_invalid_mode(cfg: MailConfig) -> None:
    """Delete document passed to read returns 400 error."""
    result = imap.read("#- Message\nid: 1", cfg)
    assert "# Error" in result
    assert "400" in result


# ---------------------------------------------------------------------------
# read — query (list)
# ---------------------------------------------------------------------------

def test_read_query_empty_inbox(cfg: MailConfig) -> None:
    """Query with no matching UIDs returns an empty list."""
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"0"])
    mock_conn.uid.return_value = ("OK", [b""])

    with patch("mail_mcp.imap.imaplib.IMAP4_SSL") as mock_cls:
        mock_cls.return_value = mock_conn
        result = imap.read("#? Message", cfg)

    assert "# []" in result


def test_read_query_returns_envelopes(cfg: MailConfig) -> None:
    """Query with results returns message envelopes with headers."""
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.uid.side_effect = [
        ("OK", [b"42"]),                                        # SEARCH
        ("OK", [(_HEADER_FETCH_META, _HEADER_BYTES)]),          # FETCH
    ]

    with patch("mail_mcp.imap.imaplib.IMAP4_SSL") as mock_cls:
        mock_cls.return_value = mock_conn
        result = imap.read("#? Message", cfg)

    assert "Test subject" in result


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def test_delete_missing_id(cfg: MailConfig) -> None:
    """Delete without id returns 400 error."""
    result = imap.delete("#- Message\nfolder: INBOX", cfg)
    assert "# Error" in result
    assert "400" in result


def test_delete_success(cfg: MailConfig) -> None:
    """Successful delete returns deleted: 1."""
    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.uid.return_value = ("OK", [b""])

    with patch("mail_mcp.imap.imaplib.IMAP4_SSL") as mock_cls:
        mock_cls.return_value = mock_conn
        result = imap.delete("#- Message\nid: 42\nfolder: INBOX", cfg)

    assert "deleted" in result
    assert "1" in result


def test_delete_invalid_mode(cfg: MailConfig) -> None:
    """Data document passed to delete returns 400 with invalid_mode."""
    result = imap.delete("# Message\nid: 1", cfg)
    assert "# Error" in result
    assert "invalid_mode" in result
