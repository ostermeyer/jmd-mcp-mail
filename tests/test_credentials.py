# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``mail_mcp._credentials``."""
from __future__ import annotations

import subprocess
import sys
import uuid
from unittest.mock import patch

import pytest

from mail_mcp import _credentials


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Clear the resolver cache before each test."""
    _credentials._reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_second_keystore_read() -> None:
    """Second resolve for the same key reuses the cached value."""
    with patch.object(
        _credentials,
        "_read_from_keystore",
        return_value="s3cret",
    ) as mock_read:
        assert _credentials.resolve("svc", "user") == "s3cret"
        assert _credentials.resolve("svc", "user") == "s3cret"
    assert mock_read.call_count == 1


def test_cache_separates_distinct_keys() -> None:
    """Distinct ``(service, username)`` pairs cache independently."""
    table = {
        ("a", "u"): "pwA",
        ("b", "u"): "pwB",
        ("a", "v"): "pwV",
    }

    def fake(service: str, username: str) -> str | None:
        return table[(service, username)]

    with patch.object(
        _credentials, "_read_from_keystore", side_effect=fake
    ):
        assert _credentials.resolve("a", "u") == "pwA"
        assert _credentials.resolve("b", "u") == "pwB"
        assert _credentials.resolve("a", "v") == "pwV"


# ---------------------------------------------------------------------------
# Miss → CredentialNotFoundError with structured seed command
# ---------------------------------------------------------------------------


def test_miss_raises_credential_not_found() -> None:
    """``None`` from the keystore reader raises ``CredentialNotFoundError``."""
    with patch.object(
        _credentials, "_read_from_keystore", return_value=None
    ):
        with pytest.raises(_credentials.CredentialNotFoundError) as exc_info:
            _credentials.resolve("svc", "user")
    err = exc_info.value
    assert err.service == "svc"
    assert err.username == "user"
    assert err.seed_command
    assert "svc" in err.seed_command
    assert "user" in err.seed_command


def test_miss_does_not_pollute_cache() -> None:
    """A miss must not write a sentinel into the cache."""
    with patch.object(
        _credentials, "_read_from_keystore", return_value=None
    ):
        with pytest.raises(_credentials.CredentialNotFoundError):
            _credentials.resolve("svc", "user")
    assert ("svc", "user") not in _credentials._cache


# ---------------------------------------------------------------------------
# macOS keystore round-trip (skipped off-macOS)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS-specific: real Keychain round-trip",
)
def test_macos_real_keychain_roundtrip() -> None:
    """Seed via ``security(1)``, resolve, then delete the item."""
    service = f"jmd-mail-test-{uuid.uuid4()}"
    username = "test-user"
    password = "test-pw-äöü-😀-with spaces"
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-s",
            service,
            "-a",
            username,
            "-w",
            password,
        ],
        check=True,
    )
    try:
        assert _credentials.resolve(service, username) == password
    finally:
        subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-s",
                service,
                "-a",
                username,
            ],
            check=True,
        )


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS-specific: 'security' binary path check",
)
def test_macos_keystore_unavailable_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``KeystoreUnavailableError`` when ``shutil.which`` returns ``None``."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(_credentials.KeystoreUnavailableError):
        _credentials.resolve("svc", "user")
