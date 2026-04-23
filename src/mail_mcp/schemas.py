# SPDX-License-Identifier: Apache-2.0
"""JMD schema strings for all mail resource types."""
from __future__ import annotations

MAILBOX: str = """\
see-also: Folder, Message, EmailAddress

#! MailBox
name: string readonly
username: string email readonly
imap-host: string readonly
imap-port: integer readonly
smtp-host: string readonly
smtp-port: integer readonly"""

FOLDER: str = """\
#! Folder
name: string readonly
path: string readonly
mailbox: string optional
parent: string readonly optional
delim: string readonly
messages: integer readonly optional
unseen: integer readonly optional

## flags[]: string readonly optional"""

EMAIL_ADDRESS: str = """\
#! EmailAddress
name: string optional
email: string email"""

MESSAGE: str = """\
#! Message
id: string readonly
folder: string readonly
mailbox: string optional
subject: string readonly
date: string datetime readonly
size: integer readonly optional
body: string optional

## from: EmailAddress readonly
## to[]: EmailAddress readonly
## cc[]: EmailAddress readonly optional
## bcc[]: EmailAddress readonly optional
## reply-to[]: EmailAddress readonly optional
## flags[]: string optional
## attachments[]: object optional
- filename: string
  content-type: string
  content-id: string optional
  size: integer
  path: string optional"""
