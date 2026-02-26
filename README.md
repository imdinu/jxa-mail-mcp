# Apple Mail MCP

<!-- mcp-name: io.github.imdinu/apple-mail-mcp -->

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://www.apple.com/macos/)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![CI](https://github.com/imdinu/apple-mail-mcp/actions/workflows/lint.yml/badge.svg)](https://github.com/imdinu/apple-mail-mcp/actions/workflows/lint.yml)

A fast MCP server for Apple Mail — **87x faster** email fetching via batch JXA, plus an FTS5 search index for **700–3500x faster** body search (~2ms vs ~7s).

**[Read the docs](https://imdinu.github.io/apple-mail-mcp/)** for the full guide.

## Quick Start

```bash
pipx install apple-mail-mcp
```

Add to your MCP client:

```json
{
  "mcpServers": {
    "mail": {
      "command": "apple-mail-mcp"
    }
  }
}
```

### Build the Search Index (Recommended)

```bash
# Requires Full Disk Access for Terminal
# System Settings → Privacy & Security → Full Disk Access → Add Terminal

apple-mail-mcp index --verbose
```

## Tools

| Tool | Purpose |
|------|---------|
| `list_accounts()` | List email accounts |
| `list_mailboxes(account?)` | List mailboxes |
| `get_emails(filter?, limit?)` | Get emails — all, unread, flagged, today, this_week |
| `get_email(message_id)` | Get single email with full content + attachments |
| `search(query, scope?)` | Search — all, subject, sender, body, attachments |
| `get_attachment(message_id, filename)` | Extract attachment content (base64) |

## Performance

| Scenario | Apple Mail MCP | Best alternative | Speedup |
|----------|---------------|-----------------|---------|
| Fetch 50 emails | 529ms | 15,288ms | **29x** |
| Body search | ~2ms | ~7,000ms (or unsupported) | **3500x** |
| List accounts | 108ms | 146ms | Fastest |

> Benchmarked against [7 other Apple Mail MCP servers](https://imdinu.github.io/apple-mail-mcp/benchmarks/) at the MCP protocol level.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `APPLE_MAIL_DEFAULT_ACCOUNT` | First account | Default email account |
| `APPLE_MAIL_DEFAULT_MAILBOX` | `INBOX` | Default mailbox |
| `APPLE_MAIL_INDEX_PATH` | `~/.apple-mail-mcp/index.db` | Index location |
| `APPLE_MAIL_INDEX_MAX_EMAILS` | `5000` | Max emails indexed per mailbox |
| `APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES` | `Drafts` | Mailboxes to skip in search |

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

## Migrating from apple-mcp?

If you used [supermemoryai/apple-mcp](https://github.com/supermemoryai/apple-mcp) (archived January 2026), apple-mail-mcp is a maintained alternative for the **Mail portion** specifically. Notes, Messages, Contacts, Calendar, and Reminders are out of scope.

| apple-mcp (`mail` tool, action) | apple-mail-mcp |
|----------------------------------|----------------|
| `read_emails` | `get_emails(filter?, limit?)` + `get_email(message_id)` |
| `search_emails` | `search(query, scope?)` — 5 scopes: all, subject, sender, body, attachments |
| `send_email` | Not yet supported (planned) |

**What's different:** available on PyPI (`pipx install apple-mail-mcp`), 87x faster email fetching via batch JXA, FTS5 search index for ~2ms body search, and disk-based sync that avoids the JXA timeouts and false-success responses.

## Development

```bash
git clone https://github.com/imdinu/apple-mail-mcp
cd apple-mail-mcp
uv sync
uv run ruff check src/
uv run pytest
```

## License

GPL-3.0-or-later
