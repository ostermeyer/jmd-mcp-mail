# SPDX-License-Identifier: Apache-2.0
"""DSGVO-konformer Kontakt-Import (Adressbuch) — strictly in-memory.

Seeds the pseudonym reverse-map from address-book exports (vCard / CSV) so
the agent can address people it has never exchanged mail with — e.g. when
the mailbox itself is locked — without their real address ever entering the
LLM context.

Sources are file PATHS only (never contents over a tool boundary):

* **primary:** ``--contacts <path>`` CLI args on the server entrypoint,
* **alternative:** the ``JMD_MCP_MAIL_CONTACT_SOURCES`` environment variable
  (an ``os.pathsep``-separated list), however it happens to be set.

Nothing is persisted. Parsing seeds the in-process reverse-map in
:mod:`mail_mcp._pseudonym` (token → real address) and keeps a list of
``(label, token)`` for the ``contacts`` tool. Real addresses live only in
that in-memory map and are never returned anywhere.

Label form is ``<Namensteil> <token>``; the token is always present and
carries uniqueness, the name part is a readability aid (given name, plus the
shortest unambiguous family-name prefix on first-name collisions, plus a
``(geschäftlich)`` / ``(privat)`` qualifier when a contact has several
addresses).
"""
from __future__ import annotations

import csv
import io
import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import vobject

from mail_mcp import _pseudonym

_ENV_SOURCES = "JMD_MCP_MAIL_CONTACT_SOURCES"

# vCard / CSV address-type → human label qualifier.
_TYPE_MAP = {"work": "geschäftlich", "home": "privat"}

# CLI-configured source paths, set by the entrypoint before startup import.
_cli_sources: list[Path] = []

# Result of the last import: PII-free (label, token) pairs, label-sorted.
_contacts: list[ContactEntry] = []


@dataclass(frozen=True)
class ContactEntry:
    """One resolvable address-book entry — label + token, never an address."""

    label: str
    token: str


@dataclass
class _RawEntry:
    """A parsed contact address prior to label assignment."""

    given: str
    family: str
    email: str
    etype: str  # normalised qualifier: "geschäftlich" | "privat" | ""


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------


def set_cli_sources(paths: Iterable[str | Path]) -> None:
    """Record the ``--contacts`` paths supplied on the command line."""
    global _cli_sources
    _cli_sources = [Path(p) for p in paths]


def configured_sources() -> list[Path]:
    """Return all configured source paths: CLI args plus the env variable."""
    sources = list(_cli_sources)
    env = os.environ.get(_ENV_SOURCES, "")
    sources += [Path(p) for p in env.split(os.pathsep) if p.strip()]
    return sources


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _norm_type(raw: str) -> str:
    """Map a vCard/CSV address type to a human qualifier, or ''."""
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
            # No structured name — fall back to the first token of FN so the
            # surname stays out of the label.
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


def _find_col(cols: dict[str, str], needles: tuple[str, ...]) -> str | None:
    """Return the original header for the first column matching a needle."""
    for low, orig in cols.items():
        if any(n in low for n in needles):
            return orig
    return None


def _parse_csv(text: str) -> list[_RawEntry]:
    """Parse CSV text into raw entries with tolerant column mapping."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    cols = {c.lower().strip(): c for c in reader.fieldnames if c}
    given_col = _find_col(cols, ("first name", "given name", "vorname"))
    family_col = _find_col(
        cols, ("last name", "family name", "surname", "nachname")
    )
    email_cols = [
        orig for low, orig in cols.items()
        if ("e-mail" in low or "email" in low)
        and "type" not in low and "display" not in low
    ]
    entries: list[_RawEntry] = []
    for row in reader:
        given = (row.get(given_col, "") or "").strip() if given_col else ""
        family = (row.get(family_col, "") or "").strip() if family_col else ""
        addrs = [
            v.strip() for col in email_cols
            if (v := (row.get(col, "") or "").strip()) and "@" in v
        ]
        for addr in addrs:
            # CSV type columns are unreliable across clients; rely on the
            # always-present token to disambiguate multiple addresses.
            entries.append(_RawEntry(given, family, addr, ""))
    return entries


def _parse_path(path: Path, text: str) -> list[_RawEntry]:
    """Dispatch to the vCard or CSV parser by suffix, with a content sniff."""
    suffix = path.suffix.lower()
    if suffix == ".vcf":
        return _parse_vcard(text)
    if suffix == ".csv":
        return _parse_csv(text)
    if "BEGIN:VCARD" in text[:64].upper():
        return _parse_vcard(text)
    return _parse_csv(text)


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
    """Build ``<Namensteil> <token>`` labels and seed the reverse map.

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


def load() -> list[ContactEntry]:
    """(Re)import all configured sources into the in-memory address book.

    A missing, unreadable or malformed source is skipped rather than fatal —
    the server must always start. Returns the resulting PII-free entries.
    """
    global _contacts
    raws: list[_RawEntry] = []
    for path in configured_sources():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            raws.extend(_parse_path(path, text))
        except Exception:  # noqa: BLE001 — never let a bad source break startup
            continue
    _contacts = _assign_labels(raws)
    return _contacts


def current() -> list[ContactEntry]:
    """Return the entries from the last import (PII-free, label-sorted)."""
    return list(_contacts)


def _reset_for_tests() -> None:
    """Clear CLI sources and the imported list. Test-only seam."""
    global _cli_sources, _contacts
    _cli_sources = []
    _contacts = []
