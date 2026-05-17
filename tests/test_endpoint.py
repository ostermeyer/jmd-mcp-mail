# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``mail_mcp._endpoint``."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from mail_mcp import _credentials
from mail_mcp._endpoint import (
    ConnectionInfo,
    Endpoint,
    TlsMode,
    parse_endpoint,
)


@pytest.fixture(autouse=True)
def _reset_credentials_cache() -> None:
    """Keep credential-cache state isolated between tests."""
    _credentials._reset_cache_for_tests()


# ---------------------------------------------------------------------------
# parse_endpoint: well-formed input
# ---------------------------------------------------------------------------


def test_smtp_submission_port_starttls() -> None:
    """Port 587 → STARTTLS (modern submission)."""
    ep = parse_endpoint("smtp.gmail.com:587")
    assert ep == Endpoint("smtp.gmail.com", 587, TlsMode.STARTTLS)


def test_smtps_port_implicit_tls() -> None:
    """Port 465 → implicit TLS (SMTPS)."""
    ep = parse_endpoint("smtp.gmail.com:465")
    assert ep == Endpoint("smtp.gmail.com", 465, TlsMode.IMPLICIT)


def test_imaps_port_implicit_tls() -> None:
    """Port 993 → implicit TLS (IMAPS)."""
    ep = parse_endpoint("imap.gmail.com:993")
    assert ep == Endpoint("imap.gmail.com", 993, TlsMode.IMPLICIT)


def test_imap_starttls_port() -> None:
    """Port 143 → STARTTLS (plain IMAP)."""
    ep = parse_endpoint("imap.example.com:143")
    assert ep == Endpoint("imap.example.com", 143, TlsMode.STARTTLS)


def test_unknown_port_defaults_to_starttls() -> None:
    """Ports outside the convention table default to STARTTLS."""
    ep = parse_endpoint("mail.example.com:2525")
    assert ep == Endpoint("mail.example.com", 2525, TlsMode.STARTTLS)


def test_ipv6_literal_in_brackets() -> None:
    """IPv6 literal ``[::1]:587`` parses correctly."""
    ep = parse_endpoint("[::1]:587")
    assert ep == Endpoint("::1", 587, TlsMode.STARTTLS)


# ---------------------------------------------------------------------------
# parse_endpoint: malformed input
# ---------------------------------------------------------------------------


def test_empty_string_rejected() -> None:
    """Empty service string raises ``ValueError``."""
    with pytest.raises(ValueError, match="empty"):
        parse_endpoint("")


def test_missing_port_rejected() -> None:
    """Service without ``:port`` raises ``ValueError``."""
    with pytest.raises(ValueError, match="missing ':port'"):
        parse_endpoint("smtp.gmail.com")


def test_non_integer_port_rejected() -> None:
    """Non-numeric port raises ``ValueError``."""
    with pytest.raises(ValueError, match="not an integer"):
        parse_endpoint("smtp.gmail.com:smtp")


def test_empty_host_rejected() -> None:
    """Empty host (``:587``) raises ``ValueError``."""
    with pytest.raises(ValueError, match="empty host"):
        parse_endpoint(":587")


def test_port_out_of_range_rejected() -> None:
    """Port outside ``1..65535`` raises ``ValueError``."""
    with pytest.raises(ValueError, match="out of range"):
        parse_endpoint("host:0")
    with pytest.raises(ValueError, match="out of range"):
        parse_endpoint("host:70000")


def test_malformed_ipv6_rejected() -> None:
    """IPv6 literal without closing bracket raises ``ValueError``."""
    with pytest.raises(ValueError, match="malformed IPv6"):
        parse_endpoint("[::1:587")


# ---------------------------------------------------------------------------
# ConnectionInfo.resolve: composes parser + credentials
# ---------------------------------------------------------------------------


def test_connection_info_resolve_happy_path() -> None:
    """``resolve`` parses endpoint and looks up password by key."""
    with patch.object(
        _credentials, "_read_from_keystore", return_value="pw"
    ) as mock_read:
        info = ConnectionInfo.resolve(
            "smtp.gmail.com:587", "andreas@gmail.com"
        )
    assert info == ConnectionInfo(
        host="smtp.gmail.com",
        port=587,
        tls_mode=TlsMode.STARTTLS,
        username="andreas@gmail.com",
        password="pw",
    )
    mock_read.assert_called_once_with(
        "smtp.gmail.com:587", "andreas@gmail.com"
    )


def test_connection_info_resolve_propagates_missing_credential() -> None:
    """Missing keystore item raises ``CredentialNotFoundError``."""
    with patch.object(
        _credentials, "_read_from_keystore", return_value=None
    ):
        with pytest.raises(_credentials.CredentialNotFoundError):
            ConnectionInfo.resolve(
                "smtp.gmail.com:587", "andreas@gmail.com"
            )


def test_connection_info_resolve_rejects_bad_endpoint() -> None:
    """Endpoint parse error surfaces before any keystore touch."""
    with patch.object(
        _credentials, "_read_from_keystore"
    ) as mock_read:
        with pytest.raises(ValueError, match="missing ':port'"):
            ConnectionInfo.resolve("smtp.gmail.com", "andreas@gmail.com")
    mock_read.assert_not_called()
