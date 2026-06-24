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

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import vobject

from mail_mcp import _config, _pseudonym

# vCard address-type → human label qualifier.
_TYPE_MAP = {"work": "geschäftlich", "home": "privat"}

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
    status: str  # "imported" | "error"
    contacts: int


@dataclass
class _RawEntry:
    """A parsed contact address prior to label assignment."""

    given: str
    family: str
    email: str
    etype: str  # normalised qualifier: "geschäftlich" | "privat" | ""


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


def _parse_vcard(text: str) -> list[_RawEntry]:
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
                _RawEntry(given, family, line.value.strip(), etype)
            )
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
            out.append(ContactEntry(label=label, token=token))
    out.sort(key=lambda c: c.label.lower())
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _vcf_files() -> list[Path]:
    """Return the sorted ``*.vcf`` files in the config directory."""
    directory = _config.config_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.vcf"))


def load() -> list[ContactEntry]:
    """(Re)import every ``*.vcf`` in the config directory.

    A file that fails to parse is reported as ``error`` and skipped — never
    fatal. Returns the resulting PII-free entries.
    """
    global _contacts, _report
    raws: list[_RawEntry] = []
    report: list[FileReport] = []
    for path in _vcf_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            entries = _parse_vcard(text)
            raws.extend(entries)
            report.append(FileReport(path.name, "imported", len(entries)))
        except Exception:  # noqa: BLE001 — a bad file must not break startup
            report.append(FileReport(path.name, "error", 0))
    _contacts = _assign_labels(raws)
    _report = report
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
