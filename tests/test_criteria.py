# SPDX-License-Identifier: Apache-2.0
"""Unit tests for imap/_criteria.py — IMAP SEARCH string builder."""
from __future__ import annotations

from jmd import JMDQueryParser

from mail_mcp.imap._criteria import build


def _build(query_doc: str) -> str:
    q = JMDQueryParser().parse(query_doc)
    criteria, _ = build(q.fields)
    return criteria


def _needs_utf8(query_doc: str) -> bool:
    q = JMDQueryParser().parse(query_doc)
    _, needs = build(q.fields)
    return needs


def test_no_criteria_returns_all() -> None:
    """Empty query returns ALL."""
    assert _build("#? Message") == "ALL"


def test_folder_only_returns_all() -> None:
    """folder: is routing-only — no SEARCH criterion."""
    assert _build("#? Message\nfolder: INBOX") == "ALL"


def test_from_equals() -> None:
    """from: equality maps to FROM."""
    result = _build("#? Message\nfrom: alice@example.com")
    assert 'FROM "alice@example.com"' in result


def test_subject_tilde() -> None:
    """subject: ~ maps to SUBJECT substring."""
    result = _build("#? Message\nsubject: ~invoice")
    assert 'SUBJECT "invoice"' in result


def test_seen_true() -> None:
    """seen: true maps to SEEN."""
    result = _build("#? Message\nseen: true")
    assert result == "SEEN"


def test_seen_false() -> None:
    """seen: false maps to UNSEEN."""
    result = _build("#? Message\nseen: false")
    assert result == "UNSEEN"


def test_negation() -> None:
    """from: !spam maps to NOT FROM."""
    result = _build("#? Message\nfrom: !spam")
    assert "NOT FROM" in result


def test_multiple_criteria() -> None:
    """Multiple criteria are ANDed (space-separated)."""
    result = _build("#? Message\nfrom: ~alice\nsubject: ~report")
    assert "FROM" in result
    assert "SUBJECT" in result


def test_cc_criterion() -> None:
    """cc: maps to CC."""
    result = _build("#? Message\ncc: ~alice")
    assert 'CC "alice"' in result


def test_since_iso_date() -> None:
    """since: converts ISO to IMAP DD-Mon-YYYY."""
    result = _build("#? Message\nsince: 2026-07-01")
    assert result == "SINCE 01-Jul-2026"


def test_before_and_on() -> None:
    """before:/on: map to BEFORE/ON."""
    result = _build(
        "#? Message\nbefore: 2026-01-31\non: 2025-12-24"
    )
    assert "BEFORE 31-Jan-2026" in result
    assert "ON 24-Dec-2025" in result


def test_invalid_date_raises() -> None:
    """A malformed date is a ValueError (→ bad_request upstream)."""
    import pytest
    with pytest.raises(ValueError, match="ISO"):
        _build("#? Message\nsince: 01.07.2026")


def test_quote_escaping() -> None:
    """Quotes and backslashes in values are escaped."""
    result = _build('#? Message\nsubject: ~say "hi"')
    assert result == 'SUBJECT "say \\"hi\\""'


def test_ascii_needs_no_utf8() -> None:
    """Plain ASCII criteria skip the CHARSET dance."""
    assert _needs_utf8("#? Message\nsubject: ~invoice") is False


def test_nonascii_flags_utf8() -> None:
    """Umlauts in values demand CHARSET UTF-8 and stay intact."""
    doc = "#? Message\nsubject: ~Entwürfe"
    assert _needs_utf8(doc) is True
    assert 'SUBJECT "Entwürfe"' in _build(doc)
