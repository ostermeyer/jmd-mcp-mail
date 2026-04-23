# SPDX-License-Identifier: Apache-2.0
"""Tests for the shared _frontmatter helpers."""
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
        fm = {"foo": 1, "bar": 2, "page-size": 10}
        unknown = check_frontmatter(
            fm, frozenset({"page-size"}), "observable",
        )
        assert set(unknown) == {"foo", "bar"}

    def test_observable_empty_when_all_known(self) -> None:
        fm = {"page-size": 10}
        unknown = check_frontmatter(
            fm, frozenset({"page-size"}), "observable",
        )
        assert unknown == []

    def test_strict_raises_on_unknown(self) -> None:
        fm = {"dry-run": True}
        with pytest.raises(ValueError, match="dry-run"):
            check_frontmatter(
                fm, frozenset({"confirm"}), "strict",
            )

    def test_strict_passes_when_all_known(self) -> None:
        fm = {"confirm": "drop-folder"}
        unknown = check_frontmatter(
            fm, frozenset({"confirm"}), "strict",
        )
        assert unknown == []


class TestIgnoredKeys:
    """prepend_ignored_keys formatting."""

    def test_empty_returns_unchanged(self) -> None:
        assert prepend_ignored_keys("# X", []) == "# X"

    def test_echoes_short_form(self) -> None:
        out = prepend_ignored_keys("# X", ["foo", "bar"])
        assert out.startswith("ignored-keys: foo, bar")
        assert "# X" in out


class TestDebug:
    """Debug-mode parsing and serialization."""

    def test_no_debug_is_inactive(self) -> None:
        dbg = parse_debug({})
        assert not dbg.active

    def test_known_value_is_requested(self) -> None:
        dbg = parse_debug({"debug": "timing"})
        assert dbg.wants("timing")
        assert dbg.active

    def test_unknown_value_is_recorded(self) -> None:
        dbg = parse_debug({"debug": "foo, timing"})
        assert dbg.wants("timing")
        assert dbg.unknown == ["foo"]

    def test_multiple_values_composable(self) -> None:
        dbg = parse_debug({"debug": "timing, mailbox"})
        assert dbg.wants("timing")
        assert dbg.wants("mailbox")

    def test_frontmatter_render_includes_timing(self) -> None:
        dbg = DebugInfo(
            requested=frozenset({"timing"}),
            unknown=[], timing_ms=12.5,
        )
        out = dbg.to_frontmatter()
        assert "debug-timing: 12.5ms" in out

    def test_prepend_debug_active(self) -> None:
        dbg = DebugInfo(
            requested=frozenset({"mailbox"}),
            unknown=[], mailbox="ionos",
        )
        out = prepend_debug("# Result", dbg)
        assert "debug-mailbox: ionos" in out
        assert "# Result" in out

    def test_prepend_debug_inactive(self) -> None:
        dbg = DebugInfo(requested=frozenset(), unknown=[])
        assert prepend_debug("# Result", dbg) == "# Result"


class TestParseFrontmatter:
    """parse_frontmatter extracts the dict."""

    def test_single_key(self) -> None:
        fm = parse_frontmatter(
            "mailbox: ionos\n\n#? Message\nfolder: INBOX"
        )
        assert fm.get("mailbox") == "ionos"

    def test_empty_when_no_frontmatter(self) -> None:
        fm = parse_frontmatter("# MailBox\nname: ionos")
        assert fm == {}
