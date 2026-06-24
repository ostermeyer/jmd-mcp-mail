# SPDX-License-Identifier: Apache-2.0
"""Endpoint parsing and TLS-mode inference for jmd-mcp-mail.

Tool signatures carry a single ``service`` string of the form
``host:port`` (IPv6: ``[host]:port``).  The transport mode is
inferred from the port by widely-used convention:

* 465 → implicit TLS (SMTPS)
* 993 → implicit TLS (IMAPS)
* 587 → STARTTLS (SMTP submission)
* 143 → STARTTLS (IMAP)
* anything else → STARTTLS attempt, server must support it

This removes the need for a per-account config file: the LLM
already knows the canonical endpoint for each provider, and the
server derives transport details mechanically.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mail_mcp import _credentials


class TlsMode(Enum):
    """Transport-layer security mode for an SMTP/IMAP connection."""

    IMPLICIT = "implicit"
    """Connect, then immediately TLS-handshake (SMTPS / IMAPS)."""

    STARTTLS = "starttls"
    """Connect plain, then upgrade via STARTTLS."""


# Port → TLS-mode convention.  Anything not in this table defaults
# to STARTTLS, which the server will negotiate on the wire; a
# server that does not advertise STARTTLS will fail the handshake
# loudly, which is the correct outcome.
_IMPLICIT_TLS_PORTS: frozenset[int] = frozenset({465, 993})


@dataclass(frozen=True)
class Endpoint:
    """A parsed ``host:port`` pair plus the inferred TLS mode."""

    host: str
    port: int
    tls_mode: TlsMode


@dataclass(frozen=True)
class ConnectionInfo:
    """Fully resolved connection parameters for one call.

    Carries the parsed endpoint together with the credentials.
    Built via :meth:`resolve`; instances are immutable and
    short-lived (one per tool call).

    Attributes:
        host: Resolved hostname (no port).
        port: TCP port.
        tls_mode: Transport-layer mode inferred from the port.
        username: SMTP/IMAP login identity.
        password: Cleartext password, retrieved from the keystore
            by :meth:`resolve`.  Must never appear in tool output.
        pseudonymize: When True (default), the read path replaces
            email identities with pseudonyms before they reach the
            LLM (GDPR data minimisation). Disabled per account via
            the registry.
        pseudonymize_domain: When True, pseudonyms carry a domain
            token so "same company" relationships survive. Opt-in.
    """

    host: str
    port: int
    tls_mode: TlsMode
    username: str
    password: str
    access_token: str = ""
    from_name: str = ""
    pseudonymize: bool = True
    pseudonymize_domain: bool = False

    @classmethod
    def resolve(
        cls, service: str, username: str, *, from_name: str = "",
        pseudonymize: bool = True, pseudonymize_domain: bool = False,
    ) -> ConnectionInfo:
        """Build a connection from ``(service, username)``.

        Args:
            service: Endpoint of the form ``host:port`` (IPv6:
                ``[host]:port``).
            username: SMTP/IMAP login.
            from_name: Optional display name for the From header.
            pseudonymize: Whether to pseudonymise identities on read.
            pseudonymize_domain: Whether to add a domain token.

        Returns:
            A fully populated :class:`ConnectionInfo`.

        Raises:
            ValueError: If ``service`` is not parseable.
            CredentialNotFoundError: If no keystore item exists
                for ``(service, username)``.
            KeystoreUnavailableError: On keystore-side failures.
        """
        endpoint = parse_endpoint(service)
        password = _credentials.resolve(service, username)
        return cls(
            host=endpoint.host,
            port=endpoint.port,
            tls_mode=endpoint.tls_mode,
            username=username,
            password=password,
            from_name=from_name,
            pseudonymize=pseudonymize,
            pseudonymize_domain=pseudonymize_domain,
        )

    @classmethod
    def for_oauth(
        cls, service: str, username: str, access_token: str,
        *, from_name: str = "",
        pseudonymize: bool = True, pseudonymize_domain: bool = False,
    ) -> ConnectionInfo:
        """Build an OAuth2 connection (XOAUTH2, no keystore password).

        Args:
            service: Endpoint of the form ``host:port``.
            username: SMTP/IMAP login.
            access_token: A bearer access token (already unsealed).
            from_name: Optional display name for the From header.
            pseudonymize: Whether to pseudonymise identities on read.
            pseudonymize_domain: Whether to add a domain token.

        Returns:
            A :class:`ConnectionInfo` with ``access_token`` set; the
            connection layer authenticates via XOAUTH2, not a password.

        Raises:
            ValueError: If ``service`` is not parseable.
        """
        endpoint = parse_endpoint(service)
        return cls(
            host=endpoint.host,
            port=endpoint.port,
            tls_mode=endpoint.tls_mode,
            username=username,
            password="",
            access_token=access_token,
            from_name=from_name,
            pseudonymize=pseudonymize,
            pseudonymize_domain=pseudonymize_domain,
        )


def parse_endpoint(service: str) -> Endpoint:
    """Parse a ``host:port`` service string.

    Args:
        service: Endpoint string.  Accepted forms:

            * ``host:port`` — typical case (``smtp.gmail.com:587``).
            * ``[host]:port`` — IPv6 literal in brackets.

    Returns:
        Parsed :class:`Endpoint` with the TLS mode inferred from
        the port number.

    Raises:
        ValueError: If the input has no port, an empty host, a
            non-integer port, or a port outside ``1..65535``.
    """
    if not service:
        raise ValueError("service is empty")

    host, port = _split_host_port(service)

    if not host:
        raise ValueError(f"service {service!r}: empty host")
    if not 1 <= port <= 65535:
        raise ValueError(
            f"service {service!r}: port {port} out of range 1..65535"
        )

    tls_mode = (
        TlsMode.IMPLICIT
        if port in _IMPLICIT_TLS_PORTS
        else TlsMode.STARTTLS
    )
    return Endpoint(host=host, port=port, tls_mode=tls_mode)


def _split_host_port(service: str) -> tuple[str, int]:
    """Split ``host:port`` into its parts, with IPv6 bracket handling.

    Args:
        service: Endpoint string.

    Returns:
        ``(host, port)`` tuple.

    Raises:
        ValueError: If no port is present, or the port is not a
            valid integer.
    """
    if service.startswith("["):
        # IPv6 literal: [::1]:587
        close = service.find("]")
        if close < 0 or not service[close + 1 :].startswith(":"):
            raise ValueError(
                f"service {service!r}: malformed IPv6 endpoint, "
                "expected '[host]:port'"
            )
        host = service[1:close]
        port_str = service[close + 2 :]
    else:
        if ":" not in service:
            raise ValueError(
                f"service {service!r}: missing ':port' suffix"
            )
        host, _, port_str = service.rpartition(":")

    try:
        port = int(port_str)
    except ValueError as exc:
        raise ValueError(
            f"service {service!r}: port {port_str!r} is not an integer"
        ) from exc

    return host, port


def xoauth2_string(username: str, access_token: str) -> str:
    """Build the SASL XOAUTH2 initial client-response string.

    The format is ``user=<login>^Aauth=Bearer <token>^A^A`` where
    ``^A`` is the Ctrl-A (``0x01``) separator. Base64-encode it for
    SMTP ``AUTH XOAUTH2``; imaplib base64-encodes it for IMAP.

    Args:
        username: The account login (usually the email address).
        access_token: A valid OAuth2 bearer access token.

    Returns:
        The XOAUTH2 SASL string.
    """
    return f"user={username}\x01auth=Bearer {access_token}\x01\x01"
