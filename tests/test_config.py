# SPDX-License-Identifier: Apache-2.0
"""Unit tests for _config.py — the out-of-reach config directory."""
from __future__ import annotations

from pathlib import Path

import pytest

from mail_mcp import _config
from mail_mcp._config import Account

_BASIC = """\
# Account[]
- label: ionos
  imap: imap.ionos.de:993
  smtp: smtp.ionos.de:587
  username: andreas@ostermeyer.de
"""

_OAUTH = """\
# Account[]
- label: outlook
  imap: outlook.office365.com:993
  smtp: smtp-mail.outlook.com:587
  username: me@live.de
  auth: oauth2
  broker-client: outlook
"""


def _write_config(text: str) -> Path:
    """Write *text* to the (isolated) config.jmd and return its path."""
    path = _config.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_config_dir_honours_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """JMD_MCP_MAIL_HOME wins over the platform default."""
    monkeypatch.setenv("JMD_MCP_MAIL_HOME", str(tmp_path / "x"))
    assert _config.config_dir() == tmp_path / "x"
    assert _config.config_file() == tmp_path / "x" / "config.jmd"


def test_load_empty_when_absent() -> None:
    """No config.jmd → empty list, no exception."""
    assert _config.load() == []


def test_load_and_resolve_basic() -> None:
    """A basic account round-trips and resolves by label."""
    _write_config(_BASIC)
    accounts = _config.load()
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.label == "ionos"
    assert acc.imap == "imap.ionos.de:993"
    assert acc.username == "andreas@ostermeyer.de"
    assert acc.auth == "basic"
    assert _config.resolve("ionos") == acc
    assert _config.resolve("nope") is None


def test_load_oauth_fields() -> None:
    """An oauth2 account carries auth and broker-client."""
    _write_config(_OAUTH)
    acc = _config.resolve("outlook")
    assert acc is not None
    assert acc.auth == "oauth2"
    assert acc.broker_client == "outlook"


def test_from_name_optional() -> None:
    """An optional from-name is parsed when present."""
    _write_config(_BASIC.rstrip() + "\n  from-name: Andreas O.\n")
    acc = _config.resolve("ionos")
    assert acc is not None
    assert acc.from_name == "Andreas O."


def test_invalid_endpoint_rejected() -> None:
    """A service string without :port is refused at load time."""
    _write_config(
        "# Account[]\n"
        "- label: x\n"
        "  imap: imap.x\n"
        "  smtp: smtp.x:587\n"
        "  username: a@b\n"
    )
    with pytest.raises(ValueError):
        _config.load()


def test_oauth_without_broker_rejected() -> None:
    """An oauth2 account without a broker-client is refused."""
    _write_config(
        "# Account[]\n"
        "- label: x\n"
        "  imap: imap.x:993\n"
        "  smtp: smtp.x:587\n"
        "  username: a@b\n"
        "  auth: oauth2\n"
    )
    with pytest.raises(ValueError, match="broker-client"):
        _config.load()


def test_projection_hides_username_and_endpoints() -> None:
    """The LLM-facing projection exposes only label/auth/broker."""
    proj = _config.projection(Account(
        "ionos", "imap.ionos.de:993", "smtp.ionos.de:587",
        "secret@ionos.de",
    ))
    assert proj == {"label": "ionos"}
    assert "secret@ionos.de" not in str(proj)


def test_migration_from_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A legacy accounts.jmd is converted into config.jmd on first load."""
    legacy = tmp_path / "legacy.jmd"
    legacy.write_text(
        "# Account[]\n"
        "- label: ionos\n"
        "  imap_service: imap.ionos.de:993\n"
        "  smtp_service: smtp.ionos.de:587\n"
        "  username: a@b.de\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JMD_MCP_MAIL_ACCOUNTS_PATH", str(legacy))
    assert not _config.config_file().exists()
    accounts = _config.load()
    assert len(accounts) == 1
    assert accounts[0].imap == "imap.ionos.de:993"
    assert _config.config_file().exists()


def test_migration_when_config_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A pre-created blank config.jmd still triggers legacy migration."""
    legacy = tmp_path / "legacy.jmd"
    legacy.write_text(
        "# Account[]\n"
        "- label: ionos\n"
        "  imap_service: imap.ionos.de:993\n"
        "  smtp_service: smtp.ionos.de:587\n"
        "  username: a@b.de\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JMD_MCP_MAIL_ACCOUNTS_PATH", str(legacy))
    # Pre-create an empty config.jmd (the footgun we hardened against).
    blank = _config.config_file()
    blank.parent.mkdir(parents=True, exist_ok=True)
    blank.write_text("", encoding="utf-8")
    accounts = _config.load()
    assert len(accounts) == 1
    assert accounts[0].label == "ionos"
