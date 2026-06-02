# SPDX-License-Identifier: Apache-2.0
"""Credential resolver for jmd-mcp-mail.

Resolves passwords for ``(service, username)`` pairs from the OS
keystore with a process-lifetime in-memory cache.  All three
mainstream desktop platforms are wired through:

* macOS — ``/usr/bin/security`` (Keychain Generic Password items).
* Linux — ``secret-tool`` from ``libsecret`` (Secret Service /
  GNOME Keyring / KWallet via the libsecret bridge).
* Windows — Win32 Credential Manager via ``advapi32!CredReadW``
  (called through ``ctypes``; no PowerShell subprocess needed).

On Windows the ``TargetName`` of the stored credential is
namespaced as ``jmd-mcp-mail:<service>:<username>``.  The Win32
Credential Manager keys generic credentials by ``TargetName``
alone, so without this composite key a second mail account on
the same host would silently overwrite the first.  macOS and
libsecret natively key by ``(service, username)`` and need no
such namespacing.

Design background: workspace memory
``architecture_mcp_credential_security``.  No generic keyring MCP
tool exposes the keystore.  Tool signatures carry
``(service, username)``; the server resolves the password here,
caches it, and never echoes it into tool output.
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys

# macOS Security framework: errSecItemNotFound
_ERRSEC_ITEM_NOT_FOUND = 44

# Win32 ERROR_NOT_FOUND from winerror.h.
_WIN32_ERROR_NOT_FOUND = 1168

# Win32 CRED_TYPE_GENERIC from wincred.h.
_WIN32_CRED_TYPE_GENERIC = 1

# Namespace prefix for Windows Credential Manager TargetName.  See
# module docstring for rationale.
_WIN32_TARGET_PREFIX = "jmd-mcp-mail"

_cache: dict[tuple[str, str], str] = {}


class CredentialNotFoundError(Exception):
    """Raised when no keystore item exists for ``(service, username)``.

    Attributes:
        service: The keystore service used in the lookup.
        username: The keystore username used in the lookup.
        seed_command: A ready-to-run shell command to seed the
            missing item via the host platform's CLI.
    """

    def __init__(self, service: str, username: str) -> None:
        """Build the exception with a platform-appropriate seed hint.

        Args:
            service: The keystore service used in the failed lookup.
            username: The keystore username used in the failed lookup.
        """
        self.service = service
        self.username = username
        self.seed_command = _seed_command(service, username)
        super().__init__(
            f"No keystore item for"
            f" ({service!r}, {username!r}).\n"
            f"Seed it with:\n  {self.seed_command}"
        )


class KeystoreUnavailableError(Exception):
    """Raised when the platform's keystore is unreachable.

    On macOS this means ``/usr/bin/security`` is missing or the
    binary failed for a reason other than a missing item.  On
    Linux it means ``secret-tool`` is missing (install
    ``libsecret-tools``) or the Secret Service daemon is not
    running (no unlocked keyring).  On Windows it means
    ``advapi32!CredReadW`` returned an unexpected error.
    """


def resolve(service: str, username: str) -> str:
    """Resolve a password for ``(service, username)`` from the keystore.

    First checks the process-lifetime cache; on miss, invokes the
    platform's keystore backend exactly once and caches the
    result.

    Args:
        service: Keystore service name.  In jmd-mcp-mail this is
            the mail-server endpoint (e.g. ``smtp.gmail.com``).
        username: Keystore account name.  In jmd-mcp-mail this is
            the SMTP/IMAP login username.

    Returns:
        The stored password.

    Raises:
        CredentialNotFoundError: If no item exists for the pair.
        KeystoreUnavailableError: If the keystore backend cannot
            be reached or returns an unexpected error.
        NotImplementedError: On platforms not supported by this
            module (anything that is neither darwin, win32, nor
            linux).
    """
    key = (service, username)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    password = _read_from_keystore(service, username)
    if password is None:
        raise CredentialNotFoundError(service, username)

    _cache[key] = password
    return password


def _read_from_keystore(service: str, username: str) -> str | None:
    """Dispatch to the platform-specific keystore reader.

    Args:
        service: Keystore service name.
        username: Keystore account name.

    Returns:
        The stored password, or ``None`` if the item is absent.

    Raises:
        KeystoreUnavailableError: If the platform backend cannot
            be reached.
        NotImplementedError: On platforms not supported by this
            module.
    """
    if sys.platform == "darwin":
        return _read_macos(service, username)
    if sys.platform == "win32":
        return _read_windows(service, username)
    if sys.platform.startswith("linux"):
        return _read_linux(service, username)
    raise NotImplementedError(
        f"Unsupported platform: {sys.platform!r}"
    )


def _read_macos(service: str, username: str) -> str | None:
    """Read a Generic Password item from the macOS Keychain.

    Args:
        service: Value of ``kSecAttrService`` (= ``-s``).
        username: Value of ``kSecAttrAccount`` (= ``-a``).

    Returns:
        The decrypted password, or ``None`` if the item does not
        exist.  Never returns an empty string for "found but
        empty" — that case is forwarded as the empty string from
        the keystore.

    Raises:
        KeystoreUnavailableError: If ``security`` is missing or returns
            an unexpected non-zero status.
    """
    binary = shutil.which("security")
    if binary is None:
        raise KeystoreUnavailableError(
            "macOS 'security' binary not found — "
            "expected at /usr/bin/security."
        )
    try:
        proc = subprocess.run(
            [
                binary,
                "find-generic-password",
                "-g",
                "-s",
                service,
                "-a",
                username,
            ],
            check=False,
            capture_output=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise KeystoreUnavailableError(
            f"Failed to invoke 'security': {exc}"
        ) from exc

    if proc.returncode == 0:
        return _parse_security_g_output(proc.stderr)

    if (
        proc.returncode == _ERRSEC_ITEM_NOT_FOUND
        or "could not be found" in proc.stderr
    ):
        return None

    raise KeystoreUnavailableError(
        f"'security' exited with code {proc.returncode}: "
        f"{proc.stderr.strip()}"
    )


def _read_linux(service: str, username: str) -> str | None:
    """Read a Secret Service item via ``secret-tool``.

    Uses the same attribute pair (``service``, ``username``) as the
    seed command (``secret-tool store ... service <s> username <u>``).
    The libsecret backend keys items by the full attribute set, so
    multiple accounts on the same host coexist without conflict.

    Args:
        service: First attribute value (the mail endpoint).
        username: Second attribute value (the login).

    Returns:
        The stored password, or ``None`` if no item matches.

    Raises:
        KeystoreUnavailableError: If ``secret-tool`` is missing
            (the ``libsecret-tools`` package is not installed) or
            it returned a non-success exit code that is not the
            documented "not found" condition.
    """
    binary = shutil.which("secret-tool")
    if binary is None:
        raise KeystoreUnavailableError(
            "Linux 'secret-tool' binary not found — install it "
            "via your distribution's package manager "
            "(e.g. 'apt install libsecret-tools' on Debian/Ubuntu, "
            "'dnf install libsecret' on Fedora)."
        )
    try:
        proc = subprocess.run(
            [
                binary,
                "lookup",
                "service",
                service,
                "username",
                username,
            ],
            check=False,
            capture_output=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise KeystoreUnavailableError(
            f"Failed to invoke 'secret-tool': {exc}"
        ) from exc

    if proc.returncode == 0:
        # secret-tool writes the password to stdout with no
        # trailing newline.  Pass it through byte-exact.
        return proc.stdout

    # secret-tool exits 1 with empty stdout when no match exists.
    # Any other non-zero status is a backend failure worth
    # surfacing (locked keyring, D-Bus not running, etc.).
    if proc.returncode == 1 and not proc.stdout:
        return None

    raise KeystoreUnavailableError(
        f"'secret-tool' exited with code {proc.returncode}: "
        f"{proc.stderr.strip()}"
    )


def _read_windows(service: str, username: str) -> str | None:
    """Read a Generic credential from the Win32 Credential Manager.

    Uses ``advapi32!CredReadW`` via ``ctypes``.  The ``TargetName``
    is the composite ``jmd-mcp-mail:<service>:<username>`` (see
    module docstring for rationale).

    Args:
        service: Mail endpoint (used in the composite TargetName).
        username: Login (used in the composite TargetName *and*
            cross-checked against the stored ``UserName`` field
            for defence in depth).

    Returns:
        The stored password, or ``None`` if no credential with
        the composite TargetName exists.

    Raises:
        KeystoreUnavailableError: If ``CredReadW`` returned an
            error other than ``ERROR_NOT_FOUND``, or if the
            stored ``UserName`` does not match ``username`` (which
            indicates the credential was seeded with a different
            namespacing convention and the caller should re-seed).
    """
    if sys.platform != "win32":  # pragma: no cover - win32 only
        raise KeystoreUnavailableError(
            "Windows Credential Manager is only available on win32."
        )

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

    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    except OSError as exc:
        raise KeystoreUnavailableError(
            f"Failed to load advapi32.dll: {exc}"
        ) from exc

    cred_read = advapi32.CredReadW
    cred_read.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_Credential)),
    ]
    cred_read.restype = wintypes.BOOL

    cred_free = advapi32.CredFree
    cred_free.argtypes = [ctypes.c_void_p]
    cred_free.restype = None

    target = _windows_target(service, username)
    cred_ptr = ctypes.POINTER(_Credential)()
    ok = cred_read(
        target,
        _WIN32_CRED_TYPE_GENERIC,
        0,
        ctypes.byref(cred_ptr),
    )
    if not ok:
        err = ctypes.get_last_error()
        if err == _WIN32_ERROR_NOT_FOUND:
            return None
        raise KeystoreUnavailableError(
            f"CredReadW failed with Win32 error code {err}."
        )

    try:
        cred = cred_ptr.contents
        stored_user = cred.UserName or ""
        if stored_user != username:
            # Composite TargetName matched but UserName mismatched
            # — should not happen if seeded via the documented
            # seed-command.  Treat as a misconfiguration that the
            # user fixes by re-seeding rather than silently
            # returning the wrong account's password.
            raise KeystoreUnavailableError(
                f"Credential at {target!r} stores UserName "
                f"{stored_user!r}, expected {username!r}.  Re-seed "
                f"with the documented command."
            )
        size = cred.CredentialBlobSize
        if size == 0:
            return ""
        blob = ctypes.string_at(cred.CredentialBlob, size)
        # cmdkey and the Credential Manager UI both write the
        # password as UTF-16-LE.  Strip any single trailing NUL
        # the writer may have included.
        text = blob.decode("utf-16-le")
        return text.rstrip("\x00")
    finally:
        cred_free(cred_ptr)


def _windows_target(service: str, username: str) -> str:
    """Compose the Win32 Credential Manager TargetName.

    Args:
        service: Mail endpoint.
        username: Login.

    Returns:
        ``jmd-mcp-mail:<service>:<username>`` — the namespace prefix
        keeps jmd-mcp-mail's entries from colliding with unrelated
        ``cmdkey /generic:<host>`` credentials the user may have.
    """
    return f"{_WIN32_TARGET_PREFIX}:{service}:{username}"


def _seed_command(service: str, username: str) -> str:
    """Return a ready-to-paste shell command to seed an item.

    Args:
        service: Keystore service to embed in the command.
        username: Keystore username to embed in the command.

    Returns:
        A platform-appropriate single-line shell command.  The
        password is intentionally *not* embedded — the user is
        expected to be prompted (macOS ``-w`` without value) or
        to fill in the ``<your-password>`` placeholder (Windows,
        Linux).  On Linux the placeholder is unnecessary because
        ``secret-tool store`` prompts tty-interactively.
    """
    if sys.platform == "darwin":
        return (
            f"security add-generic-password "
            f"-s {shlex.quote(service)} "
            f"-a {shlex.quote(username)} -w"
        )
    if sys.platform == "win32":
        target = _windows_target(service, username)
        # cmdkey has no shell-quoting story beyond the surrounding
        # double quotes; service/username from this codebase are
        # mail endpoints (host:port) and addr-spec strings, which
        # never contain " or newline.
        return (
            f'cmdkey /generic:"{target}" '
            f'/user:"{username}" /pass:<your-password>'
        )
    if sys.platform.startswith("linux"):
        return (
            f"secret-tool store --label='jmd-mcp-mail' "
            f"service {shlex.quote(service)} "
            f"username {shlex.quote(username)}"
        )
    return "(unsupported platform — no seed command available)"


def _reset_cache_for_tests() -> None:
    """Clear the in-memory cache.  Intended for unit tests only."""
    _cache.clear()


# security(1) -g writes the password to stderr.  Three observed
# forms (macOS 11..14):
#   password: "ascii-string"
#   password: 0x<HEX>
#   password: 0x<HEX>  "octal-escaped-rendering"
# We prefer the hex form whenever it is present — it is an
# unambiguous byte sequence and round-trips losslessly through
# bytes.fromhex(...).decode("utf-8").  The quoted-string form is
# the fallback for pure-ASCII items, where 'security' omits the
# hex line.
_PASSWORD_HEX_RE = re.compile(r"password:\s*0x([0-9A-Fa-f]+)")
_PASSWORD_STR_RE = re.compile(r'password:\s*"((?:[^"\\]|\\.)*)"')


def _parse_security_g_output(stderr: str) -> str:
    """Extract the password from ``security find-generic-password -g``.

    Args:
        stderr: The full stderr output of the ``security -g`` call.

    Returns:
        The password as a string — decoded from the hex form when
        present (deterministic), or from the quoted string form
        with minimal escape handling.

    Raises:
        KeystoreUnavailableError: If neither form is found.  This
            means the ``security`` output format changed under us
            and the resolver needs an update — not a credential
            problem.
    """
    hex_match = _PASSWORD_HEX_RE.search(stderr)
    if hex_match:
        return bytes.fromhex(hex_match.group(1)).decode("utf-8")
    str_match = _PASSWORD_STR_RE.search(stderr)
    if str_match:
        # Pure-ASCII fallback: undo only the escapes 'security'
        # uses for printable ASCII (backslash and double-quote).
        return (
            str_match.group(1).replace('\\"', '"').replace("\\\\", "\\")
        )
    raise KeystoreUnavailableError(
        "'security -g' output did not contain a recognized "
        f"'password:' line. stderr was:\n{stderr}"
    )
