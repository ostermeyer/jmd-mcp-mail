# SPDX-License-Identifier: Apache-2.0
r"""Account registry — labelled (service, username) triples, no passwords.

Stores a flat list of :class:`Account` records on disk so the LLM
(and the user via the LLM) can refer to a mail account by a short
human-friendly label (``ionos``, ``gmail-work``) instead of typing
the full ``(service, username)`` pair every call.

Passwords are explicitly **not** stored here — they live in the OS
keystore.  This registry holds only the metadata needed to build the
``(service, username)`` keystore lookup at tool-call time.  The
threat model is unchanged: a prompt-injected tool result can read
the labels and endpoints, but never a password.

Storage path:

* Windows  ``%APPDATA%\\jmd-mcp-mail\\accounts.jmd``
* macOS    ``~/Library/Application Support/jmd-mcp-mail/accounts.jmd``
* Linux    ``$XDG_CONFIG_HOME/jmd-mcp-mail/accounts.jmd``
  (default: ``~/.config/jmd-mcp-mail/accounts.jmd``)

The environment variable ``JMD_MCP_MAIL_ACCOUNTS_PATH``, if set,
overrides all of the above.  Tests use this seam; power users can
also use it to keep the registry in a synced folder.

The file uses JMD's canonical ``# Account[]`` array-of-objects form
— the same shape the MCP tool exposes.  Writes are atomic
(``write to .tmp`` + ``os.replace``).
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jmd import jmd_mode, jmd_to_dict, serialize

from mail_mcp._endpoint import parse_endpoint

# JMD label for one Account record.  Plural shape is `# Account[]`.
_LABEL = "Account"

# Override key consulted by :func:`_config_path` before falling back
# to the platform default.  Set in tests; honoured for power users.
_ENV_OVERRIDE = "JMD_MCP_MAIL_ACCOUNTS_PATH"

# Reasonable upper bound on label length — keeps the surface tidy
# without blocking any plausible user naming.
_MAX_LABEL_LEN = 64


@dataclass(frozen=True)
class Account:
    """One labelled mail account — endpoints plus username, no secret.

    Attributes:
        label: Short user-chosen identifier (e.g. ``ionos``,
            ``gmail-work``).  Primary key of the registry.
        imap_service: IMAP endpoint as ``host:port``
            (e.g. ``imap.ionos.de:993``).
        smtp_service: SMTP endpoint as ``host:port``
            (e.g. ``smtp.ionos.de:587``).
        username: SMTP/IMAP login (usually the full email address).
        auth: ``basic`` (password from the OS keystore, default) or
            ``oauth2`` (a sealed access token fetched from the token
            broker per call).
        broker_client: For ``oauth2`` accounts, the jmd-mcp-oauth2
            client name to fetch tokens from (e.g. ``outlook``).
        pseudonymize: Whether the read path pseudonymises email
            identities before they reach the LLM (GDPR data
            minimisation). Defaults to ``True`` (privacy by default,
            Art. 25(2)); set ``False`` to opt out per account.
        pseudonymize_domain: Whether pseudonyms carry a domain token
            so "same company" relationships survive. Opt-in; defaults
            to ``False``.
    """

    label: str
    imap_service: str
    smtp_service: str
    username: str
    auth: str = "basic"
    broker_client: str = ""
    pseudonymize: bool = True
    pseudonymize_domain: bool = False

    def as_jmd_dict(self) -> dict[str, object]:
        """Map this record to the dict shape ``jmd.serialize`` consumes.

        ``auth``, ``broker-client`` and the pseudonymisation flags are
        emitted only when they differ from their defaults, so a plain
        Basic-Auth account keeps its original four-field shape.
        """
        d: dict[str, object] = {
            "label": self.label,
            "imap_service": self.imap_service,
            "smtp_service": self.smtp_service,
            "username": self.username,
        }
        if self.auth != "basic":
            d["auth"] = self.auth
        if self.broker_client:
            d["broker-client"] = self.broker_client
        if not self.pseudonymize:
            d["pseudonymize"] = False
        if self.pseudonymize_domain:
            d["pseudonymize-domain"] = True
        return d


def _config_path() -> Path:
    """Resolve the registry path for the current platform.

    Honours ``$JMD_MCP_MAIL_ACCOUNTS_PATH`` first (test seam / power
    user override), then falls back to the OS-conventional location.
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(
            Path.home() / "AppData" / "Roaming"
        )
        return Path(base) / "jmd-mcp-mail" / "accounts.jmd"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library" / "Application Support"
            / "jmd-mcp-mail" / "accounts.jmd"
        )
    base = os.environ.get("XDG_CONFIG_HOME") or str(
        Path.home() / ".config"
    )
    return Path(base) / "jmd-mcp-mail" / "accounts.jmd"


