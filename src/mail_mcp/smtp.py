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

import re
import smtplib
from email import encoders as email_encoders
from email.message import EmailMessage
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path

import markdown as md  # type: ignore[import-untyped]
from jmd import jmd_mode, jmd_to_dict, serialize

from mail_mcp._endpoint import ConnectionInfo, TlsMode

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
    html_body = md.markdown(body_with_footer)
    date = formatdate(usegmt=True)

    if attach_paths:
        msg_obj = _build_multipart(
            info.username, to_addrs, cc_addrs, subject,
            body_with_footer, html_body, attach_paths, date,
        )
        raw_bytes = msg_obj.as_bytes()
    else:
        plain_msg = EmailMessage()
        plain_msg["From"] = info.username
        plain_msg["To"] = ", ".join(to_addrs)
        plain_msg["Subject"] = subject
        plain_msg["Date"] = date
        if cc_addrs:
            plain_msg["Cc"] = ", ".join(cc_addrs)
        plain_msg.set_content(body_with_footer)
        plain_msg.add_alternative(html_body, subtype="html")
        raw_bytes = plain_msg.as_bytes()

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
    raw_bytes = _escape_leading_dots(raw_bytes)

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
        conn.login(info.username, info.password)
        conn.sendmail(info.username, recipients, raw_bytes)


# Lines starting with `.` in QP-encoded content can be mangled by
# MTAs with buggy SMTP dot-stuffing handling (observed 2026-05-17
# against IONOS: `.com` arrives as `...com` after a soft-break
# wrap landed before the dot).  RFC 2045 §6.7 permits encoding
# `.` as `=2E` and recommends it precisely for this SMTP-
# interaction case; Python's stdlib QP encoder does not do this
# by default, so we post-process here.  The substitution only
# touches lines that *begin* with a literal `.` — those occur in
# QP-encoded text parts at soft-break continuations, never in
# headers or base64 parts.
_LEADING_DOT_RE = re.compile(rb"(?m)^\.")


def _escape_leading_dots(raw_bytes: bytes) -> bytes:
    """Replace leading `.` with `=2E` to dodge buggy MTA de-stuffing."""
    return _LEADING_DOT_RE.sub(b"=2E", raw_bytes)



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
