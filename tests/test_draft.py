# SPDX-License-Identifier: Apache-2.0
"""Unit tests for imap/draft.py and the write-tool draft routing."""
from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from mail_mcp._endpoint import ConnectionInfo, TlsMode
from mail_mcp.imap import draft, write


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


def _patch_draft_io(**overrides: Any) -> Any:
    """Patch draft's IMAP surface; returns the patch context stack."""
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch.object(draft, "open_imap", _fake_open),
    )
    mocks = {
        "append_raw": AsyncMock(return_value="7"),
        "imap_call": AsyncMock(return_value=("OK", [])),
        "find_special_folder": AsyncMock(return_value="Drafts"),
    }
    mocks.update(overrides)
    handles = {
        name: stack.enter_context(
            patch.object(draft, name, new=mock),
        )
        for name, mock in mocks.items()
    }
    return stack, handles


# ---------------------------------------------------------------------------
# create_draft
# ---------------------------------------------------------------------------


async def test_create_draft_returns_new_id() -> None:
    """A created draft echoes its folder and new id."""
    stack, handles = _patch_draft_io()
    with stack:
        result = await draft.create_draft(
            {"to": "a@example.com", "subject": "S", "body": "B"},
            _info(),
        )
    assert "# Message" in result
    assert 'id: "7"' in result
    assert "folder: Drafts" in result


async def test_create_draft_explicit_folder_wins() -> None:
    """An explicit folder: field bypasses discovery entirely."""
    stack, handles = _patch_draft_io()
    with stack:
        await draft.create_draft(
            {"subject": "S", "folder": "MyDrafts"}, _info(),
        )
    handles["find_special_folder"].assert_not_awaited()
    assert handles["append_raw"].await_args[0][1] == "MyDrafts"


async def test_create_draft_config_folder_wins_over_discovery() -> None:
    """The config drafts-folder beats SPECIAL-USE discovery."""
    stack, handles = _patch_draft_io()
    with stack:
        await draft.create_draft(
            {"subject": "S"}, _info(), drafts_folder="Entwürfe",
        )
    handles["find_special_folder"].assert_not_awaited()
    assert handles["append_raw"].await_args[0][1] == "Entwürfe"


async def test_create_draft_no_folder_found() -> None:
    """Discovery miss + failed CREATE yields no_drafts_folder."""
    stack, handles = _patch_draft_io(
        find_special_folder=AsyncMock(return_value=None),
        imap_call=AsyncMock(return_value=("NO", [])),
    )
    with stack:
        result = await draft.create_draft({"subject": "S"}, _info())
    assert "no_drafts_folder" in result


async def test_create_draft_creates_drafts_as_last_resort() -> None:
    """Discovery miss + successful CREATE targets 'Drafts'."""
    stack, handles = _patch_draft_io(
        find_special_folder=AsyncMock(return_value=None),
    )
    with stack:
        await draft.create_draft({"subject": "S"}, _info())
    assert handles["append_raw"].await_args[0][1] == "Drafts"


async def test_create_draft_validation_before_imap() -> None:
    """An empty draft errors without touching the connection."""
    stack, handles = _patch_draft_io()
    with stack:
        result = await draft.create_draft({}, _info())
    assert "missing_fields" in result
    handles["append_raw"].assert_not_awaited()


async def test_create_draft_no_footer_bcc_header() -> None:
    """Draft bytes carry no AI footer but a visible Bcc header."""
    stack, handles = _patch_draft_io()
    with stack:
        await draft.create_draft(
            {
                "to": "a@example.com",
                "subject": "S",
                "body": "B",
                "bcc": "bob@example.com",
            },
            _info(),
        )
    raw = handles["append_raw"].await_args[0][2]
    assert b"AI assistant" not in raw
    assert b"Bcc: bob@example.com" in raw
    flags = handles["append_raw"].await_args[0][3]
    assert flags == r"(\Draft)"


# ---------------------------------------------------------------------------
# update_draft
# ---------------------------------------------------------------------------


async def test_update_draft_appends_then_deletes_old() -> None:
    """The old UID is flagged and expunged after the new APPEND."""
    imap_call = AsyncMock(return_value=("OK", []))
    stack, handles = _patch_draft_io(imap_call=imap_call)
    with stack:
        result = await draft.update_draft(
            "17", "Drafts", {"subject": "v2"}, _info(),
        )
    handles["append_raw"].assert_awaited_once()
    calls = [c.args[1:] for c in imap_call.await_args_list]
    assert ("uid", "STORE", "17", "+FLAGS", r"(\Deleted)") in calls
    assert ("expunge",) in calls
    assert 'id: "7"' in result


async def test_update_draft_old_uid_missing_still_succeeds() -> None:
    """A vanished old draft yields a note, not a failure."""
    async def _imap(conn: Any, method: str, *args: Any) -> Any:
        if method == "uid" and args and args[0] == "STORE":
            return ("NO", [])
        return ("OK", [])

    stack, handles = _patch_draft_io(imap_call=AsyncMock(side_effect=_imap))
    with stack:
        result = await draft.update_draft(
            "99", "Drafts", {"subject": "v2"}, _info(),
        )
    assert "note: previous draft 99 not found" in result
    assert 'id: "7"' in result


# ---------------------------------------------------------------------------
# write-tool routing
# ---------------------------------------------------------------------------


async def test_route_no_id_calls_create() -> None:
    """# Message without id routes to create_draft."""
    with patch.object(
        write.imap_draft, "create_draft",
        new=AsyncMock(return_value="CREATED"),
    ) as mock_create:
        result = await write.write(
            "# Message\nsubject: Hi", _info(), drafts_folder="D",
        )
    assert result == "CREATED"
    assert mock_create.await_args.kwargs["drafts_folder"] == "D"


async def test_route_id_plus_content_calls_update() -> None:
    """# Message with id and body routes to update_draft."""
    with patch.object(
        write.imap_draft, "update_draft",
        new=AsyncMock(return_value="UPDATED"),
    ) as mock_update:
        result = await write.write(
            "# Message\nid: 5\nfolder: Drafts\nbody: neu", _info(),
        )
    assert result == "UPDATED"
    assert mock_update.await_args.args[:2] == ("5", "Drafts")


async def test_route_id_flags_only_stays_on_flags_path() -> None:
    """# Message with id and only flags does NOT touch drafts."""
    with (
        patch.object(
            write.imap_draft, "create_draft", new=AsyncMock(),
        ) as mock_create,
        patch.object(
            write.imap_draft, "update_draft", new=AsyncMock(),
        ) as mock_update,
        patch.object(
            write, "_update_flags",
            new=AsyncMock(return_value="FLAGS"),
        ) as mock_flags,
    ):
        result = await write.write(
            "# Message\nid: 5\n## flags[]\n- \\Seen", _info(),
        )
    assert result == "FLAGS"
    mock_create.assert_not_awaited()
    mock_update.assert_not_awaited()
    mock_flags.assert_awaited_once()


async def test_route_content_plus_move_rejected() -> None:
    """Content fields combined with move-to are refused."""
    result = await write.write(
        "move-to: Archive\n\n# Message\nid: 5\nbody: x", _info(),
    )
    assert "# Error" in result
    assert "move-to" in result
