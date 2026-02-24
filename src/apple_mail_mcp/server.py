"""
Apple Mail MCP Server

Provides MCP tools for interacting with Apple Mail via optimized JXA scripts.
Uses batch property fetching for 87x faster performance.
Includes FTS5 search index for 700-3500x faster body search.

TOOLS (5 total):
- list_accounts() - List email accounts
- list_mailboxes(account?) - List mailboxes
- get_emails(..., filter?) - Unified email listing with filters
- get_email(id) - Get single email with content
- search(query, ...) - Unified search with FTS5 support
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import Literal, TypedDict

from fastmcp import FastMCP

from .builders import AccountsQueryBuilder, QueryBuilder
from .config import get_default_account, get_default_mailbox
from .executor import (
    build_mailbox_setup_js,
    execute_query_async,
    execute_with_core_async,
)

mcp = FastMCP("Apple Mail")


# ========== Response Type Definitions ==========


class Account(TypedDict):
    """An email account in Apple Mail."""

    name: str
    id: str


class Mailbox(TypedDict):
    """A mailbox within an email account."""

    name: str
    unreadCount: int


class EmailSummary(TypedDict):
    """Summary of an email (used in list/search results)."""

    id: int
    subject: str
    sender: str
    date_received: str
    read: bool
    flagged: bool


class SearchResult(TypedDict, total=False):
    """Result from search operations."""

    id: int
    subject: str
    sender: str
    date_received: str
    score: float
    matched_in: str
    content_snippet: str
    account: str
    mailbox: str


class AttachmentSummary(TypedDict):
    """Summary of an email attachment."""

    filename: str
    mime_type: str
    size: int


class EmailFull(TypedDict, total=False):
    """Complete email with full content."""

    id: int
    subject: str
    sender: str
    content: str
    date_received: str
    date_sent: str
    read: bool
    flagged: bool
    reply_to: str
    message_id: str
    attachments: list[AttachmentSummary]


# ========== Helper Functions ==========


def _get_index_manager():
    """Get the IndexManager singleton, lazily imported."""
    from .index import IndexManager

    return IndexManager.get_instance()


def _get_account_map():
    """Get the AccountMap singleton, lazily imported."""
    from .index.accounts import AccountMap

    return AccountMap.get_instance()


def _resolve_account(account: str | None) -> str | None:
    """Resolve account, using default from env if not specified."""
    return account if account is not None else get_default_account()


def _resolve_mailbox(mailbox: str | None) -> str:
    """Resolve mailbox, using default from env if not specified."""
    return mailbox if mailbox is not None else get_default_mailbox()


# Module-level lock to prevent duplicate concurrent syncs
_sync_lock = asyncio.Lock()


def _detect_matched_columns(query: str, result) -> str:
    """Detect which columns the query matched in.

    Extracts search terms from the query and checks them against
    the result's subject, sender, and content_snippet using simple
    Python string matching.

    Returns:
        Comma-separated list like ``"subject, body"``
    """
    # Extract search terms (strip FTS5 operators and quotes)
    terms = re.findall(r"[a-zA-Z0-9]+", query.lower())
    if not terms:
        return "body"

    matched = []

    subject_lower = (result.subject or "").lower()
    sender_lower = (result.sender or "").lower()

    if any(t in subject_lower for t in terms):
        matched.append("subject")
    if any(t in sender_lower for t in terms):
        matched.append("sender")

    # Body is always included since FTS5 matched the whole content
    matched.append("body")

    return ", ".join(matched)


# ========== MCP Tools (5 total) ==========


@mcp.tool
async def list_accounts() -> list[Account]:
    """
    List all configured email accounts in Apple Mail.

    Returns:
        List of account dictionaries with 'name' and 'id' fields.

    Example:
        >>> list_accounts()
        [{"name": "Work", "id": "abc123"}, {"name": "Personal", "id": "def456"}]
    """
    script = AccountsQueryBuilder().list_accounts()
    accounts = await execute_with_core_async(script)

    # Seed the account name↔UUID cache for search filtering
    _get_account_map().load_from_jxa(accounts)

    return accounts


@mcp.tool
async def list_mailboxes(account: str | None = None) -> list[Mailbox]:
    """
    List all mailboxes for an email account.

    Args:
        account: Account name. Uses APPLE_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.

    Returns:
        List of mailbox dictionaries with 'name' and 'unreadCount' fields.

    Example:
        >>> list_mailboxes("Work")
        [{"name": "INBOX", "unreadCount": 5}, ...]
    """
    script = AccountsQueryBuilder().list_mailboxes(_resolve_account(account))
    return await execute_with_core_async(script)


@mcp.tool
async def get_emails(
    account: str | None = None,
    mailbox: str | None = None,
    filter: Literal["all", "unread", "flagged", "today", "this_week"] = "all",
    limit: int = 50,
) -> list[EmailSummary]:
    """
    Get emails from a mailbox with optional filtering.

    Retrieves emails with standard properties: id, subject, sender,
    date_received, read status, and flagged status.

    Args:
        account: Account name. Uses APPLE_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses APPLE_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.
        filter: Filter type:
            - "all": All emails (default)
            - "unread": Only unread emails
            - "flagged": Only flagged emails
            - "today": Emails received today
            - "this_week": Emails received in the last 7 days
        limit: Maximum number of emails to return (default: 50)

    Returns:
        List of email dictionaries sorted by date (newest first).

    Examples:
        >>> get_emails()  # All emails from default mailbox
        >>> get_emails(filter="unread", limit=10)  # Unread emails
        >>> get_emails("Work", "INBOX", filter="today")  # Today's work emails
    """
    query = (
        QueryBuilder()
        .from_mailbox(_resolve_account(account), _resolve_mailbox(mailbox))
        .select("standard")
    )

    # Apply filter
    if filter == "unread":
        query = query.where("data.readStatus[i] === false")
    elif filter == "flagged":
        query = query.where("data.flaggedStatus[i] === true")
    elif filter == "today":
        query = query.where("data.dateReceived[i] >= MailCore.today()")
    elif filter == "this_week":
        query = query.where("data.dateReceived[i] >= MailCore.daysAgo(7)")

    query = query.order_by("date_received", descending=True).limit(limit)

    return await execute_query_async(query)


def _build_attachment_js() -> str:
    """Return JXA snippet to extract attachment metadata from `msg`."""
    return """
