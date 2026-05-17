# SPDX-License-Identifier: Apache-2.0
"""JMD MCP server for email — IMAP + SMTP.

Four tools: read, write, delete (IMAP), send (SMTP).

Each tool carries the connection identity in its signature:
``service`` = ``host:port`` endpoint and ``username``.  The
password is resolved from the OS keystore under
``(service, username)``; seed it once via your platform's CLI
(macOS: ``security add-generic-password``).
"""
from __future__ import annotations

import time

from mcp.server.fastmcp import FastMCP

from mail_mcp import smtp
from mail_mcp._credentials import (
    CredentialNotFoundError,
    KeystoreUnavailableError,
)
from mail_mcp._endpoint import ConnectionInfo
from mail_mcp._frontmatter import (
    StrictRefusalError,
    check_frontmatter,
    parse_debug,
    parse_frontmatter,
    prepend_debug,
    prepend_ignored_keys,
)
from mail_mcp.imap import delete as imap_delete
from mail_mcp.imap import read as imap_read
from mail_mcp.imap import write as imap_write
from mail_mcp.imap.read import _error

# Known frontmatter keys per tool (observable tolerance / strict refusal).
_KNOWN_FM_READ: frozenset[str] = frozenset({
    "page", "page-size", "count", "debug",
})
_KNOWN_FM_WRITE: frozenset[str] = frozenset({
    "rename-to", "move-to", "copy-to", "debug",
})
_KNOWN_FM_DELETE: frozenset[str] = frozenset({"confirm", "debug"})
_KNOWN_FM_SEND: frozenset[str] = frozenset({"debug"})

_INSTRUCTIONS = (
    'This is JMD, not IMAP or SMTP.'
    ' Read "#! Folder" or "#! Message" to learn how.'
)

mcp = FastMCP("jmd-mcp-mail", instructions=_INSTRUCTIONS)


def _resolve_info(service: str, username: str) -> ConnectionInfo | str:
    """Resolve ``(service, username)`` to a ConnectionInfo, or an error.

    Args:
        service: Endpoint string (``host:port``).
        username: Login identity.

    Returns:
        Either a :class:`ConnectionInfo` on success, or a serialized
        JMD ``# Error`` document on credential or endpoint failure.
        Callers should ``isinstance``-check the result.
    """
    try:
        return ConnectionInfo.resolve(service, username)
    except CredentialNotFoundError as exc:
        return _error(401, "credential_missing", str(exc))
    except KeystoreUnavailableError as exc:
        return _error(500, "keystore_unavailable", str(exc))
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))


@mcp.tool()
async def read(service: str, username: str, document: str) -> str:
    """Read IMAP resources using a JMD document (https://github.com/ostermeyer/jmd-spec).

    Args:
        service: IMAP endpoint as 'host:port' (e.g.
            'imap.gmail.com:993' for IMAPS).
        username: IMAP login.
        document: JMD read/query/schema document.

    Supported labels: Folder, Folder[], Message.

    Document forms — body-form only.  Fields go on lines *after*
    the heading; the JMD parser does NOT read inline parens or
    brackets in the heading itself (silent empty parse).

        #! Folder | #! Message | #! EmailAddress    (schema)

        # Folder[]                                  (list roots)

        # Folder                                    (one folder)
        path: INBOX

        # Message                                   (one message)
        id: 42
        folder: INBOX

        #? Folder                                   (filter)
        parent: INBOX

        #? Message                                  (filter)
        folder: INBOX
        from: ~alice
        subject: ~invoice
        seen: false

    Pagination frontmatter: page, page-size, count (before the
    #? heading).

    Frontmatter policy: observable tolerance — unknown keys are
    echoed in the response as 'ignored-keys: ...'.
    Debug frontmatter: 'debug: timing' (composable).

    Credentials — first-time setup or 'credential_missing' error:
        Passwords come from the user's OS keystore, looked up by
        (service, username).  When a keystore item is missing, the
        tool returns a '# Error' document with code
        'credential_missing' that already contains the exact
        platform-appropriate seed command (security / secret-tool /
        cmdkey) in its message.

        DO NOT run the seed command yourself — the entire security
        model of this server depends on the password never crossing
        any tool call.  Instead, present the seed command to the
        user as a copy-paste shell block (just like:

            ```sh
            security add-generic-password \
                -s imap.gmail.com:993 -a you@gmail.com -w
            ```

        ) and tell them to paste it into their own terminal.  The
        keystore CLI then prompts for the password tty-interactively
        in their shell — never in the LLM context.

        A mail account typically needs *two* seed items:
        one for the IMAP endpoint (read/write/delete) and one for
        the SMTP endpoint (send).  When helping a new user onboard,
        offer both copy-paste blocks proactively.  Mainstream
        providers' canonical endpoints are well-known:
        gmail.com → imap.gmail.com:993 + smtp.gmail.com:587 (App
        Password required), outlook/Office 365 →
        outlook.office365.com:993 + smtp.office365.com:587,
        ionos.de → imap.ionos.de:993 + smtp.ionos.de:587,
        fastmail.com → imap.fastmail.com:993 + smtp.fastmail.com:587,
        gmx.{net,de} → imap.gmx.net:993 + mail.gmx.net:587,
        web.de → imap.web.de:993 + smtp.web.de:587.
    """
    info = _resolve_info(service, username)
    if isinstance(info, str):
        return info
    try:
        fm = parse_frontmatter(document)
        ignored = check_frontmatter(
            fm, _KNOWN_FM_READ, "observable",
        )
        dbg = parse_debug(fm)
        t0 = time.perf_counter()
        result = await imap_read.read(document, info)
        if dbg.active:
            dbg.timing_ms = (
                (time.perf_counter() - t0) * 1000
            )
        return prepend_debug(
            prepend_ignored_keys(result, ignored), dbg,
        )
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))


