"""Configuration for jmd-mcp-mail.

Reads mail settings from ~/.config/jmd/mail.jmd.
The account password is retrieved from the OS keyring
(service: jmd-mcp-mail, username: <configured username>).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import keyring
from jmd import jmd_to_dict

_CONFIG_PATH = Path.home() / ".config" / "jmd" / "mail.jmd"
_KEYRING_SERVICE = "jmd-mcp-mail"


@dataclass
class MailConfig:
    """Mail connection parameters for SMTP and IMAP."""

    smtp_host: str
    smtp_port: int
    imap_host: str
    imap_port: int
    username: str
    password: str


def load() -> MailConfig:
    """Load mail configuration from ~/.config/jmd/mail.jmd.

    Returns:
        MailConfig with SMTP/IMAP parameters and password from keyring.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If required fields are missing or password not in keyring.
    """
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Mail config not found: {_CONFIG_PATH}\n"
            "Create ~/.config/jmd/mail.jmd with:\n"
            "  # MailConfig\n"
            "  smtp_host: smtp.example.com\n"
            "  smtp_port: 587\n"
            "  imap_host: imap.example.com\n"
            "  imap_port: 993\n"
            "  username: you@example.com"
        )

    fields = jmd_to_dict(_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(fields, dict):
        raise ValueError("mail.jmd: expected a single document")

    smtp_host = str(fields.get("smtp_host", "")).strip()
    imap_host = str(fields.get("imap_host", "")).strip()
    username = str(fields.get("username", "")).strip()

    if not smtp_host:
        raise ValueError("mail.jmd: 'smtp_host' is required")
    if not imap_host:
        raise ValueError("mail.jmd: 'imap_host' is required")
    if not username:
        raise ValueError("mail.jmd: 'username' is required")

    smtp_port = _int_field(fields, "smtp_port", default=587)
    imap_port = _int_field(fields, "imap_port", default=993)

    password = keyring.get_password(_KEYRING_SERVICE, username)
    if password is None:
        raise ValueError(
            f"No password in keyring for {_KEYRING_SERVICE}/{username}.\n"
            "Store it first via jmd-mcp-keyring:\n"
            f"  write('# Credentials\\nservice: {_KEYRING_SERVICE}"
            f"\\nusername: {username}\\npassword: <your-password>')"
        )

    return MailConfig(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        imap_host=imap_host,
        imap_port=imap_port,
        username=username,
        password=password,
    )


def _int_field(
    fields: dict[str, object], key: str, default: int
) -> int:
    """Parse an integer field from config, falling back to default."""
    raw = fields.get(key, default)
    try:
        return int(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"mail.jmd: '{key}' must be an integer, got {raw!r}"
        ) from exc


def config_path() -> Path:
    """Return the path to the JMD mail config file."""
    return _CONFIG_PATH
