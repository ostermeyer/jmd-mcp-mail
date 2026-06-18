# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``mail_mcp.smtp``.

All SMTP connections are mocked — no real server is contacted.
Email addresses and hostnames use ``example.com`` (RFC 2606).
"""
from __future__ import annotations

import email
import email.policy
import smtplib
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from mail_mcp import smtp
from mail_mcp._endpoint import ConnectionInfo, TlsMode


@pytest.fixture
def info() -> ConnectionInfo:
    """STARTTLS connection on the standard submission port."""
    return ConnectionInfo(
        host="smtp.example.com",
        port=587,
        tls_mode=TlsMode.STARTTLS,
        username="user@example.com",
        password="test-password",
    )


@pytest.fixture
def info_smtps() -> ConnectionInfo:
    """Implicit-TLS connection on the SMTPS port."""
    return ConnectionInfo(
        host="smtp.example.com",
        port=465,
        tls_mode=TlsMode.IMPLICIT,
        username="user@example.com",
        password="test-password",
    )


@pytest.fixture
def mock_smtp() -> Generator[MagicMock, None, None]:
    """Yield a mock SMTP connection with ``smtplib.SMTP`` patched."""
    mock_conn = MagicMock()
    with patch("mail_mcp.smtp.smtplib.SMTP") as mock_cls:
        mock_cls.return_value.__enter__.return_value = mock_conn
        mock_cls.return_value.__exit__.return_value = False
        yield mock_conn


@pytest.fixture
def mock_smtp_ssl() -> Generator[MagicMock, None, None]:
    """Yield a mock SMTP_SSL connection (implicit-TLS path)."""
    mock_conn = MagicMock()
    with patch("mail_mcp.smtp.smtplib.SMTP_SSL") as mock_cls:
        mock_cls.return_value.__enter__.return_value = mock_conn
        mock_cls.return_value.__exit__.return_value = False
        yield mock_conn


# ---------------------------------------------------------------------------
# Validation errors (no transport contacted)
# ---------------------------------------------------------------------------


def test_send_missing_to(info: ConnectionInfo) -> None:
    """Missing ``to`` field returns 400 error."""
    result = smtp.send("# Message\nsubject: Hi\nbody: Text", info)
    assert "# Error" in result
    assert "400" in result


def test_send_missing_subject(info: ConnectionInfo) -> None:
    """Missing ``subject`` field returns 400 error."""
    result = smtp.send(
        "# Message\nto: r@example.com\nbody: Text", info,
    )
    assert "# Error" in result
    assert "400" in result


def test_send_missing_body(info: ConnectionInfo) -> None:
    """Missing ``body`` field returns 400 error."""
    result = smtp.send(
        "# Message\nto: r@example.com\nsubject: Hi", info,
    )
    assert "# Error" in result
    assert "400" in result


def test_send_invalid_mode(info: ConnectionInfo) -> None:
    """Query document passed to ``send`` returns 400 invalid_mode."""
    result = smtp.send("#? Message\nto: r@example.com", info)
    assert "# Error" in result
    assert "invalid_mode" in result


# ---------------------------------------------------------------------------
# Successful send — STARTTLS
# ---------------------------------------------------------------------------


def test_send_starttls_calls_starttls(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """STARTTLS path invokes ``conn.starttls()`` before login."""
    doc = (
        "# Message\n"
        "to: recipient@example.com\n"
        "subject: Hello\n"
        "body: Test message"
    )
    result = smtp.send(doc, info)
    assert "# Message" in result
    assert "sent" in result
    mock_smtp.starttls.assert_called_once()
    mock_smtp.sendmail.assert_called_once()


def test_send_includes_cc_in_recipients(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """CC recipients are included in the ``sendmail`` envelope."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "cc: cc@example.com\n"
        "subject: Hi\n"
        "body: Body"
    )
    smtp.send(doc, info)
    _, recipients, _ = mock_smtp.sendmail.call_args[0]
    assert "cc@example.com" in recipients


def test_send_bcc_not_in_headers(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """BCC recipients are envelope-only, not in message headers."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "bcc: secret@example.com\n"
        "subject: Hi\n"
        "body: Body"
    )
    smtp.send(doc, info)
    _, recipients, raw = mock_smtp.sendmail.call_args[0]
    assert "secret@example.com" in recipients
    assert b"secret@example.com" not in raw


# ---------------------------------------------------------------------------
# Successful send — implicit TLS (SMTPS, port 465)
# ---------------------------------------------------------------------------


def test_send_smtps_uses_smtp_ssl(
    info_smtps: ConnectionInfo, mock_smtp_ssl: MagicMock,
) -> None:
    """Implicit-TLS path opens ``SMTP_SSL`` and skips STARTTLS."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "body: Body"
    )
    result = smtp.send(doc, info_smtps)
    assert "sent" in result
    mock_smtp_ssl.starttls.assert_not_called()
    mock_smtp_ssl.sendmail.assert_called_once()


# ---------------------------------------------------------------------------
# SMTP errors
# ---------------------------------------------------------------------------


def test_send_auth_failure(info: ConnectionInfo) -> None:
    """``SMTPAuthenticationError`` returns 401 error document."""
    with patch("mail_mcp.smtp.smtplib.SMTP") as mock_cls:
        mock_conn = MagicMock()
        mock_conn.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"Authentication failed",
        )
        mock_cls.return_value.__enter__.return_value = mock_conn
        mock_cls.return_value.__exit__.return_value = False
        result = smtp.send(
            "# Message\nto: r@example.com\nsubject: Hi\nbody: Text",
            info,
        )
    assert "# Error" in result
    assert "401" in result


