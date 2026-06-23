# SPDX-License-Identifier: Apache-2.0
"""Unit tests for _contacts.py — vCard/CSV import into the in-memory map.

The HMAC secret is pinned via the env override so no OS keystore is touched.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from mail_mcp import _contacts, _pseudonym
from mail_mcp._pseudonym import resolve_recipient


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the secret and clear both module states around each test."""
    monkeypatch.setenv("JMD_MCP_MAIL_PSEUDONYM_SECRET", "test-secret-fixed")
    monkeypatch.delenv("JMD_MCP_MAIL_CONTACT_SOURCES", raising=False)
    _pseudonym._reset_for_tests()
    _contacts._reset_for_tests()
    yield
    _pseudonym._reset_for_tests()
    _contacts._reset_for_tests()


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

_CSV_GOOGLE = """\
Given Name,Family Name,E-mail 1 - Value
Carla,Klein,carla@firma.de
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# vCard
# ---------------------------------------------------------------------------


def test_vcard_single_entry_and_resolution(tmp_path: Path) -> None:
    """A vCard entry yields 'Given <token>' and the token resolves back."""
    _contacts.set_cli_sources([_write(tmp_path, "c.vcf", _VCARD_ONE)])
    entries = _contacts.load()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.label.startswith("Rebecca <")
    assert entry.label.endswith(">")
    assert "@" not in entry.label  # surname & address stay out
    assert resolve_recipient(entry.label) == "rebecca@firma.de"
    assert resolve_recipient(entry.token) == "rebecca@firma.de"


def test_vcard_surname_disambiguation(tmp_path: Path) -> None:
    """Colliding first names get the shortest unambiguous surname prefix."""
    _contacts.set_cli_sources([_write(tmp_path, "c.vcf", _VCARD_COLLISION)])
    labels = {e.label for e in _contacts.load()}
    assert any("Rebecca Schm." in lbl for lbl in labels)
    assert any("Rebecca Schn." in lbl for lbl in labels)


def test_vcard_multiple_addresses_typed(tmp_path: Path) -> None:
    """Each address becomes a discrete entry, distinguished by type label."""
    _contacts.set_cli_sources([_write(tmp_path, "c.vcf", _VCARD_MULTI)])
    entries = _contacts.load()
    labels = {e.label for e in entries}
    assert len(entries) == 2
    assert any("(geschäftlich)" in lbl for lbl in labels)
    assert any("(privat)" in lbl for lbl in labels)
    assert resolve_recipient(_pick(entries, "geschäftlich")) == "arne@firma.de"
    assert resolve_recipient(_pick(entries, "privat")) == "arne@privat.de"


def _pick(entries: list[_contacts.ContactEntry], needle: str) -> str:
    """Return the label of the entry whose label contains *needle*."""
    return next(e.label for e in entries if needle in e.label)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def test_csv_google_headers(tmp_path: Path) -> None:
    """Google-style CSV headers parse and resolve."""
    _contacts.set_cli_sources([_write(tmp_path, "c.csv", _CSV_GOOGLE)])
    entries = _contacts.load()
    assert len(entries) == 1
    assert entries[0].label.startswith("Carla <")
    assert resolve_recipient(entries[0].label) == "carla@firma.de"


# ---------------------------------------------------------------------------
# Sources & robustness
# ---------------------------------------------------------------------------


def test_env_var_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env variable is honoured as an alternative source."""
    path = _write(tmp_path, "c.vcf", _VCARD_ONE)
    monkeypatch.setenv("JMD_MCP_MAIL_CONTACT_SOURCES", str(path))
    entries = _contacts.load()
    assert len(entries) == 1
    assert resolve_recipient(entries[0].token) == "rebecca@firma.de"


def test_missing_source_is_non_fatal(tmp_path: Path) -> None:
    """A non-existent source path is skipped, not raised."""
    _contacts.set_cli_sources([tmp_path / "does-not-exist.vcf"])
    assert _contacts.load() == []


def test_listing_is_pii_free(tmp_path: Path) -> None:
    """Neither labels nor tokens ever contain a real address."""
    _contacts.set_cli_sources([_write(tmp_path, "c.vcf", _VCARD_MULTI)])
    for entry in _contacts.load():
        assert "@" not in entry.label
        assert "@" not in entry.token
