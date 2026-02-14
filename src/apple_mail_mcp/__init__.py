"""Apple Mail MCP - Fast MCP server for Apple Mail with FTS5 search.

Features:
- 87x faster email fetching via batch property fetching
- FTS5 full-text search index for 700-3500x faster body search

Usage:
    apple-mail-mcp            # Run MCP server (default)
    apple-mail-mcp index      # Build search index from disk
    apple-mail-mcp status     # Show index statistics
    apple-mail-mcp rebuild    # Force rebuild index
"""

from .cli import main
from .server import mcp

__all__ = ["main", "mcp"]
