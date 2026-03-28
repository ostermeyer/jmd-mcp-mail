"""Unit tests for mail_mcp.smtp.

All SMTP connections are mocked — no real server is contacted.
Email addresses and hostnames use example.com (RFC 2606).
"""
from __future__ import annotations

import smtplib
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from mail_mcp import smtp
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


@pytest.fixture
def mock_smtp() -> Generator[MagicMock, None, None]:
    """Yield a mock SMTP connection with smtplib.SMTP patched."""
    mock_conn = MagicMock()
    with patch("mail_mcp.smtp.smtplib.SMTP") as mock_cls:
        mock_cls.return_value.__enter__.return_value = mock_conn
        mock_cls.return_value.__exit__.return_value = False
        yield mock_conn


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

def test_send_missing_to(cfg: MailConfig) -> None:
    """Missing 'to' field returns 400 error."""
    result = smtp.send("# Message\nsubject: Hi\nbody: Text", cfg)
    assert "# Error" in result
    assert "400" in result


def test_send_missing_subject(cfg: MailConfig) -> None:
    """Missing 'subject' field returns 400 error."""
    result = smtp.send(
        "# Message\nto: r@example.com\nbody: Text", cfg
    )
    assert "# Error" in result
    assert "400" in result


def test_send_missing_body(cfg: MailConfig) -> None:
    """Missing 'body' field returns 400 error."""
    result = smtp.send(
        "# Message\nto: r@example.com\nsubject: Hi", cfg
    )
    assert "# Error" in result
    assert "400" in result


def test_send_invalid_mode(cfg: MailConfig) -> None:
    """Query document passed to send returns 400 with invalid_mode."""
    result = smtp.send("#? Message\nto: r@example.com", cfg)
    assert "# Error" in result
    assert "invalid_mode" in result


# ---------------------------------------------------------------------------
# Successful send
# ---------------------------------------------------------------------------

def test_send_success(cfg: MailConfig, mock_smtp: MagicMock) -> None:
    """Valid message is sent and confirmation document returned."""
    doc = (
        "# Message\n"
        "to: recipient@example.com\n"
        "subject: Hello\n"
        "body: Test message"
    )
    result = smtp.send(doc, cfg)
    assert "# Message" in result
    assert "sent" in result
    mock_smtp.sendmail.assert_called_once()


def test_send_includes_cc_in_recipients(
    cfg: MailConfig, mock_smtp: MagicMock
) -> None:
    """CC recipients are included in the sendmail call."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "cc: cc@example.com\n"
        "subject: Hi\n"
        "body: Body"
    )
    smtp.send(doc, cfg)
    _, recipients, _ = mock_smtp.sendmail.call_args[0]
    assert "cc@example.com" in recipients


def test_send_bcc_not_in_headers(
    cfg: MailConfig, mock_smtp: MagicMock
) -> None:
    """BCC recipients appear in sendmail call but not in message headers."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "bcc: secret@example.com\n"
        "subject: Hi\n"
        "body: Body"
    )
    smtp.send(doc, cfg)
    _, recipients, raw = mock_smtp.sendmail.call_args[0]
    assert "secret@example.com" in recipients
    assert b"secret@example.com" not in raw


# ---------------------------------------------------------------------------
# SMTP errors
# ---------------------------------------------------------------------------

def test_send_auth_failure(cfg: MailConfig) -> None:
    """SMTPAuthenticationError returns 401 error document."""
    with patch("mail_mcp.smtp.smtplib.SMTP") as mock_cls:
        mock_conn = MagicMock()
        mock_conn.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"Authentication failed"
        )
        mock_cls.return_value.__enter__.return_value = mock_conn
        mock_cls.return_value.__exit__.return_value = False
        result = smtp.send(
            "# Message\nto: r@example.com\nsubject: Hi\nbody: Text",
            cfg,
        )
    assert "# Error" in result
    assert "401" in result


def test_send_recipients_refused(cfg: MailConfig) -> None:
    """SMTPRecipientsRefused returns 400 error document."""
    with patch("mail_mcp.smtp.smtplib.SMTP") as mock_cls:
        mock_conn = MagicMock()
        mock_conn.sendmail.side_effect = smtplib.SMTPRecipientsRefused(
            {"r@example.com": (550, b"User unknown")}
        )
        mock_cls.return_value.__enter__.return_value = mock_conn
        mock_cls.return_value.__exit__.return_value = False
        result = smtp.send(
            "# Message\nto: r@example.com\nsubject: Hi\nbody: Text",
            cfg,
        )
    assert "# Error" in result
    assert "400" in result
