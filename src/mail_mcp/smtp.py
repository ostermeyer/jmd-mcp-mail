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
import imaplib
import smtplib

from jmd import jmd_mode, jmd_to_dict, serialize

from mail_mcp._compose import ComposeError, ComposeResult, compose
from mail_mcp._endpoint import ConnectionInfo, TlsMode, xoauth2_string
from mail_mcp._frontmatter import parse_frontmatter
from mail_mcp.imap._append import append_raw
from mail_mcp.imap._connection import open_imap
from mail_mcp.imap._special import (
    SENT_FALLBACKS,
    SENT_USE,
    find_special_folder,
)
from mail_mcp.imap._thread import (
    apply_quote,
    apply_reply_defaults,
    fetch_original,
    reply_headers,
)

_LABEL = "Message"


async def send(
    document: str,
    info: ConnectionInfo,
    *,
    imap_info: ConnectionInfo | None = None,
    store_sent: bool = True,
    sent_folder: str = "",
) -> str:
    """Send an email described by a JMD Message document.

    The blocking ``smtplib`` delivery runs via ``asyncio.to_thread``
    so the event loop is never blocked.  After a successful delivery
    a copy is appended to the account's Sent folder (best-effort —
    a storage failure yields ``sent-copy: failed`` in the response,
    never a send error).

    Args:
        document: JMD data document with ``to``, ``subject``, ``body``
            fields and optional ``cc``, ``bcc``, ``attachments[]``.
        info: Resolved SMTP connection parameters.  Built by the
            caller via :meth:`ConnectionInfo.resolve`.
        imap_info: Resolved IMAP connection parameters for the
            sent-copy; ``None`` disables storing (reported as
            ``failed`` when ``store_sent`` is on).
        store_sent: Store a copy in the Sent folder after delivery.
        sent_folder: Explicit Sent folder path from config; empty
            means SPECIAL-USE / well-known-name discovery.

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

    fm = parse_frontmatter(document)
    reply_uid = str(fm.get("in-reply-to", "")).strip()
    reply_folder = (
        str(fm.get("in-reply-to-folder", "")).strip() or "INBOX"
    )
    quote = str(fm.get("quote", "")).strip().lower() in (
        "true", "1", "yes",
    )
    if quote and not reply_uid:
        return _error(
            400, "bad_request",
            "'quote' requires an 'in-reply-to' frontmatter key "
            "naming the message to quote",
        )
    extra_headers: dict[str, str] | None = None
    if reply_uid:
        if imap_info is None:
            return _error(
                400, "imap_required",
                "reply threading needs the account's IMAP side "
                "(in-reply-to references a UID there)",
            )
        try:
            async with open_imap(imap_info) as conn:
                orig = await fetch_original(
                    conn, reply_folder, reply_uid,
                    include_body=quote,
                )
        except (imaplib.IMAP4.error, OSError) as exc:
            return _error(500, "imap_error", str(exc))
        if orig is None:
            return _error(
                404, "not_found",
                f"message {reply_uid} not found in {reply_folder} "
                "(in-reply-to)",
            )
        apply_reply_defaults(fields, orig)
        if quote:
            apply_quote(fields, orig)
        extra_headers = reply_headers(orig)

    try:
        result = compose(
            fields,
            from_addr=info.username,
            from_name=info.from_name,
            footer=True,
            bcc_in_header=False,
            require_recipients=True,
            extra_headers=extra_headers,
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

    response: dict[str, object] = {
        "to": ", ".join(result.to_addrs),
        "subject": result.subject,
        "status": "sent",
        "message-id": result.message_id,
    }
    if not store_sent:
        response["sent-copy"] = "disabled"
    else:
        stored = (
            await _store_sent_copy(imap_info, sent_folder, result)
            if imap_info is not None else None
        )
        if stored is None:
            response["sent-copy"] = "failed"
        else:
            folder, uid = stored
            response["sent-copy"] = "stored"
            response["sent-folder"] = folder
            if uid:
                response["id"] = uid
    return serialize(response, label=_LABEL)


async def _store_sent_copy(
    imap_info: ConnectionInfo,
    sent_folder: str,
    result: ComposeResult,
) -> tuple[str, str | None] | None:
    r"""Append the delivered bytes to the Sent folder (best-effort).

    The stored copy is byte-identical to what went over the wire —
    a faithful record (Bcc is envelope-only and thus absent).

    Args:
        imap_info: Resolved IMAP connection parameters.
        sent_folder: Explicit folder from config, or empty for
            discovery.
        result: The composed message that was delivered.

    Returns:
        ``(folder, uid)`` on success (uid may be None), or None on
        any failure — sending already succeeded, so storing must
        never raise.
    """
    try:
        async with open_imap(imap_info) as conn:
            folder = sent_folder or await find_special_folder(
                conn, imap_info, SENT_USE, SENT_FALLBACKS,
            )
            if not folder:
                return None
            uid = await append_raw(
                conn, folder, result.raw_bytes, r"(\Seen)",
                result.message_id,
            )
            return folder, uid
    except Exception:  # noqa: BLE001 — sent-copy is strictly best-effort
        return None


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
