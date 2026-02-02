# JXA Mail MCP - Project Instructions

## Project Overview

Fast MCP server for Apple Mail using optimized JXA scripts with batch property fetching for 87x faster performance than naive iteration, plus an FTS5 search index for **700-3500x faster** body search (~2ms vs ~7s).

**v0.4.0 Key Changes:**
- **Disk-first sync**: Fast filesystem scanning replaces slow JXA queries (<5s vs 60s timeout)
- **Consolidated tools**: 5 focused MCP tools instead of 13 redundant ones
- **Schema v3**: Added `emlx_path` column for move detection and efficient rebuilds

## Project Structure

```
src/jxa_mail_mcp/
├── __init__.py         # CLI entry point, exports main()
├── cli.py              # CLI commands (index, status, rebuild, serve)
├── server.py           # FastMCP server with 5 MCP tools
├── config.py           # Environment variable configuration
├── builders.py         # QueryBuilder, AccountsQueryBuilder
├── executor.py         # run_jxa(), execute_with_core(), execute_query()
├── index/              # FTS5 search index module
│   ├── __init__.py     # Exports IndexManager
│   ├── schema.py       # SQLite schema v3 (emlx_path column)
│   ├── manager.py      # IndexManager class (disk-based sync)
│   ├── disk.py         # .emlx reading + get_disk_inventory()
│   ├── sync.py         # Disk-based state reconciliation
│   ├── search.py       # FTS5 search functions
│   └── watcher.py      # Real-time file watcher
└── jxa/
    ├── __init__.py     # Exports MAIL_CORE_JS
    └── mail_core.js    # Shared JXA utilities (MailCore object)
```

## MCP Tools (5 total)

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `list_accounts()` | List email accounts | - |
| `list_mailboxes(account?)` | List mailboxes | account (optional) |
| `get_emails(...)` | Unified listing | filter: all/unread/flagged/today/this_week |
| `get_email(id)` | Full email content | message_id |
| `search(query, ...)` | Unified search | scope: all/subject/sender/body |

### get_emails() Filters

```python
get_emails()                      # All emails (default)
get_emails(filter="unread")       # Unread only
get_emails(filter="flagged")      # Flagged only
get_emails(filter="today")        # Received today
get_emails(filter="this_week")    # Last 7 days
```

### search() Scopes

```python
search("invoice")                          # Search everywhere (FTS5)
search("john@", scope="sender")            # Sender only (JXA)
search("meeting", scope="subject")         # Subject only (JXA)
search("deadline", scope="body")           # Body only (FTS5)
```

## Architecture

### Disk-First Sync (v0.4.0)

**Problem:** JXA-based sync was timing out at 60s for large mailboxes.

**Solution:** State reconciliation via filesystem scanning:

```
Startup Sync Flow:
1. Get DB inventory: {(account, mailbox, msg_id): emlx_path}  ← from SQLite
2. Get Disk inventory: {(account, mailbox, msg_id): emlx_path}  ← fast walk
3. Calculate diff:
   - NEW: on disk, not in DB → parse & insert
   - DELETED: in DB, not on disk → remove from DB
   - MOVED: same ID, different path → update path
```

**Performance:**

| Operation | JXA (old) | Disk (new) | Speedup |
|-----------|-----------|------------|---------|
| Startup sync | 60s timeout | <5s | **12x** |
| Handles deletions | ❌ | ✅ | - |
| Handles moves | ❌ | ✅ | - |

### Layer Separation

