# JXA Mail MCP - Project Instructions

## Project Overview

Fast MCP server for Apple Mail using optimized JXA scripts with batch property fetching for 87x faster performance than naive iteration, plus an FTS5 search index for **700-3500x faster** body search (~2ms vs ~7s).

## Project Structure

```
src/jxa_mail_mcp/
├── __init__.py         # CLI entry point, exports main()
├── cli.py              # CLI commands (index, status, rebuild, serve)
├── server.py           # FastMCP server and MCP tools
├── config.py           # Environment variable configuration
├── builders.py         # QueryBuilder, AccountsQueryBuilder
├── executor.py         # run_jxa(), execute_with_core(), execute_query()
├── index/              # FTS5 search index module
│   ├── __init__.py     # Exports IndexManager
│   ├── schema.py       # SQLite schema and migrations
│   ├── manager.py      # IndexManager class
│   ├── disk.py         # Direct .emlx file reading
│   ├── sync.py         # JXA-based incremental sync
│   └── search.py       # FTS5 search functions
└── jxa/
    ├── __init__.py     # Exports MAIL_CORE_JS
    └── mail_core.js    # Shared JXA utilities (MailCore object)
```

## Architecture

### Layer Separation

1. **cli.py** - CLI entry point, commands for indexing
2. **server.py** - MCP tool definitions, uses builders and index
3. **builders.py** - Constructs JXA scripts from Python, type-safe
4. **executor.py** - Runs scripts via osascript, handles JSON parsing
5. **index/** - FTS5 search index for fast body search
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

### Data Flow (Index Path)

```
CLI 'index' command → IndexManager.build_from_disk()
                              ↓
                    disk.scan_all_emails() → parse .emlx files
                              ↓
                    SQLite INSERT → emails table
                              ↓
                    Triggers populate emails_fts (FTS5)
```

```
search_email_bodies() → IndexManager.search()
                              ↓
                    search.search_fts() → FTS5 MATCH query
                              ↓
                    BM25 ranked results
```

### Hybrid Access Pattern

The system uses a **Hybrid Access Pattern** to solve the latency challenges of
Apple Mail automation:

| Access Method | Use Case | Latency | When Used |
|---------------|----------|---------|-----------|
| **JXA (Live)** | Real-time ops, small queries | ~100-300ms | `get_email()`, `list_mailboxes()` |
| **FTS5 (Cached)** | Body search, complex filtering | ~2-10ms | `search_email_bodies()` |
| **Disk (Batch)** | Initial indexing | ~15ms/100 emails | `jxa-mail-mcp index` |

**Why this matters:** JXA spawns a subprocess for each call (~100-300ms overhead).
For body search across thousands of emails, this would take minutes. The FTS5
index reduces this to milliseconds.

### Design Patterns

| Pattern | Location | Purpose |
|---------|----------|---------|
| **Builder** | `QueryBuilder` | Safe JXA script construction, prevents injection |
| **Singleton** | `IndexManager` | Single SQLite writer, one file watcher |
| **Facade** | `MailCore` JS | Clean API over verbose Apple Events |
| **Factory** | `create_connection()` | Consistent DB configuration |

### Known Limitations

1. **macOS Only** - Requires Apple Mail and `osascript`
2. **Mail Version** - Hardcoded to `~/Library/Mail/V10/` (macOS Ventura+)
3. **Full Disk Access** - Required for disk-based indexing

### Known Issues

1. **XMLParsedAsHTMLWarning during indexing** - BeautifulSoup may emit warnings
   when parsing .emlx files that contain XML content (like Apple's plist metadata).
   This is harmless - the HTML parser handles XML adequately for text extraction.
   The warning can be suppressed by installing `lxml` or filtering the warning:
   ```python
   from bs4 import XMLParsedAsHTMLWarning
   import warnings
   warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
   ```

2. **Deleted emails not removed from index** - The fast date-based sync only
   detects NEW emails, not deletions. Deleted emails remain in the search index
   until a full rebuild (`jxa-mail-mcp rebuild`). This is a tradeoff for ~100x
   faster sync. The `--watch` flag handles deletions in real-time via filesystem
   monitoring. TODO: Add periodic stale entry cleanup.

### Async JXA Execution

All MCP tools use async JXA execution via `asyncio.create_subprocess_exec`.
This prevents blocking the event loop during the ~100-300ms osascript subprocess
calls, allowing FastMCP to handle multiple concurrent requests.

Key async functions in `executor.py`:
- `run_jxa_async()` - Execute raw JXA script asynchronously
- `execute_with_core_async()` - Execute with MailCore library injected
- `execute_query_async()` - Execute a QueryBuilder asynchronously

## FTS5 Search Index

### Database Schema

```sql
-- Email content cache
CREATE TABLE emails (
    id INTEGER PRIMARY KEY,      -- Mail.app message ID
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    content TEXT,                -- Body text
    date_received TEXT,
    indexed_at TEXT
);

-- FTS5 index (external content - shares storage with emails table)
CREATE VIRTUAL TABLE emails_fts USING fts5(
    subject, sender, content,
    content='emails',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Triggers keep FTS in sync automatically
-- Sync state tracking per mailbox
CREATE TABLE sync_state (
    account TEXT,
    mailbox TEXT,
    last_sync TEXT,
    message_count INTEGER,
    PRIMARY KEY(account, mailbox)
);
```

### IndexManager API

```python
from jxa_mail_mcp.index import IndexManager

manager = IndexManager.get_instance()

# Build index from disk (requires Full Disk Access)
manager.build_from_disk(progress_callback=None)

# Incremental sync via JXA (for new emails)
manager.sync_updates()

# Search indexed content
results = manager.search(query, account=None, mailbox=None, limit=20)

# Get statistics
stats = manager.get_stats()  # IndexStats dataclass

# Check staleness
if manager.is_stale():
    manager.sync_updates()
```

### Mail.app Disk Storage

```
~/Library/Mail/V10/
├── [Account-UUID]/
│   └── [Mailbox].mbox/
│       └── Data/x/y/Messages/
│           ├── 12345.emlx    # Individual emails
│           └── 12346.emlx
└── MailData/
    └── Envelope Index        # SQLite with metadata
```

### .emlx File Format

```
1255                          ← Byte count of MIME content
From: sender@example.com      ← RFC 5322 headers + body
Subject: Hello
...
<?xml version="1.0"?>         ← Plist metadata footer
<plist>...</plist>
```

### Disk Reader (disk.py)

```python
from jxa_mail_mcp.index.disk import (
    find_mail_directory,      # → ~/Library/Mail/V10/
    parse_emlx,               # Parse single .emlx file
    scan_all_emails,          # Iterator over all emails
    read_envelope_index,      # Query metadata DB
)
```

### Search (search.py)

```python
from jxa_mail_mcp.index.search import (
    search_fts,              # FTS5 search with BM25
    search_fts_highlight,    # With highlighted snippets
    sanitize_fts_query,      # Escape special chars
    count_matches,           # Count without results
)
```

## Coding Standards

- **Python 3.13+**, type hints required
- **Formatter**: `uv run ruff format src/`
- **Linter**: `uv run ruff check src/`
- Line length: 80 characters

## Adding New Query Tools

Use `QueryBuilder` for read operations:

```python
from .builders import QueryBuilder
from .executor import execute_query

@mcp.tool
def get_recent_from_sender(
    sender: str,
    account: str | None = None,
    limit: int = 20
) -> list[dict]:
    """Get recent emails from a specific sender."""
    safe_sender = sender.lower().replace("'", "\\'")
    query = (
        QueryBuilder()
        .from_mailbox(account, "INBOX")
        .select("standard")  # id, subject, sender, date_received, read, flagged
        .where(f"(data.sender[i] || '').toLowerCase().includes('{safe_sender}')")
        .order_by("date_received", descending=True)
        .limit(limit)
    )
    return execute_query(query)
```

### Available Properties

```python
EMAIL_PROPERTIES = {
    "id": "id",
    "subject": "subject",
    "sender": "sender",
    "date_received": "dateReceived",
    "date_sent": "dateSent",
    "read": "readStatus",
    "flagged": "flaggedStatus",
    "deleted": "deletedStatus",
    "junk": "junkMailStatus",
    "reply_to": "replyTo",
    "message_id": "messageId",
    "source": "source",  # Raw email - expensive!
}

# Presets
PROPERTY_SETS = {
    "minimal": ["id", "subject", "sender", "date_received"],
    "standard": ["id", "subject", "sender", "date_received", "read", "flagged"],
    "full": [...all except source...]
}
```

### QueryBuilder Methods

```python
QueryBuilder()
    .from_mailbox(account, mailbox)  # Set source
    .select("standard")               # Choose properties
    .select("subject", "sender")      # Or individual props
    .where("JS expression")           # Filter (uses data.prop[i])
    .order_by("date_received")        # Sort results
    .limit(50)                        # Cap results
    .build()                          # → JXA script string
```

## Adding Non-Query Tools

For operations that don't fit `QueryBuilder` (compose, mutate, attachments), use `execute_with_core()` directly:

```python
from .executor import execute_with_core

@mcp.tool
def mark_as_read(message_id: int) -> dict:
    """Mark a message as read."""
    script = f"""
const msg = Mail.messages.byId({message_id});
msg.readStatus = true;
JSON.stringify({{success: true, id: {message_id}}});
"""
    return execute_with_core(script)
```

## Extending mail_core.js

Add utilities to `jxa/mail_core.js` for reusable JS logic:

```javascript
const MailCore = {
    // ... existing methods ...

    /**
     * Your new utility.
     */
    newUtility(param) {
        // Implementation
    },
};
```

Then use in Python-generated scripts:

```python
script = "JSON.stringify(MailCore.newUtility('value'));"
result = execute_with_core(script)
```

## Fuzzy Search

The `fuzzy_search_emails` tool uses a two-stage matching algorithm:

1. **Trigram filtering** - Fast candidate selection using character triplets
2. **Levenshtein ranking** - Precise similarity scoring for final ranking

## Body Search

The `search_email_bodies` tool uses the FTS5 index when available (~100x faster):

```python
@mcp.tool
def search_email_bodies(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 20,
    threshold: float = 0.3,
    use_index: bool = True,     # Use FTS5 index if available
) -> list[dict]:
    """Search within email body content."""
