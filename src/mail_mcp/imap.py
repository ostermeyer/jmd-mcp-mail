"""IMAP reader for jmd-mcp-mail.

Reads, searches, and deletes email messages via IMAP4_SSL (stdlib).
Supports JMD data, query, schema, and delete document modes.
HTML bodies are converted to Markdown via markdownify.
Attachments can be downloaded to a user-specified path.
"""
from __future__ import annotations

import email
import imaplib
import re
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from email.header import decode_header
from pathlib import Path

import markdownify
from jmd import (
    JMDDeleteParser,
    JMDQueryParser,
    QueryField,
    jmd_mode,
    jmd_to_dict,
    serialize,
)

from .config import MailConfig

_LABEL = "Message"

# Maximum messages returned per query (guards against huge inboxes).
_DEFAULT_PAGE_SIZE = 25


# ---------------------------------------------------------------------------
# XDG download directory
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


# ---------------------------------------------------------------------------
# IMAP connection helper
# ---------------------------------------------------------------------------

@contextmanager
def _connect(cfg: MailConfig) -> Generator[imaplib.IMAP4_SSL, None, None]:
    """Open an authenticated IMAP4_SSL connection."""
    conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        conn.login(cfg.username, cfg.password)
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Header decoding
# ---------------------------------------------------------------------------

def _decode_header(raw: str | None) -> str:
    """Decode an RFC 2047-encoded mail header to a plain string."""
    if not raw:
        return ""
    parts = []
    for chunk, charset in decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return " ".join(parts).replace("\n", " ").replace("\r", "").strip()


# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------

def _extract_body(msg: email.message.Message) -> str:
    """Extract body from a message, converting HTML to Markdown."""
    plain: str = ""
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
        return plain.strip()
    if html_raw is not None:
        html_str = html_raw.decode(html_charset or "utf-8", errors="replace")
        return markdownify.markdownify(
            html_str,
            heading_style="ATX",
            strip=["table", "thead", "tbody", "tr", "th", "td"],
        ).strip()
    return ""


# ---------------------------------------------------------------------------
# Attachment handling
# ---------------------------------------------------------------------------

def _collect_attachments(
    msg: email.message.Message,
) -> list[dict[str, object]]:
    """Collect attachment metadata from a message (no download)."""
    attachments: list[dict[str, object]] = []
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        ct = part.get_content_type()
        if "attachment" in disposition or (
            ct not in ("text/plain", "text/html")
            and part.get_filename()
        ):
            filename = _decode_header(part.get_filename()) or "attachment"
            payload = part.get_payload(decode=True)
            size = len(payload) if isinstance(payload, bytes) else 0
            attachments.append({
                "name": filename,
                "content_type": ct,
                "size": size,
            })
    return attachments


def _download_attachments(
    msg: email.message.Message,
    dest: Path,
) -> list[dict[str, object]]:
    """Download attachments to dest directory, return metadata with paths."""
    dest.mkdir(parents=True, exist_ok=True)
    attachments: list[dict[str, object]] = []
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        ct = part.get_content_type()
        if "attachment" in disposition or (
            ct not in ("text/plain", "text/html")
            and part.get_filename()
        ):
            filename = _decode_header(part.get_filename()) or "attachment"
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            out = dest / filename
            out.write_bytes(payload)
            attachments.append({
                "name": filename,
                "content_type": ct,
                "size": len(payload),
                "path": str(out),
            })
    return attachments


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def _parse_message(
    uid: str,
    raw_bytes: bytes,
    folder: str,
    download: bool = False,
    download_path: Path | None = None,
) -> dict[str, object]:
    """Parse a raw RFC 2822 message into a JMD-friendly dict."""
    msg = email.message_from_bytes(raw_bytes)
    body = _extract_body(msg)[:4000]

    if download:
        dest = download_path or _xdg_download_dir()
        attachments: list[dict[str, object]] = _download_attachments(msg, dest)
    else:
        attachments = _collect_attachments(msg)

    record: dict[str, object] = {
        "id": uid,
        "from": _decode_header(msg.get("From")),
        "to": _decode_header(msg.get("To")),
        "subject": _decode_header(msg.get("Subject")),
        "date": _decode_header(msg.get("Date")),
        "body": body,
        "folder": folder,
    }
    if attachments:
        record["attachments"] = attachments
    return record


def _parse_envelope(
    uid: str, raw_bytes: bytes, folder: str
) -> dict[str, object]:
    """Parse only headers (fast, for listing)."""
    msg = email.message_from_bytes(raw_bytes)
    return {
        "id": uid,
        "from": _decode_header(msg.get("From")),
        "to": _decode_header(msg.get("To")),
        "subject": _decode_header(msg.get("Subject")),
        "date": _decode_header(msg.get("Date")),
        "folder": folder,
    }


# ---------------------------------------------------------------------------
# IMAP SEARCH criteria builder
# ---------------------------------------------------------------------------

def _build_search_criteria(fields: list[QueryField]) -> str:
    """Translate JMD QueryFields into an IMAP SEARCH string."""
    criteria: list[str] = ["ALL"]
    imap_keys = {"from": "FROM", "to": "TO", "subject": "SUBJECT"}

    for f in fields:
        if f.key == "folder":
            continue
        imap_key = imap_keys.get(f.key)
        if not imap_key:
            continue
        op = f.condition.op
        vals = f.condition.values
        if op in ("regex", "~"):
            criteria.append(f'{imap_key} "{vals[0]}"')
        elif op == "|":
            criteria.append(f'{imap_key} "{vals[0]}"')

    return " ".join(criteria)


