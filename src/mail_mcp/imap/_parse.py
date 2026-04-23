# SPDX-License-Identifier: Apache-2.0
"""IMAP response parsers and domain dataclasses.

Converts raw imaplib byte responses into typed dataclasses, and
serializes them to JMD-friendly dicts for the serialize() layer.
"""
from __future__ import annotations

import dataclasses
import email
import email.headerregistry
import re
import subprocess
from pathlib import Path

import markdownify

from mail_mcp import utf7

# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class EmailAddressRecord:
    """Parsed email address with optional display name."""

    name: str
    email: str


@dataclasses.dataclass(slots=True)
class AttachmentRecord:
    """Email attachment metadata (payload kept in memory for download)."""

    filename: str
    content_type: str
    content_id: str
    size: int
    payload: bytes
    path: str | None = None


@dataclasses.dataclass(slots=True)
class MessageRecord:
    """Fully parsed email message."""

    uid: str
    folder: str
    subject: str
    from_: EmailAddressRecord
    to: list[EmailAddressRecord]
    cc: list[EmailAddressRecord]
    bcc: list[EmailAddressRecord]
    reply_to: list[EmailAddressRecord]
    date: str
    flags: list[str]
    size: int
    body: str
    attachments: list[AttachmentRecord]
    mailbox: str = ""


@dataclasses.dataclass(slots=True)
class FolderRecord:
    """Parsed IMAP LIST folder entry."""

    name: str
    path: str
    parent: str | None
    delim: str
    flags: list[str]
    messages: int | None = None
    unseen: int | None = None
    mailbox: str = ""


# ---------------------------------------------------------------------------
# LIST response parser
# ---------------------------------------------------------------------------

# Matches:  (\Flag1 \Flag2) "/" "Folder Name"
#       or: (\Flag1 \Flag2) "/" FolderName
_LIST_RE = re.compile(
    rb'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]+)"\s+(?P<name>.+)$'
)


def parse_list_item(raw: bytes) -> FolderRecord | None:
    """Parse a single IMAP LIST response line into a FolderRecord.

    Args:
        raw: Raw bytes from imaplib LIST response.

    Returns:
        FolderRecord, or None if the line cannot be parsed.
    """
    m = _LIST_RE.match(raw.strip())
    if not m:
        return None

    flags_raw = m.group("flags").decode(errors="replace").split()
    delim_raw = m.group("delim").decode(errors="replace")
    name_raw = m.group("name").strip()

    # Strip surrounding quotes if present.
    if name_raw.startswith(b'"') and name_raw.endswith(b'"'):
        name_raw = name_raw[1:-1]

    path = utf7.decode(name_raw)
    delim = utf7.decode(delim_raw.encode())

    if delim and delim in path:
        parent = path.rsplit(delim, 1)[0]
        name = path.rsplit(delim, 1)[1]
    else:
        parent = None
        name = path

    return FolderRecord(
        name=name,
        path=path,
        parent=parent,
        delim=delim,
        flags=[f for f in flags_raw if f],
    )


def parse_status(raw_map: dict[str, bytes]) -> tuple[int | None, int | None]:
    """Parse IMAP STATUS response into (messages, unseen).

    Args:
        raw_map: Dict from STATUS response, keys like 'MESSAGES', 'UNSEEN'.

    Returns:
        Tuple of (messages, unseen), either may be None.
    """
    def _int(key: str) -> int | None:
        val = raw_map.get(key)
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    return _int("MESSAGES"), _int("UNSEEN")


# ---------------------------------------------------------------------------
# FETCH UID extractor
# ---------------------------------------------------------------------------


def extract_uid(fetch_header: bytes) -> str:
    """Extract UID from a FETCH response header line.

    Args:
        fetch_header: e.g. b'1 (UID 42 RFC822 {512})'

    Returns:
        UID string, or '?' if not found.
    """
    m = re.search(rb"UID\s+(\d+)", fetch_header)
    return m.group(1).decode() if m else "?"


# ---------------------------------------------------------------------------
# Header decoding (RFC 2047)
# ---------------------------------------------------------------------------


