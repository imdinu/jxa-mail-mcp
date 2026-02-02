"""Incremental sync via JXA for new emails.

This module syncs new emails that arrived since the last index build.
Uses JXA (slower) because it works without Full Disk Access.

OPTIMIZED SYNC (sync_by_date):
- Uses date-based filtering: only fetch emails received after last sync
- Single JXA query across all accounts (vs. N queries per mailbox)
- ~10-100x faster than ID-comparison approach

LEGACY SYNC (sync_incremental):
- Compares all IDs between index and Mail.app
- Slower but handles edge cases like moved/deleted emails

SECURITY NOTE: All strings passed to JXA are serialized via json.dumps()
to prevent injection attacks.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..config import get_index_max_emails
from ..executor import build_mailbox_setup_js
from .schema import INSERT_EMAIL_SQL, email_to_row
from .search import add_account_mailbox_filter

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def get_last_sync_time(conn: sqlite3.Connection) -> datetime | None:
    """Get the most recent sync timestamp from any mailbox."""
    cursor = conn.execute(
        "SELECT MAX(last_sync) FROM sync_state WHERE last_sync IS NOT NULL"
    )
    row = cursor.fetchone()
    if row and row[0]:
        try:
            return datetime.fromisoformat(row[0])
        except ValueError:
            return None
    return None


def fetch_emails_since_date_jxa(
    since: datetime,
    limit_per_mailbox: int = 100,
) -> list[dict]:
    """
    Fetch all emails received after a given date via JXA.

    Uses Mail.app's `whose` clause for efficient server-side filtering.
    This is MUCH faster than fetching all IDs and comparing.

    Args:
        since: Only fetch emails received after this datetime
        limit_per_mailbox: Max emails per mailbox to prevent runaway queries

    Returns:
        List of email dicts with account/mailbox info included
    """
    from ..executor import execute_with_core

    # Format date for JXA comparison (ISO format works with JS Date)
    since_iso = since.isoformat()

    script = f"""
const cutoffDate = new Date({json.dumps(since_iso)});
const results = [];
const accounts = Mail.accounts();

for (let i = 0; i < accounts.length; i++) {{
    const account = accounts[i];
    const accountName = account.name();
    const mailboxes = account.mailboxes();

    for (let j = 0; j < mailboxes.length; j++) {{
        const mailbox = mailboxes[j];
        const mailboxName = mailbox.name();

        try {{
            // Use whose() for efficient filtering - Mail.app filters internally
            const recentMsgs = mailbox.messages.whose({{
                dateReceived: {{ '>': cutoffDate }}
            }})();

            // Limit results per mailbox
            const limit = Math.min(recentMsgs.length, {limit_per_mailbox});

            if (limit > 0) {{
                // Batch fetch properties for efficiency
                const msgSlice = recentMsgs.slice(0, limit);
                for (const msg of msgSlice) {{
                    try {{
                        results.push({{
                            id: msg.id(),
                            subject: msg.subject() || '',
                            sender: msg.sender() || '',
                            content: msg.content() || '',
                            date_received: msg.dateReceived() ?
                                msg.dateReceived().toISOString() : '',
                            account: accountName,
                            mailbox: mailboxName
                        }});
                    }} catch (e) {{
                        // Skip individual message errors
                    }}
                }}
            }}
        }} catch (e) {{
            // Skip mailbox errors (e.g., permission issues)
        }}
    }}
}}

