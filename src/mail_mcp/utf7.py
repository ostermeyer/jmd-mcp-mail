"""Modified UTF-7 codec for IMAP folder names (RFC 3501 §5.1.3).

Cherry-picked from imap_tools (https://github.com/ikvk/imap_tools),
MIT License, Copyright (c) 2018 Vladimir Kaukin.

The codec differs from standard UTF-7 (RFC 2152) in three ways:
- Shift character is '&' instead of '+'
- '&' itself is escaped as '&-'
- Modified base64 uses ',' instead of '/'
"""
from __future__ import annotations

import binascii
from collections.abc import MutableSequence

_AMPERSAND_ORD: int = ord("&")
_HYPHEN_ORD: int = ord("-")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _modified_base64(value: str) -> bytes:
    """Encode a string as modified Base64 of its UTF-16BE representation."""
    return (
        binascii.b2a_base64(value.encode("utf-16be"))
        .rstrip(b"\n=")
        .replace(b"/", b",")
    )


def _flush_nonascii(
    buf: MutableSequence[str],
    out: MutableSequence[bytes],
) -> None:
    """Encode buffered non-ASCII chars and append to output."""
    if buf:
        out.append(b"&" + _modified_base64("".join(buf)) + b"-")
        buf.clear()


def _modified_unbase64(value: bytearray) -> str:
    """Decode modified Base64 bytes back to a Unicode string."""
    return binascii.a2b_base64(
        value.replace(b",", b"/") + b"==="
    ).decode("utf-16be")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encode(name: str) -> bytes:
    """Encode a folder name to Modified UTF-7 bytes.

    Args:
        name: Unicode folder name (e.g. 'Entwürfe').

    Returns:
        Modified UTF-7 encoded bytes (e.g. b'Entw&APw-rfe').
    """
    out: list[bytes] = []
    buf: list[str] = []
    for char in name:
        code = ord(char)
        if 0x20 <= code <= 0x25 or 0x27 <= code <= 0x7E:
            _flush_nonascii(buf, out)
            out.append(char.encode())
        elif char == "&":
            _flush_nonascii(buf, out)
            out.append(b"&-")
        else:
            buf.append(char)
    _flush_nonascii(buf, out)
    return b"".join(out)


def decode(raw: bytes) -> str:
    """Decode Modified UTF-7 bytes to a Unicode folder name.

    Args:
        raw: Modified UTF-7 bytes (e.g. b'Entw&APw-rfe').

    Returns:
        Unicode folder name (e.g. 'Entwürfe').
    """
    out: list[str] = []
    encoded: bytearray = bytearray()
    for byte in raw:
        if byte == _AMPERSAND_ORD and not encoded:
            encoded.append(_AMPERSAND_ORD)
        elif byte == _HYPHEN_ORD and encoded:
            if len(encoded) == 1:
                out.append("&")
            else:
                out.append(_modified_unbase64(encoded[1:]))
            encoded = bytearray()
        elif encoded:
            encoded.append(byte)
        else:
            out.append(chr(byte))
    if encoded:
        out.append(_modified_unbase64(encoded[1:]))
    return "".join(out)
