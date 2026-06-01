# Changelog

## Unreleased

### Added

- Cross-platform credential resolution. The keystore-read path now dispatches at runtime to the platform's native backend on all three desktop OSes:
  - **Linux** — `secret-tool lookup service <s> username <u>` against any libsecret-compatible Secret Service backend (GNOME Keyring, KWallet via the bridge, …). Requires the `libsecret-tools` package (`apt install libsecret-tools` / `dnf install libsecret`).
  - **Windows** — `advapi32!CredReadW` via `ctypes`. No PowerShell subprocess; no extra dependencies beyond stdlib `ctypes`. The Credential Manager `TargetName` is namespaced as `jmd-mcp-mail:<service>:<username>` so multiple accounts on the same host coexist (the Win32 store keys generic credentials by `TargetName` alone) and so jmd-mcp-mail's entries do not collide with any existing `cmdkey /generic:<host>` credentials.
  - **macOS** — unchanged (`security find-generic-password -g`).
- One executable still serves all three platforms — no per-OS wheel, no native build step. Dispatch happens at runtime via `sys.platform`.
- Symmetric round-trip tests for Linux and Windows in `tests/test_credentials.py`, each skipping cleanly when the platform or backend isn't available (the Linux test additionally probes for a working Secret Service daemon before running).

### Added (Account registry)

- New `accounts` MCP tool and `src/mail_mcp/accounts.py` module — a small JSON-shaped-via-JMD on-disk registry of labelled `(imap_service, smtp_service, username)` triples so the LLM can refer to a mail account by short labels (`ionos`, `gmail-work`) instead of typing the full pair on every call.
- Stored at `%APPDATA%\jmd-mcp-mail\accounts.jmd` (Windows), `~/Library/Application Support/jmd-mcp-mail/accounts.jmd` (macOS), `$XDG_CONFIG_HOME/jmd-mcp-mail/accounts.jmd` (Linux). Overrideable via `JMD_MCP_MAIL_ACCOUNTS_PATH`.
- **No passwords stored** — the registry holds metadata only. Threat model unchanged: a prompt-injected tool result can read labels and endpoints, never a password. The keystore remains the only place credentials live.
- Tool surface (single dispatch by JMD mode): `#! Account` (schema), `# Account[]` (list), `# Account { … }` (upsert by label), `#- Account { label }` (delete). Atomic writes (tmp + `os.replace`). Endpoint validation reuses the production `parse_endpoint` so an account that wouldn't connect can't be saved.
- 24 unit tests in `tests/test_accounts.py` (storage round-trip, label-sorted writes, upsert-replace semantics, validation rejects, atomic-write tmp-file cleanup, all four dispatcher modes, Unicode labels).

### Docs

- README's *Setting up credentials* section now documents the seed commands for all three platforms. The "Linux and Windows are stubbed" notice is removed.
- `keystore_unavailable` troubleshooting row updated to cover all three backends.


## 0.2.1 — 2026-05-17

### Fixed

- HTML message bodies no longer triggered an MTA URL-mangling bug at IONOS (and possibly other MTAs with buggy SMTP dot-stuffing handling).  When Python's email module wrapped a long QP-encoded line such that the continuation began with `.` (e.g. the wrap landing right before `.com/` in a footer URL), some MTAs added two dots instead of removing one — producing `...com/` in the delivered message.  `smtp._deliver` now pre-escapes leading `.` characters to `=2E` (RFC 2045 §6.7's canonical QP form for `.`), defensively avoiding the trigger.  RFC-compliant in both directions; standards-conforming MUAs see the original `.` after QP-decode.

### Docs

- Linux and Windows credential-setup sections demoted to "Not yet implemented" with a stated plan for the next release (already in HEAD since v0.2 + a doc patch).


## 0.2 — 2026-05-17

**Breaking change.** Anyone running 0.1 with `~/.config/jmd/mail.jmd` will need to seed two OS-keystore items per mail account and remove the config file.  See the README's *Setting up credentials* section.

### Changed

- Tool signatures now carry the connection identity per call: `(service, username, document)`.  `service` is the mail-server endpoint (`host:port`); `username` is the IMAP/SMTP login.  The password is resolved from the OS keystore under `(service, username)` and used only inside the server process — it never enters tool output or LLM context.
- TLS mode is inferred from the port (`465`/`993` → implicit TLS, `587`/`143` → STARTTLS, everything else → STARTTLS attempt).
- macOS keystore access goes through `security -g`, parsing stderr deterministically (`password: 0x<hex>` vs `password: "string"` — no heuristics).  Linux + Windows resolver paths stubbed for now.
- README rewritten as full end-user documentation: install (`uv tool` / `pipx`), Claude Desktop / Claude Code setup, per-platform keystore seeding (`security` / `secret-tool` / `cmdkey`), provider table, tool reference, natural-language examples, troubleshooting table, security-model section.
- `read` tool docstring instructs the LLM how to help the user seed missing credentials: surface the platform-appropriate seed command from the structured `credential_missing` error as a copy-paste shell block; never execute it itself.

### Removed

- `src/mail_mcp/config.py` and the `~/.config/jmd/mail.jmd` configuration file.  The "configured accounts" abstraction is gone — the LLM passes `(service, username)` per call.
- `MailBox` / `MailBox[]` JMD label (was the multi-account enumeration view, now meaningless).  Tool grammar narrowed to `Folder`, `Folder[]`, `Message`, `Message[]`.
- `mailbox:` frontmatter key (routing + debug channel).
- `keyring` Python dependency.  Six transitive packages dropped from `uv.lock`.
- `jmd-mcp-keyring` companion server.  Seeding is out-of-band via the platform's keystore CLI, in the user's own terminal.

### Fixed

- `read` tool docstring no longer advertises inline-parenthesised (`# Message (id: X, folder: Y)`) or inline-bracketed (`#? Message [from: ~X]`) field syntax — the JMD parser silently drops these and returns empty structures.  The supported form is body fields on lines after the heading.

### Verified

- 96 unit tests passing.  Ruff + Mypy `--strict` clean across `src/` and `tests/`.
- End-to-end against IONOS: IMAP read/query/STATUS, SMTP send, both keystore items resolved through the new CLI path.

## 0.1 — 2026-03-29

Initial release.  Multi-account email server with a `~/.config/jmd/mail.jmd` config file and per-account keystore entries under a single service name.
