"""JXA Mail MCP - Fast Apple Mail automation via optimized JXA scripts.

Features:
- 87x faster email fetching via batch property fetching
- FTS5 full-text search index for ~100x faster body search
- Fuzzy search with trigram + Levenshtein matching

Usage:
    jxa-mail-mcp            # Run MCP server (default)
    jxa-mail-mcp index      # Build search index from disk
    jxa-mail-mcp status     # Show index statistics
    jxa-mail-mcp rebuild    # Force rebuild index
"""

from .cli import main
from .server import mcp

__all__ = ["main", "mcp"]
