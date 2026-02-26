# Configuration

Apple Mail MCP is configured via environment variables. All settings have sensible defaults — no configuration is required to get started.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APPLE_MAIL_DEFAULT_ACCOUNT` | First account | Default email account for all tools |
| `APPLE_MAIL_DEFAULT_MAILBOX` | `INBOX` | Default mailbox when none specified |
| `APPLE_MAIL_INDEX_PATH` | `~/.apple-mail-mcp/index.db` | SQLite index database location |
| `APPLE_MAIL_INDEX_MAX_EMAILS` | `5000` | Max emails per mailbox to index |
| `APPLE_MAIL_INDEX_STALENESS_HOURS` | `24` | Hours before index is considered stale |
| `APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES` | `Drafts` | Comma-separated mailboxes to skip in search |

### Per-Mailbox Email Limit

`APPLE_MAIL_INDEX_MAX_EMAILS` (default: 5,000) limits how many emails are indexed per mailbox. When a mailbox exceeds this limit, the most recent emails by file modification time are kept.

This prevents the index from growing unbounded for large mailboxes. To index more:

```bash
export APPLE_MAIL_INDEX_MAX_EMAILS=10000
apple-mail-mcp rebuild
```

The `rebuild` command will report how many mailboxes hit the cap. Use `apple-mail-mcp status` to check your current index size.

## MCP Client Configuration

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mail": {
      "command": "apple-mail-mcp",
      "env": {
        "APPLE_MAIL_DEFAULT_ACCOUNT": "Work",
        "APPLE_MAIL_DEFAULT_MAILBOX": "INBOX"
      }
    }
  }
}
```

### Claude Code

Edit `.mcp.json` in your project root or `~/.claude/mcp.json` globally:

```json
{
  "mcpServers": {
    "mail": {
      "command": "apple-mail-mcp",
      "env": {
        "APPLE_MAIL_DEFAULT_ACCOUNT": "Work"
      }
    }
  }
}
```

### With Real-Time Indexing

To keep the search index automatically updated:

```json
{
  "mcpServers": {
    "mail": {
      "command": "apple-mail-mcp",
      "args": ["--watch"],
      "env": {
        "APPLE_MAIL_DEFAULT_ACCOUNT": "Work"
      }
    }
  }
}
```

## CLI Commands

```bash
apple-mail-mcp            # Run MCP server (default)
apple-mail-mcp serve      # Run MCP server explicitly
apple-mail-mcp --watch    # Run with real-time index updates
apple-mail-mcp index      # Build search index from disk
apple-mail-mcp status     # Show index statistics
apple-mail-mcp rebuild    # Force rebuild index
```

## Index Location

The FTS5 index database is stored at `~/.apple-mail-mcp/index.db` by default. Override with:

```bash
export APPLE_MAIL_INDEX_PATH="/path/to/custom/index.db"
```

The database file is created with `0600` permissions (owner read/write only) for security.
