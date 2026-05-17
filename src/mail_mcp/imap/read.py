# SPDX-License-Identifier: Apache-2.0
"""IMAP read and query routing for jmd-mcp-mail."""
from __future__ import annotations

import imaplib
import math
from pathlib import Path

from jmd import JMDParser, JMDQueryParser, jmd_mode, jmd_to_dict, serialize

from mail_mcp import schemas
from mail_mcp._endpoint import ConnectionInfo
from mail_mcp.imap._connection import encode_folder, imap_call, open_imap
from mail_mcp.imap._criteria import build as build_criteria
from mail_mcp.imap._parse import (
    FolderRecord,
    extract_uid,
    folder_to_dict,
    message_to_dict,
    parse_list_item,
    parse_message,
)

_DEFAULT_PAGE_SIZE = 25
_LABEL_FOLDER = "Folder"
_LABEL_MESSAGE = "Message"
_LABEL_EMAIL_ADDRESS = "EmailAddress"


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------


def _error(status: int, code: str, message: str) -> str:
    """Serialize a JMD error document."""
    return serialize(
        {"status": status, "code": code, "message": message},
        label="Error",
    )


# ---------------------------------------------------------------------------
# Folder helpers
# ---------------------------------------------------------------------------


def _parse_status_response(
    data: list[bytes | None],
) -> tuple[int | None, int | None]:
    """Parse STATUS response data into ``(messages, unseen)``."""
    import re
    messages: int | None = None
    unseen: int | None = None
    for item in data:
        if not isinstance(item, bytes):
            continue
        m = re.search(rb"MESSAGES\s+(\d+)", item)
        if m:
            messages = int(m.group(1))
        u = re.search(rb"UNSEEN\s+(\d+)", item)
        if u:
            unseen = int(u.group(1))
    return messages, unseen


async def _list_folders(
    info: ConnectionInfo,
    pattern: str = "*",
) -> list[FolderRecord]:
    """Fetch all folders matching a pattern."""
    async with open_imap(info) as conn:
        status, data = await imap_call(conn, "list", '""', pattern)
        if status != "OK":
            return []
        records: list[FolderRecord] = []
        for item in data:
            if not isinstance(item, bytes):
                continue
            rec = parse_list_item(item)
            if rec is not None:
                records.append(rec)
        return records


async def _folder_with_status(
    info: ConnectionInfo,
    path: str,
) -> FolderRecord | None:
    """Fetch a single folder with MESSAGES and UNSEEN counts."""
    encoded = encode_folder(path)
    async with open_imap(info) as conn:
        status, data = await imap_call(conn, "list", '""', encoded)
        if status != "OK" or not data:
            return None
        rec = next(
            (parse_list_item(item)
             for item in data if isinstance(item, bytes)),
            None,
        )
        if rec is None:
            return None
        st, st_data = await imap_call(
            conn, "status", encoded, "(MESSAGES UNSEEN)"
        )
        if st == "OK":
            messages, unseen = _parse_status_response(
                [d for d in st_data if isinstance(d, bytes)]
            )
            rec.messages = messages
            rec.unseen = unseen
        return rec


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def read(document: str, info: ConnectionInfo) -> str:
    """Dispatch a JMD read document to the appropriate IMAP handler.

    Args:
        document: JMD document string.
        info: Resolved connection parameters for this call.

    Returns:
        JMD response string.
    """
    mode = jmd_mode(document)

    # ---- Schema ----
    if mode == "schema":
        label = _extract_label(document)
        match label.lower():
            case "folder":
                return schemas.FOLDER
            case "emailaddress":
                return schemas.EMAIL_ADDRESS
            case _:
                return schemas.MESSAGE

    # ---- Data ----
    if mode == "data":
        label = _extract_label(document)
        match label.lower():
            case "folder":
                return await _read_folder(document, info)
            case "folder[]":
                return await _read_root_folders(info)
            case _:
                return await _read_message(document, info)

    # ---- Query ----
    if mode == "query":
        label = _extract_label(document)
        match label.lower():
            case "folder":
                return await _query_folders(document, info)
            case _:
                return await _query_messages(document, info)

    return _error(400, "invalid_mode", f"Unsupported mode: {mode!r}")


# ---------------------------------------------------------------------------
# Folder reads
# ---------------------------------------------------------------------------


async def _read_root_folders(info: ConnectionInfo) -> str:
    """Return all root-level folders as a ``# Folder[]`` document."""
    records = await _list_folders(info)
    roots = [r for r in records if r.parent is None]
    return serialize(
        [folder_to_dict(r) for r in roots],
        label=_LABEL_FOLDER,
    )


async def _read_folder(document: str, info: ConnectionInfo) -> str:
    """Return a single folder with status counts."""
    fields = jmd_to_dict(document)
    if not isinstance(fields, dict):
        return _error(400, "invalid_document", "Expected a Folder object")
    path = str(fields.get("path", "")).strip()
    if not path:
        return _error(400, "missing_fields", "'path' is required")
    rec = await _folder_with_status(info, path)
    if rec is None:
        return _error(404, "not_found", f"Folder {path!r} not found")
    return serialize(folder_to_dict(rec), label=_LABEL_FOLDER)


