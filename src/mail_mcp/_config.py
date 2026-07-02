# SPDX-License-Identifier: Apache-2.0
"""Out-of-reach configuration for jmd-mcp-mail.

A single commented JMD file, ``config.jmd``, inside a config directory
(default ``~/.jmd-mcp-mail/``; override the directory with
``JMD_MCP_MAIL_HOME``). It holds the labelled account list. The LLM never
reads or writes this file: tools address accounts by ``label`` and the
server resolves endpoints/username/auth here — so the ``username``
(personal data) never enters the model context.

**No secrets live here** — passwords/tokens/keys stay in the OS keyring.
The file does hold the ``username`` (and, on the dsgvo branch, more), so
restrictive file permissions are recommended.

The file is the canonical ``# Account[]`` array-of-objects form::

    # Account[]
    - label: ionos
      imap: imap.ionos.de:993
      smtp: smtp.ionos.de:587
      username: andreas@ostermeyer.de
      auth: basic              # basic | oauth2
      broker-client: outlook   # only for auth: oauth2
      from-name: Andreas O.    # optional From-header display name
      drafts-folder: Entwürfe  # optional; else SPECIAL-USE/name lookup
      sent-folder: Gesendet    # optional; else SPECIAL-USE/name lookup
      store-sent: true         # optional; false for Gmail (auto-stores)
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jmd import jmd_to_dict, serialize

from mail_mcp._endpoint import parse_endpoint

_ENV_HOME = "JMD_MCP_MAIL_HOME"
# Legacy location consulted once for best-effort migration.
_ENV_OLD_ACCOUNTS = "JMD_MCP_MAIL_ACCOUNTS_PATH"
_MAX_LABEL_LEN = 64


@dataclass(frozen=True)
class Account:
    """One labelled mail account — routing metadata, no secret.

    Attributes:
        label: Short user-chosen identifier (primary key).
        imap: IMAP endpoint ``host:port``.
        smtp: SMTP endpoint ``host:port``.
        username: SMTP/IMAP login (usually the full email address).
        auth: ``basic`` (keystore password) or ``oauth2`` (sealed token).
        broker_client: For ``oauth2``, the jmd-mcp-oauth2 client name.
        from_name: Optional default display name for the From header.
        drafts_folder: Optional explicit Drafts folder path; empty
            means SPECIAL-USE / well-known-name discovery.
        sent_folder: Optional explicit Sent folder path; empty means
            SPECIAL-USE / well-known-name discovery.
        store_sent: Store a copy of sent mail in the Sent folder.
            Set ``false`` for providers that auto-store (Gmail).
    """

    label: str
    imap: str
    smtp: str
    username: str
    auth: str = "basic"
    broker_client: str = ""
    from_name: str = ""
    drafts_folder: str = ""
    sent_folder: str = ""
    store_sent: bool = True


def config_dir() -> Path:
    """Return the config directory (``JMD_MCP_MAIL_HOME`` or the default)."""
    override = os.environ.get(_ENV_HOME)
    if override:
        return Path(override)
    return Path.home() / ".jmd-mcp-mail"


def config_file() -> Path:
    """Return the path to ``config.jmd`` within the config directory."""
    return config_dir() / "config.jmd"


def _validate(account: Account) -> None:
    """Raise :class:`ValueError` if any field of *account* is invalid."""
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
    if account.auth not in ("basic", "oauth2"):
        raise ValueError(
            f"Account auth must be 'basic' or 'oauth2', got {account.auth!r}"
        )
    if account.auth == "oauth2" and not account.broker_client.strip():
        raise ValueError("oauth2 accounts require a 'broker-client' field")
    parse_endpoint(account.imap)
    parse_endpoint(account.smtp)


def _as_bool(value: Any, *, default: bool) -> bool:
    """Coerce a JMD scalar (bool or string) to a bool.

    Raises:
        ValueError: If the value is not recognizably boolean.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    raise ValueError(f"expected a boolean, got {value!r}")


