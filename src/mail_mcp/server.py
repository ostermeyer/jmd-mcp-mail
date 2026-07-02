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

from mail_mcp import _config, _sealing, smtp
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
    "access-token-sealed", "in-reply-to", "in-reply-to-folder",
})
_KNOWN_FM_DELETE: frozenset[str] = frozenset({
    "confirm", "debug", "access-token-sealed",
})
_KNOWN_FM_SEND: frozenset[str] = frozenset({
    "debug", "access-token-sealed",
    "in-reply-to", "in-reply-to-folder",
})

_INSTRUCTIONS = (
    'This is JMD, not IMAP or SMTP.'
    ' Read "#! Folder" or "#! Message" to learn how.'
    ' Address an account by its `account` label (configured out-of-band'
    ' in config.jmd; list labels via the `accounts` tool). The server'
    ' resolves endpoints/username internally. Accounts with auth: oauth2'
    ' authenticate with a short-lived sealed access token from'
    ' jmd-mcp-oauth2, passed as an access-token-sealed: frontmatter key;'
    ' basic accounts use a keystore password.'
)

mcp = FastMCP("jmd-mcp-mail", instructions=_INSTRUCTIONS)


def _resolve_info(
    account: str, document: str, *, smtp: bool = False,
) -> ConnectionInfo | str:
    """Resolve a connection for one call, or return a JMD ``# Error``.

    The *account* label is looked up in ``config.jmd``; the server picks
    the IMAP or SMTP endpoint (``smtp``), the username and the auth mode
    from that record — none of which the LLM ever supplies or sees.

    If the document's frontmatter carries ``access-token-sealed``, the
    sealed OAuth2 token is opened with this server's private key and the
    connection authenticates via XOAUTH2. An ``oauth2`` account called
    without a sealed token returns ``oauth_token_required``. Otherwise the
    password is resolved from the OS keystore (Basic Auth).

    Args:
        account: The account label (config.jmd primary key).
        document: The JMD document for this call.
        smtp: True for the SMTP endpoint (send), False for IMAP.

    Returns:
        Either a :class:`ConnectionInfo`, or a serialized JMD
        ``# Error`` document.  Callers should ``isinstance``-check.
    """
    acct = _config.resolve(account)
    if acct is None:
        return _error(
            404, "unknown_account",
            f"No account {account!r} in config.jmd. Add it out-of-band "
            "in the config directory (default ~/.jmd-mcp-mail/); list "
            "configured labels via the `accounts` tool.",
        )
    service = acct.smtp if smtp else acct.imap

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
            return ConnectionInfo.for_oauth(
                service, acct.username, token, from_name=acct.from_name,
            )
        except ValueError as exc:
            return _error(400, "bad_request", str(exc))

    if acct.auth == "oauth2":
        return _error(
            401, "oauth_token_required",
            f"Account {acct.label!r} uses OAuth2. Fetch a sealed access "
            f"token from broker client {acct.broker_client!r} (read its "
            "'# OAuthToken' with this server's recipient-pubkey from "
            "`accounts` '# PublicKey'), then retry with an "
            "'access-token-sealed:' frontmatter key.",
        )

    try:
        return ConnectionInfo.resolve(
            service, acct.username, from_name=acct.from_name,
        )
    except CredentialNotFoundError as exc:
        return _error(401, "credential_missing", str(exc))
    except KeystoreUnavailableError as exc:
        return _error(500, "keystore_unavailable", str(exc))
    except ValueError as exc:
        return _error(400, "bad_request", str(exc))


