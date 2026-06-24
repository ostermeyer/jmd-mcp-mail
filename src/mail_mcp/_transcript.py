# SPDX-License-Identifier: Apache-2.0
"""contacts.md — the token↔address re-identification transcript (out-of-reach).

Persists a human-readable lookup table in the config directory
(`~/.jmd-mcp-mail/contacts.md`) so the user can resolve a pseudonym token —
as seen in the chat — back to the real name and address. Labels alone are
ambiguous (several `Rebecca <…>`), so this table closes that gap.

It **is** the re-identification key: written only by the server, never read by
any tool, and must be kept private. Rows accumulate over time (imports +
addresses seen while reading mail) and survive restarts — each :func:`sync`
reads the existing file, merges the current session's rows by token, and
rewrites the table sorted. (Resolution for send/search is NOT re-seeded from
this file; it stays session-scoped.)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mail_mcp import _config

_FILENAME = "contacts.md"
_COLUMNS = ("token", "given", "surname", "label", "email", "source")
_HEADER = (
    "# Contacts — re-identification key (pseudonym token ↔ real address).\n"
    "# KEEP PRIVATE. Written by jmd-mcp-mail; never read by any tool.\n"
)


@dataclass(frozen=True)
class Row:
    """One transcript row mapping a token to a real name + address."""

    token: str
    given: str
    surname: str
    label: str
    email: str
    source: str


_rows: dict[str, Row] = {}
_dirty = False


def record(
    token: str,
    *,
    given: str,
    surname: str,
    label: str,
    email: str,
    source: str,
) -> None:
    """Record a token→address row (first write wins); marks the file dirty."""
    global _dirty
    if token in _rows:
        return
    _rows[token] = Row(token, given, surname, label, email, source)
    _dirty = True


def _path() -> Path:
    """Path to contacts.md in the config directory."""
    return _config.config_dir() / _FILENAME


def _parse_existing(text: str) -> dict[str, Row]:
    """Parse a previously-written contacts.md table into rows by token."""
    rows: dict[str, Row] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(_COLUMNS):
            continue
        if cells[0] == "token" or set(cells[0]) <= {"-", ":"}:
            continue  # header or separator row
        rows[cells[0]] = Row(*cells)
    return rows


def _render(rows: dict[str, Row]) -> str:
    """Render rows as a Markdown table (pipes in values neutralised)."""
    lines = [
        _HEADER,
        "| " + " | ".join(_COLUMNS) + " |",
        "|" + "|".join(["---"] * len(_COLUMNS)) + "|",
    ]
    ordered = sorted(
        rows.values(),
        key=lambda r: (r.given.lower(), r.surname.lower(), r.token),
    )
    for r in ordered:
        cells = (r.token, r.given, r.surname, r.label, r.email, r.source)
        safe = [c.replace("|", "/") for c in cells]
        lines.append("| " + " | ".join(safe) + " |")
    return "\n".join(lines) + "\n"


def sync() -> None:
    """Write contacts.md if rows were recorded, merging the existing file."""
    global _dirty
    if not _dirty:
        return
    path = _path()
    merged: dict[str, Row] = {}
    try:
        if path.exists():
            merged = _parse_existing(path.read_text(encoding="utf-8"))
    except OSError:
        merged = {}
    merged.update(_rows)  # session rows win over stale file rows
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(merged), encoding="utf-8")
    except OSError:
        return
    _dirty = False


def _reset_for_tests() -> None:
    """Clear recorded rows and the dirty flag. Test-only seam."""
    global _rows, _dirty
    _rows = {}
    _dirty = False
