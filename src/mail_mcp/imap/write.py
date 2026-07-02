# SPDX-License-Identifier: Apache-2.0
"""IMAP write routing: folder CRUD, message flags, move, copy, drafts."""
from __future__ import annotations

import imaplib

from jmd import JMDParser, jmd_mode, jmd_to_dict, serialize

from mail_mcp._endpoint import ConnectionInfo
from mail_mcp.imap import draft as imap_draft
from mail_mcp.imap._append import find_by_message_id
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

# Fields whose presence marks a # Message document as draft content
# (create without id / replace with id) rather than a flag update.
_CONTENT_FIELDS = frozenset({
    "to", "subject", "body", "cc", "bcc", "from-name", "attachments",
})


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def write(
    document: str,
    info: ConnectionInfo,
    *,
    drafts_folder: str = "",
) -> str:
    """Dispatch a JMD data document to the appropriate IMAP write handler.

    Routing:

    * ``# Folder`` → create or rename folder (``rename-to`` frontmatter)
    * ``# Message`` without ``id`` → create a draft (APPEND)
    * ``# Message`` with ``id`` + content fields → replace a draft
    * ``# Message`` with ``id`` only → update flags; or move/copy
      (``move-to``/``copy-to`` frontmatter)

    Args:
        document: JMD data document string.
        info: Resolved connection parameters for this call.
        drafts_folder: Per-account Drafts folder override from
            config.jmd (empty = discovery).

    Returns:
        JMD response document.
    """
    mode = jmd_mode(document)
    if mode != "data":
        return _error(
            400, "invalid_mode",
            "write requires a data document (# Folder or # Message)",
        )

    parser = JMDParser()
    parser.parse(document)
    fm = parser.frontmatter
    label = _extract_label(document)

    match label.lower():
        case "folder":
            return await _write_folder(document, fm, info)
        case "message":
            return await _write_message(
                document, fm, info, drafts_folder,
            )
        case _:
            return _error(
                400, "unknown_label",
                f"write does not support label {label!r}",
            )


# ---------------------------------------------------------------------------
# Folder write
# ---------------------------------------------------------------------------


async def _write_folder(
    document: str,
    fm: dict[str, object],
    info: ConnectionInfo,
) -> str:
    """Create or rename a folder."""
    fields = jmd_to_dict(document)
    if not isinstance(fields, dict):
        return _error(400, "invalid_document", "Expected a Folder object")

    path = str(fields.get("path", "")).strip()
    if not path:
        return _error(400, "missing_fields", "'path' is required")

    rename_to = str(fm.get("rename-to", "")).strip()
    encoded_src = encode_folder(path)

    try:
        async with open_imap(info) as conn:
            if rename_to:
                encoded_dst = encode_folder(rename_to)
                status, _ = await imap_call(
                    conn, "rename", encoded_src, encoded_dst
                )
                if status != "OK":
                    return _error(
                        500, "imap_error",
                        f"Could not rename {path!r} to {rename_to!r}",
                    )
                result_path = rename_to
            else:
                status, _ = await imap_call(conn, "create", encoded_src)
                if status != "OK":
                    return _error(
                        500, "imap_error",
                        f"Could not create folder {path!r}",
                    )
                result_path = path

            st2, list_data = await imap_call(
                conn, "list", '""', encode_folder(result_path)
            )
            if st2 == "OK":
                for item in list_data:
                    if isinstance(item, bytes):
                        rec = parse_list_item(item)
                        if rec is not None:
                            return serialize(
                                folder_to_dict(rec), label=_LABEL_FOLDER
                            )
            return serialize(
                {"path": result_path}, label=_LABEL_FOLDER,
            )
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))


# ---------------------------------------------------------------------------
# Message write
# ---------------------------------------------------------------------------


def _has_content(fields: dict[str, object]) -> bool:
    """True when any draft content field is present and non-empty."""
    for key in _CONTENT_FIELDS:
        val = fields.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            if val:
                return True
        elif str(val).strip():
            return True
    return False


