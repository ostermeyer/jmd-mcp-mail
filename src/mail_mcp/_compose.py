# SPDX-License-Identifier: Apache-2.0
"""Shared MIME composer for jmd-mcp-mail.

Builds RFC 5322 messages from JMD ``# Message`` fields.  Used by the
SMTP ``send`` path and the IMAP draft path (``write`` without ``id``),
which differ only in knobs: drafts carry no AI footer (the user takes
authorship by sending manually), show ``Bcc`` as a real header, and
may be partial (``require_recipients=False``).

Bodies are Markdown and become ``multipart/alternative`` (plain text +
HTML).  Serialization uses ``policy.SMTP`` (CRLF line endings), which
satisfies both ``smtplib.sendmail`` and RFC 3501 ``APPEND`` literals.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from email import policy as email_policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

import markdown as md  # type: ignore[import-untyped]

_REPO_URL = "https://github.com/ostermeyer/jmd-mcp-mail"
_FOOTER = (
    "\n\n---\n"
    "*This email was sent by an AI assistant using "
    f"[jmd-mcp-mail]({_REPO_URL}).*"
)


class ComposeError(Exception):
    """Composition failure carrying ``(status, code, message)``.

    Attributes:
        status: HTTP-ish status for the JMD ``# Error`` document.
        code: Machine-readable error code.
        message: Human-readable message.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        """Store the error triple and init the base Exception."""
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(slots=True)
class ComposeResult:
    """A composed message plus envelope-relevant metadata."""

    raw_bytes: bytes
    message_id: str
    to_addrs: list[str]
    cc_addrs: list[str]
    bcc_addrs: list[str]
    subject: str


def _split_addrs(raw: object) -> list[str]:
    """Split a comma-separated address field into a clean list."""
    return [a.strip() for a in str(raw or "").split(",") if a.strip()]


def _collect_attachment_paths(fields: dict[str, object]) -> list[Path]:
    """Extract attachment paths from the ``attachments[]`` array."""
    paths: list[Path] = []
    raw = fields.get("attachments", [])
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                p = str(item.get("path", "")).strip()
                if p:
                    paths.append(Path(p))
    return paths


def _guess_mime(path: Path) -> tuple[str, str]:
    """Return ``(maintype, subtype)`` for an attachment file."""
    ctype, cenc = mimetypes.guess_type(path.name)
    if ctype is None or cenc is not None:
        # Encoded files (e.g. .gz) are opaque bytes to mail clients.
        ctype = "application/octet-stream"
    maintype, _, subtype = ctype.partition("/")
    return maintype, subtype


def compose(
    fields: dict[str, object],
    *,
    from_addr: str,
    from_name: str = "",
    footer: bool,
    bcc_in_header: bool,
    require_recipients: bool,
    extra_headers: dict[str, str] | None = None,
) -> ComposeResult:
    """Build a MIME message from JMD ``# Message`` fields.

    Args:
        fields: Parsed JMD fields (``to``, ``subject``, ``body``,
            ``cc``, ``bcc``, ``from-name``, ``attachments``).
        from_addr: Sender address (the account username).
        from_name: Default display name for the From header; a
            per-call ``from-name`` field overrides it.
        footer: Append the AI-attribution footer to the body.
        bcc_in_header: Emit ``Bcc`` as a real header (drafts) instead
            of keeping it envelope-only (send).
        require_recipients: ``True`` demands ``to``/``subject``/
            ``body`` (send); ``False`` accepts a partial message with
            at least one of them (drafts).
        extra_headers: Additional headers set verbatim (e.g.
            ``In-Reply-To``/``References``).

    Returns:
        The composed message and its metadata.

    Raises:
        ComposeError: On validation failure or a missing attachment.
    """
    to_addrs = _split_addrs(fields.get("to"))
    cc_addrs = _split_addrs(fields.get("cc"))
    bcc_addrs = _split_addrs(fields.get("bcc"))
    subject = str(fields.get("subject", "")).strip()
    body = str(fields.get("body", "")).strip()
    name = str(fields.get("from-name", "")).strip() or from_name

    if require_recipients:
        if not to_addrs:
            raise ComposeError(400, "missing_fields", "'to' is required")
        if not subject:
            raise ComposeError(
                400, "missing_fields", "'subject' is required",
            )
        if not body:
            raise ComposeError(400, "missing_fields", "'body' is required")
    elif not (to_addrs or subject or body):
        raise ComposeError(
            400, "missing_fields",
            "a draft needs at least one of to/subject/body",
        )

    attach_paths = _collect_attachment_paths(fields)
    for path in attach_paths:
        if not path.is_file():
            raise ComposeError(
                400, "attachment_not_found",
                f"attachment {str(path)!r} does not exist",
            )

    msg = EmailMessage()
    # Optional display name on the From header (e.g. "Andreas
    # Ostermeyer <a@b.de>").  The envelope sender stays the bare
    # address — only the header carries the name.
    msg["From"] = formataddr((name, from_addr)) if name else from_addr
    if to_addrs:
        msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    if bcc_in_header and bcc_addrs:
        msg["Bcc"] = ", ".join(bcc_addrs)
    if subject:
        msg["Subject"] = subject
    msg["Date"] = formatdate(usegmt=True)
    domain = from_addr.rsplit("@", 1)[-1] if "@" in from_addr else None
    message_id = make_msgid(domain=domain)
    msg["Message-ID"] = message_id
    for key, value in (extra_headers or {}).items():
        msg[key] = value

    text = body + (_FOOTER if footer else "")
    # cte="quoted-printable" forces a 7-bit-safe transfer encoding.
    # The default would be "8bit" (raw UTF-8 bytes), which is only
    # safe when BODY=8BITMIME is negotiated — sendmail() does not do
    # that, so 8-bit parts get mangled in transit (non-ASCII →
    # mojibake).
    if text:
        # Extensions chosen for email rendering:
        #   extra      — fenced code, tables, etc.
        #   sane_lists — predictable list handling (respects start
        #                numbers, doesn't promote under-indented items
        #                to phantom <li>)
        #   nl2br      — preserve single line breaks; mail bodies are
        #                written with hard newlines, not Markdown
        #                soft-wrap convention
        html = md.markdown(
            text, extensions=["extra", "sane_lists", "nl2br"],
        )
        msg.set_content(text, cte="quoted-printable")
        msg.add_alternative(
            html, subtype="html", cte="quoted-printable",
        )
    else:
        msg.set_content("", cte="quoted-printable")

    for path in attach_paths:
        maintype, subtype = _guess_mime(path)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    # policy.SMTP serializes with CRLF line endings.  sendmail() sends
    # bytes as-is (no eol fixup), and a bare-LF message gets
    # eol-normalized by the receiving MTA — which corrupts the QP
    # soft-breaks (=\n) at the 76-char boundary.  RFC 3501 APPEND
    # literals require CRLF as well.
    raw = msg.as_bytes(policy=email_policy.SMTP)
    return ComposeResult(
        raw_bytes=raw,
        message_id=message_id,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        bcc_addrs=bcc_addrs,
        subject=subject,
    )
