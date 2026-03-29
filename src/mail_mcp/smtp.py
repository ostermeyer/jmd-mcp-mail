"""SMTP sender for jmd-mcp-mail.

Composes and sends email messages described by JMD Message documents.
Message bodies are treated as Markdown and sent as multipart/alternative
(plain text + HTML). File attachments are supported via an attachments[]
array field.
Uses smtplib (stdlib) with STARTTLS.
"""
from __future__ import annotations

import smtplib
from email import encoders as email_encoders
from email.message import EmailMessage
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import markdown as md
from jmd import jmd_mode, jmd_to_dict, serialize

from .config import MailConfig

_LABEL = "Message"


def send(document: str, cfg: MailConfig) -> str:
    """Send an email described by a JMD Message document.

    Args:
        document: JMD data document with to, subject, body fields.
        cfg: Mail configuration.

    Returns:
        JMD confirmation document or # Error document.
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

    html_body = md.markdown(body)

    if attach_paths:
        msg_obj = _build_multipart(
            cfg.username, to_addrs, cc_addrs, subject,
            body, html_body, attach_paths,
        )
        raw_bytes = msg_obj.as_bytes()
    else:
        plain_msg = EmailMessage()
        plain_msg["From"] = cfg.username
        plain_msg["To"] = ", ".join(to_addrs)
        plain_msg["Subject"] = subject
        if cc_addrs:
            plain_msg["Cc"] = ", ".join(cc_addrs)
        plain_msg.set_content(body)
        plain_msg.add_alternative(html_body, subtype="html")
        raw_bytes = plain_msg.as_bytes()

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as conn:
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
            conn.login(cfg.username, cfg.password)
            conn.sendmail(cfg.username, all_recipients, raw_bytes)
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


def _build_multipart(
    sender: str,
    to_addrs: list[str],
    cc_addrs: list[str],
    subject: str,
    plain: str,
    html: str,
    attach_paths: list[Path],
) -> MIMEMultipart:
    """Build a multipart/mixed message with alternative body + attachments."""
    outer = MIMEMultipart("mixed")
    outer["From"] = sender
    outer["To"] = ", ".join(to_addrs)
    outer["Subject"] = subject
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