```

When no index exists, falls back to JXA-based fuzzy search (slower).

### get_email Tool

Fetches a single email with full body content:

```python
@mcp.tool
def get_email(
    message_id: int,
    account: str | None = None,  # Optional hint for faster lookup
    mailbox: str | None = None,  # Optional hint for faster lookup
) -> dict:
    """Get a single email with full content."""
```

Returns: `id`, `subject`, `sender`, `content`, `date_received`, `date_sent`, `read`, `flagged`, `reply_to`, `message_id`

### Index Management Tools

```python
@mcp.tool
def index_status() -> dict:
    """Get FTS5 index statistics."""

@mcp.tool
def sync_index() -> dict:
    """Sync new emails to the index via JXA."""

@mcp.tool
def rebuild_index(account=None, mailbox=None) -> dict:
    """Force rebuild the index from disk."""
```

### MailCore Fuzzy Methods

```javascript
// Extract trigrams from string
MailCore.trigrams("hello")  // Set{"hel", "ell", "llo"}

// Jaccard similarity between trigram sets
MailCore.trigramSimilarity(set1, set2)  // 0-1

// Edit distance
MailCore.levenshtein("hello", "hallo")  // 1

// Normalized similarity
MailCore.levenshteinSimilarity("hello", "hallo")  // 0.8

