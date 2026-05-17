# SPDX-License-Identifier: Apache-2.0
"""Tests for the shared ``_frontmatter`` helpers."""
from __future__ import annotations

import pytest

from mail_mcp._frontmatter import (
    DebugInfo,
    check_frontmatter,
    parse_debug,
    parse_frontmatter,
    prepend_debug,
    prepend_ignored_keys,
)


class TestTolerance:
    """Observable tolerance / strict refusal behaviour."""

    def test_observable_returns_unknown_keys(self) -> None:
        """Observable policy reports unknown keys without raising."""
        fm = {"foo": 1, "bar": 2, "page-size": 10}
        unknown = check_frontmatter(
            fm, frozenset({"page-size"}), "observable",
        )
        assert set(unknown) == {"foo", "bar"}

    def test_observable_empty_when_all_known(self) -> None:
        """Observable policy returns ``[]`` when every key is known."""
        fm = {"page-size": 10}
        unknown = check_frontmatter(
            fm, frozenset({"page-size"}), "observable",
        )
        assert unknown == []

    def test_strict_raises_on_unknown(self) -> None:
        """Strict policy raises ``ValueError`` on any unknown key."""
        fm = {"dry-run": True}
        with pytest.raises(ValueError, match="dry-run"):
            check_frontmatter(
                fm, frozenset({"confirm"}), "strict",
            )

    def test_strict_passes_when_all_known(self) -> None:
        """Strict policy returns ``[]`` when every key is known."""
        fm = {"confirm": "drop-folder"}
        unknown = check_frontmatter(
            fm, frozenset({"confirm"}), "strict",
        )
        assert unknown == []


class TestIgnoredKeys:
    """``prepend_ignored_keys`` formatting."""

    def test_empty_returns_unchanged(self) -> None:
        """Empty ``ignored`` list yields the response untouched."""
        assert prepend_ignored_keys("# X", []) == "# X"

    def test_echoes_short_form(self) -> None:
        """Non-empty list renders as ``ignored-keys: a, b`` header."""
        out = prepend_ignored_keys("# X", ["foo", "bar"])
        assert out.startswith("ignored-keys: foo, bar")
        assert "# X" in out


class TestDebug:
    """Debug-mode parsing and serialization."""

    def test_no_debug_is_inactive(self) -> None:
        """Absent ``debug`` frontmatter yields an inactive DebugInfo."""
        dbg = parse_debug({})
        assert not dbg.active

    def test_known_value_is_requested(self) -> None:
        """A known token is recorded as requested and marks active."""
        dbg = parse_debug({"debug": "timing"})
        assert dbg.wants("timing")
        assert dbg.active

    def test_unknown_value_is_recorded(self) -> None:
        """Unknown tokens land in ``unknown`` without breaking known ones."""
        dbg = parse_debug({"debug": "foo, timing"})
        assert dbg.wants("timing")
        assert dbg.unknown == ["foo"]

    def test_multiple_values_composable(self) -> None:
        """Multiple comma-separated tokens compose without conflict."""
        dbg = parse_debug({"debug": "timing"})
        assert dbg.wants("timing")
        assert dbg.active

    def test_frontmatter_render_includes_timing(self) -> None:
        """``to_frontmatter`` emits ``debug-timing: N.Nms`` when requested."""
        dbg = DebugInfo(
            requested=frozenset({"timing"}),
            unknown=[], timing_ms=12.5,
        )
        out = dbg.to_frontmatter()
        assert "debug-timing: 12.5ms" in out

    def test_prepend_debug_active(self) -> None:
        """Active DebugInfo prepends rendered frontmatter to the response."""
        dbg = DebugInfo(
            requested=frozenset({"timing"}),
            unknown=[], timing_ms=7.0,
        )
        out = prepend_debug("# Result", dbg)
        assert "debug-timing: 7.0ms" in out
        assert "# Result" in out

    def test_prepend_debug_inactive(self) -> None:
        """Inactive DebugInfo leaves the response untouched."""
        dbg = DebugInfo(requested=frozenset(), unknown=[])
        assert prepend_debug("# Result", dbg) == "# Result"


class TestParseFrontmatter:
    """``parse_frontmatter`` extracts the dict."""

    def test_single_key(self) -> None:
        """A single ``key: value`` pair before the heading is parsed."""
        fm = parse_frontmatter(
            "rename-to: NewName\n\n# Folder\npath: OldName"
        )
        assert fm.get("rename-to") == "NewName"

    def test_empty_when_no_frontmatter(self) -> None:
        """A document without frontmatter yields an empty dict."""
        fm = parse_frontmatter("# Folder\npath: Inbox")
        assert fm == {}
