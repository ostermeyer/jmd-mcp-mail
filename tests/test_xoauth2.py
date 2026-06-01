# SPDX-License-Identifier: Apache-2.0
"""Tests for XOAUTH2 SASL building and the OAuth ConnectionInfo."""
from __future__ import annotations

from mail_mcp._endpoint import ConnectionInfo, TlsMode, xoauth2_string


def test_xoauth2_string_format() -> None:
    """The SASL string uses Ctrl-A separators and a Bearer token."""
    s = xoauth2_string("user@example.com", "TOKEN123")
    assert s == "user=user@example.com\x01auth=Bearer TOKEN123\x01\x01"


def test_for_oauth_sets_token_and_no_password() -> None:
    """`for_oauth` builds a token-bearing connection without a password."""
    info = ConnectionInfo.for_oauth(
        "outlook.office365.com:993", "user@example.com", "TOKEN123",
    )
    assert info.access_token == "TOKEN123"
    assert info.password == ""
    assert info.tls_mode == TlsMode.IMPLICIT
    assert info.host == "outlook.office365.com"
    assert info.port == 993
