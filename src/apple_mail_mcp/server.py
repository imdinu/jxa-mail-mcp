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

import json
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


class EmailFull(TypedDict):
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


@mcp.tool
async def get_email(
    message_id: int,
    account: str | None = None,
    mailbox: str | None = None,
) -> EmailFull:
    """
    Get a single email with full content.

    Retrieves complete email details including the full body text.
    Use this after finding an email via search to read its content.

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

    Example:
        >>> get_email(12345)
        {"id": 12345, "subject": "Meeting notes",
         "content": "Hi team,\\n\\nHere are the notes...", ...}
    """
    resolved_account = _resolve_account(account)
    resolved_mailbox = _resolve_mailbox(mailbox)

    mailbox_setup = build_mailbox_setup_js(resolved_account, resolved_mailbox)

    script = f"""
const targetId = {message_id};

// Try to find the message in the specified mailbox first
let msg = null;
{mailbox_setup}

// Search in specified mailbox
const ids = mailbox.messages.id();
const idx = ids.indexOf(targetId);
if (idx !== -1) {{
    msg = mailbox.messages[idx];
}}

// If not found, search all mailboxes in the account
if (!msg) {{
    const allMailboxes = account.mailboxes();
    for (let i = 0; i < allMailboxes.length && !msg; i++) {{
        const mb = allMailboxes[i];
        const mbIds = mb.messages.id();
        const mbIdx = mbIds.indexOf(targetId);
        if (mbIdx !== -1) {{
            msg = mb.messages[mbIdx];
        }}
    }}
}}

if (!msg) {{
    throw new Error('Message not found with ID: ' + targetId);
}}

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
    message_id: msg.messageId()
}});
"""
    return await execute_with_core_async(script)


@mcp.tool
async def search(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    scope: Literal["all", "subject", "sender", "body"] = "all",
    limit: int = 20,
) -> list[SearchResult]:
    """
    Search emails with automatic FTS5 optimization.

    Uses the FTS5 index for fast search (~2ms) when available.
    Falls back to JXA-based search if no index exists.

    Args:
        query: Search term or phrase
        account: Account name (optional filter)
        mailbox: Mailbox name (optional filter)
        scope: Where to search:
            - "all": Search subject, sender, AND body (default, uses FTS5)
            - "subject": Search subject only
            - "sender": Search sender only
            - "body": Search body content only
        limit: Maximum results (default: 20)

    Returns:
        List of matching emails sorted by relevance (when using FTS5)
        or by date (when using JXA fallback).

    Examples:
        >>> search("invoice")  # Search everywhere
        >>> search("john@example.com", scope="sender")  # From specific sender
        >>> search("meeting notes", scope="body")  # In email body only
    """
    resolved_account = _resolve_account(account)
    resolved_mailbox = _resolve_mailbox(mailbox)

    # Try FTS5 index for "all" or "body" scope
    if scope in ("all", "body"):
        manager = _get_index_manager()
        if manager.has_index():
            # Translate friendly name → UUID for index lookup
            acct_map = _get_account_map()
            await acct_map.ensure_loaded()

            search_account = None
            if resolved_account:
                search_account = (
                    acct_map.name_to_uuid(resolved_account)
                    or resolved_account  # fallback: maybe already a UUID
                )

            results = manager.search(
                query,
                account=search_account,
                mailbox=mailbox,  # pass raw value — None means all
                limit=limit,
            )
            return [
                {
                    "id": r.id,
                    "subject": r.subject,
                    "sender": r.sender,
                    "date_received": r.date_received,
                    "score": r.score,
                    "matched_in": "body",
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
        .from_mailbox(resolved_account, resolved_mailbox)
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
