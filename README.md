# jmd-mcp-mail

An MCP server for email — IMAP and SMTP access for LLM agents, using [JMD](https://github.com/ostermeyer/jmd-impl) as the message format.

## Features

- **Multi-account** — configure any number of IMAP/SMTP accounts
- **Full IMAP CRUD** — read, search, flag, move, copy, delete messages and folders
- **SMTP send** — compose and send email with Markdown bodies
- **Markdown round-trip** — outgoing Markdown is rendered to HTML; incoming HTML is converted back to Markdown
- **Transparent AI footer** — every sent message carries a disclosure that it was composed by an AI assistant
- **Secure credentials** — passwords stored in the OS keyring, never in plain text

## Installation

```bash
pip install git+https://github.com/ostermeyer/jmd-mcp-mail.git
```

Or with [uv](https://github.com/astral-sh/uv):

```bash
uv add git+https://github.com/ostermeyer/jmd-mcp-mail.git
```

## Configuration

Create `~/.config/jmd/mail.jmd`:

```
# MailConfig[]
- name: myaccount
  smtp-host: smtp.example.com
  smtp-port: 587
  imap-host: imap.example.com
  imap-port: 993
  username: you@example.com
```

Store the password in the OS keyring (via [jmd-mcp-keyring](https://github.com/ostermeyer/jmd-mcp-keyring) or directly):

```python
import keyring
keyring.set_password("jmd-mcp-mail", "you@example.com", "your-password")
```

## Claude Desktop / Claude Code setup

Add to your MCP configuration:

```json
{
  "mcpServers": {
    "jmd-mcp-mail": {
      "command": "jmd-mcp-mail"
    }
  }
}
```

## Usage

The server exposes four tools: `read`, `write`, `delete`, `send`.

### List folders

```
# Folder[]
```

### Search messages

```
#? Message
folder: INBOX
from: ~alice
```

### Read a message

```
# Message
id: 42
folder: INBOX
```

### Send a message

```
# Message
to: recipient@example.com
subject: Hello
body:
> Message body in **Markdown**.
```

### Move a message

```
move-to: Archive

# Message
id: 42
folder: INBOX
```

### Multi-account routing

Add `mailbox: <name>` to any document to select a specific account:

```
mailbox: myaccount

#? Message
folder: INBOX
```

## License

Copyright © 2026 Andreas Ostermeyer.
Licensed under the [GNU Affero General Public License v3.0](LICENSE).

`src/mail_mcp/utf7.py` is cherry-picked from [imap_tools](https://github.com/ikvk/imap_tools)
and remains under its original [MIT License](https://github.com/ikvk/imap_tools/blob/master/LICENSE).