def _account_from_dict(item: dict[str, Any]) -> Account:
    """Build and validate an Account from a parsed config entry."""
    try:
        account = Account(
            label=str(item["label"]),
            imap=str(item["imap"]),
            smtp=str(item["smtp"]),
            username=str(item["username"]),
            auth=str(item.get("auth", "basic")),
            broker_client=str(item.get("broker-client", "")),
            from_name=str(item.get("from-name", "")),
            drafts_folder=str(item.get("drafts-folder", "")),
            sent_folder=str(item.get("sent-folder", "")),
            store_sent=_as_bool(item.get("store-sent"), default=True),
        )
    except KeyError as exc:
        raise ValueError(
            f"Account missing required field {exc}"
        ) from exc
    _validate(account)
    return account


def load() -> list[Account]:
    """Load all accounts from ``config.jmd`` (migrating a legacy file once).

    Returns:
        Accounts in file order; empty list when nothing is configured.

    Raises:
        ValueError: If the file exists but is malformed.
        OSError: On I/O failures.
    """
    path = config_file()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.strip():
        # Absent *or* empty (a pre-created blank file) → attempt the
        # one-time migration from a legacy accounts.jmd, then re-read.
        _migrate_legacy(path)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.strip():
        return []
    try:
        data: Any = jmd_to_dict(text)
    except Exception as exc:  # noqa: BLE001 — opaque parser errors
        raise ValueError(
            f"config.jmd at {path} is malformed: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise ValueError(
            f"config.jmd at {path} must be a # Account[] document "
            f"(got {type(data).__name__})"
        )
    accounts: list[Account] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Account #{i} in {path} is not an object")
        accounts.append(_account_from_dict(item))
    return accounts


def resolve(label: str) -> Account | None:
    """Return the account with *label*, or ``None`` if there is none."""
    for account in load():
        if account.label == label:
            return account
    return None


def projection(account: Account) -> dict[str, str]:
    """LLM-facing projection: label (+ auth/broker). No address/endpoints."""
    d: dict[str, str] = {"label": account.label}
    if account.auth != "basic":
        d["auth"] = account.auth
    if account.broker_client:
        d["broker-client"] = account.broker_client
    return d


# ---------------------------------------------------------------------------
# One-time migration from the pre-config-dir accounts.jmd
# ---------------------------------------------------------------------------


def _legacy_path() -> Path:
    """The legacy ``accounts.jmd`` location consulted for migration."""
    override = os.environ.get(_ENV_OLD_ACCOUNTS)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(
            Path.home() / "AppData" / "Roaming"
        )
        return Path(base) / "jmd-mcp-mail" / "accounts.jmd"
    if sys.platform == "darwin":
        return (
            Path.home() / "Library" / "Application Support"
            / "jmd-mcp-mail" / "accounts.jmd"
        )
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "jmd-mcp-mail" / "accounts.jmd"


def _migrate_legacy(target: Path) -> None:
    """Best-effort: convert a legacy accounts.jmd into config.jmd.

    Never raises — migration failure must not block startup; the user can
    always author ``config.jmd`` by hand.
    """
    try:
        legacy = _legacy_path()
        if not legacy.exists():
            return
        data = jmd_to_dict(legacy.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return
        payload: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            entry: dict[str, Any] = {
                "label": str(item.get("label", "")),
                "imap": str(item.get("imap_service", item.get("imap", ""))),
                "smtp": str(item.get("smtp_service", item.get("smtp", ""))),
                "username": str(item.get("username", "")),
            }
            if str(item.get("auth", "basic")) != "basic":
                entry["auth"] = str(item["auth"])
            if item.get("broker-client"):
                entry["broker-client"] = str(item["broker-client"])
            payload.append(entry)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            serialize(payload, label="Account"), encoding="utf-8"
        )
        print(
            f"jmd-mcp-mail: migrated {len(payload)} account(s) from "
            f"{legacy} to {target}",
            file=sys.stderr,
        )
    except Exception:  # noqa: BLE001 — migration is strictly best-effort
        return
