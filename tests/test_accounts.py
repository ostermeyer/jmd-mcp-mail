# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``mail_mcp.accounts`` — the labelled-account registry."""
from __future__ import annotations

from pathlib import Path

import pytest

from mail_mcp import accounts
from mail_mcp.accounts import Account


@pytest.fixture(autouse=True)
def _isolated_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the registry at a temp directory for every test.

    Returns the registry path so tests that want to inspect the
    on-disk file directly can use it.
    """
    target = tmp_path / "accounts.jmd"
    monkeypatch.setenv("JMD_MCP_MAIL_ACCOUNTS_PATH", str(target))
    return target


# ---------------------------------------------------------------------------
# Storage round-trip
# ---------------------------------------------------------------------------


def test_load_returns_empty_when_file_absent() -> None:
    """No file on disk → empty list, no exception."""
    assert accounts.load() == []


def test_upsert_persists_and_load_returns_it() -> None:
    """Upsert one account, reload via load(), get it back."""
    acc = Account(
        label="ionos",
        imap_service="imap.ionos.de:993",
        smtp_service="smtp.ionos.de:587",
        username="andreas@ostermeyer.de",
    )
    accounts.upsert(acc)
    loaded = accounts.load()
    assert loaded == [acc]


def test_upsert_replaces_existing_by_label() -> None:
    """Same label → replace, not duplicate."""
    accounts.upsert(Account(
        label="work",
        imap_service="imap.example.com:993",
        smtp_service="smtp.example.com:587",
        username="alice@example.com",
    ))
    accounts.upsert(Account(
        label="work",
        imap_service="imap.example.com:993",
        smtp_service="smtp.example.com:587",
        username="alice.new@example.com",
    ))
    loaded = accounts.load()
    assert len(loaded) == 1
    assert loaded[0].username == "alice.new@example.com"


def test_save_is_label_sorted() -> None:
    """save() writes accounts in label order for stable diffs."""
    for label in ("zulu", "alpha", "mike"):
        accounts.upsert(Account(
            label=label,
            imap_service="imap.example.com:993",
            smtp_service="smtp.example.com:587",
            username=f"{label}@example.com",
        ))
    loaded = accounts.load()
    assert [a.label for a in loaded] == ["alpha", "mike", "zulu"]


def test_delete_by_label_removes_and_returns() -> None:
    """Delete returns the removed account; subsequent load() loses it."""
    acc = Account(
        label="ionos",
        imap_service="imap.ionos.de:993",
        smtp_service="smtp.ionos.de:587",
        username="andreas@ostermeyer.de",
    )
    accounts.upsert(acc)
    removed = accounts.delete_by_label("ionos")
    assert removed == acc
    assert accounts.load() == []


def test_delete_by_label_missing_returns_none() -> None:
    """Deleting an unknown label is a no-op that returns None."""
    assert accounts.delete_by_label("nope") is None


def test_atomic_write_leaves_no_tmp_file(
    _isolated_registry: Path,
) -> None:
    """Successful upsert never leaves a *.tmp sibling behind."""
    accounts.upsert(Account(
        label="ionos",
        imap_service="imap.ionos.de:993",
        smtp_service="smtp.ionos.de:587",
        username="andreas@ostermeyer.de",
    ))
    tmp = _isolated_registry.with_suffix(
        _isolated_registry.suffix + ".tmp"
    )
    assert _isolated_registry.exists()
    assert not tmp.exists()


def test_unicode_label_and_username_round_trip() -> None:
    """Non-ASCII labels and usernames survive write/read."""
    acc = Account(
        label="büro-österreich",
        imap_service="imap.example.com:993",
        smtp_service="smtp.example.com:587",
        username="hänsel@grünwald.example",
    )
    accounts.upsert(acc)
    assert accounts.load() == [acc]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_label",
    ["", "   ", "has space", "a" * 65, "tab\there"],
)
def test_invalid_label_rejected(bad_label: str) -> None:
    """Empty / whitespace-containing / oversize labels are refused."""
    with pytest.raises(ValueError):
        accounts.upsert(Account(
            label=bad_label,
            imap_service="imap.example.com:993",
            smtp_service="smtp.example.com:587",
            username="a@b.example",
        ))


def test_invalid_endpoint_rejected() -> None:
    """A service string without :port is refused at upsert time."""
    with pytest.raises(ValueError):
        accounts.upsert(Account(
            label="bad",
            imap_service="imap.example.com",  # no port
            smtp_service="smtp.example.com:587",
            username="a@b.example",
        ))


def test_empty_username_rejected() -> None:
    """An empty username is refused."""
    with pytest.raises(ValueError):
        accounts.upsert(Account(
            label="bad",
            imap_service="imap.example.com:993",
            smtp_service="smtp.example.com:587",
            username="   ",
        ))


# ---------------------------------------------------------------------------
# JMD-mode dispatcher (handle)
# ---------------------------------------------------------------------------


def test_handle_schema_returns_account_schema() -> None:
    """`#! Account` returns the Account schema verbatim."""
    from mail_mcp.schemas import ACCOUNT

    result = accounts.handle("#! Account\n")
    assert result == ACCOUNT


def test_handle_list_empty_registry() -> None:
    """`# Account[]` against empty registry returns an empty array."""
    result = accounts.handle("# Account[]\n")
    assert "# Account[]" in result


def test_handle_upsert_then_list() -> None:
    """Round-trip: upsert via JMD doc, list returns it."""
    doc = (
        "# Account\n"
        "label: ionos\n"
        "imap_service: imap.ionos.de:993\n"
        "smtp_service: smtp.ionos.de:587\n"
        "username: andreas@ostermeyer.de\n"
    )
    upsert_result = accounts.handle(doc)
    assert "ionos" in upsert_result
    assert "andreas@ostermeyer.de" in upsert_result

    listing = accounts.handle("# Account[]\n")
    assert "ionos" in listing
    assert "imap.ionos.de:993" in listing
    assert "andreas@ostermeyer.de" in listing


def test_handle_delete_returns_removed_account() -> None:
    """`#- Account { label }` removes and echoes the deleted record."""
    accounts.upsert(Account(
        label="ionos",
        imap_service="imap.ionos.de:993",
        smtp_service="smtp.ionos.de:587",
        username="andreas@ostermeyer.de",
    ))
    result = accounts.handle("#- Account\nlabel: ionos\n")
    assert "ionos" in result
    assert accounts.load() == []


def test_handle_delete_missing_label_404() -> None:
    """Deleting an unknown label returns a 404 error document."""
    result = accounts.handle("#- Account\nlabel: nope\n")
    assert "status: 404" in result
    assert "code: not_found" in result


def test_handle_upsert_missing_field_400() -> None:
    """Upsert without a required field returns 400 missing_fields."""
    doc = (
        "# Account\n"
        "label: bad\n"
        "imap_service: imap.example.com:993\n"
        # smtp_service intentionally missing
        "username: a@b.example\n"
    )
    result = accounts.handle(doc)
    assert "status: 400" in result
    assert "missing_fields" in result


def test_handle_query_mode_unsupported() -> None:
    """Query mode (`#? Account`) is explicitly rejected with a hint."""
    result = accounts.handle("#? Account\n")
    assert "status: 400" in result
    assert "unsupported_mode" in result


def test_handle_unparseable_document_400() -> None:
    """Garbage in → 400 bad_request, not an uncaught exception."""
    result = accounts.handle("this is not jmd at all")
    # Any well-formed error document is acceptable here — the
    # contract is "no exception escapes handle()".
    assert "# Error" in result


# ---------------------------------------------------------------------------
# Config-path resolution
# ---------------------------------------------------------------------------


def test_config_path_honours_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """JMD_MCP_MAIL_ACCOUNTS_PATH wins over the platform default."""
    custom = tmp_path / "custom" / "elsewhere.jmd"
    monkeypatch.setenv(
        "JMD_MCP_MAIL_ACCOUNTS_PATH", str(custom)
    )
    assert accounts._config_path() == custom
