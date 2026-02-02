# JXA Mail MCP

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://www.apple.com/macos/)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Coverage](https://img.shields.io/badge/coverage-52%25-yellow.svg)](https://github.com/imdinu/jxa-mail-mcp)

A fast MCP (Model Context Protocol) server for Apple Mail, using optimized JXA (JavaScript for Automation) scripts with batch property fetching for **87x faster** performance, plus an optional **FTS5 search index** for **700-3500x faster** body search (~2ms vs ~7s).

## Features

### Email Tools (5 total)

| Tool | Purpose | Parameters |
|------|---------|------------|
| `list_accounts()` | List email accounts | - |
| `list_mailboxes(account?)` | List mailboxes | account (optional) |
| `get_emails(...)` | Unified email listing | account?, mailbox?, filter?, limit? |
| `get_email(id)` | Get single email with content | message_id |
| `search(query, ...)` | Unified search with FTS5 | query, scope?, limit? |

### Unified `get_emails()` Filters

```python
get_emails()                      # All emails (default)
get_emails(filter="unread")       # Unread emails only
get_emails(filter="flagged")      # Flagged emails only
get_emails(filter="today")        # Emails received today
get_emails(filter="this_week")    # Emails from last 7 days
```

### Unified `search()` Scopes

```python
search("invoice")                          # Search everywhere (uses FTS5)
search("john@example.com", scope="sender") # Sender only
search("meeting notes", scope="subject")   # Subject only
search("deadline", scope="body")           # Body content only
```

## Installation

### No installation required

Use `pipx run` to run directly from PyPI:

```bash
pipx run jxa-mail-mcp
```

### With pipx (optional)

For faster startup, install globally:

```bash
pipx install jxa-mail-mcp
```

### From source

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/imdinu/jxa-mail-mcp
cd jxa-mail-mcp
uv sync
```

## Quick Start

### 1. Add to Claude Code

```json
{
  "mcpServers": {
    "mail": {
      "command": "jxa-mail-mcp"
    }
  }
}
```

### 2. Build the Search Index (Optional but Recommended)

For instant body search (~2ms instead of ~7s), build the FTS5 index:

```bash
# Grant Full Disk Access to Terminal first:
# System Settings → Privacy & Security → Full Disk Access → Add Terminal

jxa-mail-mcp index --verbose
# → Indexed 22,696 emails in 1m 7.6s
# → Database size: 130.5 MB
```

### 3. Use with Claude

Once configured, you can search emails, get today's messages, find unread emails, and more through natural conversation.

## CLI Commands

```bash
jxa-mail-mcp            # Run MCP server (default)
jxa-mail-mcp serve      # Run MCP server explicitly
jxa-mail-mcp --watch    # Run with real-time index updates
jxa-mail-mcp index      # Build search index from disk
jxa-mail-mcp status     # Show index statistics
jxa-mail-mcp rebuild    # Force rebuild index
```

### Real-Time Index Updates

Use `--watch` to automatically update the index when new emails arrive:

```bash
jxa-mail-mcp --watch
# or
jxa-mail-mcp serve --watch
```

The file watcher monitors `~/Library/Mail/V10/` for `.emlx` changes and updates the index in real-time. Requires Full Disk Access.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JXA_MAIL_DEFAULT_ACCOUNT` | First account | Default email account |
| `JXA_MAIL_DEFAULT_MAILBOX` | `INBOX` | Default mailbox |
| `JXA_MAIL_INDEX_PATH` | `~/.jxa-mail-mcp/index.db` | Index database location |
| `JXA_MAIL_INDEX_MAX_EMAILS` | `5000` | Max emails per mailbox to index |
| `JXA_MAIL_INDEX_STALENESS_HOURS` | `24` | Hours before index is stale |

### Claude Code Config

```json
{
  "mcpServers": {
    "mail": {
      "command": "jxa-mail-mcp",
      "env": {
        "JXA_MAIL_DEFAULT_ACCOUNT": "Work"
      }
    }
  }
}
```

