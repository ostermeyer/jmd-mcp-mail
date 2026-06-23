# SPDX-License-Identifier: Apache-2.0
"""Translate JMD QueryFields to an IMAP SEARCH criteria string."""
from __future__ import annotations

from jmd import QueryField

from mail_mcp import _pseudonym

# Mapping from JMD field name to IMAP SEARCH key.
_IMAP_KEY: dict[str, str] = {
    "from": "FROM",
    "to": "TO",
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


def build(fields: list[QueryField]) -> str:
    """Translate JMD query fields to an IMAP SEARCH criteria string.

    Skips fields used for routing (folder) or pagination (page, page-size).
    Multiple criteria are ANDed together by IMAP convention.

    Args:
        fields: Parsed JMD query fields.

    Returns:
        IMAP SEARCH criteria string, e.g. 'FROM "alice" SUBJECT "inv"'.
        Returns 'ALL' when no translatable criteria are present.
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

        imap_key = _IMAP_KEY.get(key)
        if not imap_key or not vals:
            continue

        # Recipient predicates may carry pseudonyms — resolve known
        # tokens back to the real address before building the SEARCH;
        # unknown values pass through for plain substring matching.
        if key in ("from", "to"):
            vals = [_pseudonym.resolve_search(str(v)) for v in vals]

        match op:
            case "=" | "~" | "regex":
                # IMAP SEARCH does substring matching natively.
                val = str(vals[0]).strip("~^$.*")
                parts.append(f'{imap_key} "{val}"')
            case "|":
                # OR across all values.
                for val in vals:
                    val = str(val).strip("~^$.*")
                    parts.append(f'{imap_key} "{val}"')
            case "!":
                # Negation: NOT (imap_key "val")
                val = str(vals[0]).strip("~^$.*!")
                parts.append(f'NOT {imap_key} "{val}"')

    return " ".join(parts) if parts else "ALL"
