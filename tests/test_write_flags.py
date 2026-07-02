# SPDX-License-Identifier: Apache-2.0
"""Unit tests for incremental flag ops in imap/write.py."""
from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

from mail_mcp._endpoint import ConnectionInfo, TlsMode
from mail_mcp.imap import write


def _info() -> ConnectionInfo:
    return ConnectionInfo(
        host="imap.example.com",
        port=993,
        tls_mode=TlsMode.IMPLICIT,
        username="user@example.com",
        password="pw",
    )


@contextlib.asynccontextmanager
async def _fake_open(
    _info: ConnectionInfo,
) -> AsyncGenerator[MagicMock, None]:
    yield MagicMock()


def _store_calls(mock: AsyncMock) -> list[tuple[object, ...]]:
    return [
        c.args[1:] for c in mock.await_args_list
        if c.args[1:3] == ("uid", "STORE")
    ]


async def test_flags_replace_uses_store_flags() -> None:
    """## flags[] issues a full STORE FLAGS replace."""
    imap_call = AsyncMock(return_value=("OK", []))
    with (
        patch.object(write, "open_imap", _fake_open),
        patch.object(write, "imap_call", new=imap_call),
    ):
        await write.write(
            "# Message\nid: 42\n## flags[]\n- \\Seen", _info(),
        )
    assert (
        "uid", "STORE", "42", "FLAGS", r"(\Seen)",
    ) in _store_calls(imap_call)


async def test_flags_add_and_remove_incremental() -> None:
    """flags-add[]/flags-remove[] map to +FLAGS / -FLAGS."""
    imap_call = AsyncMock(return_value=("OK", []))
    with (
        patch.object(write, "open_imap", _fake_open),
        patch.object(write, "imap_call", new=imap_call),
    ):
        await write.write(
            "# Message\n"
            "id: 42\n"
            "## flags-add[]\n"
            "- \\Seen\n"
            "## flags-remove[]\n"
            "- \\Flagged",
            _info(),
        )
    calls = _store_calls(imap_call)
    assert ("uid", "STORE", "42", "+FLAGS", r"(\Seen)") in calls
    assert ("uid", "STORE", "42", "-FLAGS", r"(\Flagged)") in calls
    # No full replace happened.
    assert not any(c[3] == "FLAGS" for c in calls)


async def test_replace_and_incremental_mutually_exclusive() -> None:
    """Combining flags[] with flags-add[] is refused."""
    result = await write.write(
        "# Message\n"
        "id: 42\n"
        "## flags[]\n"
        "- \\Seen\n"
        "## flags-add[]\n"
        "- \\Flagged",
        _info(),
    )
    assert "# Error" in result
    assert "flags-add" in result
