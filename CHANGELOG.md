# Changelog

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
