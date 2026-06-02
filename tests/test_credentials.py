# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``mail_mcp._credentials``."""
from __future__ import annotations

import shutil
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
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(_credentials.KeystoreUnavailableError):
        _credentials.resolve("svc", "user")


# ---------------------------------------------------------------------------
# Linux Secret-Service round-trip (skipped off-Linux or when the
# keyring daemon is unavailable)
# ---------------------------------------------------------------------------


def _linux_secret_service_ready() -> bool:
    """Best-effort probe for a usable Secret Service backend.

    Skipif-conditions for the Linux roundtrip test.  We check
    that ``secret-tool`` exists and that a benign ``search`` call
    against a guaranteed-absent attribute set returns cleanly
    (exit 0 with empty stdout when no match, vs. non-zero when
    the D-Bus / keyring daemon is missing or locked).
    """
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("secret-tool") is None:
        return False
    probe = subprocess.run(
        [
            "secret-tool",
            "search",
            "jmd-mcp-mail-probe",
            f"probe-{uuid.uuid4()}",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    # Exit 0 = backend responded (with or without matches).
    # Exit 1 = no matches (still means backend works).
    # Anything else (D-Bus error, no daemon) means unusable.
    return probe.returncode in (0, 1)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux-specific: secret-tool round-trip",
)
@pytest.mark.skipif(
    sys.platform.startswith("linux") and not _linux_secret_service_ready(),
    reason=(
        "No usable Secret Service backend "
        "(no secret-tool or no keyring daemon)"
    ),
)
def test_linux_real_secret_service_roundtrip() -> None:
    """Seed via ``secret-tool store``, resolve, then ``secret-tool clear``."""
    service = f"jmd-mail-test-{uuid.uuid4()}"
    username = "test-user"
    password = "test-pw-äöü-😀-with spaces"
    # `secret-tool store` reads the password from stdin when stdin
    # is a pipe (which it is here).  One line, no terminator
    # required, but trailing-newline trimming is up to the tool.
    subprocess.run(
        [
            "secret-tool",
            "store",
            "--label=jmd-mcp-mail-test",
            "service",
            service,
            "username",
            username,
        ],
        input=password,
        encoding="utf-8",
        check=True,
    )
    try:
        assert _credentials.resolve(service, username) == password
    finally:
        subprocess.run(
            [
                "secret-tool",
                "clear",
                "service",
                service,
                "username",
                username,
            ],
            check=True,
        )


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux-specific: 'secret-tool' binary path check",
)
def test_linux_keystore_unavailable_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``KeystoreUnavailableError`` when ``shutil.which`` returns ``None``."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(_credentials.KeystoreUnavailableError) as exc:
        _credentials.resolve("svc", "user")
    assert "secret-tool" in str(exc.value)


# ---------------------------------------------------------------------------
# Windows Credential-Manager round-trip (skipped off-Windows)
# ---------------------------------------------------------------------------


def _win32_cred_write(target: str, username: str, password: str) -> None:
    """Test helper: seed a Generic credential via ``CredWriteW``.

    Used in lieu of shelling out to ``cmdkey`` so the test can
    use any Unicode password without worrying about cmd's
    quoting and code-page handling.
    """
    if sys.platform != "win32":  # pragma: no cover - win32 only
        raise RuntimeError("Windows-only test helper")

    import ctypes
    from ctypes import wintypes

    class _Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    cred_write = advapi32.CredWriteW
    cred_write.argtypes = [
        ctypes.POINTER(_Credential),
        wintypes.DWORD,
    ]
    cred_write.restype = wintypes.BOOL

    blob = password.encode("utf-16-le")
    blob_buf = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)

    cred = _Credential()
    cred.Flags = 0
    cred.Type = _credentials._WIN32_CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.Comment = None
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(
        blob_buf, ctypes.POINTER(ctypes.c_byte)
    )
    cred.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = username

    ok = cred_write(ctypes.byref(cred), 0)
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(
            f"CredWriteW failed with Win32 error code {err}"
        )


def _win32_cred_delete(target: str) -> None:
    """Test helper: delete a Generic credential via ``CredDeleteW``."""
    if sys.platform != "win32":  # pragma: no cover - win32 only
        raise RuntimeError("Windows-only test helper")

    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    cred_delete = advapi32.CredDeleteW
    cred_delete.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    cred_delete.restype = wintypes.BOOL
    ok = cred_delete(
        target, _credentials._WIN32_CRED_TYPE_GENERIC, 0
    )
    if not ok:
        err = ctypes.get_last_error()
        # ERROR_NOT_FOUND is fine in cleanup
        if err != _credentials._WIN32_ERROR_NOT_FOUND:
            raise OSError(
                f"CredDeleteW failed with Win32 error code {err}"
            )


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-specific: real Credential Manager round-trip",
)
def test_windows_real_credential_manager_roundtrip() -> None:
    """Seed via ``CredWriteW``, resolve, then ``CredDeleteW``."""
    service = f"jmd-mail-test-{uuid.uuid4()}"
    username = "test-user"
    password = "test-pw-äöü-😀-with spaces"
    target = _credentials._windows_target(service, username)
    _win32_cred_write(target, username, password)
    try:
        assert _credentials.resolve(service, username) == password
    finally:
        _win32_cred_delete(target)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-specific: CredReadW miss-path check",
)
def test_windows_returns_none_when_credential_absent() -> None:
    """Resolving an unseeded ``(service, username)`` raises NotFound."""
    service = f"jmd-mail-test-absent-{uuid.uuid4()}"
    username = "no-such-user"
    with pytest.raises(_credentials.CredentialNotFoundError):
        _credentials.resolve(service, username)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-specific: UserName cross-check",
)
def test_windows_username_mismatch_raises_unavailable() -> None:
    """A composite TargetName with a foreign UserName is rejected."""
    service = f"jmd-mail-test-{uuid.uuid4()}"
    expected_user = "alice"
    target = _credentials._windows_target(service, expected_user)
    # Seed with the right TargetName but a *different* UserName.
    _win32_cred_write(target, "bob", "irrelevant")
    try:
        with pytest.raises(_credentials.KeystoreUnavailableError):
            _credentials.resolve(service, expected_user)
    finally:
        _win32_cred_delete(target)