let attachments = [];
try {
    const atts = msg.mailAttachments();
    if (atts && atts.length > 0) {
        for (let a of atts) {
            try {
                attachments.push({
                    filename: a.name(),
                    mime_type: a.mimeType() || 'application/octet-stream',
                    size: a.fileSize() || 0
                });
            } catch(ae) {}
        }
    }
} catch(e) {}
"""


def _build_get_email_script(message_id: int, mailbox_setup: str) -> str:
    """Build JXA script to fetch a single email by ID.

    Extracted to avoid duplication between the primary and
    fallback fetch strategies.
    """
    att_js = _build_attachment_js()
    return f"""
const targetId = {message_id};
let msg = null;
{mailbox_setup}

const ids = mailbox.messages.id();
const idx = ids.indexOf(targetId);
if (idx !== -1) {{
    msg = mailbox.messages[idx];
}}

if (!msg) {{
    throw new Error('Message not found with ID: ' + targetId);
}}

{att_js}

JSON.stringify({{
    id: msg.id(),
    subject: msg.subject(),
    sender: msg.sender(),
    content: msg.content(),
    date_received: MailCore.formatDate(msg.dateReceived()),
    date_sent: MailCore.formatDate(msg.dateSent()),
    read: msg.readStatus(),
    flagged: msg.flaggedStatus(),
    reply_to: msg.replyTo(),
    message_id: msg.messageId(),
    attachments: attachments
}});
"""


@mcp.tool
async def get_email(
    message_id: int,
    account: str | None = None,
    mailbox: str | None = None,
) -> EmailFull:
    """
    Get a single email with full content.

    Uses a 3-strategy cascade:
    1. Try the specified mailbox directly
    2. Look up location in the FTS5 index (fast, no JXA)
    3. Iterate all mailboxes with per-mailbox error handling

    Args:
        message_id: The email's unique ID (from search results)
        account: Account name (optional, helps find message faster)
        mailbox: Mailbox name (optional, helps find message faster)

    Returns:
        Email dictionary with full content including:
        - id, subject, sender, date_received, date_sent
        - content: Full plain text body
        - read, flagged status
        - reply_to, message_id (email Message-ID header)
        - attachments: List of {filename, mime_type, size}

    Note:
        The attachments list comes from JXA's mailAttachments(),
        which only reports file attachments visible in Mail.app's
        UI. Inline images, S/MIME signatures, and attachments in
        sent/bounce-back emails may not appear. Use get_attachment
        with a known filename for reliable extraction from disk.

    Example:
        >>> get_email(12345)
        {"id": 12345, "subject": "Meeting notes",
         "content": "Hi team,\\n\\nHere are the notes...", ...}
    """
    resolved_account = _resolve_account(account)
    resolved_mailbox = _resolve_mailbox(mailbox)

    # Strategy 1: Try specified mailbox
    mailbox_setup = build_mailbox_setup_js(resolved_account, resolved_mailbox)
    script = _build_get_email_script(message_id, mailbox_setup)

    try:
        return await execute_with_core_async(script)
    except Exception:
        pass  # Fall through to strategy 2

    # Strategy 2: Index lookup — find the email's real location
    # Note: message_id is only unique within (account, mailbox), so
    # we scope the lookup when the caller provides those parameters.
    try:
        manager = _get_index_manager()
        if manager.has_index():
            conn = manager._get_conn()

            # Build scoped query to avoid ambiguous matches
            where = ["message_id = ?"]
            params: list = [message_id]

            acct_map = _get_account_map()
            await acct_map.ensure_loaded()

            if resolved_account:
                idx_acct = acct_map.name_to_uuid(resolved_account)
                if idx_acct:
                    where.append("account = ?")
                    params.append(idx_acct)
            if resolved_mailbox:
                where.append("mailbox = ?")
                params.append(resolved_mailbox)

            sql = (
                "SELECT account, mailbox FROM emails WHERE "
                + " AND ".join(where)
                + " LIMIT 1"
            )
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            if row:
                idx_account = row["account"]
                idx_mailbox = row["mailbox"]

                friendly_account = acct_map.uuid_to_name(idx_account)

                setup = build_mailbox_setup_js(friendly_account, idx_mailbox)
                script = _build_get_email_script(message_id, setup)
                try:
                    return await execute_with_core_async(script)
                except Exception:
                    pass  # Fall through to strategy 3
    except Exception:
        pass  # Index unavailable, fall through

    # Strategy 3: Iterate all mailboxes with per-mailbox error handling
    acct_setup = (
        f"const account = Mail.accounts.byName({json.dumps(resolved_account)});"
        if resolved_account
        else "const account = Mail.accounts[0];"
    )
    att_js = _build_attachment_js()
    script = f"""
