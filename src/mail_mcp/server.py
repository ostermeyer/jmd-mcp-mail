# SPDX-License-Identifier: Apache-2.0
"""JMD MCP server for email — IMAP + SMTP.

Four tools: read, write, delete (IMAP), send (SMTP).

Configuration: ~/.config/jmd/mail.jmd
Password: OS keyring, service='jmd-mcp-mail', username=<configured username>
"""
from __future__ import annotations

import time

from mcp.server.fastmcp import FastMCP

from mail_mcp import config, smtp
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
    "mailbox", "page", "page-size", "count", "debug",
})
_KNOWN_FM_WRITE: frozenset[str] = frozenset({
    "mailbox", "rename-to", "move-to", "copy-to", "debug",
})
_KNOWN_FM_DELETE: frozenset[str] = frozenset({
    "mailbox", "confirm", "debug",
})
_KNOWN_FM_SEND: frozenset[str] = frozenset({
    "mailbox", "debug",
})

_INSTRUCTIONS = (
    'This is JMD, not IMAP or SMTP.'
    ' Read "#! MailBox" to learn how.'
)

mcp = FastMCP("jmd-mcp-mail", instructions=_INSTRUCTIONS)

_cfgs: dict[str, config.MailConfig] | None = None
_cfgs_mtime: float = 0.0


def _get_cfgs() -> dict[str, config.MailConfig]:
    """Return mail configs, reloading if mail.jmd changed.

    The config file is stat'd on every call (negligible cost).
    When its mtime has advanced, the config is reloaded so that
    the running server picks up edits without a restart.
    """
    global _cfgs, _cfgs_mtime
    path = config.config_path()
    mtime = path.stat().st_mtime if path.exists() else 0.0
    if _cfgs is None or mtime > _cfgs_mtime:
        _cfgs = config.load()
        _cfgs_mtime = mtime
    return _cfgs


@mcp.tool()
async def read(document: str) -> str:
    """Read IMAP resources using a JMD document (https://github.com/ostermeyer/jmd-spec).

    Supported labels: MailBox, MailBox[], Folder, Folder[], Message.

    Schema:    #! MailBox / #! Folder / #! Message / #! EmailAddress
    Read:      # MailBox[] | # MailBox (name: X) | # Folder[]
               # Folder (path: X) | # Message (id: X, folder: Y)
    Query:     #? Folder [parent: X]
               #? Message [folder: X, from: ~X, subject: ~X]

    Pagination frontmatter: page, page-size, count (before the #? heading).
    Multi-account: add 'mailbox: <name>' to route to a specific account.

    Frontmatter policy: observable tolerance — unknown keys are
    echoed in the response as 'ignored-keys: ...'.
    Debug frontmatter: 'debug: timing, mailbox' (composable).
    """
    try:
        fm = parse_frontmatter(document)
        ignored = check_frontmatter(
            fm, _KNOWN_FM_READ, "observable",
        )
        dbg = parse_debug(fm)
        if dbg.wants("mailbox"):
            dbg.mailbox = str(fm.get("mailbox", "(default)"))
        t0 = time.perf_counter()
        result = await imap_read.read(document, _get_cfgs())
        if dbg.active:
            dbg.timing_ms = (
                (time.perf_counter() - t0) * 1000
            )
        return prepend_debug(
            prepend_ignored_keys(result, ignored), dbg,
        )
    except (FileNotFoundError, ValueError) as exc:
        return _error(500, "config_error", str(exc))


@mcp.tool()
async def write(document: str) -> str:
    r"""Write to IMAP using a JMD document (https://github.com/ostermeyer/jmd-spec).

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

    Multi-account: add 'mailbox: <name>' to route to a specific
    account.

    Frontmatter policy: observable tolerance — unknown keys are
    echoed in the response as 'ignored-keys: ...'.
    Debug frontmatter: 'debug: timing, mailbox' (composable).
    """
    try:
        fm = parse_frontmatter(document)
        ignored = check_frontmatter(
            fm, _KNOWN_FM_WRITE, "observable",
        )
        dbg = parse_debug(fm)
        if dbg.wants("mailbox"):
            dbg.mailbox = str(fm.get("mailbox", "(default)"))
        t0 = time.perf_counter()
        result = await imap_write.write(document, _get_cfgs())
        if dbg.active:
            dbg.timing_ms = (
                (time.perf_counter() - t0) * 1000
            )
        return prepend_debug(
            prepend_ignored_keys(result, ignored), dbg,
        )
    except (FileNotFoundError, ValueError) as exc:
        return _error(500, "config_error", str(exc))


@mcp.tool()
async def delete(document: str) -> str:
    r"""Delete an IMAP resource using a JMD delete document (https://github.com/ostermeyer/jmd-spec).

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
    List items may be scalar UIDs (defaults folder to INBOX) or
    dicts with id and optional folder.

        #- Message[]
        - id: 42
          folder: INBOX
        - id: 43
          folder: INBOX
        - id: 44
          folder: Archive

    The deleted resource is returned as a full JMD data document.
    Multi-account: add 'mailbox: <name>' to route to a specific
    account.

    Frontmatter policy: strict refusal — unknown keys cause a
    structured error (destructive operation, no silent drops).
    Debug frontmatter: 'debug: timing, mailbox' (composable).
    """
    try:
        fm = parse_frontmatter(document)
        check_frontmatter(fm, _KNOWN_FM_DELETE, "strict")
        dbg = parse_debug(fm)
        if dbg.wants("mailbox"):
            dbg.mailbox = str(fm.get("mailbox", "(default)"))
        t0 = time.perf_counter()
        result = await imap_delete.delete(document, _get_cfgs())
        if dbg.active:
            dbg.timing_ms = (
                (time.perf_counter() - t0) * 1000
            )
        return prepend_debug(result, dbg)
    except StrictRefusalError as exc:
        return _error(
            400, "unknown_frontmatter_key", str(exc),
        )
    except (FileNotFoundError, ValueError) as exc:
        return _error(500, "config_error", str(exc))


@mcp.tool()
def send(document: str) -> str:
    """Send an email via SMTP using a JMD Message document (https://github.com/ostermeyer/jmd-spec).

    Required fields: to, subject, body (Markdown).
    Optional fields: cc, bcc (comma-separated addresses).

      # Message
      to: alice@example.com
      subject: Hello
      body:
      > Message text in **Markdown**

    Attachments via ## attachments[] with path fields (local file paths).
    Multi-account: add 'mailbox: <name>' to route to a specific account.
    Returns a confirmation document on success.

    Frontmatter policy: observable tolerance — unknown keys are
    echoed in the response as 'ignored-keys: ...'.
    Debug frontmatter: 'debug: timing, mailbox' (composable).
    """
    try:
        fm = parse_frontmatter(document)
        ignored = check_frontmatter(
            fm, _KNOWN_FM_SEND, "observable",
        )
        dbg = parse_debug(fm)
        if dbg.wants("mailbox"):
            dbg.mailbox = str(fm.get("mailbox", "(default)"))
        t0 = time.perf_counter()
        result = smtp.send(document, _get_cfgs())
        if dbg.active:
            dbg.timing_ms = (
                (time.perf_counter() - t0) * 1000
            )
        return prepend_debug(
            prepend_ignored_keys(result, ignored), dbg,
        )
    except (FileNotFoundError, ValueError) as exc:
        return smtp._error(500, "config_error", str(exc))


def main() -> None:
    """Entry point: start the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