// Combined fuzzy match (returns {score, matched} or null)
MailCore.fuzzyMatch("reserch", "research studies", 0.2)
// {score: 0.88, matched: "research"}

// Body-optimized fuzzy match (no Levenshtein, faster for long text)
MailCore.fuzzyMatchBody("unsubscribe", emailBody, 2000)
// {score: 0.95, matched: "...click to unsubscribe...", tier: "exact"}
// or {score: 0.43, matched: "unsubscribe", tier: "trigram"}
```

### Performance

| Operation | Without Index | With Index | Speedup |
|-----------|---------------|------------|---------|
| Body search | ~7,000ms | ~2-10ms | **700-3500x** |
| Metadata search | ~100ms | ~100ms | - |
| Initial index build | N/A | ~1-2 min | One-time |
| Startup sync | N/A | ~1-2s | Incremental |

#### Real-World Benchmarks (22,696 emails, 34 mailboxes)

| Metric | Value |
|--------|-------|
| Index build time | 67.6s |
| Index size | 130.5 MB (~6 KB/email) |
| Throughput | ~338 emails/second |

| Query | Results | Time |
|-------|---------|------|
| "invoice" | 20 | 2.5ms |
| "meeting tomorrow" | 20 | 1.3ms |
| "password reset" | 20 | 0.6ms |
| "shipping confirmation" | 10 | 4.1ms |

Fuzzy search adds ~33% overhead vs regular search (~480ms for 6000 emails).

## CLI Commands

```bash
jxa-mail-mcp            # Run MCP server (default)
jxa-mail-mcp serve      # Run MCP server explicitly
jxa-mail-mcp --watch    # Run with real-time index updates
jxa-mail-mcp index      # Build search index from disk
jxa-mail-mcp status     # Show index statistics
jxa-mail-mcp rebuild    # Force rebuild index
```

### File Watcher (Real-Time Updates)

The `--watch` flag enables automatic index updates when emails arrive:

```python
# How it works internally
from jxa_mail_mcp.index import IndexManager

