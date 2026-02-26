# Search & Indexing

Apple Mail MCP includes an optional **FTS5 search index** that makes body search **700–3500x faster** — ~2ms instead of ~7s.

## How It Works

```
┌─────────────────────┐     ┌──────────────────┐
│  ~/Library/Mail/V10 │     │  ~/.apple-mail-mcp│
│  ├── account-uuid/  │────▶│  └── index.db     │
│  │   └── mailbox/   │     │      (SQLite+FTS5)│
│  │       └── *.emlx │     └──────────────────┘
│  └── ...            │              │
└─────────────────────┘              ▼
                              Fast search (~2ms)
```

1. **Build from disk** — `apple-mail-mcp index` reads `.emlx` files directly (~30x faster than JXA)
2. **Startup sync** — index is reconciled with disk when the server starts (<5s)
3. **Real-time updates** — `--watch` flag monitors for new emails
4. **Fast search** — queries use SQLite FTS5 with BM25 ranking

## Building the Index

### Requirements

Building requires **Full Disk Access** for your terminal:

1. Open **System Settings**
2. Go to **Privacy & Security → Full Disk Access**
3. Add and enable **Terminal.app** (or your terminal emulator)
4. Restart your terminal

!!! note
    The MCP server itself does **not** need Full Disk Access. It uses disk-based sync to keep the index updated.

### Commands

```bash
# Build the index (first time)
apple-mail-mcp index --verbose

# Check index status
apple-mail-mcp status

# Force rebuild from scratch
apple-mail-mcp rebuild
```

### What Gets Indexed

For each email, the index stores:

| Field | Source | Searchable via FTS5 |
|-------|--------|:---:|
| `message_id` | Mail.app ID | — |
| `account` | Folder path UUID | — |
| `mailbox` | Folder path | — |
| `subject` | `.emlx` header | Yes |
| `sender` | `.emlx` header | Yes |
| `content` | `.emlx` body (HTML → text) | Yes |
| `date_received` | `.emlx` header | — |
| `emlx_path` | Filesystem path | — |
| `attachment_count` | MIME parsing | — |

Attachment metadata (filename, MIME type, file size) is stored in a separate `attachments` table, enabling `search(scope="attachments")` queries.

!!! note
    Body search (`scope="body"`) only covers indexed emails, which may be limited by the `APPLE_MAIL_INDEX_MAX_EMAILS` cap (default: 5,000 per mailbox). Subject and sender search (`scope="subject"`, `scope="sender"`) use live JXA queries against Mail.app and are not affected by this limit.

### Account UUIDs vs Friendly Names

The `account` column stores filesystem UUIDs (e.g., `24E569DF-5E45-...`), not friendly names like `"Work"`. This is intentional — the sync engine diffs `get_disk_inventory()` (UUID-keyed) against `get_db_inventory()` to detect new, deleted, and moved emails. Storing friendly names would break the diff, causing a full re-index on every sync cycle.

Instead, translation happens at search time via `AccountMap` (`index/accounts.py`), which maps names to UUIDs using JXA's `Mail.accounts.id()`. The mapping is cached for 5 minutes and seeded automatically when `list_accounts()` is called.

## Database Schema

The index uses SQLite with FTS5 external content tables:

```sql
-- Email content cache (schema v4)
CREATE TABLE emails (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    content TEXT,
    date_received TEXT,
    emlx_path TEXT,
    attachment_count INTEGER DEFAULT 0,
    indexed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(account, mailbox, message_id)
);

-- Attachment metadata (one-to-many from emails)
CREATE TABLE attachments (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    email_rowid INTEGER NOT NULL REFERENCES emails(rowid) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime_type TEXT,
    file_size INTEGER,
    content_id TEXT
);

-- FTS5 index (external content — shares storage with emails table)
CREATE VIRTUAL TABLE emails_fts USING fts5(
    subject, sender, content,
    content='emails',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Sync state tracking per mailbox
CREATE TABLE sync_state (
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    last_sync TEXT,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY(account, mailbox)
);
```

The `porter unicode61` tokenizer provides:

- **Porter stemming** — "running" matches "run", "runs", "runner"
- **Unicode support** — handles international characters correctly

## Startup Sync

Every time the server starts, it runs a fast **state reconciliation** against the filesystem:

```
1. Get DB inventory:   {(account, mailbox, msg_id): emlx_path}  ← from SQLite
2. Get Disk inventory: {(account, mailbox, msg_id): emlx_path}  ← fast walk
3. Calculate diff:
   - NEW:     on disk, not in DB → parse & insert
   - DELETED: in DB, not on disk → remove from DB
   - MOVED:   same ID, different path → update path
```

This takes **<5s** even for 20,000+ emails (vs. 60s+ timeout with the old JXA-based sync).

## Real-Time Updates

Enable automatic index updates with the `--watch` flag:

```bash
apple-mail-mcp --watch
```

The file watcher monitors `~/Library/Mail/V10/` for:

- New `.emlx` files → parse and insert into index
- Deleted `.emlx` files → remove from index
- Moved `.emlx` files → update path in index

## Performance

### Search Speed

| Query | Results | Time |
|-------|---------|------|
| "invoice" | 20 | 2.5ms |
| "meeting tomorrow" | 20 | 1.3ms |
| "password reset" | 20 | 0.6ms |
| "shipping confirmation" | 10 | 4.1ms |

### With vs. Without Index

| Operation | Without Index | With Index | Speedup |
|-----------|---------------|------------|---------|
| Body search | ~7,000ms | ~2–10ms | **700–3500x** |
| Startup sync | 60s timeout | <5s | **12x** |
| Initial build | — | ~1–2 min | One-time |
| Disk usage | — | ~6 KB/email | — |

