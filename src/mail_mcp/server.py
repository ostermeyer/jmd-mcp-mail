"""JMD MCP server for email — IMAP + SMTP.

Four tools: read, write, delete (IMAP), send (SMTP).

Configuration: ~/.config/jmd/mail.jmd
Password: OS keyring, service='jmd-mcp-mail', username=<configured username>
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mail_mcp import config, smtp
from mail_mcp.imap import delete as imap_delete
from mail_mcp.imap import read as imap_read
from mail_mcp.imap import write as imap_write
from mail_mcp.imap.read import _error

_INSTRUCTIONS = """
This server provides email access via IMAP (read/write/delete) and SMTP (send)
using JMD as the message format.  Four resource types are supported:

  MailBox      — account identity and connection info (read-only)
  Folder       — IMAP mailboxes (full CRUD)
  Message      — email messages (read, flag-update, move, copy, delete)
  EmailAddress — embedded in Message (not a standalone resource)

## Configuration

Create ~/.config/jmd/mail.jmd:

  # MailConfig
  smtp-host: smtp.example.com
  smtp-port: 587
  imap-host: imap.example.com
  imap-port: 993
  username: you@example.com

Store password in keyring (via jmd-mcp-keyring):

  write("# Credentials\\nservice: jmd-mcp-mail\\n"
        "username: you@example.com\\npassword: secret")

## Folder navigation

  read("# MailBox")                    → account info
  read("# Folder[]")                   → all root folders
  read("# Folder\\npath: INBOX")       → folder detail + counts
  read("#? Folder\\nparent: INBOX")    → subfolders of INBOX
  write("# Folder\\npath: Archive")    → create folder
  write("rename-to: Old\\n\\n# Folder\\npath: Archive")  → rename
  delete("#- Folder\\npath: Archive")  → delete folder

## Message operations

  read("#? Message\\nfolder: INBOX")                → list (headers only)
  read("#? Message\\nfrom: ~alice")                 → filter by sender
  read("# Message\\nid: 42\\nfolder: INBOX")        → full message with body
  read("download-path: ~/Downloads\\n\\n# Message\\nid: 42\\nfolder: INBOX")
                                                    → download attachments
  delete("#- Message\\nid: 42\\nfolder: INBOX")     → delete message

## Message write operations

Updating flags (\\Seen is set by the human, NOT automatically on read):

  write("# Message\\nid: 42\\nfolder: INBOX\\n## flags[]\\n- \\\\Seen")

Moving a message (WARNING: requires two IMAP round-trips):

  write("move-to: Archive\\n\\n# Message\\nid: 42\\nfolder: INBOX")

Copying a message (WARNING: requires two IMAP round-trips):

  write("copy-to: Backup\\n\\n# Message\\nid: 42\\nfolder: INBOX")

## Sending email

  send("# Message\\nto: alice@example.com\\n"
       "subject: Hello\\nbody: Message text")

Optional: cc, bcc (comma-separated), ## attachments[] with path fields.

## Important notes

- Messages are NEVER implicitly marked as \\Seen when read.
  Only set \\Seen explicitly when the human has actually read the message.
- move-to and copy-to require a second IMAP round-trip to resolve the new
  UID via HEADER Message-ID search. Use sparingly.
"""

mcp = FastMCP("jmd-mcp-mail", instructions=_INSTRUCTIONS)

_cfg: config.MailConfig | None = None


def _get_cfg() -> config.MailConfig:
    """Return cached mail config, loading on first call."""
    global _cfg
    if _cfg is None:
        _cfg = config.load()
    return _cfg


@mcp.tool()
async def read(document: str) -> str:
    """Read IMAP resources using a JMD document.

    Supported labels: MailBox, Folder, Folder[], Message.

    Schema:    #! MailBox / #! Folder / #! Message / #! EmailAddress
    Read:      # MailBox | # Folder[] | # Folder (path: X)
               # Message (id: X, folder: Y)
    Query:     #? Folder [parent: X]
               #? Message [folder: X, from: ~X, subject: ~X]

    Pagination frontmatter: page, page-size, count (before the #? heading).
    """
    try:
        return await imap_read.read(document, _get_cfg())
    except (FileNotFoundError, ValueError) as exc:
        return _error(500, "config_error", str(exc))


@mcp.tool()
async def write(document: str) -> str:
    r"""Write to IMAP: create/rename folders, update message flags, move/copy.

    Folder operations:
      # Folder  path: Archive              → create
      rename-to: New\\n\\n# Folder path: Old → rename

    Message flag update:
      # Message  id: 42  folder: INBOX
      ## flags[]
      - \\Seen

    Move/copy (frontmatter, two round-trips — use deliberately):
      move-to: Archive\\n\\n# Message  id: 42  folder: INBOX
      copy-to: Backup\\n\\n# Message  id: 42  folder: INBOX
    """
    try:
        return await imap_write.write(document, _get_cfg())
    except (FileNotFoundError, ValueError) as exc:
        return _error(500, "config_error", str(exc))


@mcp.tool()
async def delete(document: str) -> str:
    r"""Delete an IMAP resource using a JMD delete document.

    Folder:  #- Folder  path: Archive
    Message: #- Message  id: 42  folder: INBOX

    The deleted resource is returned as a full JMD data document.
    Message deletion is permanent (\\Deleted + EXPUNGE).
    """
    try:
        return await imap_delete.delete(document, _get_cfg())
    except (FileNotFoundError, ValueError) as exc:
        return _error(500, "config_error", str(exc))


@mcp.tool()
def send(document: str) -> str:
    """Send an email via SMTP using a JMD Message document.

    Required fields: to, subject, body (Markdown).
    Optional fields: cc, bcc (comma-separated addresses).

      # Message
      to: alice@example.com
      subject: Hello
      body:
      > Message text in **Markdown**

    Attachments via ## attachments[] with path fields (local file paths).
    Returns a confirmation document on success.
    """
    try:
        return smtp.send(document, _get_cfg())
    except (FileNotFoundError, ValueError) as exc:
        return smtp._error(500, "config_error", str(exc))


def main() -> None:
    """Entry point: start the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
