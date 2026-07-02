# SPDX-License-Identifier: Apache-2.0
"""Unit tests for _compose.py — shared MIME composer."""
from __future__ import annotations

import email
from email import policy as email_policy
from email.message import EmailMessage
from pathlib import Path

import pytest

from mail_mcp._compose import ComposeError, ComposeResult, compose

_FROM = "sender@example.com"


def _compose(
    fields: dict[str, object], **kwargs: object,
) -> ComposeResult:
    """Call compose() with send-like defaults, overridable per test."""
    defaults: dict[str, object] = {
        "from_addr": _FROM,
        "footer": True,
        "bcc_in_header": False,
        "require_recipients": True,
    }
    defaults.update(kwargs)
    return compose(fields, **defaults)  # type: ignore[arg-type]


def _parse(result: ComposeResult) -> EmailMessage:
    """Parse composed bytes back into an EmailMessage."""
    msg = email.message_from_bytes(
        result.raw_bytes, policy=email_policy.default,
    )
    assert isinstance(msg, EmailMessage)
    return msg


_FULL: dict[str, object] = {
    "to": "alice@example.com",
    "subject": "Hello",
    "body": "Some **text**",
}


def test_footer_appended_when_enabled() -> None:
    """footer=True appends the AI-attribution footer to the body."""
    result = _compose(_FULL, footer=True)
    plain = _parse(result).get_body(("plain",))
    assert plain is not None
    assert "AI assistant" in plain.get_content()


def test_no_footer_for_drafts() -> None:
    """footer=False leaves the body untouched (user authorship)."""
    result = _compose(_FULL, footer=False)
    plain = _parse(result).get_body(("plain",))
    assert plain is not None
    assert "AI assistant" not in plain.get_content()


def test_bcc_header_emitted_for_drafts() -> None:
    """bcc_in_header=True makes Bcc a visible header."""
    fields = dict(_FULL, bcc="bob@example.com")
    result = _compose(fields, bcc_in_header=True)
    assert _parse(result)["Bcc"] == "bob@example.com"


def test_bcc_envelope_only_for_send() -> None:
    """bcc_in_header=False keeps Bcc out of the headers."""
    fields = dict(_FULL, bcc="bob@example.com")
    result = _compose(fields, bcc_in_header=False)
    assert _parse(result)["Bcc"] is None
    assert result.bcc_addrs == ["bob@example.com"]


def test_message_id_always_present() -> None:
    """Every composed message carries a Message-ID from the sender."""
    result = _compose(_FULL)
    assert result.message_id.startswith("<")
    assert "@example.com>" in result.message_id
    assert _parse(result)["Message-ID"] == result.message_id


def test_date_header_present() -> None:
    """Every composed message carries a Date header."""
    assert _parse(_compose(_FULL))["Date"] is not None


def test_from_name_field_overrides_default() -> None:
    """A per-call from-name field beats the passed default."""
    fields = dict(_FULL, **{"from-name": "Alice A."})
    result = _compose(fields, from_name="Default D.")
    assert "Alice A." in str(_parse(result)["From"])


def test_missing_attachment_raises() -> None:
    """A non-existent attachment path is an error, not a silent skip."""
    fields = dict(
        _FULL, attachments=[{"path": "Z:/nope/missing.pdf"}],
    )
    with pytest.raises(ComposeError) as exc_info:
        _compose(fields)
    assert exc_info.value.code == "attachment_not_found"


def test_attachment_mime_type_guessed(tmp_path: Path) -> None:
    """Known extensions get a real content type."""
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    fields = dict(_FULL, attachments=[{"path": str(pdf)}])
    msg = _parse(_compose(fields))
    types = [p.get_content_type() for p in msg.iter_attachments()]
    assert types == ["application/pdf"]


def test_attachment_unknown_type_falls_back(tmp_path: Path) -> None:
    """Unknown extensions fall back to application/octet-stream."""
    blob = tmp_path / "data.xyzzy"
    blob.write_bytes(b"\x00\x01")
    fields = dict(_FULL, attachments=[{"path": str(blob)}])
    msg = _parse(_compose(fields))
    types = [p.get_content_type() for p in msg.iter_attachments()]
    assert types == ["application/octet-stream"]


def test_require_recipients_enforces_all_fields() -> None:
    """Send mode requires to, subject and body."""
    for missing in ("to", "subject", "body"):
        fields = {k: v for k, v in _FULL.items() if k != missing}
        with pytest.raises(ComposeError) as exc_info:
            _compose(fields)
        assert exc_info.value.code == "missing_fields"


def test_partial_draft_with_subject_only() -> None:
    """Draft mode accepts a subject-only message."""
    result = _compose(
        {"subject": "just a note"},
        require_recipients=False, footer=False,
    )
    msg = _parse(result)
    assert msg["Subject"] == "just a note"
    assert msg["To"] is None


def test_empty_draft_rejected() -> None:
    """Draft mode still rejects a message with no content at all."""
    with pytest.raises(ComposeError) as exc_info:
        _compose({}, require_recipients=False, footer=False)
    assert exc_info.value.code == "missing_fields"


def test_crlf_line_endings() -> None:
    """Serialized bytes use CRLF (SMTP + IMAP APPEND requirement)."""
    raw = _compose(_FULL).raw_bytes
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_extra_headers_verbatim() -> None:
    """extra_headers land in the message untouched."""
    result = _compose(
        _FULL,
        extra_headers={
            "In-Reply-To": "<orig@example.com>",
            "References": "<root@example.com> <orig@example.com>",
        },
    )
    msg = _parse(result)
    assert msg["In-Reply-To"] == "<orig@example.com>"
    assert (
        msg["References"] == "<root@example.com> <orig@example.com>"
    )


def test_empty_body_draft_composes() -> None:
    """A draft with recipients but no body still composes."""
    result = _compose(
        {"to": "alice@example.com"},
        require_recipients=False, footer=False,
    )
    assert _parse(result)["To"] == "alice@example.com"


def test_html_alternative_present() -> None:
    """Markdown body yields a text/html alternative part."""
    result = _compose(dict(_FULL, body="Some **bold** text"))
    html = _parse(result).get_body(("html",))
    assert html is not None
    assert "<strong>bold</strong>" in html.get_content()
