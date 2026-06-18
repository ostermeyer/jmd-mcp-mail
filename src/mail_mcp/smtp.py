# SPDX-License-Identifier: Apache-2.0
"""SMTP sender for jmd-mcp-mail.

Composes and sends email messages described by JMD Message documents.
Message bodies are treated as Markdown and sent as multipart/alternative
(plain text + HTML).  File attachments are supported via an
``attachments[]`` array field.

Transport: ``smtplib`` (stdlib).  The connection mode (implicit TLS
vs STARTTLS) is taken from the caller-supplied :class:`ConnectionInfo`
which derives it from the port (see ``_endpoint``).
"""
from __future__ import annotations

import base64
import smtplib
from email import encoders as email_encoders
from email import policy as email_policy
from email.message import EmailMessage
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate
from pathlib import Path

import markdown as md  # type: ignore[import-untyped]
from jmd import jmd_mode, jmd_to_dict, serialize

from mail_mcp._endpoint import ConnectionInfo, TlsMode, xoauth2_string

_LABEL = "Message"
_REPO_URL = "https://github.com/ostermeyer/jmd-mcp-mail"
_FOOTER = (
    "\n\n---\n"
    "*This email was sent by an AI assistant using "
    f"[jmd-mcp-mail]({_REPO_URL}).*"
)