## FTS5 Search Index

The FTS5 index makes `search()` ~100x faster by pre-indexing email content.

### How It Works

1. **Build from disk**: `jxa-mail-mcp index` reads `.emlx` files directly (~30x faster than JXA)
2. **Startup sync**: Index is synced with disk when server starts (fast, <5s)
3. **Real-time updates**: `--watch` flag enables file watcher for automatic updates
4. **Fast search**: Queries use SQLite FTS5 with BM25 ranking

### Requirements

Building the index requires **Full Disk Access** for Terminal:
1. Open **System Settings**
2. Go to **Privacy & Security → Full Disk Access**
3. Add and enable **Terminal.app** (or your terminal emulator)
4. Restart terminal

The MCP server itself does NOT need Full Disk Access (uses disk sync).

### Performance Comparison

| Operation | Without Index | With Index | Speedup |
|-----------|---------------|------------|---------|
| Body search | ~7,000ms | ~2-10ms | **700-3500x** |
| Startup sync | **60s timeout** | <5s | **12x** |
| Initial index build | N/A | ~1-2 min | One-time |
| Index size | N/A | ~6 KB/email | - |

#### Real-World Benchmarks (22,696 emails)

| Query | Results | Time |
|-------|---------|------|
| "invoice" | 20 | 2.5ms |
| "meeting tomorrow" | 20 | 1.3ms |
| "password reset" | 20 | 0.6ms |
| "shipping confirmation" | 10 | 4.1ms |

## Architecture

```
src/jxa_mail_mcp/
├── __init__.py         # CLI entry point
├── cli.py              # CLI commands (index, status, rebuild)
├── server.py           # FastMCP server and MCP tools (5 tools)
├── config.py           # Environment variable configuration
├── builders.py         # QueryBuilder for constructing JXA scripts
├── executor.py         # Async JXA script execution utilities
├── index/              # FTS5 search index module
│   ├── __init__.py     # Exports IndexManager
│   ├── schema.py       # SQLite schema, migrations (v3)
│   ├── manager.py      # IndexManager class
│   ├── disk.py         # Direct .emlx file reading + inventory
│   ├── sync.py         # Disk-based state reconciliation
│   ├── search.py       # FTS5 search functions
│   └── watcher.py      # Real-time file watcher
└── jxa/
    ├── __init__.py     # Exports MAIL_CORE_JS
    └── mail_core.js    # Shared JXA utilities library
```

### Design Principles

1. **Disk-first sync**: Fast filesystem scanning instead of slow JXA queries
2. **Consolidated tools**: 5 focused tools instead of 13 redundant ones
3. **Builder pattern**: `QueryBuilder` constructs optimized JXA scripts
4. **Hybrid indexing**: Disk reading for speed, state reconciliation for sync
5. **Async execution**: All JXA calls use `asyncio.create_subprocess_exec`
6. **Type safety**: Python type hints and TypedDict for clear API contracts

### Hybrid Access Pattern

| Access Method | Use Case | Latency | When Used |
|---------------|----------|---------|-----------|
| **JXA (Live)** | Real-time ops, small queries | ~100-300ms | `get_email()`, `list_mailboxes()` |
| **FTS5 (Cached)** | Body search, complex filtering | ~2-10ms | `search()` |
| **Disk (Batch)** | Initial indexing, sync | ~15ms/100 emails | `jxa-mail-mcp index`, startup |

## Performance

### Batch Property Fetching (87x faster)

Naive AppleScript/JXA iteration is extremely slow because each property access triggers a separate Apple Event IPC round-trip. We use batch property fetching instead:

```javascript
// FAST: ~0.6 seconds (87x faster than per-message iteration)
const msgs = inbox.messages;
const senders = msgs.sender();   // Single IPC call returns array
const subjects = msgs.subject(); // Single IPC call returns array
```

### Benchmark Results

