# SPDX-License-Identifier: Apache-2.0
"""Async IMAP connection context manager."""
from __future__ import annotations

import asyncio
import contextlib
import imaplib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from mail_mcp._endpoint import ConnectionInfo, TlsMode
from mail_mcp.utf7 import encode as utf7_encode


@asynccontextmanager
async def open_imap(
    info: ConnectionInfo,
) -> AsyncGenerator[imaplib.IMAP4, None]:
    """Open an authenticated IMAP connection in a thread.

    Uses ``IMAP4_SSL`` for ``TlsMode.IMPLICIT`` (typically port 993)
    and ``IMAP4`` + ``starttls()`` for ``TlsMode.STARTTLS``
    (typically port 143).  All blocking I/O runs via
    ``asyncio.to_thread()`` so the event loop is never blocked.

    Args:
        info: Resolved connection parameters from
            :meth:`ConnectionInfo.resolve`.

    Yields:
        Authenticated ``imaplib.IMAP4`` instance (``IMAP4_SSL``
        when implicit-TLS).
    """
    def _connect() -> imaplib.IMAP4:
        conn: imaplib.IMAP4
        if info.tls_mode == TlsMode.IMPLICIT:
            conn = imaplib.IMAP4_SSL(info.host, info.port)
        else:
            conn = imaplib.IMAP4(info.host, info.port)
            conn.starttls()
        conn.login(info.username, info.password)
        return conn

    conn = await asyncio.to_thread(_connect)
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(conn.logout)


async def imap_call(
    conn: imaplib.IMAP4,
    method: str,
    *args: object,
) -> tuple[str, list[bytes | None]]:
    """Invoke an imaplib method in a thread and return (status, data).

    Args:
        conn: Open IMAP connection.
        method: Method name on conn, e.g. 'uid', 'select', 'list'.
        *args: Positional arguments forwarded to the method.

    Returns:
        ``(status, data)`` tuple exactly as imaplib returns.

    Raises:
        imaplib.IMAP4.error: On IMAP-level errors.
    """
    fn = getattr(conn, method)
    status, data = await asyncio.to_thread(fn, *args)
    return str(status), list(data)


def encode_folder(path: str) -> str:
    """Encode a folder path to a quoted IMAP mailbox name.

    Handles Modified UTF-7 encoding and double-quoting of names
    that contain spaces or special characters.

    Args:
        path: Unicode folder path (e.g. 'Entwürfe' or
            'INBOX/Sent Items').

    Returns:
        Properly encoded IMAP mailbox string, quoted if necessary.
    """
    encoded = utf7_encode(path).decode("ascii")
    if " " in encoded or not encoded:
        return f'"{encoded}"'
    return encoded
