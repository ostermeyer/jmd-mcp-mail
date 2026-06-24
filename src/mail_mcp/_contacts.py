# SPDX-License-Identifier: Apache-2.0
"""DSGVO contact address book — vCard auto-discovery, strictly in-memory.

Seeds the pseudonym reverse-map from vCard exports so the agent can address
people it has never exchanged mail with — e.g. when the mailbox itself is
locked — without their real address ever entering the LLM context.

**Sources:** every ``*.vcf`` file in the config directory (default
``~/.jmd-mcp-mail/``; override via ``JMD_MCP_MAIL_HOME``). Drop a vCard export
there and it is imported at startup and on ``reimport`` — no explicit
configuration. CSV is intentionally unsupported (no canonical schema across
clients); export vCard instead.

Nothing is persisted. Parsing seeds the in-process reverse-map in
:mod:`mail_mcp._pseudonym` (token → real address) and keeps a list of
``(label, token)`` plus a per-file report. Real addresses live only in that
in-memory map and are never returned.

Label form is ``<given-name> <token>``; the token is always present and
carries uniqueness, the name part is a readability aid (given name, plus the
shortest unambiguous family-name prefix on first-name collisions, plus a
``(geschäftlich)`` / ``(privat)`` qualifier when a contact has several
addresses).
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import vobject

from mail_mcp import _config, _pseudonym, _transcript

try:  # optional: PST import needs the libpff binding (PyPI: libpff-python)
    import pypff
except ImportError:  # not installed → .pst files are reported as skipped
    pypff = None

# vCard address-type → human label qualifier.
_TYPE_MAP = {"work": "geschäftlich", "home": "privat"}

# Email-shaped value matcher for harvesting addresses from PST string props.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# MAPI property tags (fixed, language-independent).
_TAG_MESSAGE_CLASS = 0x001A
_TAG_GIVEN_NAME = 0x3A06
_TAG_SURNAME = 0x3A11
_TAG_DISPLAY_NAME = 0x3001

# Process-lifetime state. Never persisted.
_contacts: list[ContactEntry] = []
_report: list[FileReport] = []


@dataclass(frozen=True)
class ContactEntry:
    """One resolvable address-book entry — label + token, never an address."""

    label: str
    token: str


@dataclass(frozen=True)
class FileReport:
    """Per-file import outcome (PII-free)."""

    filename: str
    status: str  # "imported" | "error" | "skipped"
    contacts: int


@dataclass
class _RawEntry:
    """A parsed contact address prior to label assignment."""

    given: str
    family: str
    email: str
    etype: str  # normalised qualifier: "geschäftlich" | "privat" | ""
    source: str  # originating filename (for the transcript)


# ---------------------------------------------------------------------------
# vCard parsing
# ---------------------------------------------------------------------------


def _norm_type(raw: str) -> str:
    """Map a vCard address type to a human qualifier, or ''."""
    return _TYPE_MAP.get(raw.strip().lower(), "")


def _vcard_types(line: object) -> list[str]:
    """Return the TYPE parameter values of a vCard content line."""
    params = getattr(line, "params", {}) or {}
    types: list[str] = []
    for key, vals in params.items():
        if str(key).upper() == "TYPE":
            types.extend(vals)
    return types


def _parse_vcard(text: str, source: str) -> list[_RawEntry]:
    """Parse vCard text into raw entries (one per address)."""
    entries: list[_RawEntry] = []
    for card in vobject.readComponents(text):
        contents = card.contents
        given = family = ""
        n_list = contents.get("n", [])
        if n_list and n_list[0].value is not None:
            nval = n_list[0].value
            given = (getattr(nval, "given", "") or "").strip()
            family = (getattr(nval, "family", "") or "").strip()
        if not given and not family:
            # No structured name — fall back to the first token of FN so
            # the surname stays out of the label.
            fn_list = contents.get("fn", [])
            if fn_list and fn_list[0].value:
                parts = str(fn_list[0].value).strip().split()
                given = parts[0] if parts else ""
        email_lines = [
            e for e in contents.get("email", []) if (e.value or "").strip()
        ]
        multi = len(email_lines) > 1
        for line in email_lines:
            etype = ""
            if multi:
                for t in _vcard_types(line):
                    if _norm_type(t):
                        etype = _norm_type(t)
                        break
            entries.append(
                _RawEntry(given, family, line.value.strip(), etype, source)
            )
    return entries


# ---------------------------------------------------------------------------
# PST parsing (optional, via libpff-python / pypff)
# ---------------------------------------------------------------------------


def _pst_walk(folder: Any) -> Iterator[Any]:
    """Yield every message in *folder* and its sub-folders, recursively."""
    for i in range(folder.number_of_sub_messages):
        yield folder.get_sub_message(i)
    for j in range(folder.number_of_sub_folders):
        yield from _pst_walk(folder.get_sub_folder(j))


def _pst_entries(message: Any) -> list[Any]:
    """Flatten all record-set entries of a message."""
    out: list[Any] = []
    for s in range(message.number_of_record_sets):
        record_set = message.get_record_set(s)
        for i in range(record_set.number_of_entries):
            out.append(record_set.get_entry(i))
    return out


def _entry_string(entries: list[Any], tag: int) -> str:
    """Return the string value of the record entry with *tag*, or ''."""
    for entry in entries:
        if entry.entry_type == tag:
            try:
                return entry.get_data_as_string() or ""
            except Exception:  # noqa: BLE001 — non-string / unreadable entry
                return ""
    return ""


def _parse_pst(path: Path, source: str) -> list[_RawEntry]:
    """Extract IPM.Contact entries from a PST via pypff (value-harvest).

    pypff cannot resolve named properties (Email1/2/3 are mapped to different
    tags in every PST), so email addresses are harvested by value: any string
    property whose value is an email address, de-duplicated per contact. The
    name comes from the fixed, language-independent MAPI tags.
    """
    assert pypff is not None  # caller guards; satisfies the type checker
    entries: list[_RawEntry] = []
    pff = pypff.file()
    pff.open(str(path))
    try:
        for message in _pst_walk(pff.get_root_folder()):
            es = _pst_entries(message)
            if _entry_string(es, _TAG_MESSAGE_CLASS) != "IPM.Contact":
                continue
            given = _entry_string(es, _TAG_GIVEN_NAME)
            family = _entry_string(es, _TAG_SURNAME)
            if not given and not family:
                display = _entry_string(es, _TAG_DISPLAY_NAME).split()
                given = display[0] if display else ""
            seen: set[str] = set()
            for entry in es:
                try:
                    val = (entry.get_data_as_string() or "").strip()
                except Exception:  # noqa: BLE001 — non-string entry
                    continue
                if val and _EMAIL.fullmatch(val) and val.lower() not in seen:
                    seen.add(val.lower())
                    entries.append(
                        _RawEntry(given, family, val, "", source)
                    )
    finally:
        pff.close()
    return entries


# ---------------------------------------------------------------------------
# Label assignment
# ---------------------------------------------------------------------------


def _min_unique_prefix_len(families: list[str]) -> int:
    """Shortest family-name prefix length that makes all of them distinct."""
    fams = [f for f in families if f]
    if len(fams) <= 1:
        return 1
    longest = max(len(f) for f in fams)
    for length in range(1, longest + 1):
        prefixes = [f[:length].lower() for f in fams]
        if len(set(prefixes)) == len(prefixes):
            return length
    return longest


def _assign_labels(raws: list[_RawEntry]) -> list[ContactEntry]:
    """Build ``<given> <token>`` labels and seed the reverse map.

    Groups by given name to detect first-name collisions; within a colliding
    group, appends the shortest unambiguous family-name prefix. A contact's
    address type (when known) becomes a ``(…)`` qualifier. The token is always
    appended and guarantees uniqueness; duplicate addresses are de-duped.
    """
    by_given: dict[str, list[_RawEntry]] = defaultdict(list)
    for raw in raws:
        by_given[raw.given.lower()].append(raw)

    out: list[ContactEntry] = []
    seen_tokens: set[str] = set()
    for group in by_given.values():
        families = {r.family for r in group if r.family}
        need_surname = len({f.lower() for f in families}) > 1
        prefix_len = (
            _min_unique_prefix_len(list(families)) if need_surname else 0
        )
        for raw in group:
            name_part = raw.given
            if need_surname and raw.family:
                abbr = raw.family[:prefix_len]
                name_part = f"{name_part} {abbr}.".strip()
            if raw.etype:
                name_part = f"{name_part} ({raw.etype})".strip()
            token = _pseudonym.register(raw.email)
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            label = f"{name_part} <{token}>".strip()
            _transcript.record(
                token, given=raw.given, surname=raw.family,
                label=label, email=raw.email, source=raw.source,
            )
            out.append(ContactEntry(label=label, token=token))
    out.sort(key=lambda c: c.label.lower())
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _files(suffix: str) -> list[Path]:
    """Return the sorted files with *suffix* in the config directory."""
    directory = _config.config_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"*{suffix}"))


def load() -> list[ContactEntry]:
    """(Re)import every ``*.vcf`` and ``*.pst`` in the config directory.

    A file that fails to parse is reported as ``error`` and skipped; a
    ``*.pst`` with the libpff binding (``pypff``) absent is reported as
    ``skipped`` — never fatal. Also (re)writes ``contacts.md``. Returns the
    resulting PII-free entries.
    """
    global _contacts, _report
    raws: list[_RawEntry] = []
    report: list[FileReport] = []
    for path in _files(".vcf"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            entries = _parse_vcard(text, path.name)
            raws.extend(entries)
            report.append(FileReport(path.name, "imported", len(entries)))
        except Exception:  # noqa: BLE001 — a bad file must not break startup
            report.append(FileReport(path.name, "error", 0))
    for path in _files(".pst"):
        if pypff is None:
            report.append(FileReport(path.name, "skipped", 0))
            continue
        try:
            entries = _parse_pst(path, path.name)
            raws.extend(entries)
            report.append(FileReport(path.name, "imported", len(entries)))
        except Exception:  # noqa: BLE001 — a bad file must not break startup
            report.append(FileReport(path.name, "error", 0))
    _contacts = _assign_labels(raws)
    _report = report
    _transcript.sync()
    return _contacts


def current() -> list[ContactEntry]:
    """Return the entries from the last import (PII-free, label-sorted)."""
    return list(_contacts)


def report() -> list[FileReport]:
    """Return the per-file outcome of the last import."""
    return list(_report)


def _reset_for_tests() -> None:
    """Clear the imported list and report. Test-only seam."""
    global _contacts, _report
    _contacts = []
    _report = []
