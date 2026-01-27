"""JXA Mail MCP - Fast Apple Mail automation via optimized JXA scripts."""

from .server import mcp

__all__ = ["main", "mcp"]


def main() -> None:
    """Entry point for the MCP server."""
    mcp.run()
