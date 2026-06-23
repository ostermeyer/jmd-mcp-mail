# SPDX-License-Identifier: Apache-2.0
"""Unit tests for _pseudonym.py — no keystore or IMAP connection needed.

The HMAC secret is injected via ``$JMD_MCP_MAIL_PSEUDONYM_SECRET`` so the
tests never touch the real OS keystore.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from mail_mcp import _pseudonym
from mail_mcp._pseudonym import (
    Pseudonymizer,
    _given_name,
    resolve_recipient,
    resolve_search,
)
from mail_mcp.imap._parse import (
    EmailAddressRecord,
    message_to_dict,
    parse_message,
)


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin a deterministic secret and clear caches around each test."""
    monkeypatch.setenv("JMD_MCP_MAIL_PSEUDONYM_SECRET", "test-secret-fixed")
    _pseudonym._reset_for_tests()
    yield
    _pseudonym._reset_for_tests()


# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------


def test_token_is_deterministic_and_opaque() -> None:
    """Same address yields the same opaque (no-@) token every time."""
    p = Pseudonymizer()
    a = p.address(EmailAddressRecord(name="Alice", email="alice@acme.com"))
    b = p.address(EmailAddressRecord(name="Alice", email="alice@acme.com"))
    assert a.email == b.email
    assert "@" not in a.email
    assert a.email != "alice@acme.com"


def test_case_and_space_normalised() -> None:
    """Case/whitespace differences map to the same token."""
    p = Pseudonymizer()
    t1 = p.address(EmailAddressRecord(name="", email="Alice@Acme.com")).email
    t2 = p.address(EmailAddressRecord(name="", email=" alice@acme.com ")).email
    assert t1 == t2


def test_distinct_addresses_get_distinct_tokens() -> None:
    """Different addresses produce different tokens."""
    p = Pseudonymizer()
    t1 = p.address(EmailAddressRecord(name="", email="a@x.com")).email
    t2 = p.address(EmailAddressRecord(name="", email="b@x.com")).email
    assert t1 != t2


# ---------------------------------------------------------------------------
# Given-name extraction
# ---------------------------------------------------------------------------


def test_given_name_first_token() -> None:
    """A clean display name yields the first name only."""
    assert _given_name("Alice Schmidt") == "Alice"


def test_given_name_comma_form_is_dropped() -> None:
    """'Surname, First' is ambiguous → no name."""
    assert _given_name("Schmidt, Alice") is None


def test_given_name_role_address_dropped() -> None:
    """An address-like display name yields no given name."""
    assert _given_name("support@acme.com") is None


def test_given_name_empty() -> None:
    """No display name → no given name."""
    assert _given_name("") is None


def test_address_keeps_given_name_only() -> None:
    """Pseudonymised record carries the first name, never the surname."""
    p = Pseudonymizer()
    rec = p.address(
        EmailAddressRecord(name="Alice Schmidt", email="alice@acme.com")
    )
    assert rec.name == "Alice"


def test_address_without_display_name_is_token_only() -> None:
    """No display name → empty name, bare token."""
    p = Pseudonymizer()
    rec = p.address(EmailAddressRecord(name="", email="bob@acme.com"))
    assert rec.name == ""
    assert "@" not in rec.email


# ---------------------------------------------------------------------------
# Free-text scanning
# ---------------------------------------------------------------------------


def test_text_replaces_addresses() -> None:
    """Addresses embedded in free text are replaced with <token>."""
    p = Pseudonymizer()
    out = p.text("Please write to bob@acme.com about it.")
    assert "bob@acme.com" not in out
    assert "<" in out and ">" in out


def test_text_empty_passthrough() -> None:
    """Empty body is returned unchanged."""
    assert Pseudonymizer().text("") == ""


# ---------------------------------------------------------------------------
# Reverse resolution (inbound)
# ---------------------------------------------------------------------------


def test_resolve_recipient_round_trip() -> None:
    """A token resolves back to the original real address."""
    p = Pseudonymizer()
    token = p.address(
        EmailAddressRecord(name="Alice", email="alice@acme.com")
    ).email
    assert resolve_recipient(token) == "alice@acme.com"


def test_resolve_recipient_angle_form() -> None:
    """The 'Name <token>' form resolves on the bracketed atom."""
    p = Pseudonymizer()
    token = p.address(
        EmailAddressRecord(name="Alice", email="alice@acme.com")
    ).email
    assert resolve_recipient(f"Alice <{token}>") == "alice@acme.com"


def test_resolve_recipient_real_address_passthrough() -> None:
    """A real address (new recipient) passes through unchanged."""
    assert resolve_recipient("new@guy.com") == "new@guy.com"


def test_resolve_recipient_unknown_token_is_none() -> None:
    """An unknown bare token is rejected (None)."""
    assert resolve_recipient("zzzzzzzz") is None


def test_resolve_search_known_and_unknown() -> None:
    """Search resolution maps known tokens, passes other text through."""
    p = Pseudonymizer()
    token = p.address(EmailAddressRecord(name="", email="alice@acme.com")).email
    assert resolve_search(token) == "alice@acme.com"
    assert resolve_search("Müller") == "Müller"


# ---------------------------------------------------------------------------
# Domain disambiguation (opt-in)
# ---------------------------------------------------------------------------


def test_domain_mode_shares_domain_token() -> None:
    """Same-domain addresses share the domain part; locals differ."""
    p = Pseudonymizer(domain=True)
    t1 = p.address(EmailAddressRecord(name="", email="a@acme.com")).email
    t2 = p.address(EmailAddressRecord(name="", email="b@acme.com")).email
    assert "@" in t1 and "@" in t2
    assert t1.split("@")[1] == t2.split("@")[1]
    assert t1.split("@")[0] != t2.split("@")[0]


def test_domain_mode_round_trip() -> None:
    """Domain-form tokens still resolve back to the real address."""
    p = Pseudonymizer(domain=True)
    token = p.address(EmailAddressRecord(name="", email="a@acme.com")).email
    assert resolve_recipient(token) == "a@acme.com"


# ---------------------------------------------------------------------------
# message_to_dict integration
# ---------------------------------------------------------------------------


def _raw_message() -> bytes:
    return (
        b"From: Alice Schmidt <alice@acme.com>\r\n"
        b"To: bob@acme.com\r\n"
        b"Subject: Hi\r\n"
        b"\r\n"
        b"Reach me at alice@acme.com.\r\n"
    )


def test_message_to_dict_without_pseudonymizer_is_unchanged() -> None:
    """Default path keeps the real addresses (non-DSGVO behaviour)."""
    rec = parse_message("1", _raw_message(), "INBOX")
    d = message_to_dict(rec)
    assert d["from"] == {"name": "Alice Schmidt", "email": "alice@acme.com"}
    assert "alice@acme.com" in str(d["body"])


def test_message_to_dict_pseudonymised() -> None:
    """With a pseudonymizer, no real address survives anywhere."""
    rec = parse_message("1", _raw_message(), "INBOX")
    d = message_to_dict(rec, Pseudonymizer())
    blob = str(d)
    assert "alice@acme.com" not in blob
    assert "bob@acme.com" not in blob
    from_ = d["from"]
    assert isinstance(from_, dict)
    assert from_["name"] == "Alice"
    assert "@" not in str(from_["email"])
    # The body address is pseudonymised too.
    assert "alice@acme.com" not in str(d["body"])
