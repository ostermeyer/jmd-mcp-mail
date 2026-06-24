# SPDX-License-Identifier: Apache-2.0
"""Unit tests for _contacts.py — vCard auto-discovery from the config dir."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mail_mcp import _config, _contacts, _pseudonym
from mail_mcp._pseudonym import resolve_recipient

_VCARD_ONE = """\
BEGIN:VCARD
VERSION:3.0
N:Schmidt;Rebecca;;;
FN:Rebecca Schmidt
EMAIL;TYPE=WORK:rebecca@firma.de
END:VCARD
"""

_VCARD_COLLISION = """\
BEGIN:VCARD
VERSION:3.0
N:Schmidt;Rebecca;;;
FN:Rebecca Schmidt
EMAIL:rebecca@schmidt.de
END:VCARD
BEGIN:VCARD
VERSION:3.0
N:Schneider;Rebecca;;;
FN:Rebecca Schneider
EMAIL:rebecca@schneider.de
END:VCARD
"""

_VCARD_MULTI = """\
BEGIN:VCARD
VERSION:3.0
N:Mueller;Arne;;;
FN:Arne Mueller
EMAIL;TYPE=WORK:arne@firma.de
EMAIL;TYPE=HOME:arne@privat.de
END:VCARD
"""


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the secret and clear module state (config dir is isolated)."""
    monkeypatch.setenv("JMD_MCP_MAIL_PSEUDONYM_SECRET", "test-secret-fixed")
    _pseudonym._reset_for_tests()
    _contacts._reset_for_tests()
    yield
    _pseudonym._reset_for_tests()
    _contacts._reset_for_tests()


def _write_vcf(name: str, text: str) -> Path:
    """Write a vCard file into the (isolated) config directory."""
    directory = _config.config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _pick(entries: list[_contacts.ContactEntry], needle: str) -> str:
    """Return the label of the entry whose label contains *needle*."""
    return next(e.label for e in entries if needle in e.label)


def test_empty_when_no_vcf() -> None:
    """No vCard files in the config dir → empty address book."""
    assert _contacts.load() == []


def test_single_entry_and_resolution() -> None:
    """A vCard entry yields 'Given <token>' and the token resolves back."""
    _write_vcf("c.vcf", _VCARD_ONE)
    entries = _contacts.load()
    assert len(entries) == 1
    assert entries[0].label.startswith("Rebecca <")
    assert "@" not in entries[0].label
    assert resolve_recipient(entries[0].label) == "rebecca@firma.de"


def test_surname_disambiguation() -> None:
    """Colliding first names get the shortest unambiguous surname prefix."""
    _write_vcf("c.vcf", _VCARD_COLLISION)
    labels = {e.label for e in _contacts.load()}
    assert any("Rebecca Schm." in lbl for lbl in labels)
    assert any("Rebecca Schn." in lbl for lbl in labels)


def test_multiple_addresses_typed() -> None:
    """Each address is a discrete entry, distinguished by type qualifier."""
    _write_vcf("c.vcf", _VCARD_MULTI)
    entries = _contacts.load()
    labels = {e.label for e in entries}
    assert len(entries) == 2
    assert any("(geschäftlich)" in lbl for lbl in labels)
    assert any("(privat)" in lbl for lbl in labels)
    assert resolve_recipient(_pick(entries, "geschäftlich")) == "arne@firma.de"
    assert resolve_recipient(_pick(entries, "privat")) == "arne@privat.de"


def test_auto_discovers_multiple_files() -> None:
    """All *.vcf in the config dir are imported."""
    _write_vcf("a.vcf", _VCARD_ONE)
    _write_vcf("b.vcf", _VCARD_MULTI)
    entries = _contacts.load()
    assert len(entries) == 3  # 1 + 2


def test_report_lists_files() -> None:
    """The per-file report records each imported file."""
    _write_vcf("work.vcf", _VCARD_ONE)
    _contacts.load()
    rep = _contacts.report()
    assert len(rep) == 1
    assert rep[0].filename == "work.vcf"
    assert rep[0].status == "imported"
    assert rep[0].contacts == 1


def test_listing_is_pii_free() -> None:
    """Neither labels nor tokens ever contain a real address."""
    _write_vcf("c.vcf", _VCARD_MULTI)
    for entry in _contacts.load():
        assert "@" not in entry.label
        assert "@" not in entry.token
