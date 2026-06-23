# SPDX-License-Identifier: Apache-2.0
"""Unit tests for _masking.py and its wiring into the read path."""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from mail_mcp import _masking, _pseudonym
from mail_mcp._pseudonym import Pseudonymizer
from mail_mcp.imap._parse import message_to_dict, parse_message
from mail_mcp.imap.read import _mask_enabled


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the pseudonym secret for the integration tests."""
    monkeypatch.setenv("JMD_MCP_MAIL_PSEUDONYM_SECRET", "test-secret-fixed")
    _pseudonym._reset_for_tests()
    yield
    _pseudonym._reset_for_tests()


# ---------------------------------------------------------------------------
# Individual masks
# ---------------------------------------------------------------------------


def test_mask_ipv4() -> None:
    """IPv4 addresses are masked."""
    assert _masking.mask("host 10.20.30.40 down") == "host [ip] down"


def test_mask_ipv6() -> None:
    """Full-form IPv6 addresses are masked."""
    out = _masking.mask("addr 2001:db8:85a3:0:0:8a2e:370:7334 ok")
    assert "[ip]" in out
    assert "2001" not in out


def test_mask_phone() -> None:
    """Phone numbers (>= ~7 digits with separators) are masked."""
    out = _masking.mask("Ruf an: +49 89 459926-90 bitte")
    assert "[telefon]" in out
    assert "459926" not in out


def test_mask_fqdn() -> None:
    """Internal and public FQDNs are masked."""
    assert "[server]" in _masking.mask("auf sapprd01.firma.intern läuft es")
    assert "[server]" in _masking.mask("siehe sap-erp.firma.com dazu")


def test_mask_host_port() -> None:
    """A bare host:port (no dot) is masked by the host:port rule."""
    out = _masking.mask("verbinde sapprd01:3200 jetzt")
    assert "sapprd01" not in out
    assert "3200" not in out
    assert "[port]" in out


def test_order_ip_not_eaten_by_phone() -> None:
    """IPs are masked as [ip], never swallowed by the phone rule."""
    out = _masking.mask("10.20.30.40")
    assert out == "[ip]"
    assert "[telefon]" not in out


def test_token_like_text_untouched() -> None:
    """A pseudonym token has no dots/colons/long digit runs → untouched."""
    assert _masking.mask("<a1b2c3>") == "<a1b2c3>"


def test_empty_passthrough() -> None:
    """Empty text is returned unchanged."""
    assert _masking.mask("") == ""


# ---------------------------------------------------------------------------
# Integration with message_to_dict
# ---------------------------------------------------------------------------


def _raw() -> bytes:
    return (
        b"From: Alice <alice@acme.com>\r\n"
        b"To: bob@acme.com\r\n"
        b"Subject: Status\r\n"
        b"\r\n"
        b"Server sapprd01.firma.intern, Tel +49 89 459926-90, "
        b"mail alice@acme.com\r\n"
    )


def test_message_masked_with_pseudonymizer() -> None:
    """mask_content removes servers/phones; emails become tokens."""
    rec = parse_message("1", _raw(), "INBOX")
    d = message_to_dict(rec, Pseudonymizer(), mask_content=True)
    body = str(d["body"])
    assert "sapprd01" not in body
    assert "[server]" in body
    assert "459926" not in body
    assert "[telefon]" in body
    assert "alice@acme.com" not in body


def test_message_mask_off_keeps_content() -> None:
    """Without mask_content, content stays (emails still pseudonymised)."""
    rec = parse_message("1", _raw(), "INBOX")
    d = message_to_dict(rec, Pseudonymizer(), mask_content=False)
    body = str(d["body"])
    assert "sapprd01.firma.intern" in body
    assert "alice@acme.com" not in body  # identity layer still applies


# ---------------------------------------------------------------------------
# Frontmatter opt-out
# ---------------------------------------------------------------------------


def test_mask_enabled_default_on() -> None:
    """Masking is on when no frontmatter flag is present."""
    assert _mask_enabled("# Message\nid: 1\nfolder: INBOX") is True


def test_mask_enabled_opt_out() -> None:
    """`mask-content: false` disables masking for the call."""
    doc = "mask-content: false\n\n# Message\nid: 1\nfolder: INBOX"
    assert _mask_enabled(doc) is False


def test_mask_enabled_explicit_true() -> None:
    """`mask-content: true` keeps masking on."""
    doc = "mask-content: true\n\n#? Message\nfolder: INBOX"
    assert _mask_enabled(doc) is True