async def _write_message(
    document: str,
    fm: dict[str, object],
    info: ConnectionInfo,
    drafts_folder: str,
) -> str:
    """Create/replace a draft, update flags, or move/copy a message."""
    fields = jmd_to_dict(document)
    if not isinstance(fields, dict):
        return _error(400, "invalid_document", "Expected a Message object")

    uid = str(fields.get("id", "")).strip()
    folder = str(fields.get("folder", "INBOX")).strip()

    move_to = str(fm.get("move-to", "")).strip()
    copy_to = str(fm.get("copy-to", "")).strip()
    # Frontmatter reply reference (a UID, unlike the in-reply-to
    # *body field* which is a Message-ID header).
    reply_uid = str(fm.get("in-reply-to", "")).strip()
    reply_folder = (
        str(fm.get("in-reply-to-folder", "")).strip() or "INBOX"
    )
    quote = str(fm.get("quote", "")).strip().lower() in (
        "true", "1", "yes",
    )
    has_content = _has_content(fields)

    if quote and not reply_uid:
        return _error(
            400, "bad_request",
            "'quote' requires an 'in-reply-to' frontmatter key "
            "naming the message to quote",
        )
    if has_content and (move_to or copy_to):
        return _error(
            400, "bad_request",
            "content fields cannot be combined with move-to/copy-to",
        )

    if not uid:
        # No id → create a draft from the content fields.
        return await imap_draft.create_draft(
            fields, info,
            drafts_folder=drafts_folder,
            reply_uid=reply_uid,
            reply_folder=reply_folder,
            quote=quote,
        )
    if has_content:
        # id + content → replace the draft with this version.
        return await imap_draft.update_draft(
            uid, folder, fields, info,
            reply_uid=reply_uid,
            reply_folder=reply_folder,
            quote=quote,
        )

    if move_to:
        return await _move_message(uid, folder, move_to, info)
    if copy_to:
        return await _copy_message(uid, folder, copy_to, info)
    return await _update_flags(document, uid, folder, fields, info)


async def _update_flags(
    document: str,
    uid: str,
    folder: str,
    fields: dict[str, object],
    info: ConnectionInfo,
) -> str:
    r"""Update message flags.

    ``## flags[]`` replaces the full flag set (``STORE FLAGS``);
    ``## flags-add[]`` / ``## flags-remove[]`` change flags
    incrementally (``+FLAGS`` / ``-FLAGS``) without clobbering
    others (e.g. add ``\Seen`` while keeping ``\Flagged``).  Replace
    and incremental forms are mutually exclusive.
    """
    def _flag_list(key: str) -> list[str]:
        val = fields.get(key, [])
        items = val if isinstance(val, list) else [val]
        return [str(f).strip() for f in items if str(f).strip()]

    replace = _flag_list("flags")
    add = _flag_list("flags-add")
    remove = _flag_list("flags-remove")

    if replace and (add or remove):
        return _error(
            400, "bad_request",
            "flags[] (replace) cannot be combined with "
            "flags-add[]/flags-remove[]",
        )

    encoded = encode_folder(folder)
    try:
        async with open_imap(info) as conn:
            await imap_call(conn, "select", encoded)
            ops: list[tuple[str, list[str]]] = (
                [("+FLAGS", add)] * bool(add)
                + [("-FLAGS", remove)] * bool(remove)
                if (add or remove)
                else [("FLAGS", replace)]
            )
            for verb, flag_list in ops:
                status, _ = await imap_call(
                    conn, "uid", "STORE", uid, verb,
                    f"({' '.join(flag_list)})",
                )
                if status != "OK":
                    return _error(
                        404, "not_found",
                        f"Message {uid} not found in {folder}",
                    )
            st2, data = await imap_call(
                conn, "uid", "FETCH", uid, "(BODY.PEEK[])"
            )
            if st2 == "OK" and data and isinstance(data[0], tuple):
                raw = data[0][1]
                if isinstance(raw, bytes):
                    rec = parse_message(uid, raw, folder)
                    return serialize(
                        message_to_dict(rec), label=_LABEL_MESSAGE
                    )
            return serialize(
                {"id": uid, "folder": folder}, label=_LABEL_MESSAGE,
            )
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))