JSON.stringify(results);
"""

    try:
        result = execute_with_core(script, timeout=60)
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.warning("Failed to fetch recent emails: %s", e)
        return []


def sync_by_date(
    conn: sqlite3.Connection,
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> int:
    """
    Fast date-based sync using a single JXA query.

    Instead of comparing IDs across all mailboxes, this:
    1. Gets the last sync timestamp
    2. Fetches only emails received after that date
    3. Inserts new emails (skipping duplicates)

    This is ~10-100x faster than sync_incremental for typical use.

    Args:
        conn: Database connection
        progress_callback: Optional callback(current, total, message)

    Returns:
        Number of new emails synced
    """
    if progress_callback:
        progress_callback(0, None, "Checking last sync time...")

    # Get last sync time, default to 24 hours ago if never synced
    last_sync = get_last_sync_time(conn)
    if last_sync is None:
        # First sync after index build - check last 24 hours
        last_sync = datetime.now() - timedelta(hours=24)
        logger.info("No previous sync found, checking last 24 hours")
    else:
        logger.info("Last sync: %s", last_sync.isoformat())

    if progress_callback:
        progress_callback(0, None, "Fetching new emails...")

    # Single JXA call to get all recent emails
    emails = fetch_emails_since_date_jxa(last_sync)

    if not emails:
        logger.info("No new emails since last sync")
        if progress_callback:
            progress_callback(1, 1, "Index up to date")
        return 0

    logger.info("Found %d potentially new emails", len(emails))

    if progress_callback:
        progress_callback(0, len(emails), f"Processing {len(emails)} emails...")

    # Insert emails, skipping duplicates
    inserted = 0
    for i, email in enumerate(emails):
        try:
            conn.execute(
                INSERT_EMAIL_SQL,
                (
                    email["id"],
                    email["account"],
                    email["mailbox"],
                    email.get("subject", ""),
                    email.get("sender", ""),
                    email.get("content", ""),
                    email.get("date_received", ""),
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            # Already exists - skip
            pass
        except sqlite3.Error as e:
            logger.debug("DB error for email %s: %s", email.get("id"), e)

        if progress_callback and (i + 1) % 50 == 0:
            msg = f"Processed {i + 1} emails..."
            progress_callback(i + 1, len(emails), msg)

    # Update sync state for all affected mailboxes
    now = datetime.now().isoformat()
    mailboxes_seen = {(e["account"], e["mailbox"]) for e in emails}
    for account, mailbox in mailboxes_seen:
        conn.execute(
            """INSERT OR REPLACE INTO sync_state
               (account, mailbox, last_sync) VALUES (?, ?, ?)""",
            (account, mailbox, now),
        )

    conn.commit()

    if progress_callback:
        msg = f"Synced {inserted} new emails"
        progress_callback(len(emails), len(emails), msg)

    logger.info(
        "Synced %d new emails (out of %d candidates)", inserted, len(emails)
    )
    return inserted


def get_indexed_message_ids(
    conn: sqlite3.Connection,
    account: str | None = None,
    mailbox: str | None = None,
) -> set[int]:
    """
    Get all message IDs currently in the index.

    Args:
        conn: Database connection
        account: Optional account filter
        mailbox: Optional mailbox filter

    Returns:
        Set of message IDs (note: only unique within account/mailbox)
    """
    # Use "WHERE 1=1" pattern to allow consistent filter appending
    sql = "SELECT message_id FROM emails e WHERE 1=1"
    params: list = []
    sql = add_account_mailbox_filter(sql, params, account, mailbox)

    cursor = conn.execute(sql, params)
    return {row[0] for row in cursor}


def fetch_mailbox_ids_jxa(account: str | None, mailbox: str) -> list[int]:
    """
    Fetch all message IDs from a mailbox via JXA.

    Args:
        account: Account name (None for first account)
        mailbox: Mailbox name

    Returns:
        List of message IDs
    """
    from ..executor import execute_with_core

    # Use shared helper for safe serialization
    mailbox_setup = build_mailbox_setup_js(account, mailbox)

    script = f"""
{mailbox_setup}
if (!mailbox) {{
    JSON.stringify([]);
}} else {{
    const ids = mailbox.messages.id();
    JSON.stringify(ids || []);
}}
"""

    try:
        result = execute_with_core(script)
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.warning("Failed to fetch IDs for %s/%s: %s", account, mailbox, e)
        return []


def fetch_emails_by_ids_jxa(
    account: str | None, mailbox: str, message_ids: list[int]
) -> list[dict]:
    """
    Fetch email content for specific IDs via JXA.

    Args:
        account: Account name
        mailbox: Mailbox name
        message_ids: List of message IDs to fetch

    Returns:
        List of email dicts with id, subject, sender, content, date_received
    """
    if not message_ids:
        return []

    from ..executor import execute_with_core

    # Use shared helper for safe serialization
    mailbox_setup = build_mailbox_setup_js(account, mailbox)
    ids_json = json.dumps(message_ids)

    script = f"""
{mailbox_setup}
if (!mailbox) {{
    JSON.stringify([]);
}} else {{
    const targetIds = new Set({ids_json});

    // Batch fetch IDs to find indices
    const allIds = mailbox.messages.id() || [];
    const indices = [];
    for (let i = 0; i < allIds.length; i++) {{
        if (targetIds.has(allIds[i])) {{
            indices.push(i);
        }}
    }}

    // Fetch properties for matching messages only
    const results = [];
    for (const idx of indices) {{
        try {{
            const msg = mailbox.messages[idx];
            results.push({{
                id: msg.id(),
                subject: msg.subject() || '',
                sender: msg.sender() || '',
                content: msg.content() || '',
                date_received: MailCore.formatDate(msg.dateReceived())
            }});
        }} catch (e) {{
            // Skip messages that can't be read
        }}
    }}

    JSON.stringify(results);
}}
"""

    try:
        result = execute_with_core(script)
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.warning(
            "Failed to fetch emails for %s/%s: %s", account, mailbox, e
        )
        return []


def get_all_mailboxes_jxa() -> list[tuple[str, str]]:
    """
    Get all account/mailbox pairs via JXA.

    Returns:
        List of (account_name, mailbox_name) tuples
    """
    from ..executor import execute_with_core

    script = """
