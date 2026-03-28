"""IMAP reader for jmd-mcp-mail.

Reads, searches, and deletes email messages via IMAP4_SSL (stdlib).
Supports JMD data, query, schema, and delete document modes.
"""
from __future__ import annotations

import email
import html.parser
import imaplib
import re
from contextlib import contextmanager
from email.header import decode_header
from typing import Generator

from jmd import JMDDeleteParser, JMDQueryParser, jmd_mode, jmd_to_dict, serialize

from .config import MailConfig

_LABEL = "Message"

# Maximum messages returned per query (guards against huge inboxes).
_DEFAULT_PAGE_SIZE = 25


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
# Header decoding helpers
# ---------------------------------------------------------------------------

class _HTMLTextExtractor(html.parser.HTMLParser):
    """Minimal HTML-to-text extractor using only stdlib."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip = True
        if tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False
        if tag in ("p", "div", "tr", "li"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        """Return extracted plain text, collapsing whitespace."""
        raw = "".join(self._parts)
        # Collapse multiple blank lines
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _html_to_text(html_bytes: bytes, charset: str | None) -> str:
    """Convert HTML bytes to plain text."""
    html_str = html_bytes.decode(charset or "utf-8", errors="replace")
    extractor = _HTMLTextExtractor()
    extractor.feed(html_str)
    return extractor.text()


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


def _parse_message(uid: str, raw_bytes: bytes) -> dict:
    """Parse a raw RFC 2822 message into a JMD-friendly dict."""
    msg = email.message_from_bytes(raw_bytes)
    body = ""
    html_fallback: bytes | None = None
    html_charset: str | None = None

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            if ct == "text/plain":
                body = payload.decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
                break
            if ct == "text/html" and html_fallback is None:
                html_fallback = payload
                html_charset = part.get_content_charset()
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            if msg.get_content_type() == "text/html":
                html_fallback = payload
                html_charset = msg.get_content_charset()
            else:
                body = payload.decode(
                    msg.get_content_charset() or "utf-8", errors="replace"
                )

    if not body and html_fallback is not None:
        body = _html_to_text(html_fallback, html_charset)

    return {
        "id": uid,
        "from": _decode_header(msg.get("From")),
        "to": _decode_header(msg.get("To")),
        "subject": _decode_header(msg.get("Subject")),
        "date": _decode_header(msg.get("Date")),
        "body": body.strip()[:4000],
    }


def _parse_envelope(uid: str, raw_bytes: bytes) -> dict:
    """Parse only headers (fast, for listing)."""
    msg = email.message_from_bytes(raw_bytes)
    return {
        "id": uid,
        "from": _decode_header(msg.get("From")),
        "to": _decode_header(msg.get("To")),
        "subject": _decode_header(msg.get("Subject")),
        "date": _decode_header(msg.get("Date")),
    }


# ---------------------------------------------------------------------------
# IMAP SEARCH criteria builder
# ---------------------------------------------------------------------------

def _build_search_criteria(fields: list) -> str:
    """Translate JMD QueryFields into an IMAP SEARCH string.

    Supports: from, to, subject (substring via ~), folder is handled
    separately. Unknown fields are ignored.
    """
    criteria: list[str] = ["ALL"]
    imap_keys = {"from": "FROM", "to": "TO", "subject": "SUBJECT"}

    for f in fields:
        if f.key == "folder":
            continue  # handled at SELECT level
        imap_key = imap_keys.get(f.key)
        if not imap_key:
            continue
        op = f.condition.op
        vals = f.condition.values
        if op in ("regex", "~"):
            criteria.append(f'{imap_key} "{vals[0]}"')
        elif op == "|":
            # IMAP has no OR-list; use first value only
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
        )

    if mode == "data":
        fields = jmd_to_dict(document)
        uid = str(fields.get("id", "")).strip()
        folder = str(fields.get("folder", "INBOX")).strip()
        if not uid:
            return _error(400, "missing_fields", "'id' is required")
        return _fetch_one(cfg, folder, uid)

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


def _fetch_one(cfg: MailConfig, folder: str, uid: str) -> str:
    """Fetch a single message by UID."""
    try:
        with _connect(cfg) as conn:
            conn.select(f'"{folder}"', readonly=True)
            status, data = conn.uid("FETCH", uid, "(RFC822)")
            if status != "OK" or not data or data[0] is None:
                return _error(404, "not_found", f"Message {uid} not found in {folder}")
            raw = data[0][1]
            if not isinstance(raw, bytes):
                return _error(404, "not_found", f"Message {uid} not found in {folder}")
            record = _parse_message(uid, raw)
            record["folder"] = folder
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
            status, data = conn.uid("SEARCH", None, criteria)
            if status != "OK":
                return _error(500, "imap_error", "SEARCH failed")

            uid_list = data[0].split() if data[0] else []
            # Most recent first
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
                # Extract UID from response line
                uid_match = re.search(rb"UID (\d+)", item[0])
                uid = uid_match.group(1).decode() if uid_match else "?"
                record = _parse_envelope(uid, item[1])
                record["folder"] = folder
                records.append(record)

            return serialize(records, label=_LABEL)
    except imaplib.IMAP4.error as e:
        return _error(500, "imap_error", str(e))
    except OSError as e:
        return _error(500, "connection_error", str(e))


def delete(document: str, cfg: MailConfig) -> str:
    """Handle a JMD delete request for email.

    Marks the message as \\Deleted and expunges it.

    Args:
        document: JMD delete document string.
        cfg: Mail configuration.

    Returns:
        JMD confirmation or error document.
    """
    mode = jmd_mode(document)
    if mode != "delete":
        return _error(400, "invalid_mode", "delete requires a #- Message document")

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
                return _error(404, "not_found", f"Message {uid} not found in {folder}")
            conn.expunge()
            return serialize({"deleted": 1, "id": uid, "folder": folder}, label=_LABEL)
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
