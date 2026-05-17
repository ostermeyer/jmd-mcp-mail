# SPDX-License-Identifier: Apache-2.0
"""Frontmatter helpers: tolerance and debug mode.

Provides shared helpers used by every tool:

* :func:`check_frontmatter` — tolerance policy validation.
* :func:`prepend_ignored_keys` — observable-tolerance echo.
* :func:`parse_debug` and :class:`DebugInfo` — frontmatter-
  driven debug mode (``debug: timing``).

The design mirrors jmd-mcp-sql so the SmartSuite/mail/oauth2
servers use a consistent model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jmd import JMDParser

# Known debug values accepted by jmd-mcp-mail.
_KNOWN_DEBUG: frozenset[str] = frozenset({"timing"})


@dataclass
class DebugInfo:
    """Collects debug output during a single operation."""

    requested: frozenset[str]
    unknown: list[str]
    timing_ms: float = 0.0

    @property
    def active(self) -> bool:
        """Whether any debug value was requested."""
        return bool(self.requested)

    def wants(self, key: str) -> bool:
        """Whether the caller requested this debug value."""
        return key in self.requested

    def to_frontmatter(self) -> str:
        """Render collected debug info as JMD frontmatter lines."""
        parts: list[str] = []
        if self.unknown:
            parts.append(
                "debug-unknown: " + ", ".join(self.unknown)
            )
        if self.wants("timing"):
            parts.append(
                f"debug-timing: {self.timing_ms:.1f}ms"
            )
        return "\n".join(parts)


def parse_debug(fm: dict[str, Any]) -> DebugInfo:
    """Parse the ``debug:`` frontmatter key.

    Special values:
      * ``true`` (or the boolean ``True``) — alias for "all
        known debug channels".  This matches the natural LLM
        intuition of using ``debug: true`` as a boolean flag.
    """
    raw = fm.get("debug")
    if raw is None:
        return DebugInfo(
            requested=frozenset(), unknown=[],
        )
    if raw is True or str(raw).strip().lower() == "true":
        return DebugInfo(
            requested=_KNOWN_DEBUG, unknown=[],
        )
    values = {
        v.strip() for v in str(raw).split(",") if v.strip()
    }
    known = frozenset(values & _KNOWN_DEBUG)
    unknown = sorted(values - _KNOWN_DEBUG)
    return DebugInfo(requested=known, unknown=unknown)


def prepend_debug(
    response: str, dbg: DebugInfo,
) -> str:
    """Prepend debug frontmatter to *response* if active."""
    fm = dbg.to_frontmatter()
    if not fm:
        return response
    return f"{fm}\n\n{response}"


def parse_frontmatter(document: str) -> dict[str, Any]:
    """Parse *document* and return its frontmatter dict.

    Any parse errors (e.g. document is not valid JMD) surface as
    :class:`ValueError` from the underlying parser.
    """
    parser = JMDParser()
    parser.parse(document)
    return parser.frontmatter


class StrictRefusalError(ValueError):
    """Raised when strict refusal rejects unknown frontmatter keys.

    Inherits from :class:`ValueError` so existing ``except
    ValueError`` paths continue to work.  The structured
    attributes ``unknown`` and ``accepted`` let callers build
    detailed error responses.
    """

    def __init__(
        self, unknown: list[str], accepted: list[str],
    ) -> None:
        """Initialise the error with unknown and accepted keys."""
        self.unknown = unknown
        self.accepted = accepted
        accepted_str = (
            ", ".join(accepted) if accepted else "(none)"
        )
        super().__init__(
            f"Unknown frontmatter key(s) {unknown!r} on a"
            " destructive operation. Accepted keys:"
            f" {accepted_str}."
        )


def check_frontmatter(
    fm: dict[str, Any],
    known: frozenset[str],
    policy: str,
) -> list[str]:
    """Validate frontmatter keys against a known set.

    Args:
        fm: Parsed frontmatter dict from the JMD parser.
        known: Set of keys this operation recognises.
        policy: ``"observable"`` to return unknown keys silently,
            or ``"strict"`` to raise on unknown keys.

    Returns:
        List of unknown key names (may be empty).

    Raises:
        StrictRefusalError: When *policy* is ``"strict"`` and
            unknown keys are present.
    """
    unknown = [k for k in fm if k not in known]
    if unknown and policy == "strict":
        raise StrictRefusalError(
            unknown=unknown, accepted=sorted(known),
        )
    return unknown


def prepend_ignored_keys(
    response: str, ignored: list[str],
) -> str:
    """Prepend an ``ignored-keys`` header to *response*.

    Uses the short form from JMD Spec §23.7:
    ``ignored-keys: key1, key2``.  Returns *response* unchanged
    when *ignored* is empty.
    """
    if not ignored:
        return response
    header = "ignored-keys: " + ", ".join(ignored)
    return f"{header}\n\n{response}"