async def _move_message(
    uid: str,
    src_folder: str,
    dst_folder: str,
    info: ConnectionInfo,
) -> str:
    r"""Move a message to another folder.

    NOTE: This operation requires two IMAP round-trips:

    1. COPY + STORE ``\\Deleted`` + EXPUNGE in source folder.
    2. SEARCH by ``Message-ID`` in destination to find the new UID.

    Args:
        uid: Source UID.
        src_folder: Source folder path.
        dst_folder: Destination folder path.
        info: Resolved connection parameters.

    Returns:
        JMD Message document at new location with new UID.
    """
    encoded_src = encode_folder(src_folder)
    encoded_dst = encode_folder(dst_folder)
    try:
        async with open_imap(info) as conn:
            await imap_call(conn, "select", encoded_src)

            _hdr_fetch = "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])"
            st, data = await imap_call(
                conn, "uid", "FETCH", uid, _hdr_fetch
            )
            message_id: str | None = None
            if st == "OK" and data and isinstance(data[0], tuple):
                raw_hdr = data[0][1]
                if isinstance(raw_hdr, bytes):
                    import email as _email
                    msg = _email.message_from_bytes(raw_hdr)
                    message_id = (msg.get("Message-ID") or "").strip()

            st2, _ = await imap_call(conn, "uid", "COPY", uid, encoded_dst)
            if st2 != "OK":
                return _error(
                    500, "imap_error",
                    f"Could not copy message {uid} to {dst_folder!r}",
                )

            await imap_call(
                conn, "uid", "STORE", uid, "+FLAGS", r"(\Deleted)"
            )
            await imap_call(conn, "expunge")

            await imap_call(conn, "select", encoded_dst)
            new_uid = await find_by_message_id(conn, message_id)

            if new_uid:
                st3, data3 = await imap_call(
                    conn, "uid", "FETCH", new_uid, "(BODY.PEEK[])"
                )
                if st3 == "OK" and data3 and isinstance(data3[0], tuple):
                    raw = data3[0][1]
                    if isinstance(raw, bytes):
                        rec = parse_message(new_uid, raw, dst_folder)
                        return serialize(
                            message_to_dict(rec), label=_LABEL_MESSAGE
                        )

            return serialize(
                {"id": new_uid or "unknown", "folder": dst_folder},
                label=_LABEL_MESSAGE,
            )
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))


async def _copy_message(
    uid: str,
    src_folder: str,
    dst_folder: str,
    info: ConnectionInfo,
) -> str:
    """Copy a message to another folder.

    NOTE: Like ``move-to``, this requires two IMAP round-trips to
    resolve the new UID via SEARCH by ``Message-ID`` in the
    destination folder.

    Args:
        uid: Source UID.
        src_folder: Source folder path.
        dst_folder: Destination folder path.
        info: Resolved connection parameters.

    Returns:
        JMD Message document at new location with new UID.
    """
    encoded_src = encode_folder(src_folder)
    encoded_dst = encode_folder(dst_folder)
    try:
        async with open_imap(info) as conn:
            await imap_call(conn, "select", encoded_src)

            st, data = await imap_call(
                conn, "uid", "FETCH", uid,
                "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])",
            )
            message_id: str | None = None
            if st == "OK" and data and isinstance(data[0], tuple):
                raw_hdr = data[0][1]
                if isinstance(raw_hdr, bytes):
                    import email as _email
                    msg = _email.message_from_bytes(raw_hdr)
                    message_id = (msg.get("Message-ID") or "").strip()

            st2, _ = await imap_call(conn, "uid", "COPY", uid, encoded_dst)
            if st2 != "OK":
                return _error(
                    500, "imap_error",
                    f"Could not copy message {uid} to {dst_folder!r}",
                )

            await imap_call(conn, "select", encoded_dst)
            new_uid = await find_by_message_id(conn, message_id)

            if new_uid:
                st3, data3 = await imap_call(
                    conn, "uid", "FETCH", new_uid, "(BODY.PEEK[])"
                )
                if st3 == "OK" and data3 and isinstance(data3[0], tuple):
                    raw = data3[0][1]
                    if isinstance(raw, bytes):
                        rec = parse_message(new_uid, raw, dst_folder)
                        return serialize(
                            message_to_dict(rec), label=_LABEL_MESSAGE
                        )

            return serialize(
                {"id": new_uid or "unknown", "folder": dst_folder},
                label=_LABEL_MESSAGE,
            )
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))


