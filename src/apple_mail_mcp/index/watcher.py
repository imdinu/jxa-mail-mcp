"""File watcher for real-time index updates.

Watches ~/Library/Mail/V10/ for .emlx file changes and updates the index.

Uses watchfiles (Rust-based, efficient) to monitor:
- New emails → parse and add to index
- Deleted emails → remove from index

The watcher runs in a background thread and batches updates to avoid
overwhelming the database with rapid changes.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .disk import find_mail_directory, parse_emlx
from .schema import (
    INSERT_ATTACHMENT_SQL,
    INSERT_EMAIL_SQL,
    create_connection,
    email_to_row,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Regex to extract account/mailbox from path
# ~/Library/Mail/V10/[AccountUUID]/[Mailbox].mbox/.../*.emlx
PATH_PATTERN = re.compile(r"/V\d+/([^/]+)/([^/]+)\.mbox/.*?/(\d+)\.emlx$")

# Constants for safety limits
MAX_PENDING_CHANGES = 10000  # Prevent unbounded memory growth
DELETE_BATCH_SIZE = 500  # SQLite variable limit safety
FILE_RETRY_DELAY_MS = 200  # Wait for Mail.app to finish writing
MAX_FILE_RETRIES = 3


class IndexWatcher:
    """
    Watches Mail directory for changes and updates the index.

    Usage:
        watcher = IndexWatcher(db_path, on_update=callback)
        watcher.start()
        # ... later ...
        watcher.stop()
    """

    def __init__(
        self,
        db_path: Path,
        on_update: Callable[[int, int], None] | None = None,
        debounce_ms: int = 500,
    ):
        """
        Initialize the watcher.

        Args:
            db_path: Path to the index database
            on_update: Optional callback(added, removed) after processing
            debounce_ms: Milliseconds to wait before processing changes
        """
        self.db_path = db_path
        self.on_update = on_update
        self.debounce_ms = debounce_ms

        self._mail_dir: Path | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Pending changes (debounced)
        self._pending_adds: dict[
            tuple[str, str, int], Path
        ] = {}  # (acc, mb, id) -> path
        self._pending_deletes: set[tuple[str, str, int]] = (
            set()
        )  # (acc, mb, id)
        self._pending_lock = threading.Lock()

        # Persistent connection for the watcher thread
        self._conn: sqlite3.Connection | None = None

    def start(self) -> bool:
        """
        Start watching for changes.

        Returns:
            True if started successfully, False if mail dir not found
        """
        try:
            self._mail_dir = find_mail_directory()
        except FileNotFoundError:
            logger.warning("Mail directory not found, watcher not started")
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="IndexWatcher",
            daemon=True,
        )
        self._thread.start()
        logger.info("File watcher started for %s", self._mail_dir)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop watching and wait for thread to finish."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

        # Close persistent connection
        if self._conn:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

        logger.info("File watcher stopped")

    @property
    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._thread is not None and self._thread.is_alive()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a persistent connection for this thread."""
        if self._conn is None:
            self._conn = create_connection(self.db_path)
        return self._conn

    def _watch_loop(self) -> None:
        """Main watch loop (runs in background thread)."""
        try:
            from watchfiles import Change, watch
        except ImportError:
            logger.error("watchfiles not installed, file watcher unavailable")
            return

        if not self._mail_dir:
            return

        logger.debug("Starting watch loop on %s", self._mail_dir)

        for changes in watch(
            self._mail_dir,
            stop_event=self._stop_event,
            debounce=self.debounce_ms,
            recursive=True,
        ):
            if self._stop_event.is_set():
                break

            # Collect changes
            for change_type, path_str in changes:
                if not path_str.endswith(".emlx"):
                    continue

                # Security: Validate path is within mail directory
                try:
                    path = Path(path_str).resolve()
                    if not str(path).startswith(str(self._mail_dir.resolve())):
                        logger.warning(
                            "Ignoring path outside mail dir: %s", path
                        )
                        continue
                except (OSError, ValueError) as e:
                    logger.warning("Invalid path %s: %s", path_str, e)
                    continue

                parsed = self._parse_path(path)
                if not parsed:
                    continue

                account, mailbox, message_id = parsed
                key = (account, mailbox, message_id)

                with self._pending_lock:
                    # Prevent unbounded memory growth
                    total_pending = len(self._pending_adds) + len(
                        self._pending_deletes
                    )
                    if total_pending >= MAX_PENDING_CHANGES:
                        logger.warning(
                            "Pending limit (%d) reached, clearing",
                            MAX_PENDING_CHANGES,
                        )
                        # Clear half to make room
                        self._pending_adds.clear()

                    if change_type == Change.added:
                        self._pending_adds[key] = path
                        self._pending_deletes.discard(key)
                    elif change_type == Change.deleted:
                        self._pending_deletes.add(key)
                        self._pending_adds.pop(key, None)
                    elif change_type == Change.modified:
                        # Treat as add (re-index)
                        self._pending_adds[key] = path

            # Process after debounce period
            self._process_pending()

    def _parse_path(self, path: Path) -> tuple[str, str, int] | None:
        """
        Extract account, mailbox, and message ID from path.

        Returns:
            (account_name, mailbox_name, message_id) or None if invalid
        """
        match = PATH_PATTERN.search(str(path))
        if not match:
            return None

        account_uuid, mailbox_dir, message_id_str = match.groups()

        try:
            message_id = int(message_id_str)
        except ValueError:
            return None

        # Use UUID as account name (more reliable than trying to map)
        account_name = account_uuid
        mailbox_name = mailbox_dir

        return account_name, mailbox_name, message_id

    def _process_pending(self) -> None:
        """Process pending adds and deletes."""
        with self._pending_lock:
            adds = dict(self._pending_adds)
            deletes = set(self._pending_deletes)
            self._pending_adds.clear()
            self._pending_deletes.clear()

        if not adds and not deletes:
            return

        added_count = 0
        deleted_count = 0

        try:
            conn = self._get_conn()

            # Process deletes in batches to avoid SQLite variable limit
            if deletes:
                delete_list = list(deletes)
                for i in range(0, len(delete_list), DELETE_BATCH_SIZE):
                    batch = delete_list[i : i + DELETE_BATCH_SIZE]
                    # Use composite key for deletion
                    for account, mailbox, msg_id in batch:
                        sql = """DELETE FROM emails WHERE account = ?
                                 AND mailbox = ? AND message_id = ?"""
                        conn.execute(sql, (account, mailbox, msg_id))
                    deleted_count += len(batch)

            # Process adds with retry for files still being written
            for key, path in adds.items():
                account, mailbox, _ = key
                email = None

                # Retry logic for race condition with Mail.app writing
                for attempt in range(MAX_FILE_RETRIES):
                    try:
                        email = parse_emlx(path)
                        if email:
                            break
                    except OSError as e:
                        if attempt < MAX_FILE_RETRIES - 1:
                            logger.debug(
                                "Retry %d for %s: %s", attempt + 1, path, e
                            )
                            time.sleep(FILE_RETRY_DELAY_MS / 1000)
                        else:
                            logger.warning(
                                "Failed to parse %s after retries: %s", path, e
                            )
                    except (ValueError, UnicodeDecodeError) as e:
                        logger.warning("Error parsing %s: %s", path, e)
                        break

                if email:
                    try:
                        attachments = email.attachments or []
                        row = email_to_row(
                            {
                                "id": email.id,
                                "subject": email.subject,
                                "sender": email.sender,
                                "content": email.content,
                                "date_received": email.date_received,
                            },
                            account,
                            mailbox,
                            str(path),
                            attachment_count=len(attachments),
                        )
                        conn.execute(INSERT_EMAIL_SQL, row)

                        if attachments:
                            rowid = conn.execute(
                                "SELECT last_insert_rowid()"
                            ).fetchone()[0]
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

                        added_count += 1
                    except sqlite3.IntegrityError as e:
                        logger.debug("Duplicate email %s: %s", key, e)
                    except sqlite3.Error as e:
                        logger.error("Database error for %s: %s", key, e)

            conn.commit()

        except sqlite3.Error as e:
            logger.error("Database error in watcher: %s", e)

        # Notify callback
        if self.on_update and (added_count or deleted_count):
            try:
                self.on_update(added_count, deleted_count)
            except Exception as e:  # Broad: user callback
                logger.warning("Error in on_update callback: %s", e)

        if added_count or deleted_count:
            logger.debug(
                "Processed: +%d -%d emails", added_count, deleted_count
            )


def create_watcher(
    db_path: Path,
    on_update: Callable[[int, int], None] | None = None,
) -> IndexWatcher:
    """
    Create and return a new IndexWatcher.

    Args:
        db_path: Path to the index database
        on_update: Optional callback(added, removed) after changes

    Returns:
        Configured IndexWatcher (call .start() to begin watching)
    """
    return IndexWatcher(db_path, on_update=on_update)
