# SPDX-License-Identifier: Apache-2.0
"""One-way content masking for free text before it reaches the LLM.

Implements the content-layer regex masks from MTT's internal policy
"Anonymisierungsregeln für die Arbeit mit KI / LLM" (A. Töpperwien). This
complements the *identity* pseudonymisation in :mod:`mail_mcp._pseudonym`:

* E-mail addresses are deliberately **not** handled here — they are
  pseudonymised reversibly upstream (``Vorname <token>``) so replies and
  searches keep working. By the time masking runs, raw addresses are gone.
* The remaining patterns become opaque, one-way placeholders.

Pattern order is significant (FQDN → IPv4 → IPv6 → phone → host:port), per
the source policy, so e.g. the greedy phone pattern does not swallow IPs.

These are regex heuristics, not semantic detection. Known limitations
(documented in the source policy): version numbers can look like IPs, and
plain ``HH:MM`` times can match the host:port pattern. NER-hard categories
(names, postal addresses, Art. 9 data) are out of scope and remain the
user's and the legal framework's responsibility.
"""
from __future__ import annotations

import re

_FQDN = re.compile(
    r"\b(?:[a-zA-Z0-9-]+\.)+(?:intern|local|corp|com|net|de|io)\b"
)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b")
_PHONE = re.compile(r"\+?\d[\d\s().\-/]{6,}\d")
_HOSTPORT = re.compile(r"\b[a-zA-Z0-9.\-\[\]]+:\d{2,5}\b")

# Applied in this exact order — see module docstring.
_MASKS: list[tuple[re.Pattern[str], str]] = [
    (_FQDN, "[server]"),
    (_IPV4, "[ip]"),
    (_IPV6, "[ip]"),
    (_PHONE, "[telefon]"),
    (_HOSTPORT, "[server]:[port]"),
]


def mask(text: str) -> str:
    """Apply the content masks to *text* in policy order.

    Args:
        text: Free text (mail subject or body), ideally already
            identity-pseudonymised so raw addresses are gone.

    Returns:
        The text with servers/IPs/phones/host:port occurrences replaced
        by one-way placeholders.
    """
    if not text:
        return text
    for pattern, repl in _MASKS:
        text = pattern.sub(repl, text)
    return text
