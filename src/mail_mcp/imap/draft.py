# SPDX-License-Identifier: Apache-2.0
"""Draft creation and update — IMAP APPEND into the Drafts folder.

The human-in-the-loop counterpart to ``send``: the agent composes,
the user finalizes and sends from their own mail client.  Drafts
therefore carry **no** AI-attribution footer and show ``Bcc`` as a
real header (the client must display it).
"""
from __future__ import annotations

import imaplib

from jmd import serialize

from mail_mcp._compose import ComposeError, ComposeResult, compose
from mail_mcp._endpoint import ConnectionInfo
from mail_mcp.imap._append import append_raw
from mail_mcp.imap._connection import encode_folder, imap_call, open_imap
from mail_mcp.imap._parse import message_to_dict, parse_message
from mail_mcp.imap._special import (
    DRAFTS_FALLBACKS,
    DRAFTS_USE,
    find_special_folder,
)
from mail_mcp.imap.read import _error

_LABEL_MESSAGE = "Message"
_DRAFT_FLAGS = r"(\Draft)"


def _compose_draft(
    fields: dict[str, object],
    info: ConnectionInfo,
    extra_headers: dict[str, str] | None = None,
) -> ComposeResult:
    """Compose with draft knobs: no footer, Bcc visible, partial ok."""
    return compose(
        fields,
        from_addr=info.username,
        from_name=info.from_name,
        footer=False,
        bcc_in_header=True,
        require_recipients=False,
        extra_headers=extra_headers,
    )


async def _resolve_drafts_folder(
    conn: imaplib.IMAP4,
    info: ConnectionInfo,
    fields: dict[str, object],
    drafts_folder: str,
) -> str | None:
    """Pick the target folder: field > config > discovery > create.

    Args:
        conn: Open, authenticated IMAP connection.
        info: Connection parameters.
        fields: Parsed JMD fields (an explicit ``folder`` wins).
        drafts_folder: Per-account config override.

    Returns:
        Folder path, or None when nothing works.
    """
    explicit = str(fields.get("folder", "")).strip()
    if explicit:
        return explicit
    if drafts_folder:
        return drafts_folder
    found = await find_special_folder(
        conn, info, DRAFTS_USE, DRAFTS_FALLBACKS,
    )
    if found:
        return found
    # Last resort: create a Drafts folder, like mail clients do.
    status, _ = await imap_call(conn, "create", encode_folder("Drafts"))
    return "Drafts" if status == "OK" else None


async def _fetch_message_doc(
    conn: imaplib.IMAP4,
    uid: str | None,
    folder: str,
    result: ComposeResult,
) -> str:
    """Serialize the stored draft (re-fetch confirms storage)."""
    if uid:
        await imap_call(conn, "select", encode_folder(folder))
        st, data = await imap_call(
            conn, "uid", "FETCH", uid, "(BODY.PEEK[])"
        )
        if st == "OK" and data and isinstance(data[0], tuple):
            raw = data[0][1]
            if isinstance(raw, bytes):
                rec = parse_message(uid, raw, folder)
                return serialize(
                    message_to_dict(rec), label=_LABEL_MESSAGE
                )
    return serialize(
        {
            "id": uid or "unknown",
            "folder": folder,
            "subject": result.subject,
            "message-id": result.message_id,
        },
        label=_LABEL_MESSAGE,
    )


async def create_draft(
    fields: dict[str, object],
    info: ConnectionInfo,
    *,
    drafts_folder: str = "",
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Create a draft message via IMAP APPEND.

    Args:
        fields: Parsed JMD ``# Message`` fields (no ``id``); needs at
            least one of ``to``/``subject``/``body``.
        info: Resolved IMAP connection parameters.
        drafts_folder: Per-account config override for the target.
        extra_headers: Verbatim headers (reply threading).

    Returns:
        JMD ``# Message`` document of the stored draft, or
        ``# Error``.
    """
    try:
        result = _compose_draft(fields, info, extra_headers)
    except ComposeError as exc:
        return _error(exc.status, exc.code, exc.message)
    try:
        async with open_imap(info) as conn:
            target = await _resolve_drafts_folder(
                conn, info, fields, drafts_folder,
            )
            if target is None:
                return _error(
                    500, "no_drafts_folder",
                    "could not find or create a Drafts folder; set "
                    "'drafts-folder' in config.jmd or pass a "
                    "'folder:' field",
                )
            uid = await append_raw(
                conn, target, result.raw_bytes,
                _DRAFT_FLAGS, result.message_id,
            )
            return await _fetch_message_doc(conn, uid, target, result)
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))


async def update_draft(
    uid: str,
    folder: str,
    fields: dict[str, object],
    info: ConnectionInfo,
    *,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Replace a draft: APPEND the new version, delete the old one.

    Replace semantics — the document must restate the *complete*
    draft; fields are not merged with the previous version.  The
    order (append, then delete) means a mid-flight failure can leave
    two drafts behind, but never zero.

    Args:
        uid: UID of the draft being replaced.
        folder: Folder the old draft lives in (new one goes there
            too).
        fields: Parsed JMD ``# Message`` fields (full new content).
        info: Resolved IMAP connection parameters.
        extra_headers: Verbatim headers (reply threading).

    Returns:
        JMD ``# Message`` document of the new draft, or ``# Error``.
    """
    try:
        result = _compose_draft(fields, info, extra_headers)
    except ComposeError as exc:
        return _error(exc.status, exc.code, exc.message)
    try:
        async with open_imap(info) as conn:
            new_uid = await append_raw(
                conn, folder, result.raw_bytes,
                _DRAFT_FLAGS, result.message_id,
            )
            note: str | None = None
            await imap_call(conn, "select", encode_folder(folder))
            st, _ = await imap_call(
                conn, "uid", "STORE", uid, "+FLAGS", r"(\Deleted)"
            )
            if st == "OK":
                await imap_call(conn, "expunge")
            else:
                note = f"previous draft {uid} not found in {folder}"
            doc = await _fetch_message_doc(
                conn, new_uid, folder, result,
            )
            if note:
                doc = f"note: {note}\n\n{doc}"
            return doc
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))