| Method | Time | Speedup |
|--------|------|---------|
| AppleScript (per-message) | 54.1s | 1x |
| JXA (per-message) | 53.9s | 1x |
| **JXA (batch fetching)** | **0.62s** | **87x** |

### Disk-First Sync (12x faster)

| Sync Method | Time | Status |
|-------------|------|--------|
| JXA date-based (old) | **60s timeout** | ❌ |
| **Disk state reconciliation** | **<5s** | ✅ |

## Development

```bash
uv sync
uv run ruff check src/
uv run ruff format src/

# Run unit tests
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Manual test
uv run python -c "
import asyncio
from jxa_mail_mcp.server import list_accounts, get_emails
print('Accounts:', len(asyncio.run(list_accounts())))
print('Emails:', len(asyncio.run(get_emails(filter='today'))))
"

# Test index
uv run python -c "
from jxa_mail_mcp.index import IndexManager
m = IndexManager.get_instance()
if m.has_index():
    stats = m.get_stats()
    print(f'Indexed: {stats.email_count} emails')
"
```

## Security

### Implemented Protections

| Threat | Mitigation | Location |
|--------|------------|----------|
| SQL Injection | Parameterized queries | `search.py`, `sync.py` |
| JXA Injection | `json.dumps()` serialization | `sync.py`, `executor.py` |
| FTS5 Query Injection | Special character escaping | `search.py` |
| XSS via HTML Emails | BeautifulSoup HTML parsing | `disk.py` |
| DoS via Large Files | 25 MB file size limit | `disk.py` |
| Path Traversal | Path validation in watcher | `watcher.py` |
| Data Exposure | Database created with 0600 permissions | `schema.py` |

## Known Issues

### XMLParsedAsHTMLWarning during indexing

When running `jxa-mail-mcp index`, you may see warnings like:

```
XMLParsedAsHTMLWarning: It looks like you're using an HTML parser to parse an XML document.
```

This is **harmless** - BeautifulSoup's HTML parser handles the XML plist metadata in `.emlx` files adequately for text extraction. The warning can be suppressed by installing `lxml`:

```bash
pip install lxml
```

### FTS5 search ignores account/mailbox filters

Body search via `search()` currently searches **all indexed emails** regardless of account/mailbox parameters. This is because the disk indexer stores account UUIDs from folder paths, while JXA returns friendly names (e.g., "iCloud"). The mismatch prevents filtering.

**Impact:** Search results may include emails from all accounts, not just the specified one.

## Troubleshooting

### ModuleNotFoundError after install

If you get `ModuleNotFoundError: No module named 'jxa_mail_mcp'` even though
the package is installed, reset the virtual environment:

```bash
rm -rf .venv
uv sync --upgrade
```

### Full Disk Access denied

The `jxa-mail-mcp index` command requires Full Disk Access to read Mail.app's
data files. Grant access in:

**System Settings → Privacy & Security → Full Disk Access → Add Terminal**

Then restart your terminal.

## Migration from v0.3.x

v0.4.0 introduces breaking changes to consolidate the API:

### Removed Tools → Replacements

| Old Tool | New Usage |
|----------|-----------|
| `get_todays_emails()` | `get_emails(filter="today")` |
| `get_unread_emails()` | `get_emails(filter="unread")` |
| `get_flagged_emails()` | `get_emails(filter="flagged")` |
| `search_emails()` | `search()` |
| `fuzzy_search_emails()` | `search()` |
| `search_email_bodies()` | `search()` |
| `index_status()` | CLI: `jxa-mail-mcp status` |
| `sync_index()` | Automatic at startup |
| `rebuild_index()` | CLI: `jxa-mail-mcp rebuild` |

### Schema Migration

After upgrading, rebuild the index to populate the new `emlx_path` column:

```bash
jxa-mail-mcp rebuild
```

### Removed CLI Flag

The `--no-sync` flag has been removed since disk-based sync is now fast (<5s).

## License

GPL-3.0-or-later
