# SPDX-License-Identifier: Apache-2.0
"""Async IMAP4_SSL connection context manager."""
from __future__ import annotations

import asyncio
import contextlib
import imaplib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from mail_mcp.config import MailConfig
from mail_mcp.utf7 import encode as utf7_encode


@asynccontextmanager
async def open_imap(
    cfg: MailConfig,
) -> AsyncGenerator[imaplib.IMAP4_SSL, None]:
    """Open an authenticated IMAP4_SSL connection in a thread.

    Yields an imaplib.IMAP4_SSL instance.  All blocking I/O runs via
    asyncio.to_thread() so the event loop is never blocked.

    Args:
        cfg: Mail configuration with IMAP host, port, and credentials.

    Yields:
        Authenticated imaplib.IMAP4_SSL connection.
    """
    def _connect() -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
        conn.login(cfg.username, cfg.password)
        return conn

    conn = await asyncio.to_thread(_connect)
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(conn.logout)


async def imap_call(
    conn: imaplib.IMAP4_SSL,
    method: str,
    *args: object,
) -> tuple[str, list[bytes | None]]:
    """Invoke an imaplib method in a thread and return (status, data).

    Args:
        conn: Open IMAP4_SSL connection.
        method: Method name on conn, e.g. 'uid', 'select', 'list'.
        *args: Positional arguments forwarded to the method.

    Returns:
        (status, data) tuple exactly as imaplib returns.

    Raises:
        imaplib.IMAP4.error: On IMAP-level errors.
    """
    fn = getattr(conn, method)
    status, data = await asyncio.to_thread(fn, *args)
    return str(status), list(data)


def encode_folder(path: str) -> str:
    """Encode a folder path to a quoted IMAP mailbox name.

    Handles Modified UTF-7 encoding and double-quoting of names that
    contain spaces or special characters.

    Args:
        path: Unicode folder path (e.g. 'Entwürfe' or 'INBOX/Sent Items').

    Returns:
        Properly encoded IMAP mailbox string, quoted if necessary.
    """
    encoded = utf7_encode(path).decode("ascii")
    # Quote if the name contains spaces or is empty.
    if " " in encoded or not encoded:
        return f'"{encoded}"'
    return encoded