# ---------------------------------------------------------------------------
# Public read / delete
# ---------------------------------------------------------------------------

def read(document: str, cfg: MailConfig) -> str:
    """Handle a JMD read request for email.

    Args:
        document: JMD document string.
        cfg: Mail configuration.

    Returns:
        JMD response string.
    """
    mode = jmd_mode(document)

    if mode == "schema":
        return (
            "#! Message\n"
            "id: string readonly\n"
            "from: string readonly\n"
            "to: string readonly\n"
            "subject: string\n"
            "date: string readonly\n"
            "body: string optional\n"
            "folder: string optional\n"
            "download: boolean optional\n"
            "path: string optional\n"
        )

    if mode == "data":
        fields = jmd_to_dict(document)
        uid = str(fields.get("id", "")).strip()
        folder = str(fields.get("folder", "INBOX")).strip()
        download = bool(fields.get("download", False))
        path_raw = str(fields.get("path", "")).strip()
        download_path = Path(path_raw) if path_raw else None
        if not uid:
            return _error(400, "missing_fields", "'id' is required")
        return _fetch_one(cfg, folder, uid, download, download_path)

    if mode == "query":
        query = JMDQueryParser().parse(document)
        folder = "INBOX"
        for f in query.fields:
            if f.key == "folder" and f.condition.values:
                folder = f.condition.values[0]
                break
        criteria = _build_search_criteria(query.fields)
        return _fetch_list(cfg, folder, criteria)

    return _error(400, "invalid_mode", f"Unsupported mode for read: {mode!r}")


def _fetch_one(
    cfg: MailConfig,
    folder: str,
    uid: str,
    download: bool,
    download_path: Path | None,
) -> str:
    """Fetch a single message by UID."""
    try:
        with _connect(cfg) as conn:
            conn.select(f'"{folder}"', readonly=True)
            status, data = conn.uid("FETCH", uid, "(RFC822)")
            if status != "OK" or not data or data[0] is None:
                return _error(
                    404, "not_found",
                    f"Message {uid} not found in {folder}",
                )
            raw = data[0][1]
            if not isinstance(raw, bytes):
                return _error(
                    404, "not_found",
                    f"Message {uid} not found in {folder}",
                )
            record = _parse_message(
                uid, raw, folder, download, download_path
            )
            return serialize(record, label=_LABEL)
    except imaplib.IMAP4.error as e:
        return _error(500, "imap_error", str(e))
    except OSError as e:
        return _error(500, "connection_error", str(e))


def _fetch_list(
    cfg: MailConfig,
    folder: str,
    criteria: str,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> str:
    """Search and fetch message headers."""
    try:
        with _connect(cfg) as conn:
            conn.select(f'"{folder}"', readonly=True)
            status, data = conn.uid("SEARCH", "UTF-8", criteria)
            if status != "OK":
                return _error(500, "imap_error", "SEARCH failed")

            uid_list = data[0].split() if data[0] else []
            uid_list = list(reversed(uid_list))[:page_size]

            if not uid_list:
                return serialize([], label=_LABEL)

            uid_str = b",".join(uid_list).decode()
            status, data = conn.uid(
                "FETCH", uid_str, "(BODY.PEEK[HEADER])"
            )
            if status != "OK":
                return _error(500, "imap_error", "FETCH failed")

            records = []
            for item in data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                uid_match = re.search(rb"UID (\d+)", item[0])
                uid = uid_match.group(1).decode() if uid_match else "?"
                records.append(_parse_envelope(uid, item[1], folder))

            return serialize(records, label=_LABEL)
    except imaplib.IMAP4.error as e:
        return _error(500, "imap_error", str(e))
    except OSError as e:
        return _error(500, "connection_error", str(e))


def delete(document: str, cfg: MailConfig) -> str:
    r"""Handle a JMD delete request for email.

    Marks the message as \Deleted and expunges it.

    Args:
        document: JMD delete document string.
        cfg: Mail configuration.

    Returns:
        JMD confirmation or error document.
    """
    mode = jmd_mode(document)
    if mode != "delete":
        return _error(
            400, "invalid_mode", "delete requires a #- Message document"
        )

    parsed = JMDDeleteParser().parse(document)
    ids = parsed.identifiers
    uid = str(ids.get("id", "")).strip()
    folder = str(ids.get("folder", "INBOX")).strip()

    if not uid:
        return _error(400, "missing_fields", "'id' is required")

    try:
        with _connect(cfg) as conn:
            conn.select(f'"{folder}"')
            status, _ = conn.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
            if status != "OK":
                return _error(
                    404, "not_found",
                    f"Message {uid} not found in {folder}",
                )
            conn.expunge()
            return serialize(
                {"deleted": 1, "id": uid, "folder": folder},
                label=_LABEL,
            )
    except imaplib.IMAP4.error as e:
        return _error(500, "imap_error", str(e))
    except OSError as e:
        return _error(500, "connection_error", str(e))


def _error(status: int, code: str, message: str) -> str:
    """Serialise a JMD error document."""
    return serialize(
        {"status": status, "code": code, "message": message},
        label="Error",
    )
