# SPDX-License-Identifier: Apache-2.0
"""contacts.md — the token↔address re-identification transcript (out-of-reach).

Persists a human-readable lookup table in the config directory
(`~/.jmd-mcp-mail/contacts.md`) so the user can resolve a pseudonym token —
as seen in the chat — back to the real name and address. Labels alone are
ambiguous (several `Rebecca <…>`), so this table closes that gap.

It **is** the re-identification key: written only by the server, never read by
any tool, and must be kept private.

It is a transcript of the CURRENT session only — never a data source. The
in-memory rows mirror the live token→address map (both grow as contacts are
imported and mail is read), and :func:`sync` simply rewrites the file from
those rows; it never merges a previous run's file. :func:`purge` deletes it,
called at startup (so a prior session's key never lingers) and best-effort on
shutdown. A hard kill cannot be intercepted, but the next startup's purge
clears the file before anything is written.

(Resolution for send/search is likewise session-scoped and is NEVER re-seeded
from this file — the file documents the session, it does not drive it.)
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
    """Rewrite contacts.md from this session's rows (overwrite, no merge).

    The file mirrors the in-memory session state; a previous run's file is
    overwritten, never merged. No-op until a row has been recorded — a stale
    file with nothing new to write is cleared by :func:`purge` at startup, not
    here.
    """
    global _dirty
    if not _dirty:
        return
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(_rows), encoding="utf-8")
    except OSError:
        return
    _dirty = False


def purge() -> None:
    """Best-effort delete of contacts.md (session boundary / shutdown).

    Called at startup (drop any prior session's key before writing) and on
    graceful shutdown. A hard kill cannot run it; the next startup will.
    """
    try:
        _path().unlink(missing_ok=True)
    except OSError:
        pass


def _reset_for_tests() -> None:
    """Clear recorded rows and the dirty flag. Test-only seam."""
    global _rows, _dirty
    _rows = {}
    _dirty = False
