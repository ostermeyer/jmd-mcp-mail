# SPDX-License-Identifier: Apache-2.0
"""IMAP APPEND with new-UID resolution (RFC 4315 UIDPLUS).

Servers with UIDPLUS answer APPEND with an ``APPENDUID`` response
code carrying the new UID.  Servers without it get a ``Message-ID``
SEARCH fallback — which is why the composer stamps a Message-ID on
every message it builds.
"""
from __future__ import annotations

import imaplib
import re
import time

from mail_mcp.imap._connection import encode_folder, imap_call

_APPENDUID_RE = re.compile(rb"APPENDUID\s+\d+\s+(\d+)")


async def find_by_message_id(
    conn: imaplib.IMAP4,
    message_id: str | None,
) -> str | None:
    """Search for a message by ``Message-ID`` in the selected folder.

    Args:
        conn: Open, selected IMAP connection.
        message_id: RFC 2822 ``Message-ID`` value (with angle
            brackets).

    Returns:
        UID string, or None if not found.
    """
    if not message_id:
        return None
    criteria = f'HEADER Message-ID "{message_id}"'
    status, data = await imap_call(conn, "uid", "SEARCH", criteria)
    if status == "OK" and data and isinstance(data[0], bytes) and data[0]:
        uids = data[0].split()
        if uids:
            return uids[-1].decode()
    return None


async def append_raw(
    conn: imaplib.IMAP4,
    folder: str,
    raw: bytes,
    flags: str,
    message_id: str,
) -> str | None:
    r"""APPEND raw message bytes and resolve the new UID.

    Args:
        conn: Open, authenticated IMAP connection.
        folder: Target folder path (unencoded).
        raw: RFC 5322 message bytes with CRLF line endings
            (RFC 3501 literal requirement).
        flags: IMAP flag list, e.g. ``(\Draft)``.
        message_id: The message's Message-ID, used as the UID
            fallback when the server lacks UIDPLUS.

    Returns:
        The new UID, or None when it cannot be determined.

    Raises:
        imaplib.IMAP4.error: When the APPEND itself fails.
    """
    status, data = await imap_call(
        conn, "append", encode_folder(folder), flags,
        imaplib.Time2Internaldate(time.time()), raw,
    )
    if status != "OK":
        raise imaplib.IMAP4.error(f"APPEND to {folder!r} failed")
    # APPENDUID placement varies by server (tagged OK text vs
    # untagged line) — scan every bytes item imaplib hands back.
    for item in data:
        if isinstance(item, bytes):
            m = _APPENDUID_RE.search(item)
            if m:
                return m.group(1).decode()
    await imap_call(conn, "select", encode_folder(folder))
    return await find_by_message_id(conn, message_id)
