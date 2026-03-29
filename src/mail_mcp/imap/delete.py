"""IMAP delete routing: folder and message deletion."""
from __future__ import annotations

import imaplib

from jmd import JMDDeleteParser, jmd_mode, serialize

from mail_mcp.config import MailConfig, resolve
from mail_mcp.imap._connection import encode_folder, imap_call, open_imap
from mail_mcp.imap._parse import (
    folder_to_dict,
    message_to_dict,
    parse_list_item,
    parse_message,
)
from mail_mcp.imap.read import _error, _extract_label

_LABEL_FOLDER = "Folder"
_LABEL_MESSAGE = "Message"


async def delete(document: str, cfgs: dict[str, MailConfig]) -> str:
    """Dispatch a JMD delete document to the appropriate IMAP handler.

    Args:
        document: JMD delete document string (#-).
        cfgs: All configured mail accounts.

    Returns:
        JMD response document (the deleted resource).
    """
    mode = jmd_mode(document)
    if mode != "delete":
        return _error(
            400, "invalid_mode",
            "delete requires a #- document",
        )

    cfg = resolve(document, cfgs)
    label = _extract_label(document)
    match label.lower():
        case "folder":
            return await _delete_folder(document, cfg)
        case "message":
            return await _delete_message(document, cfg)
        case _:
            return _error(
                400, "unknown_label",
                f"delete does not support label {label!r}",
            )


# ---------------------------------------------------------------------------
# Folder delete
# ---------------------------------------------------------------------------


async def _delete_folder(document: str, cfg: MailConfig) -> str:
    """Delete a folder and return its final document."""
    parsed = JMDDeleteParser().parse(document)
    ids = parsed.identifiers
    path = str(ids.get("path", "")).strip()
    if not path:
        return _error(400, "missing_fields", "'path' is required")

    encoded = encode_folder(path)
    try:
        async with open_imap(cfg) as conn:
            # Read folder info before deletion.
            st_list, list_data = await imap_call(conn, "list", '""', encoded)
            rec = None
            if st_list == "OK":
                for item in list_data:
                    if isinstance(item, bytes):
                        rec = parse_list_item(item)
                        if rec is not None:
                            rec.mailbox = cfg.name
                            break

            status, _ = await imap_call(conn, "delete", encoded)
            if status != "OK":
                return _error(
                    500, "imap_error",
                    f"Could not delete folder {path!r}",
                )

            if rec is not None:
                return serialize(folder_to_dict(rec), label=_LABEL_FOLDER)
            return serialize(
                {"path": path, "mailbox": cfg.name}, label=_LABEL_FOLDER
            )
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))


# ---------------------------------------------------------------------------
# Message delete
# ---------------------------------------------------------------------------


async def _delete_message(document: str, cfg: MailConfig) -> str:
    """Fetch a message, delete it, and return its full document."""
    parsed = JMDDeleteParser().parse(document)
    ids = parsed.identifiers
    uid = str(ids.get("id", "")).strip()
    folder = str(ids.get("folder", "INBOX")).strip()

    if not uid:
        return _error(400, "missing_fields", "'id' is required")

    encoded = encode_folder(folder)
    try:
        async with open_imap(cfg) as conn:
            await imap_call(conn, "select", encoded)

            # Fetch full message before deletion.
            status, data = await imap_call(
                conn, "uid", "FETCH", uid, "(BODY.PEEK[])"
            )
            match (status, data):
                case ("OK", [item, *_]) if isinstance(item, tuple):
                    raw = item[1]
                    if not isinstance(raw, bytes):
                        return _error(
                            404, "not_found",
                            f"Message {uid} not found in {folder}",
                        )
                    rec = parse_message(uid, raw, folder)
                    rec.mailbox = cfg.name
                case _:
                    return _error(
                        404, "not_found",
                        f"Message {uid} not found in {folder}",
                    )

            # Delete.
            await imap_call(
                conn, "uid", "STORE", uid, "+FLAGS", r"(\Deleted)"
            )
            await imap_call(conn, "expunge")

            return serialize(message_to_dict(rec), label=_LABEL_MESSAGE)
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))
