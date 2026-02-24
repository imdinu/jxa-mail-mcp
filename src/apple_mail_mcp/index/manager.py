"""IndexManager - Central interface for the FTS5 search index.

Provides:
- build_from_disk(): Pre-index emails by reading .emlx files directly
- sync_updates(): Incremental sync via JXA for new emails
- search(): Fast FTS5 search with BM25 ranking
- get_stats(): Index statistics for status reporting

Thread Safety:
- Uses threading.Lock for connection management
- Database connections use check_same_thread=False
- File watcher runs in separate thread with its own connection
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import (
    get_index_max_emails,
    get_index_path,
    get_index_staleness_hours,
)
from .schema import (
    INSERT_ATTACHMENT_SQL,
    INSERT_EMAIL_SQL,
    init_database,
    optimize_fts_index,
    rebuild_fts_index,
)
from .search import SearchResult  # Re-use, don't duplicate

if TYPE_CHECKING:
    from collections.abc import Callable

    from .watcher import IndexWatcher

logger = logging.getLogger(__name__)


@dataclass
class IndexStats:
    """Statistics about the search index."""

    email_count: int
    mailbox_count: int
    last_sync: datetime | None
    db_size_mb: float
    staleness_hours: float | None


# SearchResult is imported from .search to avoid duplication


class IndexManager:
    """
    Manages the FTS5 search index for email body search.

    The index is stored at ~/.apple-mail-mcp/index.db by default.
    Use environment variables to customize:
    - APPLE_MAIL_INDEX_PATH: Database location
    - APPLE_MAIL_INDEX_MAX_EMAILS: Max emails per mailbox (5000)
    - APPLE_MAIL_INDEX_STALENESS_HOURS: Hours before stale (24)

    Thread Safety:
    - get_instance() uses class-level lock
    - _get_conn() uses instance-level lock
    - Watcher runs in separate thread with its own connection
    """

    _instance: IndexManager | None = None
    _instance_lock = threading.Lock()

    def __init__(self, db_path: Path | None = None):
        """
        Initialize the IndexManager.

        Args:
            db_path: Custom database path (uses config default if None)
        """
        self._db_path = db_path or get_index_path()
        self._conn: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()
        self._watcher: IndexWatcher | None = None
        self._watcher_callback: Callable[[int, int], None] | None = None

    @classmethod
    def get_instance(cls) -> IndexManager:
        """Get the singleton IndexManager instance (thread-safe)."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = IndexManager()
            return cls._instance

    @property
    def db_path(self) -> Path:
        """Get the database file path."""
        return self._db_path

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the database connection (thread-safe)."""
        with self._conn_lock:
            if self._conn is None:
                self._conn = init_database(self._db_path)
            return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def has_index(self) -> bool:
        """Check if an index database exists."""
        return self._db_path.exists()

    def get_stats(self) -> IndexStats:
        """
        Get index statistics.

        Returns:
            IndexStats with counts, size, and staleness info
        """
        conn = self._get_conn()

        # Email count
        cursor = conn.execute("SELECT COUNT(*) FROM emails")
        email_count = cursor.fetchone()[0]

        # Mailbox count
        cursor = conn.execute(
            "SELECT COUNT(DISTINCT account || '/' || mailbox) FROM emails"
        )
        mailbox_count = cursor.fetchone()[0]

        # Last sync time
        cursor = conn.execute("SELECT MAX(last_sync) FROM sync_state")
        row = cursor.fetchone()
        last_sync = None
        staleness_hours = None
        if row and row[0]:
            last_sync = datetime.fromisoformat(row[0])
            delta = (datetime.now() - last_sync).total_seconds()
            staleness_hours = delta / 3600

        # Database file size
        db_size_mb = 0.0
        if self._db_path.exists():
            db_size_mb = self._db_path.stat().st_size / (1024 * 1024)

        return IndexStats(
            email_count=email_count,
            mailbox_count=mailbox_count,
            last_sync=last_sync,
            db_size_mb=db_size_mb,
            staleness_hours=staleness_hours,
        )

    def is_stale(self) -> bool:
        """Check if the index needs a sync."""
        stats = self.get_stats()
        if stats.staleness_hours is None:
            return True
        return stats.staleness_hours > get_index_staleness_hours()

    def build_from_disk(
        self,
        progress_callback: Callable[[int, int | None, str], None] | None = None,
    ) -> int:
        """
        Build the index by reading .emlx files directly from disk.

        This requires Full Disk Access permission for the terminal.
        Much faster than fetching via JXA (~30x faster).

        Args:
            progress_callback: Optional callback(current, total, message)

        Returns:
            Number of emails indexed

        Raises:
            PermissionError: If Full Disk Access is not granted
            FileNotFoundError: If Mail directory not found
        """
        from .disk import find_mail_directory, scan_all_emails

        # Verify we can access the mail directory
        mail_dir = find_mail_directory()

        conn = self._get_conn()
        max_per_mailbox = get_index_max_emails()

        # Track counts per mailbox to enforce limits
        mailbox_counts: dict[tuple[str, str], int] = {}
        total_indexed = 0

        # Clear existing data for rebuild
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM emails")
        conn.execute("DELETE FROM sync_state")

        # Disable triggers during bulk insert for performance
        conn.execute("DROP TRIGGER IF EXISTS emails_ai")
        conn.execute("DROP TRIGGER IF EXISTS emails_ad")
        conn.execute("DROP TRIGGER IF EXISTS emails_au")

        batch: list[tuple] = []
        # Deferred attachment rows: (email_tuple_index, attachments)
        batch_attachments: list[tuple[int, list]] = []
        batch_size = 500

        try:
            for email_data in scan_all_emails(mail_dir):
                key = (email_data["account"], email_data["mailbox"])
                count = mailbox_counts.get(key, 0)

                if count >= max_per_mailbox:
                    continue

                mailbox_counts[key] = count + 1

                attachments = email_data.get("attachments", [])
                batch.append(
                    (
                        email_data["id"],
                        email_data["account"],
                        email_data["mailbox"],
                        email_data.get("subject", ""),
                        email_data.get("sender", ""),
                        email_data.get("content", ""),
                        email_data.get("date_received", ""),
                        email_data.get("emlx_path", ""),
                        len(attachments),
                    )
                )
                if attachments:
                    batch_attachments.append((len(batch) - 1, attachments))

                if len(batch) >= batch_size:
                    self._flush_batch(conn, batch, batch_attachments)
                    total_indexed += len(batch)

                    if progress_callback:
                        msg = f"Indexed {total_indexed} emails..."
                        progress_callback(total_indexed, None, msg)

                    batch = []
                    batch_attachments = []

        finally:
            # Flush any remaining partial batch (crash-safe)
            if batch:
                self._flush_batch(conn, batch, batch_attachments)
                total_indexed += len(batch)

            # Update sync state for whatever we managed to index
            if mailbox_counts:
                now = datetime.now().isoformat()
                for (account, mailbox), count in mailbox_counts.items():
                    conn.execute(
                        """INSERT OR REPLACE INTO sync_state
                           (account, mailbox, last_sync, message_count)
                           VALUES (?, ?, ?, ?)""",
                        (account, mailbox, now, count),
                    )
                conn.commit()

            # Rebuild FTS index (must run even if scan crashed
            # mid-iteration, otherwise emails table has rows
            # but FTS5 is empty)
            if total_indexed > 0:
                if progress_callback:
                    msg = "Building search index..."
                    progress_callback(total_indexed, total_indexed, msg)

                rebuild_fts_index(conn)
                optimize_fts_index(conn)

            # Re-enable triggers (use rowid, not message_id)
            conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS emails_ai
                AFTER INSERT ON emails BEGIN
                    INSERT INTO emails_fts(rowid, subject, sender, content)
                    VALUES (new.rowid, new.subject, new.sender, new.content);
                END;

                CREATE TRIGGER IF NOT EXISTS emails_ad
                AFTER DELETE ON emails BEGIN
                    INSERT INTO emails_fts(
                        emails_fts, rowid, subject, sender, content
                    ) VALUES(
                        'delete', old.rowid, old.subject,
                        old.sender, old.content
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS emails_au
                AFTER UPDATE ON emails BEGIN
                    INSERT INTO emails_fts(
                        emails_fts, rowid, subject, sender, content
                    ) VALUES(
                        'delete', old.rowid, old.subject,
                        old.sender, old.content
                    );
                    INSERT INTO emails_fts(rowid, subject, sender, content)
                    VALUES (new.rowid, new.subject, new.sender, new.content);
                END;
            """)

        return total_indexed

    @staticmethod
    def _flush_batch(
        conn: sqlite3.Connection,
        batch: list[tuple],
        batch_attachments: list[tuple[int, list]],
    ) -> None:
        """Insert a batch of emails and their attachment metadata."""
        conn.executemany(INSERT_EMAIL_SQL, batch)

        if batch_attachments:
            # For each email that had attachments, look up its rowid
            # and insert attachment rows
            for idx, attachments in batch_attachments:
                row_tuple = batch[idx]
                msg_id, account, mailbox = (
                    row_tuple[0],
                    row_tuple[1],
                    row_tuple[2],
                )
                cursor = conn.execute(
                    "SELECT rowid FROM emails "
                    "WHERE message_id = ? AND account = ? "
                    "AND mailbox = ?",
                    (msg_id, account, mailbox),
                )
                row = cursor.fetchone()
                if row:
                    rowid = row[0]
                    for att in attachments:
                        conn.execute(
                            INSERT_ATTACHMENT_SQL,
                            (
                                rowid,
                                att.filename,
                                att.mime_type,
                                att.file_size,
                                att.content_id,
                            ),
                        )

        conn.commit()

    def sync_updates(
        self,
        progress_callback: Callable[[int, int | None, str], None] | None = None,
    ) -> int:
        """
        Sync index with disk using state reconciliation.

        Compares the filesystem with the database to detect:
        - New emails (on disk, not in DB)
        - Deleted emails (in DB, not on disk)
        - Moved emails (same ID, different path)

        This is much faster than the old JXA-based sync (~30x faster)
        and handles deletions correctly.

        Args:
            progress_callback: Optional callback(current, total, message)

        Returns:
            Number of changes (added + deleted + moved)
        """
        from .disk import find_mail_directory
        from .sync import sync_from_disk

        try:
            mail_dir = find_mail_directory()
        except (FileNotFoundError, PermissionError) as e:
            logger.warning("Cannot access mail directory for sync: %s", e)
            return 0

        result = sync_from_disk(
            self._get_conn(),
            mail_dir,
            progress_callback,
        )
        return result.total_changes

    def search(
        self,
        query: str,
        account: str | None = None,
        mailbox: str | None = None,
        limit: int = 20,
        exclude_mailboxes: list[str] | None = None,
    ) -> list[SearchResult]:
        """
        Search indexed emails using FTS5.

        Args:
            query: Search query (supports FTS5 syntax)
            account: Optional account filter
            mailbox: Optional mailbox filter
            limit: Maximum results (default: 20)
            exclude_mailboxes: Mailboxes to exclude from results

        Returns:
            List of SearchResult ordered by relevance (BM25 score)
        """
        from .search import search_fts

        return search_fts(
            self._get_conn(),
            query,
            account=account,
            mailbox=mailbox,
            limit=limit,
            exclude_mailboxes=exclude_mailboxes,
        )

    def rebuild(
        self,
        account: str | None = None,
        mailbox: str | None = None,
        progress_callback: Callable[[int, int | None, str], None] | None = None,
    ) -> int:
        """
        Force rebuild of the index.

        Args:
            account: Optional account to rebuild (all if None)
            mailbox: Optional mailbox to rebuild (all in account if None)
            progress_callback: Optional progress callback

        Returns:
            Number of emails re-indexed
        """
        conn = self._get_conn()

        # Delete existing entries for rebuild scope
        if account and mailbox:
            conn.execute(
                "DELETE FROM emails WHERE account = ? AND mailbox = ?",
                (account, mailbox),
            )
        elif account:
            conn.execute("DELETE FROM emails WHERE account = ?", (account,))
        else:
            conn.execute("DELETE FROM emails")

        conn.commit()

        # Rebuild from disk
        return self.build_from_disk(progress_callback)

    def get_indexed_message_ids(
        self, account: str | None = None, mailbox: str | None = None
    ) -> set[int]:
        """
        Get all message IDs currently in the index.

        Note: Message IDs are only unique within (account, mailbox).

        Args:
            account: Optional account filter
            mailbox: Optional mailbox filter

        Returns:
            Set of message IDs
        """
        conn = self._get_conn()

        if account and mailbox:
            sql = """SELECT message_id FROM emails
                     WHERE account = ? AND mailbox = ?"""
            cursor = conn.execute(sql, (account, mailbox))
        elif account:
            cursor = conn.execute(
                "SELECT message_id FROM emails WHERE account = ?", (account,)
            )
        else:
            cursor = conn.execute("SELECT message_id FROM emails")

        return {row[0] for row in cursor}

    # ─────────────────────────────────────────────────────────────────
    # File Watcher Methods
    # ─────────────────────────────────────────────────────────────────

    def start_watcher(
        self,
        on_update: Callable[[int, int], None] | None = None,
    ) -> bool:
        """
        Start the file watcher for real-time index updates.

        Watches ~/Library/Mail/V10/ for .emlx changes and automatically
        updates the index when emails are added or deleted.

        Args:
            on_update: Optional callback(added_count, removed_count)
                       called after each batch of changes

        Returns:
            True if watcher started, False if already running or failed
        """
        if self._watcher is not None and self._watcher.is_running:
            return False

        from .watcher import IndexWatcher

        self._watcher_callback = on_update
        self._watcher = IndexWatcher(
            db_path=self._db_path,
            on_update=on_update,
        )

        return self._watcher.start()

    def stop_watcher(self) -> None:
        """Stop the file watcher if running."""
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    @property
    def watcher_running(self) -> bool:
        """Check if the file watcher is running."""
        return self._watcher is not None and self._watcher.is_running
