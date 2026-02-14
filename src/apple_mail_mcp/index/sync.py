"""Disk-based sync for email index.

Syncs the index with the current state of emails on disk using
state reconciliation (comparing disk inventory with DB inventory).

SECURITY NOTE: All strings passed to JXA are serialized via json.dumps()
to prevent injection attacks.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_index_max_emails
from .schema import INSERT_EMAIL_SQL, email_to_row

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a disk-based sync operation."""

    added: int
    deleted: int
    moved: int
    errors: int

    @property
    def total_changes(self) -> int:
        return self.added + self.deleted + self.moved


def get_db_inventory(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str, int], str]:
    """
    Get inventory of all emails in the database.

    Args:
        conn: Database connection

    Returns:
        Dict mapping (account, mailbox, msg_id) -> emlx_path (or "" if NULL)
    """
    cursor = conn.execute(
        "SELECT account, mailbox, message_id, emlx_path FROM emails"
    )

    inventory: dict[tuple[str, str, int], str] = {}
    for row in cursor:
        key = (row[0], row[1], row[2])
        inventory[key] = row[3] or ""

    return inventory


def sync_from_disk(
    conn: sqlite3.Connection,
    mail_dir: Path,
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> SyncResult:
    """
    Sync index with disk using state reconciliation.

    This is the PRIMARY sync method (replaces JXA-based sync).
    Compares disk inventory with DB inventory to detect:
    - NEW: on disk, not in DB → parse & insert
    - DELETED: in DB, not on disk → remove from DB
    - MOVED: same ID, different path → update path

    Args:
        conn: Database connection
        mail_dir: Path to ~/Library/Mail/V10/
        progress_callback: Optional callback(current, total, message)

    Returns:
        SyncResult with counts of added/deleted/moved emails
    """
    from .disk import get_disk_inventory, parse_emlx

    if progress_callback:
        progress_callback(0, None, "Scanning disk inventory...")

    # Get current state from disk (fast - no content parsing)
    disk_inv = get_disk_inventory(mail_dir)

    if progress_callback:
        progress_callback(0, None, "Loading database inventory...")

    # Get current state from database
    db_inv = get_db_inventory(conn)

    # Calculate diffs
    disk_keys = set(disk_inv.keys())
    db_keys = set(db_inv.keys())

    new_keys = disk_keys - db_keys
    deleted_keys = db_keys - disk_keys
    common_keys = disk_keys & db_keys

    # Check for moved emails (same key, different path)
    moved_keys = {
        key
        for key in common_keys
        if db_inv[key] and disk_inv[key] != db_inv[key]
    }

    total_ops = len(new_keys) + len(deleted_keys) + len(moved_keys)

    if progress_callback:
        progress_callback(
            0,
            total_ops,
            f"Syncing: {len(new_keys)} new, {len(deleted_keys)} deleted, "
            f"{len(moved_keys)} moved",
        )

    logger.info(
        "Sync diff: %d new, %d deleted, %d moved",
        len(new_keys),
        len(deleted_keys),
        len(moved_keys),
    )

    added = 0
    deleted = 0
    moved = 0
    errors = 0
    processed = 0

    max_per_mailbox = get_index_max_emails()
    mailbox_counts: dict[tuple[str, str], int] = {}

    # Get current counts per mailbox
    cursor = conn.execute(
        "SELECT account, mailbox, COUNT(*) FROM emails "
        "GROUP BY account, mailbox"
    )
    for row in cursor:
        mailbox_counts[(row[0], row[1])] = row[2]

    # Process NEW emails (parse content and insert)
    for key in new_keys:
        account, mailbox, msg_id = key
        path = disk_inv[key]

        # Check mailbox limit
        mb_key = (account, mailbox)
        current_count = mailbox_counts.get(mb_key, 0)
        if current_count >= max_per_mailbox:
            continue

        try:
            parsed = parse_emlx(Path(path))
            if parsed:
                row = email_to_row(
                    {
                        "id": parsed.id,
                        "subject": parsed.subject,
                        "sender": parsed.sender,
                        "content": parsed.content,
                        "date_received": parsed.date_received,
                    },
                    account,
                    mailbox,
                    path,
                )
                conn.execute(INSERT_EMAIL_SQL, row)
                added += 1
                mailbox_counts[mb_key] = current_count + 1
        except Exception as e:
            logger.debug("Failed to parse %s: %s", path, e)
            errors += 1

        processed += 1
        if progress_callback and processed % 100 == 0:
            progress_callback(processed, total_ops, f"Added {added} emails...")

    # Process DELETED emails (remove from DB)
    for key in deleted_keys:
        account, mailbox, msg_id = key
        try:
            conn.execute(
                "DELETE FROM emails WHERE account = ? AND mailbox = ? "
                "AND message_id = ?",
                (account, mailbox, msg_id),
            )
            deleted += 1
        except sqlite3.Error as e:
            logger.debug("Failed to delete %s: %s", key, e)
            errors += 1

        processed += 1

    # Process MOVED emails (update path)
    for key in moved_keys:
        account, mailbox, msg_id = key
        new_path = disk_inv[key]
        try:
            conn.execute(
                "UPDATE emails SET emlx_path = ? WHERE account = ? "
                "AND mailbox = ? AND message_id = ?",
                (new_path, account, mailbox, msg_id),
            )
            moved += 1
        except sqlite3.Error as e:
            logger.debug("Failed to update path for %s: %s", key, e)
            errors += 1

        processed += 1

    # Update sync state
    now = datetime.now().isoformat()
    affected_mailboxes = set()
    for key in new_keys | deleted_keys | moved_keys:
        affected_mailboxes.add((key[0], key[1]))

    for account, mailbox in affected_mailboxes:
        count = mailbox_counts.get((account, mailbox), 0)
        conn.execute(
            """INSERT OR REPLACE INTO sync_state
               (account, mailbox, last_sync, message_count)
               VALUES (?, ?, ?, ?)""",
            (account, mailbox, now, count),
        )

    # If no changes but we did a sync, still update a sync timestamp
    if not affected_mailboxes:
        conn.execute(
            """INSERT OR REPLACE INTO sync_state
               (account, mailbox, last_sync, message_count)
               VALUES (?, ?, ?, ?)""",
            ("_global", "_sync", now, 0),
        )

    conn.commit()

    if progress_callback:
        progress_callback(
            total_ops, total_ops, f"Sync complete: +{added} -{deleted} ~{moved}"
        )

    logger.info(
        "Sync complete: added=%d, deleted=%d, moved=%d, errors=%d",
        added,
        deleted,
        moved,
        errors,
    )

    return SyncResult(added=added, deleted=deleted, moved=moved, errors=errors)