def _as_bool(value: object, default: bool) -> bool:
    """Coerce a JMD scalar to bool, falling back to *default* on absence.

    Accepts native booleans and the usual truthy string spellings; an
    absent value (``None``) yields *default*.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _validate(account: Account) -> None:
    """Raise :class:`ValueError` if any field of ``account`` is invalid.

    Validates label shape (non-empty, no whitespace, length bound),
    a non-empty username, and that both service strings parse as
    proper ``host:port`` endpoints via the production endpoint
    parser (so the registry can never hold something the real
    connection layer would reject).
    """
    if not account.label or not account.label.strip():
        raise ValueError("Account label must not be empty")
    if any(c.isspace() for c in account.label):
        raise ValueError(
            f"Account label {account.label!r} must not contain whitespace"
        )
    if len(account.label) > _MAX_LABEL_LEN:
        raise ValueError(
            f"Account label {account.label!r} exceeds "
            f"{_MAX_LABEL_LEN} characters"
        )
    if not account.username.strip():
        raise ValueError("Account username must not be empty")
    # Reuse the production endpoint parser — keeps validation rules
    # in one place; whatever the real connection layer accepts, the
    # registry accepts.
    if account.auth not in ("basic", "oauth2"):
        raise ValueError(
            f"Account auth must be 'basic' or 'oauth2', "
            f"got {account.auth!r}"
        )
    if account.auth == "oauth2" and not account.broker_client.strip():
        raise ValueError(
            "oauth2 accounts require a 'broker-client' field"
        )
    parse_endpoint(account.imap_service)
    parse_endpoint(account.smtp_service)


def load() -> list[Account]:
    """Load all accounts from disk; empty list if the file is absent.

    Returns:
        Accounts in the order they appear on disk (typically
        label-sorted, since :func:`save` sorts before writing).

    Raises:
        ValueError: If the file exists but is malformed or missing
            required fields.
        OSError: On I/O failures (permissions, disk error).
    """
    path = _config_path()
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        data: Any = jmd_to_dict(text)
    except Exception as exc:  # noqa: BLE001 — opaque parser errors
        raise ValueError(
            f"Account registry at {path} is malformed: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise ValueError(
            f"Account registry at {path} must be a # Account[] "
            f"document (got {type(data).__name__})"
        )
    accounts: list[Account] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(
                f"Account #{i} in {path} is not an object"
            )
        try:
            accounts.append(
                Account(
                    label=str(item["label"]),
                    imap_service=str(item["imap_service"]),
                    smtp_service=str(item["smtp_service"]),
                    username=str(item["username"]),
                    auth=str(item.get("auth", "basic")),
                    broker_client=str(item.get("broker-client", "")),
                    pseudonymize=_as_bool(
                        item.get("pseudonymize"), True
                    ),
                    pseudonymize_domain=_as_bool(
                        item.get("pseudonymize-domain"), False
                    ),
                )
            )
        except KeyError as exc:
            raise ValueError(
                f"Account #{i} in {path} is missing field {exc}"
            ) from exc
    return accounts


def save(accounts: Iterable[Account]) -> None:
    """Atomically write the full account list to disk.

    Sorts by label first so the on-disk ordering is deterministic
    (helpful for human inspection and diff review).  Writes via a
    sibling ``.tmp`` file and ``os.replace`` so a crash mid-write
    never leaves the registry truncated.

    Args:
        accounts: Iterable of :class:`Account` records.

    Raises:
        OSError: On I/O failures.
    """
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        a.as_jmd_dict()
        for a in sorted(accounts, key=lambda a: a.label)
    ]
    body = serialize(payload, label=_LABEL)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


def upsert(account: Account) -> Account:
    """Insert a new account or replace the existing one with the same label.

    Args:
        account: The account to store.

    Returns:
        The stored account (echoed back for convenience).

    Raises:
        ValueError: If validation fails.
        OSError: On write failures.
    """
    _validate(account)
    existing = [a for a in load() if a.label != account.label]
    existing.append(account)
    save(existing)
    return account


def delete_by_label(label: str) -> Account | None:
    """Remove the account with ``label``.

    Args:
        label: The label to remove.

    Returns:
        The removed account, or ``None`` if no account had that
        label.
    """
    accounts = load()
    kept = [a for a in accounts if a.label != label]
    if len(kept) == len(accounts):
        return None
    removed = next(a for a in accounts if a.label == label)
    save(kept)
    return removed


# ---------------------------------------------------------------------------
# JMD-mode dispatcher (entry point for the `accounts` MCP tool)
# ---------------------------------------------------------------------------


def handle(document: str) -> str:
    """Dispatch by JMD mode and return a JMD response document.

    Supported modes:

    * ``#! Account`` → the Account schema.
    * ``# Account[]`` → the full registry (list, label-sorted).
    * ``# Account { label, imap_service, smtp_service, username }``
      → upsert by label.
    * ``#- Account { label }`` → delete by label.

    Query mode (``#? Account``) is intentionally not supported:
    the registry is small enough that the LLM can list and filter
    in one round-trip.

    Args:
        document: JMD document selecting the operation.

    Returns:
        A JMD document — either the result on success, or a
        ``# Error`` document on failure.
    """
    # Local import to dodge a schemas → accounts circular import.
    from mail_mcp.schemas import ACCOUNT, PUBLIC_KEY

    try:
        mode = jmd_mode(document)
    except Exception as exc:  # noqa: BLE001 — opaque parser errors
        return _error(
            400, "bad_request", f"unparseable document: {exc}"
        )

    try:
        if mode == "schema":
            return PUBLIC_KEY if _is_pubkey(document) else ACCOUNT
        if mode == "data":
            if _is_pubkey(document):
                return _handle_public_key()
            return _handle_data(document)
        if mode == "delete":
            return _handle_delete(document)
        if mode == "query":
            return _error(
                400, "unsupported_mode",
                "Query mode (#? Account) is not supported for the "
                "account registry; use # Account[] to list all "
                "and filter on the caller side.",
            )
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))
    except OSError as exc:
        return _error(500, "io_error", str(exc))

    return _error(400, "bad_request", f"unsupported mode: {mode!r}")


def _handle_data(document: str) -> str:
    """data-mode dispatch: list-form vs object-form upsert."""
    data = jmd_to_dict(document)
    if isinstance(data, list):
        accounts = load()
        return serialize(
            [a.as_jmd_dict() for a in accounts],
            label=_LABEL,
        )
    if isinstance(data, dict):
        try:
            account = Account(
                label=str(data["label"]),
                imap_service=str(data["imap_service"]),
                smtp_service=str(data["smtp_service"]),
                username=str(data["username"]),
                auth=str(data.get("auth", "basic")),
                broker_client=str(data.get("broker-client", "")),
                pseudonymize=_as_bool(data.get("pseudonymize"), True),
                pseudonymize_domain=_as_bool(
                    data.get("pseudonymize-domain"), False
                ),
            )
        except KeyError as exc:
            return _error(
                400, "missing_fields",
                f"Account upsert requires label, imap_service, "
                f"smtp_service, username (missing {exc})",
            )
        upsert(account)
        return serialize(account.as_jmd_dict(), label=_LABEL)
    return _error(
        400, "bad_request",
        f"Expected # Account or # Account[] "
        f"(got {type(data).__name__})",
    )


def _handle_delete(document: str) -> str:
    """delete-mode dispatch: remove by label."""
    data = jmd_to_dict(document)
    if not isinstance(data, dict):
        return _error(
            400, "bad_request",
            "#- Account expects an object with a label field",
        )
    label = str(data.get("label", "")).strip()
    if not label:
        return _error(
            400, "missing_fields",
            "#- Account requires a label field",
        )
    removed = delete_by_label(label)
    if removed is None:
        return _error(
            404, "not_found",
            f"No account with label {label!r}",
        )
    return serialize(removed.as_jmd_dict(), label=_LABEL)


def _error(status: int, code: str, message: str) -> str:
    """Serialize a JMD ``# Error`` document."""
    return serialize(
        {"status": status, "code": code, "message": message},
        label="Error",
    )


def find_by_endpoint(service: str, username: str) -> Account | None:
    """Return a registered account matching *service* + *username*.

    Matches on either the IMAP or SMTP endpoint.  Used to give an
    OAuth2-aware error when a Basic-Auth credential is missing.
    Best-effort: swallows registry load errors.
    """
    try:
        accounts = load()
    except (ValueError, OSError):
        return None
    for a in accounts:
        if a.username == username and service in (
            a.imap_service, a.smtp_service,
        ):
            return a
    return None


def _root_label(document: str) -> str:
    """Extract the root label from a JMD document heading."""
    for line in document.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#!?- ").split("[", 1)[0].strip()
    return ""


def _is_pubkey(document: str) -> bool:
    """Whether *document*'s root label is ``PublicKey``."""
    return _root_label(document).lower() == "publickey"


def _handle_public_key() -> str:
    """Return this server's X25519 public key for the token broker."""
    from mail_mcp import _sealing

    return serialize({"key": _sealing.public_key()}, label="PublicKey")