@mcp.tool()
async def write(service: str, username: str, document: str) -> str:
    r"""Write to IMAP using a JMD document (https://github.com/ostermeyer/jmd-spec).

    Args:
        service: IMAP endpoint as 'host:port'.
        username: IMAP login.
        document: JMD data document (# Folder or # Message).

    Folder — create:

        # Folder
        path: Archive

    Folder — rename (rename-to in frontmatter):

        rename-to: NewName

        # Folder
        path: OldName

    Message — update flags:

        # Message
        id: 42
        folder: INBOX
        ## flags[]
        - \Seen

    Message — move/copy (frontmatter, two IMAP round-trips):

        move-to: Archive

        # Message
        id: 42
        folder: INBOX

    Or copy-to instead of move-to for a non-destructive copy.

    Frontmatter policy: observable tolerance — unknown keys are
    echoed in the response as 'ignored-keys: ...'.
    Debug frontmatter: 'debug: timing' (composable).
    """
    info = _resolve_info(service, username)
    if isinstance(info, str):
        return info
    try:
        fm = parse_frontmatter(document)
        ignored = check_frontmatter(
            fm, _KNOWN_FM_WRITE, "observable",
        )
        dbg = parse_debug(fm)
        t0 = time.perf_counter()
        result = await imap_write.write(document, info)
        if dbg.active:
            dbg.timing_ms = (
                (time.perf_counter() - t0) * 1000
            )
        return prepend_debug(
            prepend_ignored_keys(result, ignored), dbg,
        )
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))


@mcp.tool()
async def delete(service: str, username: str, document: str) -> str:
    r"""Delete an IMAP resource using a JMD delete document (https://github.com/ostermeyer/jmd-spec).

    Args:
        service: IMAP endpoint as 'host:port'.
        username: IMAP login.
        document: JMD delete document (#- Folder, #- Message,
            or #- Message[]).

    Folder — requires confirm: drop-folder because the drop is
    irreversible and removes all contained messages:

        confirm: drop-folder

        #- Folder
        path: Archive

    Message — permanent (\Deleted + EXPUNGE):

        #- Message
        id: 42
        folder: INBOX

    Bulk message delete (#- Message[]): many in one call.

        #- Message[]
        - id: 42
          folder: INBOX
        - id: 43
          folder: Archive

    Frontmatter policy: strict refusal — unknown keys cause a
    structured error (destructive operation, no silent drops).
    Debug frontmatter: 'debug: timing' (composable).
    """
    info = _resolve_info(service, username)
    if isinstance(info, str):
        return info
    try:
        fm = parse_frontmatter(document)
        check_frontmatter(fm, _KNOWN_FM_DELETE, "strict")
        dbg = parse_debug(fm)
        t0 = time.perf_counter()
        result = await imap_delete.delete(document, info)
        if dbg.active:
            dbg.timing_ms = (
                (time.perf_counter() - t0) * 1000
            )
        return prepend_debug(result, dbg)
    except StrictRefusalError as exc:
        return _error(
            400, "unknown_frontmatter_key", str(exc),
        )
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))



@mcp.tool()
def send(service: str, username: str, document: str) -> str:
    """Send an email via SMTP using a JMD Message document (https://github.com/ostermeyer/jmd-spec).

    Args:
        service: SMTP endpoint as 'host:port' (e.g.
            'smtp.gmail.com:587' for STARTTLS submission,
            'smtp.gmail.com:465' for implicit TLS).
        username: SMTP login.  Usually the sender's full email
            address.
        document: JMD Message document.

    Required fields in *document*: to, subject, body (Markdown).
    Optional fields: cc, bcc (comma-separated addresses),
    attachments[] (each with a 'path' field).

      # Message
      to: alice@example.com
      subject: Hello
      body:
      > Message text in **Markdown**

    The password is resolved from the OS keystore under
    (service, username); seed it once via your platform's
    keystore CLI (macOS: ``security add-generic-password``).

    Frontmatter policy: observable tolerance — unknown keys are
    echoed in the response as 'ignored-keys: ...'.
    Debug frontmatter: 'debug: timing' (composable).
    """
    try:
        fm = parse_frontmatter(document)
        ignored = check_frontmatter(
            fm, _KNOWN_FM_SEND, "observable",
        )
        dbg = parse_debug(fm)
        t0 = time.perf_counter()
        try:
            info = ConnectionInfo.resolve(service, username)
        except CredentialNotFoundError as exc:
            return smtp._error(401, "credential_missing", f"{exc}")
        except KeystoreUnavailableError as exc:
            return smtp._error(
                500, "keystore_unavailable", str(exc),
            )
        result = smtp.send(document, info)
        if dbg.active:
            dbg.timing_ms = (
                (time.perf_counter() - t0) * 1000
            )
        return prepend_debug(
            prepend_ignored_keys(result, ignored), dbg,
        )
    except ValueError as exc:
        return smtp._error(400, "bad_request", str(exc))



def main() -> None:
    """Entry point: start the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
