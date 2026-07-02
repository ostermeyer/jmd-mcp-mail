# SPDX-License-Identifier: Apache-2.0
"""Reply threading: fetch original headers, derive reply defaults.

Given the UID of the message being answered, this module pulls the
headers needed to keep the thread intact (``In-Reply-To`` +
``References`` per RFC 5322 §3.6.4) and derives sensible defaults
for ``subject`` (``Re:`` prefix) and ``to`` (original ``Reply-To``
or ``From``).
"""
from __future__ import annotations

import email as email_lib
import imaplib
from dataclasses import dataclass
from email.utils import parseaddr

from mail_mcp.imap._connection import encode_folder, imap_call
from mail_mcp.imap._parse import _decode_header

_HEADER_FIELDS = (
    "(BODY.PEEK[HEADER.FIELDS "
    "(MESSAGE-ID REFERENCES SUBJECT FROM REPLY-TO)])"
)

# Subjects already carrying a reply prefix are left untouched.
_REPLY_PREFIXES = ("re:", "aw:", "antw:", "sv:")


@dataclass(slots=True)
class OriginalHeaders:
    """Thread-relevant headers of the message being answered."""

    message_id: str
    references: str
    subject: str
    from_addr: str
    reply_to: str


async def fetch_original(
    conn: imaplib.IMAP4,
    folder: str,
    uid: str,
) -> OriginalHeaders | None:
    """Fetch the threading headers of one message.

    Args:
        conn: Open, authenticated IMAP connection.
        folder: Folder the original lives in.
        uid: UID of the original message.

    Returns:
        The original's headers, or None when the message is missing.
    """
    await imap_call(conn, "select", encode_folder(folder), True)
    status, data = await imap_call(
        conn, "uid", "FETCH", uid, _HEADER_FIELDS
    )
    if status != "OK" or not data or not isinstance(data[0], tuple):
        return None
    raw = data[0][1]
    if not isinstance(raw, bytes):
        return None
    msg = email_lib.message_from_bytes(raw)
    return OriginalHeaders(
        message_id=str(msg.get("Message-ID") or "").strip(),
        references=" ".join(
            str(msg.get("References") or "").split()
        ),
        subject=_decode_header(msg.get("Subject")),
        from_addr=str(msg.get("From") or "").strip(),
        reply_to=str(msg.get("Reply-To") or "").strip(),
    )


def reply_headers(orig: OriginalHeaders) -> dict[str, str]:
    """Build In-Reply-To/References for a reply to *orig*."""
    if not orig.message_id:
        return {}
    return {
        "In-Reply-To": orig.message_id,
        "References": (
            f"{orig.references} {orig.message_id}".strip()
        ),
    }


def reply_subject(orig_subject: str) -> str:
    """Prefix ``Re:`` unless the subject already carries one."""
    s = orig_subject.strip()
    if s.lower().startswith(_REPLY_PREFIXES):
        return s
    return f"Re: {s}" if s else "Re:"


def reply_to_default(orig: OriginalHeaders) -> str:
    """The reply recipient: Reply-To when set, else From (bare)."""
    source = orig.reply_to or orig.from_addr
    _, addr = parseaddr(source)
    return addr or source


def apply_reply_defaults(
    fields: dict[str, object],
    orig: OriginalHeaders,
) -> None:
    """Fill missing ``to``/``subject`` fields from the original."""
    if not str(fields.get("to", "") or "").strip():
        fields["to"] = reply_to_default(orig)
    if not str(fields.get("subject", "") or "").strip():
        fields["subject"] = reply_subject(orig.subject)
