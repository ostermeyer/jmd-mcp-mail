"""JMD MCP server for email — SMTP sender (Phase 1).

Exposes a single write tool for sending emails via SMTP.
IMAP read/delete support will follow in a later phase.

Configuration is read from ~/.config/jmd/mail.jmd.
The account password must be stored in the OS keyring under
service='jmd-mcp-mail', username=<configured username>.

Usage::

    jmd-mcp-mail
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import config, smtp

_INSTRUCTIONS = """
This server sends email via SMTP using JMD as the message format.

## Configuration

Create ~/.config/jmd/mail.jmd:

  # SMTPConfig
  host: smtp.gmail.com
  port: 587
  username: you@example.com

Store your password in the keyring (via jmd-mcp-keyring):

  write("# Credentials\\nservice: jmd-mcp-mail\\nusername: you@example.com\\npassword: your-app-password")

## Sending a message

  write("# Message\\nto: recipient@example.com\\nsubject: Hello\\nbody: Message text")

Multiple recipients (comma-separated):

  write("# Message\\nto: alice@example.com, bob@example.com\\ncc: charlie@example.com\\nsubject: Meeting\\nbody: See you there.")

## Fields

  to:      required — one or more recipient addresses (comma-separated)
  subject: required — message subject
  body:    required — plain text body
  cc:      optional — carbon copy addresses (comma-separated)
  bcc:     optional — blind carbon copy addresses (comma-separated)

## Error handling

All errors return a # Error document:

  # Error
  status: 401
  code: auth_failed
  message: SMTP authentication failed
"""

mcp = FastMCP("jmd-mcp-mail", instructions=_INSTRUCTIONS)

_cfg: config.SMTPConfig | None = None


def _get_cfg() -> config.SMTPConfig:
    """Return cached SMTP config, loading on first call."""
    global _cfg
    if _cfg is None:
        _cfg = config.load()
    return _cfg


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
        cfg = _get_cfg()
    except (FileNotFoundError, ValueError) as e:
        return smtp._error(500, "config_error", str(e))
    return smtp.send(document, cfg)


def main() -> None:
    """Entry point: start the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
