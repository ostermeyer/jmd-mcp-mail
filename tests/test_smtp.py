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


async def test_send_missing_to(info: ConnectionInfo) -> None:
    """Missing ``to`` field returns 400 error."""
    result = await smtp.send("# Message\nsubject: Hi\nbody: Text", info)
    assert "# Error" in result
    assert "400" in result


async def test_send_missing_subject(info: ConnectionInfo) -> None:
    """Missing ``subject`` field returns 400 error."""
    result = await smtp.send(
        "# Message\nto: r@example.com\nbody: Text", info,
    )
    assert "# Error" in result
    assert "400" in result


async def test_send_missing_body(info: ConnectionInfo) -> None:
    """Missing ``body`` field returns 400 error."""
    result = await smtp.send(
        "# Message\nto: r@example.com\nsubject: Hi", info,
    )
    assert "# Error" in result
    assert "400" in result


async def test_send_invalid_mode(info: ConnectionInfo) -> None:
    """Query document passed to ``send`` returns 400 invalid_mode."""
    result = await smtp.send("#? Message\nto: r@example.com", info)
    assert "# Error" in result
    assert "invalid_mode" in result


# ---------------------------------------------------------------------------
# Successful send — STARTTLS
# ---------------------------------------------------------------------------


async def test_send_starttls_calls_starttls(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """STARTTLS path invokes ``conn.starttls()`` before login."""
    doc = (
        "# Message\n"
        "to: recipient@example.com\n"
        "subject: Hello\n"
        "body: Test message"
    )
    result = await smtp.send(doc, info)
    assert "# Message" in result
    assert "sent" in result
    mock_smtp.starttls.assert_called_once()
    mock_smtp.sendmail.assert_called_once()


async def test_send_includes_cc_in_recipients(
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
    await smtp.send(doc, info)
    _, recipients, _ = mock_smtp.sendmail.call_args[0]
    assert "cc@example.com" in recipients


async def test_send_bcc_not_in_headers(
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
    await smtp.send(doc, info)
    _, recipients, raw = mock_smtp.sendmail.call_args[0]
    assert "secret@example.com" in recipients
    assert b"secret@example.com" not in raw


# ---------------------------------------------------------------------------
# Successful send — implicit TLS (SMTPS, port 465)
# ---------------------------------------------------------------------------


async def test_send_smtps_uses_smtp_ssl(
    info_smtps: ConnectionInfo, mock_smtp_ssl: MagicMock,
) -> None:
    """Implicit-TLS path opens ``SMTP_SSL`` and skips STARTTLS."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "body: Body"
    )
    result = await smtp.send(doc, info_smtps)
    assert "sent" in result
    mock_smtp_ssl.starttls.assert_not_called()
    mock_smtp_ssl.sendmail.assert_called_once()


# ---------------------------------------------------------------------------
# SMTP errors
# ---------------------------------------------------------------------------


async def test_send_auth_failure(info: ConnectionInfo) -> None:
    """``SMTPAuthenticationError`` returns 401 error document."""
    with patch("mail_mcp.smtp.smtplib.SMTP") as mock_cls:
        mock_conn = MagicMock()
        mock_conn.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"Authentication failed",
        )
        mock_cls.return_value.__enter__.return_value = mock_conn
        mock_cls.return_value.__exit__.return_value = False
        result = await smtp.send(
            "# Message\nto: r@example.com\nsubject: Hi\nbody: Text",
            info,
        )
    assert "# Error" in result
    assert "401" in result


async def test_send_recipients_refused(info: ConnectionInfo) -> None:
    """``SMTPRecipientsRefused`` returns 400 error document."""
    with patch("mail_mcp.smtp.smtplib.SMTP") as mock_cls:
        mock_conn = MagicMock()
        mock_conn.sendmail.side_effect = smtplib.SMTPRecipientsRefused(
            {"r@example.com": (550, b"User unknown")},
        )
        mock_cls.return_value.__enter__.return_value = mock_conn
        mock_cls.return_value.__exit__.return_value = False
        result = await smtp.send(
            "# Message\nto: r@example.com\nsubject: Hi\nbody: Text",
            info,
        )
    assert "# Error" in result
    assert "400" in result


# ---------------------------------------------------------------------------
# Leading dots are left to smtplib's own RFC-821 dot-stuffing
# ---------------------------------------------------------------------------


async def test_send_does_not_pre_escape_leading_dots(
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
    await smtp.send(doc, info)
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


async def test_send_nonascii_uses_quoted_printable_not_8bit(
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
    await smtp.send(doc, info)
    _, _, raw = mock_smtp.sendmail.call_args[0]
    assert b"Content-Transfer-Encoding: 8bit" not in raw
    assert b"quoted-printable" in raw
    # Raw UTF-8 of the arrow must NOT be present (it is QP-encoded).
    assert "→".encode() not in raw


async def test_send_nonascii_roundtrips_in_both_parts(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """Decoded plain and HTML parts both preserve non-ASCII characters."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "body: Pfeil → Umlaute äöü"
    )
    await smtp.send(doc, info)
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


async def test_send_long_line_uses_crlf_soft_breaks(
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
    await smtp.send(doc, info)
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


async def test_send_html_renders_ordered_and_unordered_lists(
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
    await smtp.send(doc, info)
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


async def test_send_from_name_sets_display_name(
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
    await smtp.send(doc, info)
    envelope_from, _, raw = mock_smtp.sendmail.call_args[0]
    # Envelope sender stays the bare address — only the header changes.
    assert envelope_from == "user@example.com"
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert msg["From"] == "Andreas Ostermeyer <user@example.com>"


async def test_send_without_from_name_uses_bare_address(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """Omitting 'from-name' leaves the From header as the bare address."""
    doc = (
        "# Message\n"
        "to: r@example.com\n"
        "subject: Hi\n"
        "body: Text"
    )
    await smtp.send(doc, info)
    _, _, raw = mock_smtp.sendmail.call_args[0]
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert msg["From"] == "user@example.com"


# ---------------------------------------------------------------------------
# Sent-copy after delivery
# ---------------------------------------------------------------------------

_DOC = (
    "# Message\n"
    "to: r@example.com\n"
    "subject: Hi\n"
    "body: Text"
)


def _imap_info() -> ConnectionInfo:
    return ConnectionInfo(
        host="imap.example.com",
        port=993,
        tls_mode=TlsMode.IMPLICIT,
        username="user@example.com",
        password="pw",
    )


async def test_send_reports_message_id(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """The response document carries the generated Message-ID."""
    result = await smtp.send(_DOC, info)
    assert "message-id:" in result
    assert "@example.com>" in result


async def test_sent_copy_disabled(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """store_sent=False reports the copy as disabled."""
    result = await smtp.send(_DOC, info, store_sent=False)
    assert "sent-copy: disabled" in result


async def test_sent_copy_failed_without_imap(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """No IMAP side degrades to failed — the send itself succeeds."""
    result = await smtp.send(_DOC, info, imap_info=None)
    assert "status: sent" in result
    assert "sent-copy: failed" in result


async def test_sent_copy_stored(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """With a working IMAP side the copy lands in the Sent folder."""
    import contextlib
    from collections.abc import AsyncGenerator
    from unittest.mock import AsyncMock

    @contextlib.asynccontextmanager
    async def _fake_open(
        _info: ConnectionInfo,
    ) -> AsyncGenerator[MagicMock, None]:
        yield MagicMock()

    append = AsyncMock(return_value="4711")
    with (
        patch.object(smtp, "open_imap", _fake_open),
        patch.object(
            smtp, "find_special_folder",
            new=AsyncMock(return_value="Sent"),
        ),
        patch.object(smtp, "append_raw", new=append),
    ):
        result = await smtp.send(_DOC, info, imap_info=_imap_info())
    assert "sent-copy: stored" in result
    assert "sent-folder: Sent" in result
    assert '"4711"' in result
    # The stored bytes are exactly the delivered bytes (\Seen flag).
    _, _, delivered = mock_smtp.sendmail.call_args[0]
    assert append.await_args.args[2] == delivered
    assert append.await_args.args[3] == r"(\Seen)"


async def test_sent_copy_failure_never_breaks_send(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """An IMAP exception during the copy still reports status: sent."""
    import contextlib
    from collections.abc import AsyncGenerator

    @contextlib.asynccontextmanager
    async def _broken_open(
        _info: ConnectionInfo,
    ) -> AsyncGenerator[MagicMock, None]:
        raise OSError("imap down")
        yield MagicMock()  # pragma: no cover

    with patch.object(smtp, "open_imap", _broken_open):
        result = await smtp.send(_DOC, info, imap_info=_imap_info())
    assert "status: sent" in result
    assert "sent-copy: failed" in result


# ---------------------------------------------------------------------------
# Reply threading (in-reply-to frontmatter)
# ---------------------------------------------------------------------------


async def test_send_reply_threads_and_defaults_to(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """A reply gets In-Reply-To/References and a defaulted To."""
    import contextlib
    from collections.abc import AsyncGenerator
    from unittest.mock import AsyncMock

    from mail_mcp.imap._thread import OriginalHeaders

    @contextlib.asynccontextmanager
    async def _fake_open(
        _info: ConnectionInfo,
    ) -> AsyncGenerator[MagicMock, None]:
        yield MagicMock()

    orig = OriginalHeaders(
        message_id="<orig@example.com>",
        references="<root@example.com>",
        subject="Zahlen",
        from_addr="Alice <alice@example.com>",
        reply_to="",
    )
    doc = (
        "in-reply-to: 42\n"
        "\n"
        "# Message\n"
        "body: Passt, danke!"
    )
    with (
        patch.object(smtp, "open_imap", _fake_open),
        patch.object(
            smtp, "fetch_original", new=AsyncMock(return_value=orig),
        ),
    ):
        result = await smtp.send(
            doc, info, imap_info=_imap_info(), store_sent=False,
        )
    assert "status: sent" in result
    _, recipients, raw = mock_smtp.sendmail.call_args[0]
    assert recipients == ["alice@example.com"]
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert msg["In-Reply-To"] == "<orig@example.com>"
    assert msg["References"] == "<root@example.com> <orig@example.com>"
    assert msg["Subject"] == "Re: Zahlen"


async def test_send_reply_with_quote(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """quote: true appends the original text below the reply body."""
    import contextlib
    from collections.abc import AsyncGenerator
    from unittest.mock import AsyncMock

    from mail_mcp.imap._thread import OriginalHeaders

    @contextlib.asynccontextmanager
    async def _fake_open(
        _info: ConnectionInfo,
    ) -> AsyncGenerator[MagicMock, None]:
        yield MagicMock()

    orig = OriginalHeaders(
        message_id="<orig@example.com>",
        references="",
        subject="Zahlen",
        from_addr="Alice <alice@example.com>",
        reply_to="",
        date="Tue, 01 Jul 2026 10:00:00 +0000",
        body="Originaltext mit Details.",
    )
    doc = "in-reply-to: 42\nquote: true\n\n# Message\nbody: Passt!"
    with (
        patch.object(smtp, "open_imap", _fake_open),
        patch.object(
            smtp, "fetch_original", new=AsyncMock(return_value=orig),
        ),
    ):
        result = await smtp.send(
            doc, info, imap_info=_imap_info(), store_sent=False,
        )
    assert "status: sent" in result
    _, _, raw = mock_smtp.sendmail.call_args[0]
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    plain = next(
        p for p in msg.walk() if p.get_content_type() == "text/plain"
    ).get_content()
    assert plain.startswith("Passt!")
    assert "> Originaltext mit Details." in plain


async def test_send_quote_without_reply_rejected(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """Quote without in-reply-to is refused before any delivery."""
    doc = "quote: true\n\n# Message\nto: r@example.com\nsubject: x\nbody: y"
    result = await smtp.send(doc, info)
    assert "# Error" in result
    assert "in-reply-to" in result
    mock_smtp.sendmail.assert_not_called()


async def test_send_reply_without_imap_side_errors(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """in-reply-to without an IMAP side is refused, nothing is sent."""
    doc = "in-reply-to: 42\n\n# Message\nbody: x"
    result = await smtp.send(doc, info, imap_info=None)
    assert "imap_required" in result
    mock_smtp.sendmail.assert_not_called()


async def test_send_reply_original_missing_errors(
    info: ConnectionInfo, mock_smtp: MagicMock,
) -> None:
    """A vanished original aborts the send with 404."""
    import contextlib
    from collections.abc import AsyncGenerator
    from unittest.mock import AsyncMock

    @contextlib.asynccontextmanager
    async def _fake_open(
        _info: ConnectionInfo,
    ) -> AsyncGenerator[MagicMock, None]:
        yield MagicMock()

    doc = "in-reply-to: 42\n\n# Message\nbody: x"
    with (
        patch.object(smtp, "open_imap", _fake_open),
        patch.object(
            smtp, "fetch_original", new=AsyncMock(return_value=None),
        ),
    ):
        result = await smtp.send(doc, info, imap_info=_imap_info())
    assert "not_found" in result
    mock_smtp.sendmail.assert_not_called()
