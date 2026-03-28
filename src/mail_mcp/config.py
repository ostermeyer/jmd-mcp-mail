"""Configuration for jmd-mcp-mail.

Reads SMTP settings from ~/.config/jmd/mail.jmd.
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
class SMTPConfig:
    """SMTP connection parameters."""

    host: str
    port: int
    username: str
    password: str


def load() -> SMTPConfig:
    """Load SMTP configuration from ~/.config/jmd/mail.jmd.

    Returns:
        SMTPConfig with host, port, username, and password.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If required fields are missing or password not in keyring.
    """
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Mail config not found: {_CONFIG_PATH}\n"
            "Create ~/.config/jmd/mail.jmd with:\n"
            "  # SMTPConfig\n"
            "  host: smtp.example.com\n"
            "  port: 587\n"
            "  username: you@example.com"
        )

    fields = jmd_to_dict(_CONFIG_PATH.read_text(encoding="utf-8"))

    host = str(fields.get("host", "")).strip()
    port_raw = fields.get("port", 587)
    username = str(fields.get("username", "")).strip()

    if not host:
        raise ValueError("mail.jmd: 'host' is required")
    if not username:
        raise ValueError("mail.jmd: 'username' is required")

    try:
        port = int(port_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"mail.jmd: 'port' must be an integer, got {port_raw!r}") from exc

    password = keyring.get_password(_KEYRING_SERVICE, username)
    if password is None:
        raise ValueError(
            f"No password in keyring for {_KEYRING_SERVICE}/{username}.\n"
            "Store it first:\n"
            f"  write('# Credentials\\nservice: {_KEYRING_SERVICE}"
            f"\\nusername: {username}\\npassword: <your-password>')"
        )

    return SMTPConfig(host=host, port=port, username=username, password=password)


def config_path() -> Path:
    """Return the path to the JMD mail config file."""
    return _CONFIG_PATH
