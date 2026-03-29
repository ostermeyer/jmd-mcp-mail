"""Unit tests for imap/_criteria.py — IMAP SEARCH string builder."""
from __future__ import annotations

from jmd import JMDQueryParser

from mail_mcp.imap._criteria import build


def _build(query_doc: str) -> str:
    q = JMDQueryParser().parse(query_doc)
    return build(q.fields)


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
