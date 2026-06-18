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

from mail_mcp import _sealing, smtp
from mail_mcp import accounts as accounts_module
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
    "access-token-sealed",
})
_KNOWN_FM_WRITE: frozenset[str] = frozenset({
    "rename-to", "move-to", "copy-to", "debug",
    "access-token-sealed",
})
_KNOWN_FM_DELETE: frozenset[str] = frozenset({
    "confirm", "debug", "access-token-sealed",
})
_KNOWN_FM_SEND: frozenset[str] = frozenset({
    "debug", "access-token-sealed",
})

_INSTRUCTIONS = (
    'This is JMD, not IMAP or SMTP.'
    ' Read "#! Folder" or "#! Message" to learn how.'
    ' Accounts marked auth: oauth2 in the registry authenticate with a'
    ' short-lived sealed access token from jmd-mcp-oauth2, passed as an'
    ' access-token-sealed: frontmatter key (the read tool explains the'
    ' steps); basic accounts use a keystore password as before.'
)

mcp = FastMCP("jmd-mcp-mail", instructions=_INSTRUCTIONS)


def _resolve_info(
    service: str, username: str, document: str,
) -> ConnectionInfo | str:
    """Resolve a connection for one call, or return a JMD ``# Error``.

    If *document*'s frontmatter carries ``access-token-sealed``, the
    sealed OAuth2 access token is opened with this server's private
    key and the connection authenticates via XOAUTH2 — no keystore
    password is read.  Otherwise the password is resolved from the OS
    keystore (Basic Auth) as before.  A missing Basic-Auth credential
    for a registered ``oauth2`` account returns a structured
    ``oauth_token_required`` hint instead of ``credential_missing``.

    Args:
        service: Endpoint string (``host:port``).
        username: Login identity.
        document: The JMD document for this call.

    Returns:
        Either a :class:`ConnectionInfo`, or a serialized JMD
        ``# Error`` document.  Callers should ``isinstance``-check.
    """
    try:
        fm = parse_frontmatter(document)
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))
    sealed = fm.get("access-token-sealed")
    if sealed:
        try:
            token = _sealing.unseal(str(sealed))
        except Exception as exc:  # noqa: BLE001 — opaque nacl/b64 errors
            return _error(
                400, "bad_sealed_token",
                f"could not open the sealed access token: {exc}",
            )
        try:
            return ConnectionInfo.for_oauth(service, username, token)
        except ValueError as exc:
            return _error(400, "bad_request", str(exc))
    try:
        return ConnectionInfo.resolve(service, username)
    except CredentialNotFoundError as exc:
        acct = accounts_module.find_by_endpoint(service, username)
        if acct is not None and acct.auth == "oauth2":
            return _error(
                401, "oauth_token_required",
                f"Account {acct.label!r} uses OAuth2. Fetch a sealed "
                f"access token from broker client "
                f"{acct.broker_client!r}: read its '# OAuthToken' with "
                "this server's recipient-pubkey (from `accounts` with "
                "'# PublicKey'), then retry with an "
                "'access-token-sealed:' frontmatter key.",
            )
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

    Pagination frontmatter (before the #? heading): page, page-size.
    Results are newest-first. `count` switches to COUNT-ONLY mode — the
    response is just `total: N` with an empty list (no message items);
    omit `count` to receive the items.

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

    OAuth2 accounts (Microsoft, Gmail, …) — no password, a sealed
    token instead:
        Providers that disabled Basic Auth are marked
        ``auth: oauth2`` in the registry and name a ``broker-client``
        (a jmd-mcp-oauth2 client).  For these, do NOT seed a keystore
        password; hand this server a short-lived *sealed* access
        token per call:

          1. Get this server's public key — call ``accounts`` with
             ``# PublicKey`` (returns ``key: <base64>``).
          2. Ask the token broker (jmd-mcp-oauth2) to seal a token to
             that key — its ``read`` with::

                 # OAuthToken
                 name: <broker-client>
                 recipient-pubkey: <key from step 1>

             One-time first: authorize the broker via its ``write``
             ``# OAuthSession { name: <broker-client> }`` (a
             device-code or browser login).
          3. Pass the returned ``ciphertext`` to THIS call as a
             frontmatter key::

                 access-token-sealed: <ciphertext>

                 # Folder[]

             It is opened here with our private key and used via
             XOAUTH2; the plaintext token never crosses a tool call.

        Calling an oauth2 account without a sealed token returns
        ``oauth_token_required``, naming the broker-client and steps.
    """
    info = _resolve_info(service, username, document)
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
    info = _resolve_info(service, username, document)
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
    info = _resolve_info(service, username, document)
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
    attachments[] (each with a 'path' field), and **from-name**.

    ** from-name ** — optional display name for the From header. When
    set, recipients see "from-name <username>" instead of the bare
    address (e.g. from-name: Andreas Ostermeyer → "Andreas Ostermeyer
    <a@b.de>"). The envelope sender is unaffected; only the header
    carries the name. Omit it to send from the bare address.

      # Message
      to: alice@example.com
      subject: Hello
      from-name: Andreas Ostermeyer
      body:
      > Message text in **Markdown**

    The password is resolved from the OS keystore under
    (service, username); seed it once via your platform's
    keystore CLI (macOS: ``security add-generic-password``).

    OAuth2 accounts use a sealed token instead of a password: pass it
    as an ``access-token-sealed:`` frontmatter key (see the ``read``
    tool for how to obtain one from jmd-mcp-oauth2).

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
        info = _resolve_info(service, username, document)
        if isinstance(info, str):
            return info
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



@mcp.tool()
def accounts(document: str) -> str:
    r"""Manage the local Account registry via a JMD document (https://github.com/ostermeyer/jmd-spec).

    Args:
        document: JMD document selecting the operation (see below).

    The registry is a flat list of labelled ``(imap_service,
    smtp_service, username)`` triples stored under
    ``%APPDATA%\\jmd-mcp-mail\\accounts.jmd`` (Windows),
    ``~/Library/Application Support/jmd-mcp-mail/accounts.jmd``
    (macOS) or ``$XDG_CONFIG_HOME/jmd-mcp-mail/accounts.jmd``
    (Linux).  **No passwords** are stored here — only the metadata
    needed to construct the ``(service, username)`` keystore lookup
    at tool-call time.

    Supported document forms:

        #! Account                                  (schema)

        # Account[]                                 (list all)

        # Account                                   (upsert by label)
        label: ionos
        imap_service: imap.ionos.de:993
        smtp_service: smtp.ionos.de:587
        username: andreas@ostermeyer.de

        #- Account                                  (delete by label)
        label: ionos

    Typical workflow:

      1. **List** with ``# Account[]`` to see what the user has
         configured.  Pick one for read / send.
      2. **Upsert** with ``# Account { ... }`` when the user adds a
         new account.  *Also* offer the user the keystore seed
         commands (see the `read` tool docstring) — the registry
         carries no password, so without those two keystore items
         the account cannot authenticate.
      3. **Delete** with ``#- Account { label }`` when the user
         retires an account.  This does NOT delete the keystore
         items; the user can drop those separately with their
         platform's keystore CLI.

    The registry is a convenience layer for *labels and endpoints*;
    the source of truth for "can this account authenticate?" is the
    OS keystore.  An upsert without matching keystore items is a
    valid intermediate state (and the user will hit
    ``credential_missing`` on the first real call, which carries the
    seed command).

    OAuth2 accounts:
        Set ``auth: oauth2`` and a ``broker-client`` (the
        jmd-mcp-oauth2 client name) on the account instead of seeding
        a keystore password.  Read this server's public key with the
        ``# PublicKey`` form below; the `read`/`send` tools explain
        how the agent fetches a sealed token from the broker and
        passes it as an ``access-token-sealed:`` frontmatter key.

        # Account                                   (oauth2 upsert)
        label: outlook
        imap_service: outlook.office365.com:993
        smtp_service: smtp-mail.outlook.com:587
        username: you@outlook.com
        auth: oauth2
        broker-client: outlook

        # PublicKey                                 (this server's key)
    """
    try:
        return accounts_module.handle(document)
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))


def main() -> None:
    """Entry point: start the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
