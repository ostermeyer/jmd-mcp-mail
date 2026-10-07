# SPDX-License-Identifier: Apache-2.0
"""Tests for destructive IMAP delete confirmation boundaries."""
from __future__ import annotations

import pytest

from mail_mcp._endpoint import ConnectionInfo, TlsMode
from mail_mcp.imap import delete as imap_delete


def _info() -> ConnectionInfo:
    """Return inert connection data; confirmation rejects before I/O."""
    return ConnectionInfo(
        host="mail.example",
        port=993,
        tls_mode=TlsMode.IMPLICIT,
        username="user@example",
        password="secret",
    )


@pytest.mark.asyncio
async def test_single_message_delete_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No single-message delete handler runs without its confirmation."""

    async def _unexpected(
        document: str,
        info: ConnectionInfo,
    ) -> str:
        raise AssertionError("message deletion must not reach IMAP")

    monkeypatch.setattr(imap_delete, "_delete_message", _unexpected)

    result = await imap_delete.delete(
        "#- Message\nid: 42\nfolder: INBOX",
        _info(),
    )

    assert "confirmation_required" in result
    assert "delete-message" in result


@pytest.mark.asyncio
async def test_bulk_message_delete_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No bulk message delete handler runs without its confirmation."""

    async def _unexpected(
        parsed: object,
        info: ConnectionInfo,
    ) -> str:
        raise AssertionError("bulk deletion must not reach IMAP")

    monkeypatch.setattr(imap_delete, "_bulk_delete_messages", _unexpected)

    result = await imap_delete.delete(
        "#- Message[]\n- id: 42\n  folder: INBOX",
        _info(),
    )

    assert "confirmation_required" in result
    assert "delete-message" in result


@pytest.mark.asyncio
async def test_confirmed_message_delete_reaches_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact message confirmation permits the delete dispatcher."""
    called: list[None] = []

    async def _confirmed(
        document: str,
        info: ConnectionInfo,
    ) -> str:
        called.append(None)
        return "# Message\nid: 42"

    monkeypatch.setattr(imap_delete, "_delete_message", _confirmed)

    result = await imap_delete.delete(
        "confirm: delete-message\n\n#- Message\nid: 42\nfolder: INBOX",
        _info(),
    )

    assert result == "# Message\nid: 42"
    assert called == [None]
