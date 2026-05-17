# SPDX-License-Identifier: Apache-2.0
"""Credential resolver for jmd-mcp-mail.

Resolves passwords for ``(service, username)`` pairs from the OS
keystore with a process-lifetime in-memory cache.  macOS is wired
to ``security(1)``; Windows and Linux are stubs that raise
:class:`NotImplementedError` — each gets its own follow-up slice.

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
    """Raised when the platform's keystore CLI is unreachable.

    On macOS this means ``/usr/bin/security`` is missing or the
    binary failed for a reason other than a missing item.
    """


def resolve(service: str, username: str) -> str:
    """Resolve a password for ``(service, username)`` from the keystore.

    First checks the process-lifetime cache; on miss, invokes the
    platform's keystore CLI exactly once and caches the result.

    Args:
        service: Keystore service name.  In jmd-mcp-mail this is
            the mail-server endpoint (e.g. ``smtp.gmail.com``).
        username: Keystore account name.  In jmd-mcp-mail this is
            the SMTP/IMAP login username.

    Returns:
        The stored password.

    Raises:
        CredentialNotFoundError: If no item exists for the pair.
        KeystoreUnavailableError: If the keystore CLI cannot be reached
            or returns an unexpected error.
        NotImplementedError: On platforms not yet supported.
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
        KeystoreUnavailableError: If the platform CLI cannot be reached.
        NotImplementedError: On platforms not yet supported.
    """
    if sys.platform == "darwin":
        return _read_macos(service, username)
    if sys.platform == "win32":
        raise NotImplementedError(
            "Windows keystore support is not yet implemented "
            "(planned in a follow-up slice)."
        )
    if sys.platform.startswith("linux"):
        raise NotImplementedError(
            "Linux keystore support is not yet implemented "
            "(planned in a follow-up slice)."
        )
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


def _seed_command(service: str, username: str) -> str:
    """Return a ready-to-paste shell command to seed an item.

    Args:
        service: Keystore service to embed in the command.
        username: Keystore username to embed in the command.

    Returns:
        A platform-appropriate single-line shell command.  The
        password is intentionally *not* embedded — the user is
        expected to be prompted (macOS ``-w`` without value) or
        to fill in a placeholder (Windows, Linux).
    """
    if sys.platform == "darwin":
        return (
            f"security add-generic-password "
            f"-s {shlex.quote(service)} "
            f"-a {shlex.quote(username)} -w"
        )
    if sys.platform == "win32":
        return (
            f"cmdkey /generic:{service} "
            f"/user:{username} /pass:<your-password>"
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


