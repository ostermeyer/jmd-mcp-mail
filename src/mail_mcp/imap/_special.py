# SPDX-License-Identifier: Apache-2.0
r"""Special-use folder discovery (RFC 6154) with name fallbacks.

Finds the Drafts / Sent folder of an account.  Servers that announce
SPECIAL-USE attributes (``\Drafts``, ``\Sent``) on LIST win; when
absent, a case-insensitive match against well-known folder names is
tried (including localized ones — German providers use ``Entwürfe``
and ``Gesendete Elemente``).

Results are cached per ``(host, username, use)`` for the process
lifetime — folder sets are near-static, and a server restart clears
the cache naturally.  Only positive results are cached.
"""
from __future__ import annotations

import imaplib
from collections.abc import Sequence

from mail_mcp._endpoint import ConnectionInfo
from mail_mcp.imap._connection import imap_call
from mail_mcp.imap._parse import FolderRecord, parse_list_item

DRAFTS_USE = "\\Drafts"
SENT_USE = "\\Sent"

DRAFTS_FALLBACKS: tuple[str, ...] = (
    "Drafts",
    "Draft",
    "INBOX.Drafts",
    "INBOX/Drafts",
    "Entwürfe",
    "[Gmail]/Drafts",
)
SENT_FALLBACKS: tuple[str, ...] = (
    "Sent",
    "Sent Items",
    "Sent Messages",
    "INBOX.Sent",
    "INBOX/Sent",
    "Gesendet",
    "Gesendete Elemente",
    "Gesendete Objekte",
    "[Gmail]/Sent Mail",
)

_cache: dict[tuple[str, str, str], str] = {}


def pick_special(
    records: Sequence[FolderRecord],
    use: str,
    fallbacks: Sequence[str],
) -> str | None:
    r"""Pick the folder matching a special-use attribute, or by name.

    Args:
        records: Parsed LIST entries.
        use: SPECIAL-USE attribute, e.g. ``\Drafts``.
        fallbacks: Well-known folder paths tried case-insensitively
            when no record carries the attribute.

    Returns:
        The folder path, or ``None`` when nothing matches.
    """
    use_lower = use.lower()
    for rec in records:
        if any(f.lower() == use_lower for f in rec.flags):
            return rec.path
    by_path = {rec.path.lower(): rec.path for rec in records}
    for name in fallbacks:
        found = by_path.get(name.lower())
        if found is not None:
            return found
    return None


async def find_special_folder(
    conn: imaplib.IMAP4,
    info: ConnectionInfo,
    use: str,
    fallbacks: Sequence[str],
) -> str | None:
    r"""Resolve a special-use folder on an open connection.

    Args:
        conn: Open, authenticated IMAP connection.
        info: Connection parameters (cache key: host + username).
        use: SPECIAL-USE attribute, e.g. ``\Drafts``.
        fallbacks: Name fallbacks for servers without SPECIAL-USE.

    Returns:
        The folder path, or ``None`` when nothing matches.
    """
    key = (info.host, info.username, use)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    status, data = await imap_call(conn, "list", '""', "*")
    if status != "OK":
        return None
    records = [
        rec
        for item in data
        if isinstance(item, bytes)
        and (rec := parse_list_item(item)) is not None
    ]
    found = pick_special(records, use, fallbacks)
    if found is not None:
        _cache[key] = found
    return found
