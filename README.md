# jmd-mcp-mail

[![CI](https://github.com/ostermeyer/jmd-mcp-mail/actions/workflows/ci.yml/badge.svg)](https://github.com/ostermeyer/jmd-mcp-mail/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

An MCP server that lets an LLM agent (Claude Desktop, Claude Code, …) work with your email — IMAP read/write/delete and SMTP send — using [JMD](https://github.com/ostermeyer/jmd-impl) as the on-the-wire format.

## What's special

- **Per-call identity.** Each tool call carries `(service, username)` — the mail-server endpoint and the login. No global config file, no "configured accounts" to maintain. The LLM passes the identity for the account it's working with right now.
- **Credentials never enter the LLM context.** Passwords live in the OS keystore (macOS Keychain, Windows Credential Manager, Linux Secret Service). The server reads them via the platform's keystore CLI in its own process and uses them in IMAP/SMTP handshakes — they're never returned in any tool output, never logged.
- **Seeding stays out-of-band.** New keystore items are created by the user in their own terminal via a copy-paste shell command. The password is typed into the keystore CLI's tty-interactive prompt and never traverses any tool call.
- **JMD-native I/O.** Tool inputs and outputs are JMD documents (Markdown-shaped, LLM-friendly). Mail bodies round-trip Markdown ↔ HTML transparently.
- **Transparent AI footer.** Every sent message carries a short disclosure that it was composed by an AI assistant.

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

This is the only setup step.  You do it once per mail account, in your own terminal.

A mail account needs **two keystore items** because IMAP (read/write/delete) and SMTP (send) are separate endpoints, each with its own authentication:

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

The LLM knows the canonical endpoints for mainstream providers (Gmail, Outlook/Office 365, IONOS, Fastmail, GMX, web.de, …) and will offer you the exact copy-paste commands when you say something like *"set up my IONOS account andreas@example.com"*.  If you try to send or read first, the server returns an error that contains the exact seed command, and the agent will surface it for you.

### Provider notes

| Provider | IMAP | SMTP | Notes |
|---|---|---|---|
| Gmail | `imap.gmail.com:993` | `smtp.gmail.com:587` | Requires an [App Password](https://support.google.com/accounts/answer/185833) (2FA must be enabled) |
| Outlook / Office 365 | `outlook.office365.com:993` | `smtp.office365.com:587` | OAuth-only accounts work only with an App Password; XOAUTH2 not yet supported |
| IONOS | `imap.ionos.de:993` | `smtp.ionos.de:587` | Plain account password |
| Fastmail | `imap.fastmail.com:993` | `smtp.fastmail.com:587` | App-specific password required |
| GMX | `imap.gmx.net:993` | `mail.gmx.net:587` | IMAP must be enabled in account settings |
| web.de | `imap.web.de:993` | `smtp.web.de:587` | IMAP must be enabled in account settings |

## Account registry (optional)

If you don't want to type the full `(service, username)` pair for every call, you can register short labels for your accounts and refer to them by label later. The registry lives in a small JMD file on disk:

| OS | Path |
|---|---|
| Windows | `%APPDATA%\jmd-mcp-mail\accounts.jmd` |
| macOS | `~/Library/Application Support/jmd-mcp-mail/accounts.jmd` |
| Linux | `$XDG_CONFIG_HOME/jmd-mcp-mail/accounts.jmd` (default `~/.config/jmd-mcp-mail/accounts.jmd`) |

(Override the location with `JMD_MCP_MAIL_ACCOUNTS_PATH=…` if you want, e.g. to keep it in a synced folder.)

**No passwords live here** — only the metadata (`label`, `imap_service`, `smtp_service`, `username`). The actual credentials stay in the OS keystore, looked up by `(service, username)` at tool-call time exactly as before. The registry is just a convenience layer.

You'll usually never edit the file by hand. The agent does it through the `accounts` tool:

- *"Show me my configured mail accounts."* → agent calls `accounts` with `# Account[]`.
- *"Save my IONOS account andreas@ostermeyer.de as `ionos`."* → agent calls `accounts` with a `# Account { … }` upsert, and (if the keystore items don't yet exist) also offers you the two seed commands.
- *"Drop the old work account."* → agent calls `accounts` with `#- Account { label: work }`. The keystore items themselves are not touched — drop those with your platform's CLI if you want them gone too.

## Tools

All four mail tools take `(service, username, document)`; the `accounts` tool takes just `(document)`.  All return a JMD document (data, query result, or `# Error`).

### `read` — IMAP read and query

`service` = IMAP endpoint.  Supports schema (`#! Folder`, `#! Message`), data reads (`# Folder[]`, `# Folder (path: …)`, `# Message (id: …, folder: …)`), and queries (`#? Folder`, `#? Message …`) with pagination (`page`, `page-size`, `count` frontmatter).

### `write` — IMAP write

`service` = IMAP endpoint.

- `# Folder { path: X }` — create a folder.
- `rename-to: Y` frontmatter + `# Folder { path: X }` — rename.
- `# Message { id, folder, ## flags[] }` — set message flags.
- `move-to: Y` or `copy-to: Y` frontmatter — move/copy a message between folders.

### `delete` — IMAP delete

`service` = IMAP endpoint.  Strict frontmatter (unknown keys are refused, not silently dropped — this is destructive).

- `#- Message { id, folder }` — delete a single message.
- `#- Message[]` array — bulk delete.
- `#- Folder { path }` with `confirm: drop-folder` — irreversibly drop a folder and all its messages.

### `send` — SMTP send

`service` = SMTP endpoint.  Body is a `# Message` with `to`, `subject`, `body` (Markdown).  Optional: `cc`, `bcc`, `## attachments[]`.

### `accounts` — labelled-account registry

Single tool that dispatches by JMD mode against the on-disk registry described in *Account registry* above:

- `#! Account` — schema.
- `# Account[]` — list all configured accounts (sorted by label).
- `# Account { label, imap_service, smtp_service, username }` — upsert by label (insert or replace).
- `#- Account { label }` — delete the account with `label`. Keystore items are *not* touched.

The registry stores **no passwords** — only the metadata. An upsert without matching keystore items is a valid intermediate state; the next read/send call will then return `credential_missing` with the seed command, which the agent will surface to you.

## Examples

Said to the agent, in natural language:

- *"Set up my Gmail account andreas@example.com."* → agent offers the two seed commands.
- *"List the folders in my INBOX."* → agent calls `read` with `# Folder[]`.
- *"Show me the 10 most recent mails."* → agent calls `read` with `page-size: 10` + `#? Message`.
- *"Find unread mails from Alice in the last week."* → `#? Message` query with seen/from/since predicates.
- *"Send a quick reply saying 'Got it, thanks.' to message 42 in INBOX."* → agent reads message 42 to get the sender, then calls `send`.
- *"Move the newsletter from Fermania to the Archive folder."* → `write` with `move-to: Archive`.

## Troubleshooting

The server returns errors as JMD `# Error` documents with `status`, `code`, and a human-readable `message`.  The agent will read them and either fix the call or surface the issue to you.

| Code | Status | Cause / fix |
|---|---|---|
| `credential_missing` | 401 | No keystore item for `(service, username)`.  The error message contains the exact seed command — the agent will offer it to you. |
| `keystore_unavailable` | 500 | The platform's keystore backend could not be reached. macOS: `/usr/bin/security` missing or returned an unexpected error. Linux: `secret-tool` missing (install `libsecret-tools`) or no Secret Service daemon running. Windows: `advapi32!CredReadW` failed with an unexpected error code. |
| `auth_failed` | 401 | Server rejected the credentials.  Gmail/Outlook usually means "App Password required" — re-seed with the App Password instead of your account password. |
| `connection_error` | 500 | Network-level failure (DNS, timeout, TLS handshake).  Usually a typo in the endpoint or a flaky network. |
| `bad_request` | 400 | Malformed endpoint (e.g. missing `:port`) or invalid JMD document.  The agent should fix this itself. |
| `unknown_frontmatter_key` | 400 | A `delete` call had an unrecognised frontmatter key.  Destructive ops refuse rather than silently drop. |

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
