# SPDX-License-Identifier: Apache-2.0
"""Unit tests for imap/_special.py — special-use folder discovery."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from mail_mcp._endpoint import ConnectionInfo, TlsMode
from mail_mcp.imap import _special
from mail_mcp.imap._parse import FolderRecord
from mail_mcp.imap._special import (
    DRAFTS_FALLBACKS,
    DRAFTS_USE,
    SENT_FALLBACKS,
    SENT_USE,
    find_special_folder,
    pick_special,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Generator[None, None, None]:
    """Isolate the module-level discovery cache per test."""
    _special._cache.clear()
    yield
    _special._cache.clear()


def _folder(path: str, flags: list[str] | None = None) -> FolderRecord:
    return FolderRecord(
        name=path.rsplit("/", 1)[-1],
        path=path,
        parent=None,
        delim="/",
        flags=flags or [],
    )


def test_special_use_flag_wins_over_name() -> None:
    r"""A \Drafts attribute beats a name-matching folder."""
    records = [
        _folder("Drafts"),
        _folder("Brouillons", ["\\Drafts"]),
    ]
    assert pick_special(records, DRAFTS_USE, DRAFTS_FALLBACKS) == (
        "Brouillons"
    )


def test_special_use_flag_case_insensitive() -> None:
    r"""Attribute matching ignores case (\drafts vs \Drafts)."""
    records = [_folder("X", ["\\drafts"])]
    assert pick_special(records, DRAFTS_USE, DRAFTS_FALLBACKS) == "X"


def test_fallback_name_match() -> None:
    """Without attributes, well-known names match case-insensitively."""
    records = [_folder("INBOX"), _folder("entwürfe")]
    assert pick_special(records, DRAFTS_USE, DRAFTS_FALLBACKS) == (
        "entwürfe"
    )


def test_sent_fallback_german() -> None:
    """German Sent folder names are recognized."""
    records = [_folder("Gesendete Objekte")]
    assert pick_special(records, SENT_USE, SENT_FALLBACKS) == (
        "Gesendete Objekte"
    )


def test_no_match_returns_none() -> None:
    """Nothing matching yields None."""
    assert pick_special([_folder("INBOX")], SENT_USE, SENT_FALLBACKS) is None


def _info() -> ConnectionInfo:
    return ConnectionInfo(
        host="imap.example.com",
        port=993,
        tls_mode=TlsMode.IMPLICIT,
        username="user@example.com",
        password="pw",
    )


async def test_find_caches_positive_result() -> None:
    """A second lookup is served from the cache without LIST."""
    list_data = [b'(\\HasNoChildren \\Drafts) "/" Drafts']
    with patch.object(
        _special, "imap_call", new=AsyncMock(return_value=("OK", list_data)),
    ) as mock_call:
        first = await find_special_folder(
            object(), _info(), DRAFTS_USE, DRAFTS_FALLBACKS,  # type: ignore[arg-type]
        )
        second = await find_special_folder(
            object(), _info(), DRAFTS_USE, DRAFTS_FALLBACKS,  # type: ignore[arg-type]
        )
    assert first == second == "Drafts"
    assert mock_call.await_count == 1


async def test_find_negative_result_not_cached() -> None:
    """A miss is retried on the next call (only hits are cached)."""
    with patch.object(
        _special, "imap_call", new=AsyncMock(return_value=("OK", [])),
    ) as mock_call:
        for _ in range(2):
            result = await find_special_folder(
                object(), _info(), SENT_USE, SENT_FALLBACKS,  # type: ignore[arg-type]
            )
            assert result is None
    assert mock_call.await_count == 2