@mcp.tool()
async def read(account: str, document: str) -> str:
    """Read IMAP resources using a JMD document (https://github.com/ostermeyer/jmd-spec).

    Args:
        account: Account label configured out-of-band in config.jmd.
            The server resolves the IMAP endpoint and username from it;
            you never pass or see them. List labels via `accounts`.
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
        cc: ~bob
        subject: ~invoice
        seen: false
        since: 2026-06-01
        before: 2026-07-01

    Date criteria (since / before / on) take ISO dates (YYYY-MM-DD)
    and compare the server-side arrival date at day granularity —
    since is inclusive, before exclusive. Non-ASCII search values
    (umlauts etc.) are handled automatically via CHARSET UTF-8.

    Messages expose 'message-id', 'in-reply-to' and 'references'
    (RFC 5322 header values) for thread inspection. NOTE: these
    read-side fields carry Message-ID strings; the 'in-reply-to'
    FRONTMATTER key on write/send takes an IMAP UID instead.

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
    info = _resolve_info(account, document)
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
async def write(account: str, document: str) -> str:
    r"""Write to IMAP using a JMD document (https://github.com/ostermeyer/jmd-spec).

    Args:
        account: Account label configured out-of-band in config.jmd
            (server resolves the IMAP endpoint and username).
        document: JMD data document (# Folder or # Message).

    Folder — create:

        # Folder
        path: Archive

    Folder — rename (rename-to in frontmatter):

        rename-to: NewName

        # Folder
        path: OldName

    Message — create a DRAFT (# Message without id): the message is
    stored in the account's Drafts folder with the \Draft flag, so
    the user can review, edit and send it from their own mail client
    (human-in-the-loop alternative to `send`). At least one of
    to/subject/body is required — partial drafts are fine. Drafts
    carry NO AI-attribution footer (the user takes authorship by
    sending), and bcc appears as a real header. The target folder is
    discovered automatically (SPECIAL-USE / well-known names); an
    explicit 'folder:' field overrides it. The response echoes the
    stored draft including its new 'id'.

        # Message
        to: alice@example.com
        subject: Quarterly numbers
        body:
        > Hi Alice — draft text in **Markdown** …

    Message — replace a draft (# Message with id AND content fields):
    REPLACE semantics, no field merge — restate the complete draft.
    The new version is appended first, then the old one is deleted
    (a failure can leave a duplicate, never a loss). Returns the new
    draft with its new id.

        # Message
        id: 17
        folder: Drafts
        to: alice@example.com
        subject: Quarterly numbers (v2)
        body:
        > …

    Message — update flags. ## flags[] REPLACES the whole flag set;
    ## flags-add[] / ## flags-remove[] change flags incrementally
    without clobbering others (preferred for single-flag changes).
    Replace and incremental forms are mutually exclusive.

        # Message
        id: 42
        folder: INBOX
        ## flags[]
        - \Seen

    or incrementally:

        # Message
        id: 42
        folder: INBOX
        ## flags-add[]
        - \Seen
        ## flags-remove[]
        - \Flagged

    Message — move/copy (frontmatter, two IMAP round-trips):

        move-to: Archive

        # Message
        id: 42
        folder: INBOX

    Or copy-to instead of move-to for a non-destructive copy.

    Reply drafts — 'in-reply-to' FRONTMATTER (draft create/replace
    only): references the message being answered by its IMAP UID
    ('in-reply-to-folder' defaults to INBOX). The server fetches the
    original, sets the In-Reply-To/References headers (thread stays
    intact), prefixes the subject with 'Re:' and defaults 'to' to
    the original's Reply-To/From — so a minimal reply draft is just
    a body. NOTE the naming trap: this frontmatter key takes a UID;
    the *read-side field* 'in-reply-to' on a Message is the RFC 5322
    Message-ID header. They are different planes.

        in-reply-to: 42
        in-reply-to-folder: INBOX

        # Message
        body:
        > Got it, thanks!

    Frontmatter policy: observable tolerance — unknown keys are
    echoed in the response as 'ignored-keys: ...'.
    Debug frontmatter: 'debug: timing' (composable).
    """
    info = _resolve_info(account, document)
    if isinstance(info, str):
        return info
    acct = _config.resolve(account)
    drafts_folder = acct.drafts_folder if acct else ""
    try:
        fm = parse_frontmatter(document)
        ignored = check_frontmatter(
            fm, _KNOWN_FM_WRITE, "observable",
        )
        dbg = parse_debug(fm)
        t0 = time.perf_counter()
        result = await imap_write.write(
            document, info, drafts_folder=drafts_folder,
        )
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
async def delete(account: str, document: str) -> str:
    r"""Delete an IMAP resource using a JMD delete document (https://github.com/ostermeyer/jmd-spec).

    Args:
        account: Account label configured out-of-band in config.jmd
            (server resolves the IMAP endpoint and username).
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
    info = _resolve_info(account, document)
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
async def send(account: str, document: str) -> str:
    """Send an email via SMTP using a JMD Message document (https://github.com/ostermeyer/jmd-spec).

    Args:
        account: Account label configured out-of-band in config.jmd
            (server resolves the SMTP endpoint and sender username).
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

    After a successful delivery a copy is stored in the account's
    Sent folder (unless store-sent: false is configured, e.g. for
    Gmail which auto-stores). The response reports it as
    'sent-copy: stored | failed | disabled' plus 'sent-folder' and
    the copy's 'id' when known. A failed sent-copy does NOT mean the
    mail failed — 'status: sent' is authoritative.

    Replying — 'in-reply-to' FRONTMATTER: references the message
    being answered by its IMAP UID ('in-reply-to-folder' defaults to
    INBOX). Sets In-Reply-To/References so the recipient's thread
    stays intact, prefixes 'Re:' and defaults 'to' to the original's
    Reply-To/From. (Do not confuse with the read-side 'in-reply-to'
    field, which is a Message-ID header, not a UID.)

        in-reply-to: 42

        # Message
        body:
        > Reply text …

    TIP — draft instead of send: to let the user review and send the
    mail themselves, create a draft via the `write` tool
    (# Message without id). Drafts carry no AI footer.

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
        info = _resolve_info(account, document, smtp=True)
        if isinstance(info, str):
            return info
        acct = _config.resolve(account)
        store_sent = acct.store_sent if acct else True
        sent_folder = acct.sent_folder if acct else ""
        is_reply = bool(str(fm.get("in-reply-to", "")).strip())
        imap_info = None
        if store_sent or is_reply:
            maybe = _resolve_info(account, document)
            if isinstance(maybe, str):
                if is_reply:
                    # Threading is mandatory when requested — never
                    # send an unthreaded reply.
                    return maybe
                # Sent-copy alone is best-effort: an unresolvable
                # IMAP side degrades to sent-copy: failed.
            else:
                imap_info = maybe
        result = await smtp.send(
            document, info,
            imap_info=imap_info,
            store_sent=store_sent,
            sent_folder=sent_folder,
        )
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
    r"""List configured accounts (READ-ONLY) via a JMD document (https://github.com/ostermeyer/jmd-spec).

    Accounts are authored out-of-band in ``config.jmd`` (config
    directory, default ``~/.jmd-mcp-mail/``). This tool **cannot** create
    or change them — there is no write path (the username is personal
    data and must not flow through a tool call).

    Supported document forms:

        #! Account        (schema of a config.jmd account)
        # Account[]       (list accounts: label, auth, broker-client)
        # PublicKey       (this server's X25519 key for OAuth2 sealing)

    Only ``label`` / ``auth`` / ``broker-client`` are returned — never
    username or endpoints. A write/delete attempt returns
    ``config_readonly``.

    Onboarding a new account: show the user a ``config.jmd`` ``# Account``
    block to add, plus (for basic auth) the keystore seed command; they
    apply both out-of-band. Mainstream endpoints: gmail.com →
    imap.gmail.com:993 + smtp.gmail.com:587 (App Password), outlook/365 →
    outlook.office365.com:993 + smtp-mail.outlook.com:587 (oauth2),
    ionos.de → imap.ionos.de:993 + smtp.ionos.de:587.

    Optional per-account keys worth suggesting: ``drafts-folder`` /
    ``sent-folder`` (explicit folder paths when SPECIAL-USE discovery
    picks wrong) and ``store-sent: false`` for Gmail (which stores
    sent mail server-side — avoids duplicates).
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
