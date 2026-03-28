"""SMTP sender for jmd-mcp-mail.

Composes and sends email messages from JMD Message documents.
Uses smtplib (stdlib) with STARTTLS.
"""
from __future__ import annotations

import smtplib
from email.headerregistry import Address
from email.message import EmailMessage

from jmd import jmd_mode, jmd_to_dict, serialize

from .config import SMTPConfig

_LABEL = "Message"


def send(document: str, cfg: SMTPConfig) -> str:
    """Send an email described by a JMD Message document.

    Args:
        document: JMD data document with to, subject, body fields.
        cfg: SMTP configuration.

    Returns:
        JMD confirmation document or # Error document.
    """
    mode = jmd_mode(document)
    if mode != "data":
        return _error(400, "invalid_mode", "write requires a data document (# Message)")

    fields = jmd_to_dict(document)

    to_raw = str(fields.get("to", "")).strip()
    subject = str(fields.get("subject", "")).strip()
    body = str(fields.get("body", "")).strip()
    cc_raw = str(fields.get("cc", "")).strip()
    bcc_raw = str(fields.get("bcc", "")).strip()

    if not to_raw:
        return _error(400, "missing_fields", "'to' is required")
    if not subject:
        return _error(400, "missing_fields", "'subject' is required")
    if not body:
        return _error(400, "missing_fields", "'body' is required")

    to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
    cc_addrs = [a.strip() for a in cc_raw.split(",") if a.strip()]
    bcc_addrs = [a.strip() for a in bcc_raw.split(",") if a.strip()]

    msg = EmailMessage()
    msg["From"] = cfg.username
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg.set_content(body)

    all_recipients = to_addrs + cc_addrs + bcc_addrs

    try:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as conn:
            conn.ehlo()
            conn.starttls()
            conn.ehlo()
            conn.login(cfg.username, cfg.password)
            conn.sendmail(cfg.username, all_recipients, msg.as_bytes())
    except smtplib.SMTPAuthenticationError as e:
        return _error(401, "auth_failed", f"SMTP authentication failed: {e.smtp_error.decode()}")
    except smtplib.SMTPRecipientsRefused as e:
        refused = ", ".join(e.recipients)
        return _error(400, "recipients_refused", f"Recipients refused: {refused}")
    except smtplib.SMTPException as e:
        return _error(500, "smtp_error", str(e))
    except OSError as e:
        return _error(500, "connection_error", str(e))

    return serialize(
        {
            "to": ", ".join(to_addrs),
            "subject": subject,
            "status": "sent",
        },
        label=_LABEL,
    )


def _error(status: int, code: str, message: str) -> str:
    """Serialise a JMD error document."""
    return serialize(
        {"status": status, "code": code, "message": message},
        label="Error",
    )
