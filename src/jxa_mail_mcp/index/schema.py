"""SQLite schema for FTS5 email search index.

The schema uses:
- emails: Base table storing email content and metadata
- emails_fts: FTS5 virtual table for full-text search with external content
- sync_state: Tracks sync progress per mailbox

IMPORTANT: Message IDs from .emlx filenames are only unique within a mailbox,
NOT globally. We use (account, mailbox, message_id) as the unique constraint.
"""

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Current schema version for migrations
SCHEMA_VERSION = 2  # Bumped for composite key fix

# Default PRAGMAs for all connections (centralized to avoid drift)
DEFAULT_PRAGMAS = {
    "journal_mode": "WAL",  # Better concurrent read performance
    "synchronous": "NORMAL",  # Good balance of safety and speed
    "busy_timeout": 5000,  # Wait up to 5s for locks
}

# Centralized SQL for email insertion (used by manager, sync, watcher)
# Uses INSERT OR REPLACE for idempotent upserts on composite key
INSERT_EMAIL_SQL = """INSERT OR REPLACE INTO emails
    (message_id, account, mailbox, subject, sender, content, date_received)
    VALUES (?, ?, ?, ?, ?, ?, ?)"""


def email_to_row(
    email: dict, account: str, mailbox: str
) -> tuple[int, str, str, str, str, str, str]:
    """
    Convert an email dict to a database row tuple.

    Centralizes field extraction to ensure consistency across:
    - manager.py (disk indexing)
    - sync.py (JXA incremental sync)
    - watcher.py (real-time file watching)

    Args:
        email: Email dict with id, subject, sender, content, date_received
        account: Account name/identifier
        mailbox: Mailbox name

    Returns:
        Tuple matching INSERT_EMAIL_SQL parameter order
    """
    return (
        email["id"],
        account,
        mailbox,
        email.get("subject", ""),
        email.get("sender", ""),
        email.get("content", ""),
        email.get("date_received", ""),
    )


def create_connection(db_path: Path) -> sqlite3.Connection:
    """
    Create a database connection with standard configuration.

    This factory ensures consistent PRAGMA settings across all connection
    points (IndexManager, file watcher, etc.) to prevent configuration drift.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        Configured connection with WAL mode, busy timeout, and Row factory
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Apply standard PRAGMAs
    for pragma, value in DEFAULT_PRAGMAS.items():
        conn.execute(f"PRAGMA {pragma}={value}")

    return conn


def get_schema_sql() -> str:
    """Return the complete schema creation SQL."""
    return """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Email content cache
-- Note: rowid is auto-generated for FTS5 content_rowid compatibility
-- message_id is the Mail.app ID (from .emlx filename), unique per mailbox only
CREATE TABLE IF NOT EXISTS emails (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,     -- Mail.app ID (per-mailbox only)
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    content TEXT,                    -- Body text
    date_received TEXT,
    indexed_at TEXT DEFAULT (datetime('now')),
    UNIQUE(account, mailbox, message_id)  -- Composite uniqueness
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_emails_account_mailbox
    ON emails(account, mailbox);
CREATE INDEX IF NOT EXISTS idx_emails_date
    ON emails(date_received DESC);
CREATE INDEX IF NOT EXISTS idx_emails_message_id
    ON emails(message_id);

-- FTS5 index (external content - shares storage with emails table)
-- Uses porter stemmer for English + unicode61 for international text
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject,
    sender,
    content,
    content='emails',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS index in sync with emails table
CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, subject, sender, content)
    VALUES (new.rowid, new.subject, new.sender, new.content);
END;

CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, sender, content)
    VALUES('delete', old.rowid, old.subject, old.sender, old.content);
END;

CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, sender, content)
    VALUES('delete', old.rowid, old.subject, old.sender, old.content);
    INSERT INTO emails_fts(rowid, subject, sender, content)
    VALUES (new.rowid, new.subject, new.sender, new.content);
END;

-- Sync state tracking per mailbox
CREATE TABLE IF NOT EXISTS sync_state (
    account TEXT NOT NULL,
    mailbox TEXT NOT NULL,
    last_sync TEXT,
    message_count INTEGER DEFAULT 0,
    PRIMARY KEY(account, mailbox)
);
"""


def init_database(db_path: Path) -> sqlite3.Connection:
    """
    Initialize the database with schema, creating parent directories if needed.

    Args:
        db_path: Path to the SQLite database file

    Returns:
        Open database connection with check_same_thread=False for thread safety

    Security:
        Sets file permissions to 0600 (owner read/write only) on new databases
        to protect sensitive email content from other users on shared systems.
    """
    # Ensure parent directory exists with secure permissions
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Track if this is a new database for permission setting
    is_new_db = not db_path.exists()

    # Create connection with standard configuration
    conn = create_connection(db_path)

    # Set secure file permissions on new databases (owner read/write only)
    # Must be done after sqlite3.connect() creates the file
    if is_new_db:
        try:
            os.chmod(db_path, 0o600)
            logger.debug("Set secure permissions (0600) on %s", db_path)
        except OSError as e:
            logger.warning(
                "Could not set secure permissions on %s: %s", db_path, e
            )

    # Check current schema version
    sql = "SELECT name FROM sqlite_master "
    sql += "WHERE type='table' AND name='schema_version'"
    cursor = conn.execute(sql)
    if cursor.fetchone() is None:
        # Fresh database - create schema
        logger.info(
            "Creating fresh database schema (version %d)", SCHEMA_VERSION
        )
        conn.executescript(get_schema_sql())
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
        )
        conn.commit()
    else:
        # Check for migrations
        cursor = conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = cursor.fetchone()
        current_version = row[0] if row else 0

        if current_version < SCHEMA_VERSION:
            logger.info(
                "Migrating database from version %d to %d",
                current_version,
                SCHEMA_VERSION,
            )
            _run_migrations(conn, current_version, SCHEMA_VERSION)

    return conn


def _run_migrations(
    conn: sqlite3.Connection, from_version: int, to_version: int
) -> None:
    """
    Run schema migrations.

    Args:
        conn: Database connection
        from_version: Current schema version
        to_version: Target schema version
    """
    if from_version < 2:
        # Migration from v1 to v2: Change from id-as-primary-key to composite
        # This requires rebuilding the table since SQLite doesn't support
        # changing primary keys
        logger.warning(
            "Schema migration v1→v2 requires rebuild. "
            "Run 'jxa-mail-mcp rebuild' to re-index."
        )

        # Drop old tables and recreate
        conn.executescript("""
            DROP TABLE IF EXISTS emails_fts;
            DROP TABLE IF EXISTS emails;
            DROP TABLE IF EXISTS sync_state;
        """)

        # Recreate with new schema
        conn.executescript(get_schema_sql())

    conn.execute("UPDATE schema_version SET version = ?", (to_version,))
    conn.commit()


def rebuild_fts_index(conn: sqlite3.Connection) -> None:
    """
    Rebuild the FTS index from the emails table.

    Use this after bulk inserts without triggers or to fix corruption.
    """
    conn.execute("INSERT INTO emails_fts(emails_fts) VALUES('rebuild')")
    conn.commit()


def optimize_fts_index(conn: sqlite3.Connection) -> None:
    """
    Optimize the FTS index for better query performance.

    Call periodically after many insertions.
    """
    conn.execute("INSERT INTO emails_fts(emails_fts) VALUES('optimize')")
    conn.commit()