def send(document: str, info: ConnectionInfo) -> str:
    """Send an email described by a JMD Message document.

    Args:
        document: JMD data document with ``to``, ``subject``, ``body``
            fields and optional ``cc``, ``bcc``, ``attachments[]``.
        info: Resolved connection parameters (host/port/TLS-mode/
            username/password).  Built by the caller via
            :meth:`ConnectionInfo.resolve`.

    Returns:
        JMD confirmation document on success, ``# Error`` document
        otherwise.
    """
    mode = jmd_mode(document)
    if mode != "data":
        return _error(
            400, "invalid_mode",
            "send requires a data document (# Message)",
        )

    fields = jmd_to_dict(document)
    if not isinstance(fields, dict):
        return _error(400, "invalid_document", "Expected a Message object")

    to_raw = str(fields.get("to", "")).strip()
    subject = str(fields.get("subject", "")).strip()
    body = str(fields.get("body", "")).strip()
    cc_raw = str(fields.get("cc", "")).strip()
    bcc_raw = str(fields.get("bcc", "")).strip()
    from_name = str(fields.get("from-name", "")).strip()
    attachments_raw = fields.get("attachments", [])

    if not to_raw:
        return _error(400, "missing_fields", "'to' is required")
    if not subject:
        return _error(400, "missing_fields", "'subject' is required")
    if not body:
        return _error(400, "missing_fields", "'body' is required")

    to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
    cc_addrs = [a.strip() for a in cc_raw.split(",") if a.strip()]
    bcc_addrs = [a.strip() for a in bcc_raw.split(",") if a.strip()]
    all_recipients = to_addrs + cc_addrs + bcc_addrs

    attach_paths: list[Path] = []
    if isinstance(attachments_raw, list):
        for item in attachments_raw:
            if isinstance(item, dict):
                p = str(item.get("path", "")).strip()
                if p:
                    attach_paths.append(Path(p))

    body_with_footer = body + _FOOTER
    # Extensions chosen for email rendering:
    #   extra      — fenced code, tables, etc.
    #   sane_lists — predictable list handling (respects start numbers,
    #                doesn't promote under-indented items to phantom <li>)
    #   nl2br      — preserve single line breaks; mail bodies are written
    #                with hard newlines, not Markdown soft-wrap convention
    html_body = md.markdown(
        body_with_footer,
        extensions=["extra", "sane_lists", "nl2br"],
    )
    date = formatdate(usegmt=True)

    # Optional display name on the From header (e.g. "Andreas Ostermeyer
    # <a@b.de>"). The envelope sender stays the bare address (info.username
    # in _deliver) — only the header carries the name.
    from_header = (
        formataddr((from_name, info.username)) if from_name
        else info.username
    )

    if attach_paths:
        msg_obj = _build_multipart(
            from_header, to_addrs, cc_addrs, subject,
            body_with_footer, html_body, attach_paths, date,
        )
        # policy.SMTP serializes with CRLF line endings. sendmail()
        # sends bytes as-is (no eol fixup), and a bare-LF message gets
        # eol-normalized by the receiving MTA — which corrupts the QP
        # soft-breaks (=\n) at the 76-char boundary. CRLF avoids that.
        raw_bytes = msg_obj.as_bytes(policy=email_policy.SMTP)
    else:
        plain_msg = EmailMessage()
        plain_msg["From"] = from_header
        plain_msg["To"] = ", ".join(to_addrs)
        plain_msg["Subject"] = subject
        plain_msg["Date"] = date
        if cc_addrs:
            plain_msg["Cc"] = ", ".join(cc_addrs)
        # cte="quoted-printable" forces a 7-bit-safe transfer encoding.
        # The default would be "8bit" (raw UTF-8 bytes), which is only
        # safe when BODY=8BITMIME is negotiated — sendmail() does not do
        # that, so 8-bit parts get mangled in transit (non-ASCII → mojibake).
        plain_msg.set_content(body_with_footer, cte="quoted-printable")
        plain_msg.add_alternative(
            html_body, subtype="html", cte="quoted-printable",
        )
        # policy.SMTP → CRLF line endings (see note above): without it
        # the bare-LF QP soft-breaks get corrupted in transit.
        raw_bytes = plain_msg.as_bytes(policy=email_policy.SMTP)

    try:
        _deliver(info, all_recipients, raw_bytes)
    except smtplib.SMTPAuthenticationError as exc:
        err_msg = (
            exc.smtp_error.decode()
            if isinstance(exc.smtp_error, bytes)
            else str(exc.smtp_error)
        )
        return _error(
            401, "auth_failed",
            f"SMTP authentication failed: {err_msg}",
        )
    except smtplib.SMTPRecipientsRefused as exc:
        refused = ", ".join(exc.recipients)
        return _error(
            400, "recipients_refused", f"Recipients refused: {refused}"
        )
    except smtplib.SMTPException as exc:
        return _error(500, "smtp_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))

    return serialize(
        {"to": ", ".join(to_addrs), "subject": subject, "status": "sent"},
        label=_LABEL,
    )


def _deliver(
    info: ConnectionInfo,
    recipients: list[str],
    raw_bytes: bytes,
) -> None:
    """Open an SMTP connection per ``info.tls_mode`` and deliver.

    Args:
        info: Resolved connection parameters.
        recipients: Envelope-to addresses (To + Cc + Bcc).
        raw_bytes: Encoded message bytes.

    Raises:
        smtplib.SMTPException: Any SMTP-level failure.
        OSError: Network-level failure (connect, DNS, timeout).
    """
    if info.tls_mode == TlsMode.IMPLICIT:
        cm: smtplib.SMTP = smtplib.SMTP_SSL(
            info.host, info.port, timeout=30,
        )
    else:
        cm = smtplib.SMTP(info.host, info.port, timeout=30)

    with cm as conn:
        if info.tls_mode == TlsMode.STARTTLS:
            conn.ehlo()
            conn.starttls()
        conn.ehlo()
        if info.access_token:
            sasl = xoauth2_string(info.username, info.access_token)
            code, msg = conn.docmd(
                "AUTH",
                "XOAUTH2 " + base64.b64encode(sasl.encode()).decode(),
            )
            if code != 235:
                raise smtplib.SMTPAuthenticationError(code, msg)
        else:
            conn.login(info.username, info.password)
        conn.sendmail(info.username, recipients, raw_bytes)



def _build_multipart(
    sender: str,
    to_addrs: list[str],
    cc_addrs: list[str],
    subject: str,
    plain: str,
    html: str,
    attach_paths: list[Path],
    date: str,
) -> MIMEMultipart:
    """Build a multipart/mixed message with alternative body + attachments."""
    outer = MIMEMultipart("mixed")
    outer["From"] = sender
    outer["To"] = ", ".join(to_addrs)
    outer["Subject"] = subject
    outer["Date"] = date
    if cc_addrs:
        outer["Cc"] = ", ".join(cc_addrs)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    outer.attach(alt)

    for path in attach_paths:
        if not path.exists():
            continue
        part = MIMEBase("application", "octet-stream")
        part.set_payload(path.read_bytes())
        email_encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=path.name,
        )
        outer.attach(part)

    return outer


def _error(status: int, code: str, message: str) -> str:
    """Serialize a JMD error document."""
    return serialize(
        {"status": status, "code": code, "message": message},
        label="Error",
    )
