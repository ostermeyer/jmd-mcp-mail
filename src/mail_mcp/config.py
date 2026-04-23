# SPDX-License-Identifier: Apache-2.0
"""Configuration for jmd-mcp-mail.

Reads mail settings from ~/.config/jmd/mail.jmd.
Supports a single # MailConfig account or a # MailConfig[] list.
Passwords are retrieved from the OS keyring
(service: jmd-mcp-mail, username: <configured username>).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import keyring
from jmd import jmd_to_dict

_CONFIG_PATH = Path.home() / ".config" / "jmd" / "mail.jmd"
_KEYRING_SERVICE = "jmd-mcp-mail"

# Matches 'mailbox: value' at the top level of any JMD document.
_MAILBOX_RE = re.compile(r"^mailbox:\s*(.+)$", re.MULTILINE)


@dataclass
class MailConfig:
    """Mail connection parameters for one account.

    The password is *not* stored in this object.  It is retrieved
    lazily from the OS keyring the first time :attr:`password` is
    accessed, so that operations which do not require an active
    connection (e.g. schema reads, listing accounts) work without
    having the password stored yet.
    """

    name: str
    smtp_host: str
    smtp_port: int
    imap_host: str
    imap_port: int
    username: str

    @property
    def password(self) -> str:
        """Return the password from the keyring (lazy).

        Raises:
            ValueError: If no password is stored under the
                (service, username) pair in the OS keyring.
        """
        pw = keyring.get_password(_KEYRING_SERVICE, self.username)
        if pw is None:
            raise ValueError(
                f"No password in keyring for"
                f" {_KEYRING_SERVICE}/{self.username}.\n"
                "Store it via jmd-mcp-keyring:\n"
                f"  write('# Credentials\\n"
                f"service: {_KEYRING_SERVICE}\\n"
                f"username: {self.username}\\n"
                "password: <your-password>')"
            )
        return pw


def resolve(document: str, cfgs: dict[str, MailConfig]) -> MailConfig:
    """Return the MailConfig named by 'mailbox:' in document, or first.

    Args:
        document: Any JMD document string.
        cfgs: All loaded configs keyed by name.

    Returns:
        The matching MailConfig.

    Raises:
        ValueError: If the named mailbox is not configured.
    """
    m = _MAILBOX_RE.search(document)
    if m:
        name = m.group(1).strip()
        if name not in cfgs:
            raise ValueError(
                f"Unknown mailbox: {name!r}. Configured: {list(cfgs)}"
            )
        return cfgs[name]
    return next(iter(cfgs.values()))


def load() -> dict[str, MailConfig]:
    """Load mail configuration from ~/.config/jmd/mail.jmd.

    Supports both a single # MailConfig and a # MailConfig[] list.
    Field names may be kebab-case (smtp-host) or snake_case (smtp_host).

    Returns:
        Dict mapping account name → MailConfig.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If required fields are missing or password not in keyring.
    """
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Mail config not found: {_CONFIG_PATH}\n"
            "Create ~/.config/jmd/mail.jmd with:\n"
            "  # MailConfig\n"
            "  name: myaccount\n"
            "  smtp-host: smtp.example.com\n"
            "  smtp-port: 587\n"
            "  imap-host: imap.example.com\n"
            "  imap-port: 993\n"
            "  username: you@example.com"
        )

    raw = jmd_to_dict(_CONFIG_PATH.read_text(encoding="utf-8"))

    if isinstance(raw, dict):
        entries = [raw]
    elif isinstance(raw, list):
        entries = [e for e in raw if isinstance(e, dict)]
    else:
        raise ValueError("mail.jmd: expected # MailConfig or # MailConfig[]")

    if not entries:
        raise ValueError("mail.jmd: no accounts configured")

    cfgs: dict[str, MailConfig] = {}
    for fields in entries:
        cfg = _parse_one(fields)
        cfgs[cfg.name] = cfg
    return cfgs


def config_path() -> Path:
    """Return the path to the JMD mail config file."""
    return _CONFIG_PATH


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _field(
    fields: dict[str, object],
    *keys: str,
    default: str = "",
) -> str:
    """Return first matching key value as string, supporting kebab/snake."""
    for key in keys:
        val = fields.get(key)
        if val is not None:
            return str(val).strip()
    return default


def _int_field(
    fields: dict[str, object],
    *keys: str,
    default: int,
) -> int:
    """Parse an integer from first matching key, falling back to default."""
    for key in keys:
        raw = fields.get(key)
        if raw is not None:
            try:
                return int(str(raw))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"mail.jmd: {key!r} must be an integer, got {raw!r}"
                ) from exc
    return default


def _parse_one(fields: dict[str, object]) -> MailConfig:
    """Parse a single MailConfig entry from a dict of fields.

    The password is *not* looked up here — it is resolved lazily
    via :attr:`MailConfig.password` when an actual IMAP/SMTP
    connection is established.  This allows schema reads and
    account listings to work before a password has been stored.
    """
    smtp_host = _field(fields, "smtp-host", "smtp_host")
    imap_host = _field(fields, "imap-host", "imap_host")
    username = _field(fields, "username")
    name = _field(fields, "name") or username

    if not smtp_host:
        raise ValueError("mail.jmd: 'smtp-host' is required")
    if not imap_host:
        raise ValueError("mail.jmd: 'imap-host' is required")
    if not username:
        raise ValueError("mail.jmd: 'username' is required")

    smtp_port = _int_field(
        fields, "smtp-port", "smtp_port", default=587
    )
    imap_port = _int_field(
        fields, "imap-port", "imap_port", default=993
    )

    return MailConfig(
        name=name,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        imap_host=imap_host,
        imap_port=imap_port,
        username=username,
    )