async def _query_folders(document: str, info: ConnectionInfo) -> str:
    """Return filtered folder list with pagination frontmatter."""
    parser = JMDParser()
    parser.parse(document)
    fm = parser.frontmatter

    count_only = "count" in fm
    page_size = int(fm["page-size"]) if "page-size" in fm else 50
    page = max(1, int(fm["page"])) if "page" in fm else 1

    query = JMDQueryParser().parse(document)
    parent_filter: str | None = None
    for f in query.fields:
        if f.key == "parent" and f.condition.values:
            parent_filter = str(f.condition.values[0])

    records = await _list_folders(info)

    if parent_filter is not None:
        records = [r for r in records if r.parent == parent_filter]

    total = len(records)
    if count_only:
        return f"total: {total}\n\n# []\n"

    pages = max(1, math.ceil(total / page_size)) if page_size else 1
    offset = (page - 1) * page_size
    page_records = records[offset:offset + page_size]

    frontmatter = (
        f"total: {total}\n"
        f"page: {page}\n"
        f"pages: {pages}\n"
        f"page-size: {page_size}\n"
    )
    return frontmatter + "\n" + serialize(
        [folder_to_dict(r) for r in page_records],
        label=_LABEL_FOLDER,
    )


# ---------------------------------------------------------------------------
# Message reads
# ---------------------------------------------------------------------------


async def _read_message(document: str, info: ConnectionInfo) -> str:
    """Fetch a single message by UID."""
    fields = jmd_to_dict(document)
    if not isinstance(fields, dict):
        return _error(400, "invalid_document", "Expected a Message object")

    uid = str(fields.get("id", "")).strip()
    folder = str(fields.get("folder", "INBOX")).strip()
    download = bool(fields.get("download", False))
    path_raw = str(fields.get("path", "")).strip()
    download_dest: Path | None = None
    if download:
        from mail_mcp.imap._parse import _xdg_download_dir
        download_dest = Path(path_raw) if path_raw else _xdg_download_dir()

    if not uid:
        return _error(400, "missing_fields", "'id' is required")

    encoded = encode_folder(folder)
    try:
        async with open_imap(info) as conn:
            await imap_call(conn, "select", encoded, True)
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
                    rec = parse_message(uid, raw, folder, download_dest)
                    return serialize(
                        message_to_dict(rec), label=_LABEL_MESSAGE
                    )
                case _:
                    return _error(
                        404, "not_found",
                        f"Message {uid} not found in {folder}",
                    )
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))


async def _query_messages(document: str, info: ConnectionInfo) -> str:
    """Search and list message headers with pagination."""
    parser = JMDParser()
    parser.parse(document)
    fm = parser.frontmatter

    count_only = "count" in fm
    page_size = (
        int(fm["page-size"]) if "page-size" in fm else _DEFAULT_PAGE_SIZE
    )
    page = max(1, int(fm["page"])) if "page" in fm else 1

    query = JMDQueryParser().parse(document)
    folder = "INBOX"
    for f in query.fields:
        if f.key == "folder" and f.condition.values:
            folder = str(f.condition.values[0])

    criteria = build_criteria(query.fields)
    encoded = encode_folder(folder)

    try:
        async with open_imap(info) as conn:
            await imap_call(conn, "select", encoded, True)
            status, data = await imap_call(conn, "uid", "SEARCH", criteria)
            if status != "OK":
                return _error(500, "imap_error", "SEARCH failed")

            raw_uids = data[0]
            all_uids = (
                list(reversed(raw_uids.split()))
                if isinstance(raw_uids, bytes) and raw_uids
                else []
            )
            total = len(all_uids)

            if count_only:
                return f"total: {total}\n\n# []\n"

            pages = max(1, math.ceil(total / page_size)) if page_size else 1
            frontmatter = (
                f"total: {total}\n"
                f"page: {page}\n"
                f"pages: {pages}\n"
                f"page-size: {page_size}\n"
            )

            offset = (page - 1) * page_size
            page_uids = all_uids[offset:offset + page_size]

            if not page_uids:
                return frontmatter + "\n" + serialize(
                    [], label=_LABEL_MESSAGE
                )

            uid_str = b",".join(page_uids).decode()
            st2, fetch_data = await imap_call(
                conn, "uid", "FETCH", uid_str, "(BODY.PEEK[HEADER])"
            )
            if st2 != "OK":
                return _error(500, "imap_error", "FETCH failed")

            records: list[dict[str, object]] = []
            for item in fetch_data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                uid = extract_uid(item[0])
                if not isinstance(item[1], bytes):
                    continue
                rec = parse_message(
                    uid, item[1], folder, headers_only=True
                )
                records.append(message_to_dict(rec))

            return frontmatter + "\n" + serialize(
                records, label=_LABEL_MESSAGE
            )
    except imaplib.IMAP4.error as exc:
        return _error(500, "imap_error", str(exc))
    except OSError as exc:
        return _error(500, "connection_error", str(exc))


# ---------------------------------------------------------------------------
# Label extractor
# ---------------------------------------------------------------------------


def _extract_label(document: str) -> str:
    """Extract the root label from a JMD document heading."""
    for line in document.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            content = stripped.lstrip("#").lstrip("!?- ").strip()
            return content
    return ""
