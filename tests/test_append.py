# SPDX-License-Identifier: Apache-2.0
"""Unit tests for imap/_append.py — APPEND + new-UID resolution."""
from __future__ import annotations

import imaplib
from unittest.mock import AsyncMock, patch

import pytest

from mail_mcp.imap import _append
from mail_mcp.imap._append import append_raw, find_by_message_id

_MSGID = "<x@example.com>"


async def test_appenduid_from_response_data() -> None:
    """UIDPLUS servers deliver the UID via APPENDUID."""
    reply = ("OK", [b"[APPENDUID 38505 3955] APPEND completed"])
    with patch.object(
        _append, "imap_call", new=AsyncMock(return_value=reply),
    ) as mock_call:
        uid = await append_raw(
            object(), "Drafts", b"raw", r"(\Draft)", _MSGID,  # type: ignore[arg-type]
        )
    assert uid == "3955"
    # No fallback SELECT/SEARCH needed.
    assert mock_call.await_count == 1


async def test_appenduid_scanned_across_items() -> None:
    """APPENDUID may arrive on any data line — all are scanned."""
    reply = ("OK", [None, b"noise", b"OK [APPENDUID 1 42] done"])
    with patch.object(
        _append, "imap_call", new=AsyncMock(return_value=reply),
    ):
        uid = await append_raw(
            object(), "Drafts", b"raw", r"(\Draft)", _MSGID,  # type: ignore[arg-type]
        )
    assert uid == "42"


async def test_fallback_to_message_id_search() -> None:
    """Without UIDPLUS the Message-ID SEARCH resolves the UID."""
    replies = [
        ("OK", [b"APPEND completed"]),          # append (no APPENDUID)
        ("OK", [b"5 EXISTS"]),                  # select
        ("OK", [b"7 9"]),                       # uid SEARCH
    ]
    with patch.object(
        _append, "imap_call", new=AsyncMock(side_effect=replies),
    ) as mock_call:
        uid = await append_raw(
            object(), "Drafts", b"raw", r"(\Draft)", _MSGID,  # type: ignore[arg-type]
        )
    assert uid == "9"
    assert mock_call.await_count == 3


async def test_append_failure_raises() -> None:
    """A NO reply to APPEND raises an IMAP error."""
    with patch.object(
        _append, "imap_call",
        new=AsyncMock(return_value=("NO", [b"quota exceeded"])),
    ):
        with pytest.raises(imaplib.IMAP4.error):
            await append_raw(
                object(), "Drafts", b"raw", r"(\Draft)", _MSGID,  # type: ignore[arg-type]
            )


async def test_find_by_message_id_none() -> None:
    """An empty Message-ID short-circuits to None."""
    with patch.object(
        _append, "imap_call", new=AsyncMock(),
    ) as mock_call:
        assert await find_by_message_id(object(), "") is None  # type: ignore[arg-type]
    assert mock_call.await_count == 0
