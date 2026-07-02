# SPDX-License-Identifier: Apache-2.0
"""Translate JMD QueryFields to an IMAP SEARCH criteria string."""
from __future__ import annotations

import datetime

from jmd import QueryField

# Mapping from JMD field name to IMAP SEARCH key.
_IMAP_KEY: dict[str, str] = {
    "from": "FROM",
    "to": "TO",
    "cc": "CC",
    "subject": "SUBJECT",
    "body": "TEXT",
}

# JMD flag fields that map to IMAP flag keywords.
_FLAG_KEY: dict[str, tuple[str, str]] = {
    "seen": ("SEEN", "UNSEEN"),
    "answered": ("ANSWERED", "UNANSWERED"),
    "flagged": ("FLAGGED", "UNFLAGGED"),
    "draft": ("DRAFT", "UNDRAFT"),
    "deleted": ("DELETED", "UNDELETED"),
}

# Date fields (ISO input) mapping to IMAP date criteria.  IMAP
# compares the internal (arrival) date at day granularity; SINCE is
# inclusive, BEFORE exclusive.
_DATE_KEY: dict[str, str] = {
    "since": "SINCE",
    "before": "BEFORE",
    "on": "ON",
}

# Hardcoded English month names — strftime("%b") is locale-dependent
# and IMAP demands the RFC 3501 English abbreviations.
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _quote(val: str) -> str:
    """Escape and double-quote a SEARCH string value."""
    escaped = val.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _imap_date(val: str) -> str:
    """Convert an ISO date (YYYY-MM-DD) to IMAP DD-Mon-YYYY.

    Raises:
        ValueError: If the value is not a valid ISO date.
    """
    try:
        d = datetime.date.fromisoformat(val.strip())
    except ValueError as exc:
        raise ValueError(
            f"invalid date {val!r} — expected ISO format YYYY-MM-DD"
        ) from exc
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def build(fields: list[QueryField]) -> tuple[str, bool]:
    """Translate JMD query fields to an IMAP SEARCH criteria string.

    Skips fields used for routing (folder) or pagination (page,
    page-size).  Multiple criteria are ANDed together by IMAP
    convention.

    Args:
        fields: Parsed JMD query fields.

    Returns:
        ``(criteria, needs_utf8)`` — the SEARCH string (``'ALL'``
        when no translatable criteria are present) and whether it
        contains non-ASCII characters and must therefore be issued
        as ``SEARCH CHARSET UTF-8`` with UTF-8-encoded bytes.

    Raises:
        ValueError: On malformed date values.
    """
    parts: list[str] = []

    for f in fields:
        key = f.key
        op = f.condition.op
        vals = f.condition.values

        # Skip routing and pagination fields.
        if key in ("folder", "page", "page-size", "count"):
            continue

        # Flag criteria: seen: true / seen: false
        if key in _FLAG_KEY:
            if vals and str(vals[0]).lower() in ("true", "1"):
                parts.append(_FLAG_KEY[key][0])
            else:
                parts.append(_FLAG_KEY[key][1])
            continue

        # Date criteria: since/before/on with ISO dates.
        if key in _DATE_KEY:
            if vals:
                parts.append(
                    f"{_DATE_KEY[key]} {_imap_date(str(vals[0]))}"
                )
            continue

        imap_key = _IMAP_KEY.get(key)
        if not imap_key or not vals:
            continue

        match op:
            case "=" | "~" | "regex":
                # IMAP SEARCH does substring matching natively.
                val = str(vals[0]).strip("~^$.*")
                parts.append(f"{imap_key} {_quote(val)}")
            case "|":
                # OR across all values.
                for val in vals:
                    val = str(val).strip("~^$.*")
                    parts.append(f"{imap_key} {_quote(val)}")
            case "!":
                # Negation: NOT (imap_key "val")
                val = str(vals[0]).strip("~^$.*!")
                parts.append(f"NOT {imap_key} {_quote(val)}")

    criteria = " ".join(parts) if parts else "ALL"
    needs_utf8 = any(ord(c) > 127 for c in criteria)
    return criteria, needs_utf8