const targetId = {message_id};
let msg = null;
{acct_setup}

const allMailboxes = account.mailboxes();
for (let i = 0; i < allMailboxes.length && !msg; i++) {{
    try {{
        const mb = allMailboxes[i];
        const mbIds = mb.messages.id();
        const mbIdx = mbIds.indexOf(targetId);
        if (mbIdx !== -1) {{
            msg = mb.messages[mbIdx];
        }}
    }} catch(e) {{
        // Skip inaccessible mailboxes (Junk/Drafts -1728)
    }}
}}

if (!msg) {{
    throw new Error('Message not found with ID: ' + targetId);
}}

{att_js}

JSON.stringify({{
    id: msg.id(),
    subject: msg.subject(),
    sender: msg.sender(),
    content: msg.content(),
    date_received: MailCore.formatDate(msg.dateReceived()),
    date_sent: MailCore.formatDate(msg.dateSent()),
    read: msg.readStatus(),
    flagged: msg.flaggedStatus(),
    reply_to: msg.replyTo(),
    message_id: msg.messageId(),
    attachments: attachments
}});
"""
    return await execute_with_core_async(script)


MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB


class AttachmentContent(TypedDict, total=False):
    """Attachment content returned by get_attachment."""

    filename: str
    mime_type: str
    size: int
    content_base64: str
    truncated: bool


@mcp.tool
async def get_attachment(
    message_id: int,
    filename: str,
    account: str | None = None,
    mailbox: str | None = None,
) -> AttachmentContent:
    """
    Get the content of a specific email attachment.

    Looks up the email's .emlx file path in the index and extracts
    the attachment binary content, returning it as base64. This
    parses the raw MIME structure, so it works for all attachment
    types including inline images and S/MIME signatures.

    Requires the search index. If upgrading from v0.1.2, run
    'apple-mail-mcp rebuild' to populate attachment metadata.

    Args:
        message_id: The email's unique ID
        filename: The attachment filename to extract
        account: Account name (optional, used for index lookup)
        mailbox: Mailbox name (optional, used for index lookup)

    Returns:
        Dictionary with filename, mime_type, size, and content_base64.
        If the attachment exceeds 10 MB, returns metadata only with
        truncated=True.

    Example:
        >>> get_attachment(12345, "invoice.pdf")
        {"filename": "invoice.pdf", "mime_type": "application/pdf",
         "size": 52340, "content_base64": "JVBERi0x..."}
    """
    from .index.disk import get_attachment_content

    # Look up emlx_path from the index, scoped by account/mailbox
    # when provided (message_id is only unique within a mailbox)
    manager = _get_index_manager()
    if not manager.has_index():
        raise ValueError("No search index. Run 'apple-mail-mcp index'.")

    conn = manager._get_conn()

    where = ["message_id = ?"]
    params: list = [message_id]
    if account:
        acct_map = _get_account_map()
        await acct_map.ensure_loaded()
        idx_acct = acct_map.name_to_uuid(account) or account
        where.append("account = ?")
        params.append(idx_acct)
    if mailbox:
        where.append("mailbox = ?")
        params.append(mailbox)

    sql = (
        "SELECT emlx_path FROM emails WHERE " + " AND ".join(where) + " LIMIT 1"
    )
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    if not row or not row["emlx_path"]:
        raise ValueError(f"Email {message_id} not found in index.")

    emlx_path = Path(row["emlx_path"])
    result = await asyncio.to_thread(
        get_attachment_content, emlx_path, filename
    )
    if result is None:
        raise ValueError(
            f"Attachment '{filename}' not found in email {message_id}."
        )

    raw_bytes, mime_type = result

    if len(raw_bytes) > MAX_ATTACHMENT_SIZE:
        return {
            "filename": filename,
            "mime_type": mime_type,
            "size": len(raw_bytes),
            "truncated": True,
        }

    return {
        "filename": filename,
        "mime_type": mime_type,
        "size": len(raw_bytes),
        "content_base64": base64.b64encode(raw_bytes).decode("ascii"),
    }


@mcp.tool
async def search(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    scope: Literal["all", "subject", "sender", "body", "attachments"] = "all",
    limit: int = 20,
    exclude_mailboxes: list[str] | None = None,
) -> list[SearchResult]:
    """
    Search emails with automatic FTS5 optimization.

    Uses the FTS5 index for fast search (~2ms) when available.
    Falls back to JXA-based search if no index exists.

    Args:
        query: Search term or phrase
        account: Account name (optional filter).
            For FTS scopes (all/body/attachments): None searches all accounts.
            For JXA scopes (subject/sender): None uses the default account.
        mailbox: Mailbox name (optional filter).
            For FTS scopes (all/body/attachments): None searches all mailboxes.
            For JXA scopes (subject/sender): None uses the default mailbox.
        scope: Where to search:
            - "all": Search subject, sender, AND body (default, uses FTS5)
            - "subject": Search subject only (JXA, single mailbox)
            - "sender": Search sender only (JXA, single mailbox)
            - "body": Search body content only (FTS5)
            - "attachments": Search by attachment filename (SQL)
        limit: Maximum results (default: 20)
        exclude_mailboxes: Mailboxes to exclude (default: ["Drafts"]).
            Only applies to FTS and attachment scopes.

    Returns:
        List of matching emails sorted by relevance (when using FTS5)
        or by date (when using JXA fallback).

    Examples:
        >>> search("invoice")  # Search everywhere
        >>> search("john@example.com", scope="sender")
        >>> search("meeting notes", scope="body")
    """
    if exclude_mailboxes is None:
        exclude_mailboxes = ["Drafts"]

    # Attachment filename search (SQL LIKE query, no JXA needed)
    if scope == "attachments":
        manager = _get_index_manager()
        if not manager.has_index():
            return []

        conn = manager._get_conn()
        like_pattern = f"%{query}%"

        sql = """
            SELECT e.message_id, e.account, e.mailbox,
                   e.subject, e.sender, e.date_received,
                   a.filename
            FROM attachments a
            JOIN emails e ON a.email_rowid = e.rowid
            WHERE a.filename LIKE ?
        """
        params: list = [like_pattern]

        if account:
            acct_map = _get_account_map()
            await acct_map.ensure_loaded()
            search_acct = acct_map.name_to_uuid(account) or account
            sql += " AND e.account = ?"
            params.append(search_acct)
        if mailbox:
            sql += " AND e.mailbox = ?"
            params.append(mailbox)
        if exclude_mailboxes:
            placeholders = ",".join("?" for _ in exclude_mailboxes)
            sql += f" AND e.mailbox NOT IN ({placeholders})"
            params.extend(exclude_mailboxes)

        sql += " ORDER BY e.date_received DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(sql, params)
        acct_map = _get_account_map()
        await acct_map.ensure_loaded()

        return [
            {
                "id": row["message_id"],
                "subject": row["subject"],
                "sender": row["sender"],
                "date_received": row["date_received"],
                "score": 1.0,
                "matched_in": f"attachment: {row['filename']}",
                "account": acct_map.uuid_to_name(row["account"]),
                "mailbox": row["mailbox"],
            }
            for row in cursor
        ]

    # S5: Split FTS5 vs JXA resolution
    # FTS5: None = search all accounts/mailboxes
    fts_account = account
    fts_mailbox = mailbox
    # JXA: resolve defaults (needs a concrete target)
    jxa_account = _resolve_account(account)
    jxa_mailbox = _resolve_mailbox(mailbox)

    # Try FTS5 index for "all" or "body" scope
    if scope in ("all", "body"):
        manager = _get_index_manager()
        if manager.has_index():
            # S2: Auto-sync stale index before search
            if manager.is_stale():
                async with _sync_lock:
                    if manager.is_stale():  # double-check
                        await asyncio.to_thread(manager.sync_updates)

            # Translate friendly name → UUID for index lookup
            acct_map = _get_account_map()
            await acct_map.ensure_loaded()

            search_account = None
            if fts_account:
                search_account = (
                    acct_map.name_to_uuid(fts_account)
                    or fts_account  # fallback: maybe already UUID
                )

            results = manager.search(
                query,
                account=search_account,
                mailbox=fts_mailbox,
                limit=limit,
                exclude_mailboxes=exclude_mailboxes,
            )
            return [
                {
                    "id": r.id,
                    "subject": r.subject,
                    "sender": r.sender,
                    "date_received": r.date_received,
                    "score": r.score,
                    "matched_in": _detect_matched_columns(query, r),
                    "content_snippet": r.content_snippet,
                    "account": acct_map.uuid_to_name(r.account),
                    "mailbox": r.mailbox,
                }
                for r in results
            ]

    # JXA-based search for subject/sender or when no index
    safe_query_js = json.dumps(query.lower())

    if scope == "subject":
        filter_expr = (
            f"(data.subject[i] || '').toLowerCase().includes({safe_query_js})"
        )
    elif scope == "sender":
        filter_expr = (
            f"(data.sender[i] || '').toLowerCase().includes({safe_query_js})"
        )
    else:
        # "all" without index - search subject and sender
        filter_expr = f"""(
            (data.subject[i] || '').toLowerCase().includes({safe_query_js}) ||
            (data.sender[i] || '').toLowerCase().includes({safe_query_js})
        )"""

    q = (
        QueryBuilder()
        .from_mailbox(jxa_account, jxa_mailbox)
        .select("standard")
        .where(filter_expr)
        .order_by("date_received", descending=True)
        .limit(limit)
    )

    emails = await execute_query_async(q)

    # Convert to SearchResult format
    return [
        {
            "id": e["id"],
            "subject": e["subject"],
            "sender": e["sender"],
            "date_received": e["date_received"],
            "score": 1.0,  # No ranking for JXA search
            "matched_in": scope if scope != "all" else "metadata",
        }
        for e in emails
    ]


if __name__ == "__main__":
    mcp.run()