1. **cli.py** - CLI entry point, commands for indexing
2. **server.py** - 5 MCP tools, uses builders and index
3. **builders.py** - Constructs JXA scripts from Python, type-safe
4. **executor.py** - Runs scripts via osascript, handles JSON parsing
5. **index/** - FTS5 search index with disk-based sync
6. **jxa/mail_core.js** - Shared JS utilities injected into all scripts

### Data Flow (JXA Path)

```
MCP Tool → QueryBuilder.build() → executor.execute_query()
                                        ↓
                           MAIL_CORE_JS + script body
                                        ↓
                              osascript -l JavaScript
                                        ↓
                              JSON.parse(stdout)
```

### Data Flow (Disk Sync)

```
Server startup → IndexManager.sync_updates()
                        ↓
         sync.sync_from_disk(conn, mail_dir)
                        ↓
    disk.get_disk_inventory() → walk filesystem
    sync.get_db_inventory()   → query SQLite
                        ↓
              Calculate diff: NEW, DELETED, MOVED
                        ↓
    NEW → parse_emlx() → INSERT
    DELETED → DELETE from DB
    MOVED → UPDATE emlx_path
```

### Hybrid Access Pattern

| Access Method | Use Case | Latency | When Used |
|---------------|----------|---------|-----------|
| **JXA (Live)** | Real-time ops, small queries | ~100-300ms | `get_email()`, `list_mailboxes()` |
| **FTS5 (Cached)** | Body search, complex filtering | ~2-10ms | `search()` |
| **Disk (Batch)** | Indexing, sync | ~15ms/100 emails | startup, `jxa-mail-mcp index` |

### Design Patterns

| Pattern | Location | Purpose |
|---------|----------|---------|
| **Builder** | `QueryBuilder` | Safe JXA script construction, prevents injection |
| **Singleton** | `IndexManager` | Single SQLite writer, one file watcher |
| **Facade** | `MailCore` JS | Clean API over verbose Apple Events |
| **Factory** | `create_connection()` | Consistent DB configuration |
| **State Reconciliation** | `sync_from_disk()` | Fast diff-based sync |

## FTS5 Search Index

### Database Schema (v3)

```sql
-- Email content cache
CREATE TABLE emails (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,     -- Mail.app ID (per-mailbox only)
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    content TEXT,                    -- Body text
    date_received TEXT,
    emlx_path TEXT,                  -- NEW in v3: path for sync
    indexed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(account, mailbox, message_id)
);

CREATE INDEX idx_emails_path ON emails(emlx_path);

-- FTS5 index (external content - shares storage with emails table)
CREATE VIRTUAL TABLE emails_fts USING fts5(
    subject, sender, content,
    content='emails',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Triggers keep FTS in sync automatically
-- Sync state tracking per mailbox
CREATE TABLE sync_state (
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    last_sync TEXT,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY(account, mailbox)
);
```

### Schema Migrations

```python
# In schema.py
SCHEMA_VERSION = 3

def _run_migrations(conn, from_version, to_version):
    if from_version < 2:
        # v1→v2: Composite key (requires rebuild)
        ...
    if from_version < 3:
        # v2→v3: Add emlx_path column
        conn.execute("ALTER TABLE emails ADD COLUMN emlx_path TEXT")
        conn.execute("CREATE INDEX idx_emails_path ON emails(emlx_path)")
```

### IndexManager API

```python
from jxa_mail_mcp.index import IndexManager

manager = IndexManager.get_instance()

# Build index from disk (requires Full Disk Access)
manager.build_from_disk(progress_callback=None)

# Disk-based sync (fast, <5s)
changes = manager.sync_updates()  # Returns total changes count

# Search indexed content
results = manager.search(query, account=None, mailbox=None, limit=20)

# Get statistics
stats = manager.get_stats()  # IndexStats dataclass

# Check staleness
if manager.is_stale():
    manager.sync_updates()
```

### Disk Functions

```python
from jxa_mail_mcp.index.disk import (
    find_mail_directory,      # → ~/Library/Mail/V10/
    parse_emlx,               # Parse single .emlx file
    scan_all_emails,          # Iterator over all emails (with content)
    get_disk_inventory,       # Fast walk, NO content parsing
    read_envelope_index,      # Query metadata DB
)

# Fast inventory (for sync)
inventory = get_disk_inventory(mail_dir)
# Returns: {(account, mailbox, msg_id): "/path/to/email.emlx", ...}
```

### Sync Functions

```python
from jxa_mail_mcp.index.sync import (
    get_db_inventory,     # Get {(account, mailbox, msg_id): path} from DB
    sync_from_disk,       # State reconciliation
    SyncResult,           # Dataclass with added/deleted/moved counts
)

result = sync_from_disk(conn, mail_dir, progress_callback)
# result.added, result.deleted, result.moved, result.errors
```

## Coding Standards

- **Python 3.13+**, type hints required
- **Formatter**: `uv run ruff format src/`
- **Linter**: `uv run ruff check src/`
- Line length: 80 characters

## Adding New Query Tools

With the consolidated API, extend `get_emails()` filters or `search()` scopes:

```python
# In server.py - adding a new filter
@mcp.tool
async def get_emails(
    ...
    filter: Literal["all", "unread", "flagged", "today", "this_week", "starred"] = "all",
    ...
):
    ...
    elif filter == "starred":
        query = query.where("data.flaggedStatus[i] === true")
```

For completely new operations, use `execute_with_core_async()`:

```python
from .executor import execute_with_core_async

@mcp.tool
async def mark_as_read(message_id: int) -> dict:
    """Mark a message as read."""
    script = f"""
const msg = Mail.messages.byId({message_id});
msg.readStatus = true;
JSON.stringify({{success: true, id: {message_id}}});
"""
    return await execute_with_core_async(script)
```

## MailCore Date Helpers

```javascript
// Get today at midnight
MailCore.today()  // Date

// Get N days ago at midnight
MailCore.daysAgo(7)  // Date (for "this_week" filter)

// Format for JSON
MailCore.formatDate(date)  // ISO string or null
```

## CLI Commands

```bash
jxa-mail-mcp            # Run MCP server (default)
jxa-mail-mcp serve      # Run MCP server explicitly
jxa-mail-mcp --watch    # Run with real-time index updates
jxa-mail-mcp index      # Build search index from disk
jxa-mail-mcp status     # Show index statistics
jxa-mail-mcp rebuild    # Force rebuild index
```

**Note:** The `--no-sync` flag was removed in v0.4.0 since disk-based sync is fast.

## Testing

### Unit Tests

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_search.py
```

### Manual Testing

```bash
# Import test
uv run python -c "from jxa_mail_mcp import mcp; print('OK')"

# Test new unified tools
uv run python -c "
import asyncio
from jxa_mail_mcp.server import get_emails, search
print('Today:', len(asyncio.run(get_emails(filter='today'))))
print('Search:', len(asyncio.run(search('invoice'))))
"

# Test index
uv run python -c "
from jxa_mail_mcp.index import IndexManager
m = IndexManager.get_instance()
if m.has_index():
    stats = m.get_stats()
    print(f'Emails: {stats.email_count}')
"
```

## Critical: JXA Performance

**ALWAYS use batch property fetching.** Never iterate messages individually:

```javascript
// WRONG - 87x slower
for (let msg of inbox.messages()) {
    results.push({ from: msg.sender() });  // IPC per message
}

// RIGHT - Use MailCore.batchFetch
const data = MailCore.batchFetch(msgs, ["sender", "subject"]);
for (let i = 0; i < data.sender.length; i++) {
    results.push({ from: data.sender[i] });
}
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `JXA_MAIL_DEFAULT_ACCOUNT` | First account | Default email account |
| `JXA_MAIL_DEFAULT_MAILBOX` | `INBOX` | Default mailbox |
| `JXA_MAIL_INDEX_PATH` | `~/.jxa-mail-mcp/index.db` | Index database location |
| `JXA_MAIL_INDEX_MAX_EMAILS` | `5000` | Max emails per mailbox |
| `JXA_MAIL_INDEX_STALENESS_HOURS` | `24` | Hours before refresh |

## Known Limitations

1. **macOS Only** - Requires Apple Mail and `osascript`
2. **Mail Version** - Hardcoded to `~/Library/Mail/V10/` (macOS Ventura+)
3. **Full Disk Access** - Required for disk-based indexing and sync

## Known Issues

1. **XMLParsedAsHTMLWarning during indexing** - BeautifulSoup may emit warnings
   when parsing .emlx files that contain XML content (like Apple's plist metadata).
   This is harmless - the HTML parser handles XML adequately for text extraction.

2. **FTS5 search ignores account/mailbox filters** - The disk indexer stores
   account UUIDs from folder paths, while JXA returns friendly names. This
   mismatch prevents filtering. TODO: Map UUIDs to friendly names.

## Security

### Implemented Protections

| Threat | Mitigation | Location |
|--------|------------|----------|
| **SQL Injection** | Parameterized queries with `?` placeholders | search.py, sync.py |
| **JXA Injection** | `json.dumps()` serialization for all strings | sync.py |
| **FTS5 Query Injection** | Special character escaping via regex | search.py |
| **XSS via HTML Emails** | BeautifulSoup HTML parsing (not regex) | disk.py |
| **DoS via Large Files** | 25 MB file size limit (`MAX_EMLX_SIZE`) | disk.py |
| **DoS via Spam** | Max emails per mailbox limit (configurable) | manager.py |
| **Path Traversal** | Path validation in file watcher | watcher.py |
| **Data Exposure** | Database created with 0600 permissions | schema.py |
| **Unbounded Memory** | Pending changes limit in watcher | watcher.py |

## Migration from v0.3.x

### Removed MCP Tools

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

After upgrading, run `jxa-mail-mcp rebuild` to populate `emlx_path` column.

### Removed CLI Flag

The `--no-sync` flag was removed since disk-based sync is fast (<5s).
