# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest


@pytest.fixture(autouse=True)
def _isolated_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point the config dir (and legacy path) at a throwaway location.

    Protects every test from touching the real ``~/.jmd-mcp-mail`` and
    neutralises the best-effort legacy migration (the legacy path points
    at a non-existent temp file).
    """
    monkeypatch.setenv("JMD_MCP_MAIL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "JMD_MCP_MAIL_ACCOUNTS_PATH", str(tmp_path / "legacy-absent.jmd"),
    )


@pytest.fixture
def mem_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, str]]:
    """Back the keyring lib with an in-memory dict for one test."""
    store: dict[str, str] = {}

    def _get(service: str, username: str) -> str | None:
        return store.get(f"{service}/{username}")

    def _set(service: str, username: str, password: str) -> None:
        store[f"{service}/{username}"] = password

    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(keyring, "set_password", _set)
    yield store
