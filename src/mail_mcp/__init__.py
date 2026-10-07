# SPDX-License-Identifier: Apache-2.0
"""JMD-native MCP server for email — IMAP/SMTP access for LLM agents."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("jmd-mcp-mail")
except PackageNotFoundError:
    __version__ = "unknown"