manager = IndexManager.get_instance()

# Start watcher with callback
def on_update(added: int, removed: int):
    print(f"Index updated: +{added} -{removed}")

manager.start_watcher(on_update=on_update)

# Later...
manager.stop_watcher()
```

The watcher:
- Uses `watchfiles` (Rust-based, efficient)
- Monitors `~/Library/Mail/V10/` recursively
- Debounces rapid changes (500ms default)
- Parses new `.emlx` files and adds to index
- Removes deleted emails from index
- Requires Full Disk Access

## Testing

### Unit Tests

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_search.py

# Run specific test class
uv run pytest tests/test_schema.py::TestFtsTriggers

# Run with coverage (if installed)
uv run pytest --cov=jxa_mail_mcp
```

Test files:
- `tests/test_search.py` - FTS5 search, query sanitization
- `tests/test_schema.py` - Database schema, triggers, migrations
- `tests/test_disk.py` - .emlx parsing, HTML stripping

### Manual Testing

```bash
# Import test
uv run python -c "from jxa_mail_mcp import mcp; print('OK')"

# Test specific tool
uv run python -c "
from jxa_mail_mcp.server import get_todays_emails
print(get_todays_emails())
"

# Test QueryBuilder output
uv run python -c "
from jxa_mail_mcp.builders import QueryBuilder
print(QueryBuilder().from_mailbox('Work').select('minimal').build())
"

# Test index
uv run python -c "
from jxa_mail_mcp.index import IndexManager
m = IndexManager.get_instance()
print('Has index:', m.has_index())
if m.has_index():
    stats = m.get_stats()
    print(f'Emails: {stats.email_count}')
"

# Run MCP server
jxa-mail-mcp
# or
uv run jxa-mail-mcp
```

## Troubleshooting

### ModuleNotFoundError: No module named 'jxa_mail_mcp'

If you see this error even though the package appears installed:

```bash
uv run python -c "from jxa_mail_mcp import mcp"
# ModuleNotFoundError: No module named 'jxa_mail_mcp'
```

**Fix:** Remove and recreate the virtual environment:

```bash
rm -rf .venv
uv sync --upgrade
```

This happens when the venv gets into an inconsistent state, especially after:
- Switching Python versions
- Editing `pyproject.toml` dependencies
- Interrupted installs
- Moving the project directory

The `--upgrade` flag ensures all packages are freshly resolved and installed.

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
| `JXA_MAIL_DEFAULT_MAILBOX` | `Inbox` | Default mailbox |
| `JXA_MAIL_INDEX_PATH` | `~/.jxa-mail-mcp/index.db` | Index database location |
| `JXA_MAIL_INDEX_MAX_EMAILS` | `5000` | Max emails per mailbox |
| `JXA_MAIL_INDEX_STALENESS_HOURS` | `24` | Hours before refresh |

## Dependencies

- **fastmcp**: MCP server framework
- **cyclopts**: CLI argument parsing
- **beautifulsoup4**: Robust HTML stripping (XSS prevention)

Dev: ruff (linting/formatting), pytest (testing)

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

### Why These Protections Matter

1. **SQL/FTS5 Injection**: User search queries are passed to SQLite; without
   sanitization, malicious queries could extract or corrupt data.

2. **JXA Injection**: Mailbox/account names come from Mail.app and could contain
   malicious characters. `json.dumps()` creates safe JavaScript literals.

3. **XSS Prevention**: Email bodies may contain HTML with `<script>` tags.
   BeautifulSoup properly parses malformed HTML that regex can't handle
   (e.g., `<<script>` bypass attempts).

4. **File Permissions**: The index contains email content. 0600 permissions
   ensure only the owner can read it on shared systems.

### Verified Safe

- **Shell Injection via executor**: Not vulnerable because `subprocess.run()`
  uses a list of arguments (not `shell=True`). Single quotes in mailbox names
  are safely passed as data, not interpreted by the shell.
