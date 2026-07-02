# SPDX-License-Identifier: Apache-2.0
"""SMTP sender for jmd-mcp-mail.

Sends email messages described by JMD Message documents.  Composition
(Markdown body → multipart/alternative, attachments, Message-ID/Date)
lives in :mod:`mail_mcp._compose`; this module owns validation, the
SMTP transport and the response document.

Transport: ``smtplib`` (stdlib).  The connection mode (implicit TLS
vs STARTTLS) is taken from the caller-supplied :class:`ConnectionInfo`
which derives it from the port (see ``_endpoint``).
"""
from __future__ import annotations

import asyncio
import base64
import smtplib

from jmd import jmd_mode, jmd_to_dict, serialize

from mail_mcp._compose import ComposeError, compose
from mail_mcp._endpoint import ConnectionInfo, TlsMode, xoauth2_string

_LABEL = "Message"


async def send(document: str, info: ConnectionInfo) -> str:
    """Send an email described by a JMD Message document.

    The blocking ``smtplib`` delivery runs via ``asyncio.to_thread``
    so the event loop is never blocked.

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

    try:
        result = compose(
            fields,
            from_addr=info.username,
            from_name=info.from_name,
            footer=True,
            bcc_in_header=False,
            require_recipients=True,
        )
    except ComposeError as exc:
        return _error(exc.status, exc.code, exc.message)

    all_recipients = (
        result.to_addrs + result.cc_addrs + result.bcc_addrs
    )

    try:
        await asyncio.to_thread(
            _deliver, info, all_recipients, result.raw_bytes,
        )
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
        {
            "to": ", ".join(result.to_addrs),
            "subject": result.subject,
            "status": "sent",
            "message-id": result.message_id,
        },
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


def _error(status: int, code: str, message: str) -> str:
    """Serialize a JMD error document."""
    return serialize(
        {"status": status, "code": code, "message": message},
        label="Error",
    )
