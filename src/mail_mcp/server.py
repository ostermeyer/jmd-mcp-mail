"""JMD MCP server for email — SMTP + IMAP.

Exposes three tools — read, write, delete — using JMD as the message
format.  SMTP handles outgoing mail; IMAP handles reading and deletion.

Configuration is read from ~/.config/jmd/mail.jmd.
The account password must be stored in the OS keyring under
service='jmd-mcp-mail', username=<configured username>.

Usage::

    jmd-mcp-mail
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import config, imap, smtp

_INSTRUCTIONS = """
This server provides email access via SMTP (send) and IMAP (read/delete)
using JMD as the message format.

## Configuration

Create ~/.config/jmd/mail.jmd:

  # MailConfig
  smtp_host: smtp.example.com
  smtp_port: 587
  imap_host: imap.example.com
  imap_port: 993
  username: you@example.com

Store your password in the keyring (via jmd-mcp-keyring):

  write("# Credentials\\nservice: jmd-mcp-mail\\n"
        "username: you@example.com\\npassword: your-password")

## Sending a message

  write("# Message\\nto: recipient@example.com\\n"
        "subject: Hello\\nbody: Message text")

Optional fields: cc, bcc (comma-separated addresses).

## Reading messages

List recent messages from INBOX (headers only):

  read("#? Message")

Filter by folder:

  read("#? Message\\nfolder: Sent")

Filter by sender (substring):

  read("#? Message\\nfrom: ~gmail")

Filter by subject:

  read("#? Message\\nsubject: ~invoice")

Fetch one message with full body:

  read("# Message\\nid: 12345\\nfolder: INBOX")

Describe schema:

  read("#! Message")

## Deleting a message

  delete("#- Message\\nid: 12345\\nfolder: INBOX")

## Error handling

All errors return a # Error document:

  # Error
  status: 404
  code: not_found
  message: Message 12345 not found in INBOX
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
def read(document: str) -> str:
    """Read email messages using a JMD document.

    Schema document (#! Message): describe the message structure.

    Data document (# Message): fetch one message by id.
    Requires id field; folder defaults to INBOX.

        # Message
        id: 12345
        folder: INBOX

    Query document (#? Message): list and filter messages.
    Returns headers only (no body). Folder defaults to INBOX.

        #? Message
        folder: INBOX
        from: ~example.com
        subject: ~invoice
    """
    try:
        return imap.read(document, _get_cfg())
    except (FileNotFoundError, ValueError) as e:
        return imap._error(500, "config_error", str(e))


@mcp.tool()
def write(document: str) -> str:
    """Send an email using a JMD Message document.

    Requires to, subject, and body fields.

        # Message
        to: recipient@example.com
        subject: Hello
        body: Message text here

    Optional fields: cc, bcc (comma-separated addresses).
    Returns a confirmation document on success.
    """
    try:
        return smtp.send(document, _get_cfg())
    except (FileNotFoundError, ValueError) as e:
        return smtp._error(500, "config_error", str(e))


@mcp.tool()
def delete(document: str) -> str:
    r"""Delete an email using a JMD delete document.

    Requires id and folder fields.

        #- Message
        id: 12345
        folder: INBOX

    The message is permanently deleted (flagged \\Deleted + EXPUNGE).
    """
    try:
        return imap.delete(document, _get_cfg())
    except (FileNotFoundError, ValueError) as e:
        return imap._error(500, "config_error", str(e))


def main() -> None:
    """Entry point: start the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