def _decode_header(raw: str | None) -> str:
    """Decode an RFC 2047-encoded mail header to plain text.

    Args:
        raw: Raw header string, possibly RFC 2047 encoded.

    Returns:
        Decoded plain text string.
    """
    if not raw:
        return ""
    from email.header import decode_header
    parts = []
    for chunk, charset in decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return " ".join(parts).replace("\n", " ").replace("\r", "").strip()


# ---------------------------------------------------------------------------
# EmailAddress parsing
# ---------------------------------------------------------------------------


def _parse_address(raw: str | None) -> EmailAddressRecord:
    """Parse a single RFC 5322 address into an EmailAddressRecord.

    Args:
        raw: Raw address string (e.g. '"Alice" <alice@example.com>').

    Returns:
        EmailAddressRecord with name and email fields.
    """
    if not raw:
        return EmailAddressRecord(name="", email="")
    decoded = _decode_header(raw)
    m = re.match(r'^"?([^"<]*?)"?\s*<([^>]+)>', decoded)
    if m:
        return EmailAddressRecord(
            name=m.group(1).strip(),
            email=m.group(2).strip(),
        )
    if "@" in decoded:
        return EmailAddressRecord(name="", email=decoded.strip())
    return EmailAddressRecord(name=decoded.strip(), email="")


def _parse_address_list(raw: str | None) -> list[EmailAddressRecord]:
    """Parse a comma-separated address list.

    Args:
        raw: Raw header value, possibly multiple addresses.

    Returns:
        List of EmailAddressRecord instances.
    """
    if not raw:
        return []
    decoded = _decode_header(raw)
    # Split on commas not inside angle brackets.
    addrs = re.split(r",(?![^<]*>)", decoded)
    return [_parse_address(a.strip()) for a in addrs if a.strip()]


# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------


