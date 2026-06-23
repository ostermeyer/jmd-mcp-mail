# SPDX-License-Identifier: Apache-2.0
"""DSGVO pseudonymisation of email identities at the server boundary.

Minimises personally-identifying email data *before* it crosses the
tool boundary into the LLM context (and thus, potentially, a third
country).  This is **data minimisation / defense-in-depth** (GDPR
Art. 5(1)(c), Art. 25, Recital 28): it strengthens the legitimate-
interest balancing test for third parties and shrinks what is exposed
should the transfer be compromised.  It does NOT, by itself, carry the
legal basis for the transfer — that rests on the DPA + transfer
mechanism (see ``docs/dsgvo-pseudonymisierung.md``).

Model
-----
* An address is replaced on the way OUT with ``Vorname <key>``, e.g.
  ``Alice <a1b2c3>``.  ``<key>`` is a **deterministic one-way token**
  (truncated HMAC-SHA256 keyed by a per-installation secret); it is not
  decryptable, only matchable.  Stable across sessions without any
  mapping table on disk.
* The given name is taken from the display name only when it is cleanly
  extractable; otherwise the token stands alone.  The name is residual
  PII accepted for usability — a deliberate trade-off.
* The reverse lookup (token -> real address) lives **in memory only**,
  populated as messages are read.  On the way IN (send, search) a token
  is resolved back to the real address.  Unknown bare tokens are
  rejected — the LLM can only address identities the server has seen
  (containment).

Only the 32-byte secret is persisted, in the OS keystore (reusing the
existing ``keyring`` dependency).  No mapping table is ever written to
disk.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from typing import TYPE_CHECKING

import keyring

if TYPE_CHECKING:
    from mail_mcp.imap._parse import EmailAddressRecord

_KEYRING_SERVICE = "jmd-mcp-mail"
_SECRET_KEY = "__pseudonym_secret_v1__"

# Power-user / test override: a raw secret string. When set, the
# keystore is not touched (handy for CI and for keeping the secret in
# a synced vault).
_ENV_SECRET = "JMD_MCP_MAIL_PSEUDONYM_SECRET"

# Truncation lengths (base32 chars). 8 chars ≈ 40 bits — collisions are
# negligible at personal-mailbox scale; the domain token is shorter
# because it only has to separate the handful of domains one corresponds
# with.
_TOKEN_LEN = 8
_DOMAIN_TOKEN_LEN = 4

# Process-lifetime caches. Never persisted.
_secret_cache: bytes | None = None
_reverse: dict[str, str] = {}

# Liberal-but-bounded address matcher for free-text scanning. Deliberately
# does not chase obfuscated forms ("alice at acme dot com") — see the spec.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)

# A display-name first token counts as a given name only if it is a
# single run of Latin letters (plus hyphen/apostrophe). Non-Latin scripts
# and "Surname, First" forms fall back to a bare token — conservative by
# design.
_GIVEN_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*$")

# Extract the addr-spec from a "Name <addr>" string, or the bare atom.
_ANGLE_RE = re.compile(r"<([^>]+)>")


def _load_secret() -> bytes:
    """Return the per-installation HMAC secret, creating it on first use.

    Resolution order: process cache, ``$JMD_MCP_MAIL_PSEUDONYM_SECRET``,
    OS keystore, freshly-generated 32 bytes (then stored in the keystore).
    """
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    env = os.environ.get(_ENV_SECRET)
    if env:
        _secret_cache = env.encode("utf-8")
        return _secret_cache
    stored = keyring.get_password(_KEYRING_SERVICE, _SECRET_KEY)
    if stored:
        _secret_cache = base64.b64decode(stored)
        return _secret_cache
    raw = secrets.token_bytes(32)
    keyring.set_password(
        _KEYRING_SERVICE, _SECRET_KEY, base64.b64encode(raw).decode("ascii")
    )
    _secret_cache = raw
    return _secret_cache


def _hmac_token(value: str, length: int) -> str:
    """Return a lowercase base32 HMAC token of *value*, truncated."""
    digest = hmac.new(
        _load_secret(), value.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b32encode(digest).decode("ascii").lower().rstrip("=")[:length]


def _norm(address: str) -> str:
    """Normalise an address for hashing (case/space only)."""
    return address.strip().lower()


def _given_name(name: str | None) -> str | None:
    """Extract a usable given name from a display name, else ``None``.

    Conservative: no name when it contains ``@`` or ``,`` (e.g.
    ``"Surname, First"``), and the first whitespace token must be a clean
    run of Latin letters. Anything ambiguous falls back to a bare token.
    """
    name = (name or "").strip()
    if not name or "@" in name or "," in name:
        return None
    first = name.split()[0]
    return first if _GIVEN_RE.match(first) else None


def _atom(value: str) -> str:
    """Return the addr-spec inside ``<...>`` or the bare trimmed value."""
    v = value.strip()
    m = _ANGLE_RE.search(v)
    return (m.group(1) if m else v).strip()


class Pseudonymizer:
    """Per-call outbound pseudonymiser; shares the process-wide reverse map.

    Args:
        domain: When True, append a domain token so "same company"
            relationships survive (``Alice <key@dom>``). Opt-in.
    """

    def __init__(self, domain: bool = False) -> None:
        """Initialise with the domain-disambiguation flag."""
        self.domain = domain

    def _bracket(self, address: str) -> str:
        """Return the token for *address* and record the reverse mapping."""
        norm = _norm(address)
        token = _hmac_token(norm, _TOKEN_LEN)
        if self.domain and "@" in norm:
            dom = norm.rsplit("@", 1)[1]
            token = f"{token}@{_hmac_token('domain:' + dom, _DOMAIN_TOKEN_LEN)}"
        # Store the address as seen (deliverable form), not the normalised
        # one — sending must use a real, routable address.
        _reverse[token] = address
        return token

    def address(self, rec: EmailAddressRecord) -> EmailAddressRecord:
        """Pseudonymise one address record (name + email)."""
        from mail_mcp.imap._parse import EmailAddressRecord

        if not rec.email:
            # Name-only artefact — nothing to tokenise; drop the name
            # rather than leak it.
            return EmailAddressRecord(name="", email="")
        return EmailAddressRecord(
            name=_given_name(rec.name) or "",
            email=self._bracket(rec.email),
        )

    def text(self, s: str) -> str:
        """Replace any email addresses in free text with ``<token>``."""
        if not s:
            return s
        return _EMAIL_RE.sub(lambda m: f"<{self._bracket(m.group(0))}>", s)


def resolve_recipient(value: str) -> str | None:
    """Resolve an inbound recipient string to a real address.

    Returns the real address for a known token; the input unchanged when
    it is already a real address (a user-provided new recipient); or
    ``None`` for an unknown bare token (the caller should reject the send).
    """
    atom = _atom(value)
    if atom in _reverse:
        return _reverse[atom]
    if "@" in atom:
        return atom
    return None


def resolve_search(value: str) -> str:
    """Resolve a search predicate value, leaving unknown values intact.

    Known tokens become the real address (so IMAP SEARCH matches the
    right correspondent); anything else passes through unchanged so a
    plain substring search still works.
    """
    atom = _atom(value)
    return _reverse.get(atom, value)


def _reset_for_tests() -> None:
    """Clear the secret cache and reverse map. Test-only seam."""
    global _secret_cache
    _secret_cache = None
    _reverse.clear()
