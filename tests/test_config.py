"""Unit tests for mail_mcp.config.

Uses a temporary directory for the config file and mocks the keyring.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mail_mcp import config

_FULL_CONFIG = (
    "# MailConfig\n"
    "smtp_host: smtp.example.com\n"
    "smtp_port: 587\n"
    "imap_host: imap.example.com\n"
    "imap_port: 993\n"
    "username: u@example.com\n"
)


@pytest.fixture(autouse=True)
def override_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redirect _CONFIG_PATH to a temporary directory for each test."""
    monkeypatch.setattr(config, "_CONFIG_PATH", tmp_path / "mail.jmd")


def _write(tmp_path: Path, content: str) -> None:
    """Write content to mail.jmd in tmp_path."""
    (tmp_path / "mail.jmd").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------

def test_load_missing_file() -> None:
    """FileNotFoundError raised when config file is absent."""
    with pytest.raises(FileNotFoundError):
        config.load()


def test_load_missing_smtp_host(tmp_path: Path) -> None:
    """ValueError raised when smtp_host is missing."""
    _write(
        tmp_path,
        "# MailConfig\n"
        "imap_host: imap.example.com\n"
        "username: u@example.com\n",
    )
    with pytest.raises(ValueError, match="smtp_host"):
        config.load()


def test_load_missing_imap_host(tmp_path: Path) -> None:
    """ValueError raised when imap_host is missing."""
    _write(
        tmp_path,
        "# MailConfig\n"
        "smtp_host: smtp.example.com\n"
        "username: u@example.com\n",
    )
    with pytest.raises(ValueError, match="imap_host"):
        config.load()


def test_load_missing_username(tmp_path: Path) -> None:
    """ValueError raised when username is missing."""
    _write(
        tmp_path,
        "# MailConfig\n"
        "smtp_host: smtp.example.com\n"
        "imap_host: imap.example.com\n",
    )
    with pytest.raises(ValueError, match="username"):
        config.load()


def test_load_missing_password(tmp_path: Path) -> None:
    """ValueError raised when keyring has no password for the account."""
    _write(tmp_path, _FULL_CONFIG)
    with patch(
        "mail_mcp.config.keyring.get_password", return_value=None
    ):
        with pytest.raises(ValueError, match="No password"):
            config.load()


def test_load_success(tmp_path: Path) -> None:
    """MailConfig is populated correctly from file and keyring."""
    _write(tmp_path, _FULL_CONFIG)
    with patch(
        "mail_mcp.config.keyring.get_password", return_value="secret"
    ):
        cfg = config.load()
    assert cfg.smtp_host == "smtp.example.com"
    assert cfg.imap_host == "imap.example.com"
    assert cfg.username == "u@example.com"
    assert cfg.password == "secret"
    assert cfg.smtp_port == 587
    assert cfg.imap_port == 993


def test_load_default_ports(tmp_path: Path) -> None:
    """Omitted port fields default to 587 (SMTP) and 993 (IMAP)."""
    _write(
        tmp_path,
        "# MailConfig\n"
        "smtp_host: smtp.example.com\n"
        "imap_host: imap.example.com\n"
        "username: u@example.com\n",
    )
    with patch(
        "mail_mcp.config.keyring.get_password", return_value="pw"
    ):
        cfg = config.load()
    assert cfg.smtp_port == 587
    assert cfg.imap_port == 993