def _xdg_download_dir() -> Path:
    """Return the XDG user download directory, falling back to ~/Downloads."""
    try:
        result = subprocess.run(
            ["xdg-user-dir", "DOWNLOAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return Path.home() / "Downloads"


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain-text body, converting HTML to Markdown if needed.

    Args:
        msg: Parsed email.message.Message object.

    Returns:
        Plain text body (max 4000 chars), stripped.
    """
    plain = ""
    html_raw: bytes | None = None
    html_charset: str | None = None

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            if ct == "text/plain" and not plain:
                plain = payload.decode(
                    part.get_content_charset() or "utf-8",
                    errors="replace",
                )
            elif ct == "text/html" and html_raw is None:
                html_raw = payload
                html_charset = part.get_content_charset()
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            if msg.get_content_type() == "text/html":
                html_raw = payload
                html_charset = msg.get_content_charset()
            else:
                plain = payload.decode(
                    msg.get_content_charset() or "utf-8",
                    errors="replace",
                )

    if plain:
        return plain.strip()[:4000]
    if html_raw is not None:
        html_str = html_raw.decode(html_charset or "utf-8", errors="replace")
        return markdownify.markdownify(
            html_str,
            heading_style="ATX",
            strip=["table", "thead", "tbody", "tr", "th", "td"],
        ).strip()[:4000]
    return ""


# ---------------------------------------------------------------------------
# Attachment collection
# ---------------------------------------------------------------------------


def _collect_attachments(
    msg: email.message.Message,
    download_dest: Path | None,
) -> list[AttachmentRecord]:
    """Collect attachments, optionally saving to disk.

    Args:
        msg: Parsed email.message.Message.
        download_dest: If set, write attachment bytes to this directory.

    Returns:
        List of AttachmentRecord instances.
    """
    records: list[AttachmentRecord] = []
    if download_dest:
        download_dest.mkdir(parents=True, exist_ok=True)

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        ct = part.get_content_type()
        is_attachment = "attachment" in disposition or (
            ct not in ("text/plain", "text/html") and part.get_filename()
        )
        if not is_attachment:
            continue

        filename = _decode_header(part.get_filename()) or "attachment"
        content_id = str(part.get("Content-ID") or "").strip("<>")
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue

        path: str | None = None
        if download_dest:
            dest = download_dest / filename
            dest.write_bytes(payload)
            path = str(dest)

        records.append(AttachmentRecord(
            filename=filename,
            content_type=ct,
            content_id=content_id,
            size=len(payload),
            payload=payload,
            path=path,
        ))
    return records


# ---------------------------------------------------------------------------
# Full message parser
# ---------------------------------------------------------------------------


def parse_message(
    uid: str,
    raw_bytes: bytes,
    folder: str,
    download_dest: Path | None = None,
    headers_only: bool = False,
) -> MessageRecord:
    """Parse raw RFC 2822 bytes into a MessageRecord.

    Args:
        uid: IMAP UID string.
        raw_bytes: Full RFC 2822 message bytes.
        folder: Folder path the message was fetched from.
        download_dest: If set, attachments are saved here.
        headers_only: If True, skip body and attachments.

    Returns:
        MessageRecord with all parsed fields.
    """
    msg = email.message_from_bytes(raw_bytes)

    flags_raw = str(msg.get("X-IMAP-Flags") or "")
    flags = [f.strip() for f in flags_raw.split() if f.strip()]

    size_raw = msg.get("X-IMAP-Size")
    size = int(size_raw) if size_raw and str(size_raw).isdigit() else 0

    body = "" if headers_only else _extract_body(msg)
    attachments = (
        [] if headers_only
        else _collect_attachments(msg, download_dest)
    )

    return MessageRecord(
        uid=uid,
        folder=folder,
        subject=_decode_header(msg.get("Subject")),
        from_=_parse_address(msg.get("From")),
        to=_parse_address_list(msg.get("To")),
        cc=_parse_address_list(msg.get("Cc")),
        bcc=_parse_address_list(msg.get("Bcc")),
        reply_to=_parse_address_list(msg.get("Reply-To")),
        date=_decode_header(msg.get("Date")),
        flags=flags,
        size=size,
        body=body,
        attachments=attachments,
    )


# ---------------------------------------------------------------------------
# JMD serialization helpers
# ---------------------------------------------------------------------------


def address_to_dict(rec: EmailAddressRecord) -> dict[str, object]:
    """Convert an EmailAddressRecord to a JMD-serializable dict."""
    d: dict[str, object] = {"email": rec.email}
    if rec.name:
        d["name"] = rec.name
    return d


def attachment_to_dict(rec: AttachmentRecord) -> dict[str, object]:
    """Convert an AttachmentRecord to a JMD-serializable dict."""
    d: dict[str, object] = {
        "filename": rec.filename,
        "content-type": rec.content_type,
        "size": rec.size,
    }
    if rec.content_id:
        d["content-id"] = rec.content_id
    if rec.path:
        d["path"] = rec.path
    return d


def message_to_dict(rec: MessageRecord) -> dict[str, object]:
    """Convert a MessageRecord to a JMD-serializable dict.

    Args:
        rec: Parsed message record.

    Returns:
        Dict suitable for passing to jmd.serialize().
    """
    d: dict[str, object] = {
        "id": rec.uid,
        "folder": rec.folder,
        "subject": rec.subject,
        "date": rec.date,
    }
    if rec.mailbox:
        d["mailbox"] = rec.mailbox
    if rec.size:
        d["size"] = rec.size
    if rec.body:
        d["body"] = rec.body
    if rec.flags:
        d["flags"] = rec.flags
    # Nested objects must come after all scalar fields.
    d["from"] = address_to_dict(rec.from_)
    d["to"] = [address_to_dict(a) for a in rec.to]
    if rec.cc:
        d["cc"] = [address_to_dict(a) for a in rec.cc]
    if rec.bcc:
        d["bcc"] = [address_to_dict(a) for a in rec.bcc]
    if rec.reply_to:
        d["reply-to"] = [address_to_dict(a) for a in rec.reply_to]
    if rec.attachments:
        d["attachments"] = [attachment_to_dict(a) for a in rec.attachments]
    return d


def folder_to_dict(rec: FolderRecord) -> dict[str, object]:
    """Convert a FolderRecord to a JMD-serializable dict.

    Args:
        rec: Parsed folder record.

    Returns:
        Dict suitable for passing to jmd.serialize().
    """
    d: dict[str, object] = {
        "name": rec.name,
        "path": rec.path,
        "delim": rec.delim,
    }
    if rec.mailbox:
        d["mailbox"] = rec.mailbox
    if rec.parent is not None:
        d["parent"] = rec.parent
    if rec.flags:
        d["flags"] = rec.flags
    if rec.messages is not None:
        d["messages"] = rec.messages
    if rec.unseen is not None:
        d["unseen"] = rec.unseen
    return d