def test_send_recipients_refused(info: ConnectionInfo) -> None:
    """``SMTPRecipientsRefused`` returns 400 error document."""
    with patch("mail_mcp.smtp.smtplib.SMTP") as mock_cls:
        mock_conn = MagicMock()
        mock_conn.sendmail.side_effect = smtplib.SMTPRecipientsRefused(
            {"r@example.com": (550, b"User unknown")},
        )
        mock_cls.return_value.__enter__.return_value = mock_conn
        mock_cls.return_value.__exit__.return_value = False
        result = smtp.send(
            "# Message\nto: r@example.com\nsubject: Hi\nbody: Text",
            info,
        )
    assert "# Error" in result
    assert "400" in result


# ---------------------------------------------------------------------------
# Leading dots are left to smtplib's own RFC-821 dot-stuffing
# ---------------------------------------------------------------------------


def test_send_does_not_pre_escape_leading_dots(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """Leading dots stay literal; smtplib does the SMTP dot-stuffing.

    We used to rewrite a line-leading `.` to `=2E` as a defense against
    a corrupted soft-break before the dot (observed against IONOS). That
    was a symptom of bare-LF soft-breaks; the CRLF policy fix removes the
    root cause, and smtplib.SMTP.data() already dot-stuffs per RFC 821.
    So no `=2E` pre-escaping should appear in the bytes we hand to
    sendmail — the literal dot is preserved and stuffed by smtplib.
    """
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "body:\n"
        "> normale Zeile\n"
        "> .com-fuehrender-punkt"
    )
    smtp.send(doc, info)
    _, _, raw = mock_smtp.sendmail.call_args[0]
    assert b"=2E" not in raw
    # The leading-dot text is present (decoded), not rewritten.
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    plain = next(
        p for p in msg.walk() if p.get_content_type() == "text/plain"
    ).get_content()
    assert ".com-fuehrender-punkt" in plain


# ---------------------------------------------------------------------------
# Body encoding (non-ASCII transport safety) + Markdown rendering
# ---------------------------------------------------------------------------


def test_send_nonascii_uses_quoted_printable_not_8bit(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """Non-ASCII parts are quoted-printable, never raw 8bit.

    8bit text parts get mangled in transit when BODY=8BITMIME isn't
    negotiated (sendmail does not). The arrow (U+2192) must therefore
    be QP-encoded, and its raw UTF-8 bytes must not appear verbatim.
    """
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "body: Pfeil → Umlaut ä"
    )
    smtp.send(doc, info)
    _, _, raw = mock_smtp.sendmail.call_args[0]
    assert b"Content-Transfer-Encoding: 8bit" not in raw
    assert b"quoted-printable" in raw
    # Raw UTF-8 of the arrow must NOT be present (it is QP-encoded).
    assert "→".encode() not in raw


def test_send_nonascii_roundtrips_in_both_parts(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """Decoded plain and HTML parts both preserve non-ASCII characters."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "body: Pfeil → Umlaute äöü"
    )
    smtp.send(doc, info)
    _, _, raw = mock_smtp.sendmail.call_args[0]
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    for ctype in ("text/plain", "text/html"):
        part = next(
            p for p in msg.walk() if p.get_content_type() == ctype
        )
        assert part["Content-Transfer-Encoding"] == "quoted-printable"
        content = part.get_content()
        assert "→" in content
        assert "äöü" in content


def test_send_long_line_uses_crlf_soft_breaks(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    r"""Long lines wrap with CRLF QP soft-breaks, never bare LF.

    sendmail() sends bytes as-is; a bare-LF soft-break (=\n) gets
    eol-normalized by the receiving MTA, dropping the char at the
    76-col boundary (Claude->Cl=ude). policy.SMTP forces CRLF so the
    soft-break survives transit intact.
    """
    marker = "Claude-SmartSuite-Marker-Ende"
    body = "Auffuellungstext " * 8 + marker  # push marker past col 76
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        f"body: {body}"
    )
    smtp.send(doc, info)
    _, _, raw = mock_smtp.sendmail.call_args[0]
    # QP soft-breaks are present (long line) and all CRLF, none bare-LF.
    assert b"=\r\n" in raw
    assert b"=\n" not in raw
    # Content survives the wrap intact.
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    html = next(
        p for p in msg.walk() if p.get_content_type() == "text/html"
    ).get_content()
    assert marker in html


def test_send_html_renders_ordered_and_unordered_lists(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """A numbered list and a bullet list render as separate <ol>/<ul>."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "body:\n"
        "> 1. eins\n"
        "> 2. zwei\n"
        "> \n"
        "> - a\n"
        "> - b"
    )
    smtp.send(doc, info)
    _, _, raw = mock_smtp.sendmail.call_args[0]
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    html_part = next(
        p for p in msg.walk() if p.get_content_type() == "text/html"
    )
    html = html_part.get_content()
    assert "<ol>" in html
    assert "<ul>" in html


# ---------------------------------------------------------------------------
# from-name (optional display name on the From header)
# ---------------------------------------------------------------------------


def test_send_from_name_sets_display_name(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """'from-name' produces a 'Name <addr>' From header."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "from-name: Andreas Ostermeyer\n"
        "body: Text"
    )
    smtp.send(doc, info)
    envelope_from, _, raw = mock_smtp.sendmail.call_args[0]
    # Envelope sender stays the bare address — only the header changes.
    assert envelope_from == "user@example.com"
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert msg["From"] == "Andreas Ostermeyer <user@example.com>"


def test_send_without_from_name_uses_bare_address(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """Omitting 'from-name' leaves the From header as the bare address."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "body: Text"
    )
    smtp.send(doc, info)
    _, _, raw = mock_smtp.sendmail.call_args[0]
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert msg["From"] == "user@example.com"
