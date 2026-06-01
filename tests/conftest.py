# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures."""
from __future__ import annotations

from collections.abc import Iterator

import keyring
import pytest


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
