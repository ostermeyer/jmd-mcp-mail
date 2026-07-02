# jmd-mcp-mail

[![CI](https://github.com/ostermeyer/jmd-mcp-mail/actions/workflows/ci.yml/badge.svg)](https://github.com/ostermeyer/jmd-mcp-mail/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

An MCP server that lets an LLM agent (Claude Desktop, Claude Code, …) work with your email — IMAP read/write/delete and SMTP send — using [JMD](https://github.com/ostermeyer/jmd-impl) as the on-the-wire format.

## What's special

- **Label addressing, out-of-band config.** Accounts live in one out-of-reach config file (`config.jmd`) that you author directly. Each tool call carries just an `account` label; the server resolves the endpoints and login internally, so your email address and the endpoints never enter the LLM context.
- **Credentials never enter the LLM context.** Passwords live in the OS keystore (macOS Keychain, Windows Credential Manager, Linux Secret Service). The server reads them via the platform's keystore CLI in its own process and uses them in IMAP/SMTP handshakes — they're never returned in any tool output, never logged.
- **Seeding stays out-of-band.** New keystore items are created by the user in their own terminal via a copy-paste shell command. The password is typed into the keystore CLI's tty-interactive prompt and never traverses any tool call.
- **JMD-native I/O.** Tool inputs and outputs are JMD documents (Markdown-shaped, LLM-friendly). Mail bodies round-trip Markdown ↔ HTML transparently.
- **Human-in-the-loop drafts.** The agent can store a finished mail as an IMAP draft instead of sending it — you review, edit and hit send in your own mail client.
- **Transparent AI footer.** Every *sent* message carries a short disclosure that it was composed by an AI assistant. Drafts carry none — by sending manually, you take authorship.

## Requirements

- Python ≥ 3.10.
- Runtime dependencies (pulled automatically by your installer):
  - [`jmd-format`](https://pypi.org/project/jmd-format/) ≥ 0.5 — the JMD reference implementation.
  - [`mcp[cli]`](https://pypi.org/project/mcp/) ≥ 1.0 — the Model Context Protocol SDK.
  - [`markdown`](https://pypi.org/project/Markdown/) ≥ 3.5 and [`markdownify`](https://pypi.org/project/markdownify/) ≥ 0.11 — Markdown ↔ HTML round-trip for message bodies.
- **All three desktop platforms**: macOS, Linux (GNOME/KDE via libsecret) and Windows. The credential resolver dispatches to the platform's native keystore at runtime — one wheel, no per-OS builds.
  - **Linux** additionally requires `secret-tool` (Debian/Ubuntu: `apt install libsecret-tools`; Fedora: `dnf install libsecret`) and an unlocked Secret Service backend (GNOME Keyring, KWallet via the libsecret bridge, …).

## Install

With [uv](https://github.com/astral-sh/uv):

```sh
uv tool install git+https://github.com/ostermeyer/jmd-mcp-mail.git
```

Or with [pipx](https://pipx.pypa.io/):

```sh
pipx install git+https://github.com/ostermeyer/jmd-mcp-mail.git
```

Either way you get a `jmd-mcp-mail` executable on `PATH`.

## Configure your MCP host

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or the equivalent for your platform:

```json
{
  "mcpServers": {
    "jmd-mcp-mail": {
      "command": "jmd-mcp-mail"
    }
  }
}
```

Restart Claude Desktop.

### Claude Code

```sh
claude mcp add jmd-mcp-mail jmd-mcp-mail
```

## Setting up credentials

Each account is set up in two out-of-band steps, both in your own terminal — never through a tool call: **(1)** add it to `config.jmd` (see *Account configuration* below), and **(2)** seed its password into the OS keystore as shown here. (OAuth2 accounts skip step 2 — see *OAuth2 accounts*.)

A Basic-Auth account needs **two keystore items** because IMAP (read/write/delete) and SMTP (send) are separate endpoints, each with its own authentication:

| Operation | Endpoint shape | Example (IONOS) |
|---|---|---|
| Read / write / delete | `imap.<provider>:993` | `imap.ionos.de:993` |
| Send | `smtp.<provider>:587` | `smtp.ionos.de:587` |

### macOS

For each endpoint, paste this in your terminal (replacing `…` with your endpoint and username):

```sh
security add-generic-password -s "imap.…:993" -a "you@…" -w
security add-generic-password -s "smtp.…:587" -a "you@…" -w
```

The `-w` (no value) makes `security` prompt for the password tty-interactively.  Type the password, retype to confirm.

### Linux (GNOME Keyring / KWallet via libsecret)

For each endpoint, paste this in your terminal (replacing `…` with your endpoint and username):

```sh
secret-tool store --label='jmd-mcp-mail' service "imap.…:993" username "you@…"
secret-tool store --label='jmd-mcp-mail' service "smtp.…:587" username "you@…"
```

`secret-tool` prompts for the password tty-interactively. The Secret Service backend (GNOME Keyring on GNOME, KWallet via the libsecret bridge on KDE, or any other libsecret-compatible store) must be unlocked at the time the server runs. `secret-tool` ships in the `libsecret-tools` package on Debian/Ubuntu and in `libsecret` on Fedora.

### Windows (Credential Manager)

For each endpoint, paste this in PowerShell or `cmd.exe` (replacing `…` and `<your-password>`):

```powershell
cmdkey /generic:"jmd-mcp-mail:imap.…:993:you@…" /user:"you@…" /pass:<your-password>
cmdkey /generic:"jmd-mcp-mail:smtp.…:587:you@…" /user:"you@…" /pass:<your-password>
```

The `jmd-mcp-mail:<service>:<username>` namespace prefix keeps these entries out of any plain `cmdkey /generic:<host>` credentials you may already have, and lets multiple accounts on the same host coexist (the Win32 Credential Manager keys generic credentials by `TargetName` alone).

If typing the password on the command line is uncomfortable, you can also seed via the **Control Panel → Credential Manager → Windows Credentials → Add a generic credential** UI, using the same composite `Internet or network address` (`jmd-mcp-mail:<service>:<username>`), the same `User name`, and your password.

### Don't know the endpoints?  Just ask the agent.

The LLM knows the canonical endpoints for mainstream providers (Gmail, Outlook/Office 365, IONOS, Fastmail, GMX, web.de, …). Say *"help me set up my IONOS account andreas@example.com"* and it will give you a ready-to-paste `config.jmd` block **and** the keystore seed commands to apply. If you read or send before seeding, the server returns `credential_missing` with the exact seed command, which the agent surfaces.

### Provider notes

| Provider | IMAP | SMTP | Notes |
|---|---|---|---|
| Gmail | `imap.gmail.com:993` | `smtp.gmail.com:587` | Requires an [App Password](https://support.google.com/accounts/answer/185833) (2FA must be enabled) |
| Outlook / Office 365 | `outlook.office365.com:993` | `smtp-mail.outlook.com:587` | Basic Auth disabled — use **OAuth2** (see *OAuth2 accounts* above) |
| IONOS | `imap.ionos.de:993` | `smtp.ionos.de:587` | Plain account password |
| Fastmail | `imap.fastmail.com:993` | `smtp.fastmail.com:587` | App-specific password required |
| GMX | `imap.gmx.net:993` | `mail.gmx.net:587` | IMAP must be enabled in account settings |
| web.de | `imap.web.de:993` | `smtp.web.de:587` | IMAP must be enabled in account settings |

## Account configuration (`config.jmd`)

Accounts are defined in **one commented JMD file you author directly**. The LLM never reads or writes it — so your email address and the endpoints stay out of the model context; tool calls carry only the `account` label. The file lives in a config directory:

| OS | Path |
|---|---|
| all platforms | `~/.jmd-mcp-mail/config.jmd` |

(Override the *directory* with `JMD_MCP_MAIL_HOME=…`, e.g. to keep it in a synced folder.)

**No passwords live here** — only routing metadata; passwords stay in the OS keystore (see *Setting up credentials*). Author it by hand:

```
# Account[]
- label: ionos
  imap: imap.ionos.de:993
  smtp: smtp.ionos.de:587
  username: andreas@ostermeyer.de
  from-name: Andreas Ostermeyer    # optional From-header display name
  drafts-folder: Entwürfe          # optional; else SPECIAL-USE/name lookup
  sent-folder: Gesendete Objekte   # optional; else SPECIAL-USE/name lookup
  store-sent: true                 # optional; false for Gmail (auto-stores)
- label: outlook
  imap: outlook.office365.com:993
  smtp: smtp-mail.outlook.com:587
  username: you@outlook.com
  auth: oauth2                     # basic (default) or oauth2
  broker-client: outlook           # required for oauth2
```

The `accounts` tool is **read-only** — it lets the agent see which labels exist, never their addresses:

- *"Show me my configured mail accounts."* → agent calls `accounts` with `# Account[]` and gets back labels only (with `auth`/`broker-client`).
- To add or change an account, edit `config.jmd` yourself (the agent can hand you a ready-to-paste block plus the keystore seed commands). There is no tool that writes the file — the username is personal data and must not flow through a tool call.

If you used the earlier `accounts.jmd` registry, it is migrated into `config.jmd` automatically on first run.

## OAuth2 accounts (Microsoft, Gmail, …)

Providers that disabled Basic Auth require OAuth2. This server does **not** run the OAuth2 flow itself — the [jmd-mcp-oauth2](https://github.com/ostermeyer/jmd-mcp-oauth2) token broker does. Mail receives a short-lived **sealed** access token per call and authenticates via XOAUTH2; the plaintext token never crosses a tool boundary, and no mailbox password is stored.

**One-time setup**

1. Define the account in `config.jmd` with `auth: oauth2` and a `broker-client` (the jmd-mcp-oauth2 client name) — see *Account configuration* above for the field shape.

2. Authorize the broker session once (device-code or browser login): in jmd-mcp-oauth2, `write` a `# OAuthSession { name: outlook }`.

**Per call** — the agent does this automatically (the `read`/`send` tool descriptions spell it out):

1. `accounts` → `# PublicKey` — this server's public key.
2. jmd-mcp-oauth2 `read` → `# OAuthToken { name: outlook, recipient-pubkey: <key> }` → a sealed `ciphertext`.
3. Any mail call with `access-token-sealed: <ciphertext>` in the document frontmatter.

Call an OAuth2 account without a token and the server replies with `oauth_token_required`, naming the broker-client and the steps.

**No keystore password** is needed for OAuth2 accounts. The only secrets are the broker's refresh token (held by jmd-mcp-oauth2) and this server's X25519 private key — generated once and kept in the OS keyring (32 bytes; well within every platform's credential-size limit).

## Tools

All four mail tools take `(account, document)` — `account` is a label from `config.jmd`, which the server resolves to the IMAP/SMTP endpoint and login. The `accounts` tool takes just `(document)`. All return a JMD document (data, query result, or `# Error`).

### `read` — IMAP read and query

`account` = a configured account label (IMAP side). Supports schema (`#! Folder`, `#! Message`), data reads (`# Folder[]`, `# Folder (path: …)`, `# Message (id: …, folder: …)`), and queries (`#? Folder`, `#? Message …`) with pagination (`page`, `page-size`, `count` frontmatter). Message queries filter on `from`/`to`/`cc`/`subject`/`body`, flag booleans (`seen`, `answered`, …) and date criteria `since`/`before`/`on` (ISO dates, server-side arrival date, day granularity). Non-ASCII search values are handled via `SEARCH CHARSET UTF-8` automatically. Messages expose `message-id`/`in-reply-to`/`references` for thread inspection.

Long bodies are **paginated, never silently truncated**: past one page (default 4000 chars, cut at line boundaries) the response carries `body-chars`/`body-pages`/`body-page` next to the page text; further pages via a `body-page: N` field, the whole body via `body-page-size: 0`. To *quote* a long mail in a reply, prefer `quote: true` on `write`/`send` — the server appends the full text itself.

### `write` — IMAP write

`account` = a configured account label (IMAP side).

- `# Folder { path: X }` — create a folder.
- `rename-to: Y` frontmatter + `# Folder { path: X }` — rename.
- `# Message` **without** `id` — **create a draft**: the message is stored in the Drafts folder with `\Draft`, for the user to finalize and send from their own mail client. At least one of `to`/`subject`/`body`; partial drafts are fine. No AI footer; `bcc` appears as a real header. Target folder: explicit `folder:` field > `drafts-folder` config key > SPECIAL-USE/name discovery.
- `# Message` with `id` **and** content fields — **replace a draft** (full restate, no field merge; append-then-delete, never a loss).
- `in-reply-to: <uid>` frontmatter (+ optional `in-reply-to-folder:`, default INBOX) — make the draft a threaded reply: In-Reply-To/References are set, `Re:` prefixed, `to` defaulted from the original.
- `quote: true` (with `in-reply-to`) — the server appends the original's **full text** below the reply body as a `> `-quoted block with attribution line, entirely server-side: the original never enters the LLM context, and the quote is byte-faithful.
- `# Message { id, folder, ## flags[] }` — replace message flags; `## flags-add[]` / `## flags-remove[]` change them incrementally (mutually exclusive with the replace form).
- `move-to: Y` or `copy-to: Y` frontmatter — move/copy a message between folders.

### `delete` — IMAP delete

`account` = a configured account label (IMAP side).  Strict frontmatter (unknown keys are refused, not silently dropped — this is destructive).

- `#- Message { id, folder }` — delete a single message.
- `#- Message[]` array — bulk delete.
- `#- Folder { path }` with `confirm: drop-folder` — irreversibly drop a folder and all its messages.

### `send` — SMTP send

`account` = a configured account label (SMTP side).  Body is a `# Message` with `to`, `subject`, `body` (Markdown).  Optional: `cc`, `bcc`, `## attachments[]`, `from-name` (overrides the account's default).

- **Sent copy** — after successful delivery the exact delivered bytes are stored in the Sent folder (`sent-folder` config key or SPECIAL-USE/name discovery). The response reports `sent-copy: stored | failed | disabled` — a failed copy never means a failed send. Disable with `store-sent: false` (Gmail stores server-side already).
- **Reply threading** — `in-reply-to: <uid>` frontmatter (+ optional `in-reply-to-folder:`) references the message being answered by its IMAP UID; the server sets In-Reply-To/References, prefixes `Re:` and defaults `to` from the original's Reply-To/From. Note: the *read-side field* `in-reply-to` on a Message is the RFC 5322 Message-ID header — a different plane than this UID-taking frontmatter key.
- **Server-side quoting** — add `quote: true` and the original's full text is appended below the body as a `> `-quoted block (attribution + blockquote), without the original ever entering the LLM context.
- Forwarding (`message/rfc822` embedding) is future work — quote the body and re-attach files manually for now.

### `accounts` — read-only account view

Read-only projection of `config.jmd` (see *Account configuration*):

- `#! Account` — schema.
- `# Account[]` — list configured accounts: `label` (plus `auth`, `broker-client`). Never the username or endpoints.
- `# PublicKey` — this server's X25519 public key for the OAuth2 sealing flow.

There is **no write path**: a `# Account { … }` (upsert) or `#- Account` (delete) returns `config_readonly`. Add or change accounts by editing `config.jmd`.

## Examples

Said to the agent, in natural language:

- *"Set up my Gmail account andreas@example.com."* → agent offers a `config.jmd` block plus the two keystore seed commands to apply.
- *"List the folders in my gmail account."* → agent calls `read` with `account: gmail` + `# Folder[]`.
- *"Show me the 10 most recent mails."* → agent calls `read` with `page-size: 10` + `#? Message`.
- *"Find unread mails from Alice in the last week."* → `#? Message` query with seen/from/since predicates.
- *"Send a quick reply saying 'Got it, thanks.' to message 42 in INBOX."* → agent calls `send` with `in-reply-to: 42` frontmatter — the thread stays intact and `to`/`subject` default from the original.
- *"Draft a reply to message 42 — I want to review it before it goes out."* → agent calls `write` with `in-reply-to: 42` and a `# Message` body; the draft lands in your Drafts folder for you to finalize and send.
- *"Move the newsletter from Fermania to the Archive folder."* → `write` with `move-to: Archive`.
- *"Mark message 7 as read but keep the flag."* → `write` with `## flags-add[] \Seen`.

## Troubleshooting

The server returns errors as JMD `# Error` documents with `status`, `code`, and a human-readable `message`.  The agent will read them and either fix the call or surface the issue to you.

| Code | Status | Cause / fix |
|---|---|---|
| `unknown_account` | 404 | No account with that label in `config.jmd`.  Add it out-of-band, or list labels via the `accounts` tool. |
| `config_readonly` | 405 | A write/delete was attempted through the `accounts` tool.  Edit `config.jmd` instead. |
| `credential_missing` | 401 | No keystore item for the account's `(service, username)`.  The error message contains the exact seed command — the agent will offer it to you. |
| `oauth_token_required` | 401 | The account is `auth: oauth2` but no sealed token was supplied. Fetch one from the named `broker-client` (jmd-mcp-oauth2) and retry with an `access-token-sealed:` frontmatter key. |
| `bad_sealed_token` | 400 | The `access-token-sealed` ciphertext could not be opened with this server's private key (wrong recipient key or corruption). Re-fetch a token sealed to the current `# PublicKey`. |
| `keystore_unavailable` | 500 | The platform's keystore backend could not be reached. macOS: `/usr/bin/security` missing or returned an unexpected error. Linux: `secret-tool` missing (install `libsecret-tools`) or no Secret Service daemon running. Windows: `advapi32!CredReadW` failed with an unexpected error code. |
| `auth_failed` | 401 | Server rejected the credentials.  Gmail/Outlook usually means "App Password required" — re-seed with the App Password instead of your account password. |
| `connection_error` | 500 | Network-level failure (DNS, timeout, TLS handshake).  Usually a typo in the endpoint or a flaky network. |
| `bad_request` | 400 | Malformed endpoint (e.g. missing `:port`) or invalid JMD document.  The agent should fix this itself. |
| `unknown_frontmatter_key` | 400 | A `delete` call had an unrecognised frontmatter key.  Destructive ops refuse rather than silently drop. |
| `missing_fields` | 400 | `send` needs `to`+`subject`+`body`; a draft needs at least one of them. |
| `attachment_not_found` | 400 | An `attachments[]` path does not exist on disk.  Nothing was sent or stored. |
| `no_drafts_folder` | 500 | No Drafts folder could be found or created.  Set `drafts-folder:` in `config.jmd` or pass a `folder:` field. |
| `imap_required` | 400 | `in-reply-to` on `send` needs the account's IMAP side (the value is an IMAP UID). |

## Security model

This server's threat model puts a wall between the LLM and your secrets:

1. **No keystore-MCP exists.**  There is no generic tool that exposes `keystore.read(…)` to the LLM.  A prompt-injected tool result therefore cannot exfiltrate credentials through this server.
2. **Read happens in the server process only.**  When the server needs a password, it asks the platform's keystore backend in its own process — `security -g` on macOS, `secret-tool lookup` on Linux, `advapi32!CredReadW` (via `ctypes`) on Windows — parses the result, and uses it directly in the IMAP/SMTP handshake.  Passwords are never returned in any tool output and never logged.
3. **Seeding happens out-of-band.**  The user types the password into the keystore CLI's tty-interactive prompt in their own terminal.  It does not traverse a tool call.

The remaining attack surface is the OS keystore itself (anyone with your unlocked user session can read it — on Linux/Windows there are no per-process ACLs, and macOS Keychain ACLs are not used by this server).  This matches the trust model of any other application reading credentials from the user's keystore.

## License

Copyright © 2026 Andreas Ostermeyer.

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).
Use it, fork it, extend it, ship it.

---

`src/mail_mcp/utf7.py` is cherry-picked from [imap_tools](https://github.com/ikvk/imap_tools) and remains under its original [MIT License](https://github.com/ikvk/imap_tools/blob/master/LICENSE).