const results = [];
const accounts = Mail.accounts();
const accountNames = Mail.accounts.name();

for (let i = 0; i < accounts.length; i++) {
    const account = accounts[i];
    const accountName = accountNames[i];
    const mailboxNames = account.mailboxes.name() || [];

    for (const mbName of mailboxNames) {
        results.push([accountName, mbName]);
    }
}

JSON.stringify(results);
"""

    try:
        result = execute_with_core(script)
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.warning("Failed to get mailboxes: %s", e)
        return []


def sync_incremental(
    conn: sqlite3.Connection,
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> int:
    """
    Sync new emails via JXA.

    Compares indexed IDs with Mail.app and fetches only new emails.
    Much faster than rebuild for startup sync.

    Args:
        conn: Database connection
        progress_callback: Optional callback(current, total, message)

    Returns:
        Number of new emails synced
    """
    max_per_mailbox = get_index_max_emails()
    total_synced = 0

    # Get all mailboxes
    if progress_callback:
        progress_callback(0, None, "Discovering mailboxes...")

    mailboxes = get_all_mailboxes_jxa()
    if not mailboxes:
        logger.info("No mailboxes found to sync")
        return 0

    logger.info("Syncing %d mailboxes", len(mailboxes))

    for i, (account, mailbox) in enumerate(mailboxes):
        if progress_callback:
            progress_callback(
                i, len(mailboxes), f"Syncing {account}/{mailbox}..."
            )

        # Get already indexed IDs for this mailbox
        indexed_ids = get_indexed_message_ids(conn, account, mailbox)

        # Check if we're at the limit
        if len(indexed_ids) >= max_per_mailbox:
            logger.debug("Mailbox %s/%s at limit, skipping", account, mailbox)
            continue

        # Get current IDs from Mail.app
        current_ids = fetch_mailbox_ids_jxa(account, mailbox)
        if not current_ids:
            continue

        # Find new IDs
        new_ids = [mid for mid in current_ids if mid not in indexed_ids]

        if not new_ids:
            continue

        # Limit to stay under max
        remaining_capacity = max_per_mailbox - len(indexed_ids)
        new_ids = new_ids[:remaining_capacity]

        logger.debug(
            "Fetching %d new emails from %s/%s", len(new_ids), account, mailbox
        )

        # Fetch content for new emails (in batches)
        batch_size = 50
        for batch_start in range(0, len(new_ids), batch_size):
            batch_ids = new_ids[batch_start : batch_start + batch_size]
            emails = fetch_emails_by_ids_jxa(account, mailbox, batch_ids)

            if not emails:
                continue

            # Insert into database using centralized SQL and tuple converter
            for email in emails:
                try:
                    row = email_to_row(email, account, mailbox)
                    conn.execute(INSERT_EMAIL_SQL, row)
                except sqlite3.IntegrityError:
                    logger.debug(
                        "Duplicate email ID %s in %s/%s",
                        email["id"],
                        account,
                        mailbox,
                    )
                except sqlite3.Error as e:
                    logger.error("Database error: %s", e)

            total_synced += len(emails)

        # Update sync state
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO sync_state
               (account, mailbox, last_sync, message_count)
               VALUES (?, ?, ?, ?)""",
            (account, mailbox, now, len(indexed_ids) + len(new_ids)),
        )

        conn.commit()

    if progress_callback:
        progress_callback(len(mailboxes), len(mailboxes), "Sync complete")

    logger.info("Synced %d new emails", total_synced)
    return total_synced
