# SPDX-License-Identifier: Apache-2.0
r"""Read-only ``accounts`` tool — a sanitized projection of the config.

Accounts live in the out-of-reach ``config.jmd`` (see
:mod:`mail_mcp._config`), authored by the user out-of-band. This tool does
**not** create or edit accounts; it only exposes a PII-free projection so
the agent knows which labels exist (and how each authenticates), plus this
server's public key for the OAuth2 sealing flow.

* ``#! Account``   → the Account schema.
* ``# Account[]``  → list of ``{ label, auth?, broker-client? }`` — never
                     username or endpoints.
* ``# PublicKey``  → this server's X25519 public key.

Creating or changing accounts is done by editing ``config.jmd``; there is
no tool path to write it (the ``username`` is personal data and must not
flow through a tool call).
"""
from __future__ import annotations

from jmd import jmd_mode, jmd_to_dict, serialize

from mail_mcp import _config

_LABEL = "Account"


def handle(document: str) -> str:
    """Dispatch a read-only ``accounts`` request to a JMD response.

    Args:
        document: JMD document selecting the operation.

    Returns:
        A JMD document — the result on success, or a ``# Error`` document.
    """
    # Local import to dodge a schemas → accounts circular import.
    from mail_mcp.schemas import ACCOUNT, PUBLIC_KEY

    try:
        mode = jmd_mode(document)
    except Exception as exc:  # noqa: BLE001 — opaque parser errors
        return _error(400, "bad_request", f"unparseable document: {exc}")

    try:
        if mode == "schema":
            return PUBLIC_KEY if _is_pubkey(document) else ACCOUNT
        if mode == "data":
            if _is_pubkey(document):
                return _handle_public_key()
            return _handle_list(document)
        if mode == "delete":
            return _readonly_error()
        if mode == "query":
            return _error(
                400, "unsupported_mode",
                "Query mode (#? Account) is not supported; use "
                "# Account[] to list and filter on the caller side.",
            )
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))
    except OSError as exc:
        return _error(500, "io_error", str(exc))

    return _error(400, "bad_request", f"unsupported mode: {mode!r}")


def _handle_list(document: str) -> str:
    """List projection for ``# Account[]``; an upsert attempt is refused."""
    data = jmd_to_dict(document)
    if isinstance(data, dict):
        # A single-object # Account is an upsert attempt — refused.
        return _readonly_error()
    accounts = _config.load()
    return serialize(
        [_config.projection(a) for a in accounts], label=_LABEL,
    )


def _readonly_error() -> str:
    """The error returned for any write/delete attempt."""
    return _error(
        405, "config_readonly",
        "The account registry is read-only. Add or change accounts by "
        "editing config.jmd in the config directory (default "
        "~/.jmd-mcp-mail/) out-of-band; the agent cannot write it.",
    )


def _error(status: int, code: str, message: str) -> str:
    """Serialize a JMD ``# Error`` document."""
    return serialize(
        {"status": status, "code": code, "message": message},
        label="Error",
    )


def _root_label(document: str) -> str:
    """Extract the root label from a JMD document heading."""
    for line in document.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#!?- ").split("[", 1)[0].strip()
    return ""


def _is_pubkey(document: str) -> bool:
    """Whether *document*'s root label is ``PublicKey``."""
    return _root_label(document).lower() == "publickey"


def _handle_public_key() -> str:
    """Return this server's X25519 public key for the token broker."""
    from mail_mcp import _sealing

    return serialize({"key": _sealing.public_key()}, label="PublicKey")
