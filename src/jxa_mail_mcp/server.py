"""
JXA Mail MCP Server

Provides MCP tools for interacting with Apple Mail via optimized JXA scripts.
Uses batch property fetching for 87x faster performance.
Includes FTS5 search index for ~100x faster body search.
"""

from __future__ import annotations

import json
from typing import TypedDict

from fastmcp import FastMCP

from .builders import AccountsQueryBuilder, QueryBuilder
from .config import get_default_account, get_default_mailbox
from .executor import (
    build_mailbox_setup_js,
    execute_query_async,
    execute_with_core_async,
)

mcp = FastMCP("JXA Mail")


# ========== Response Type Definitions ==========
# TypedDict provides explicit typing for API responses, improving
# code completion, documentation, and type checking.


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


class FuzzySearchResult(TypedDict, total=False):
    """Result from fuzzy search operations."""

    id: int
    subject: str
    sender: str
    date_received: str
    read: bool
    flagged: bool
    score: float
    matched_in: str
    matched_text: str
    # Additional fields when searching indexed content
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


class IndexStatus(TypedDict, total=False):
    """Status information about the FTS5 search index."""

    exists: bool
    message: str
    index_path: str
    email_count: int
    mailbox_count: int
    db_size_mb: float
    last_sync: str | None
    staleness_hours: float | None
    is_stale: bool


class OperationResult(TypedDict, total=False):
    """Result from index operations (sync, rebuild)."""

    success: bool
    message: str
    new_emails: int
    emails_indexed: int


# ========== JXA Script Helpers ==========


def _build_fuzzy_search_script(
    mailbox_setup: str,
    query_js: str,
    threshold: float,
    limit: int,
    include_body: bool = False,
) -> str:
    """
    Build a JXA script for fuzzy email search.

    This helper reduces duplication between fuzzy_search_emails() and
    search_email_bodies() (JXA fallback).

    Args:
        mailbox_setup: JXA code to set up account/mailbox variables
        query_js: JSON-serialized query string
        threshold: Minimum similarity score
        limit: Maximum results to return
        include_body: Whether to search email body content

    Returns:
        Complete JXA script string
    """
    # Properties to fetch
    if include_body:
        props = "['id', 'subject', 'sender', 'content', "
        props += "'dateReceived', 'readStatus', 'flaggedStatus']"
    else:
        props = "['id', 'subject', 'sender', "
        props += "'dateReceived', 'readStatus', 'flaggedStatus']"

    # Content extraction (only if searching body)
    if include_body:
        content_line = "const content = data.content[i] || '';"
    else:
        content_line = ""

    # Body match logic
    if include_body:
        body_match = (
            "const bodyMatch = MailCore.fuzzyMatchBody(query, content);"
        )
        body_check = """if (bodyMatch && bodyMatch.score > bestScore) {
        bestScore = bodyMatch.score;
        matchedIn = 'body';
        matchedText = bodyMatch.matched;
    }"""
    else:
        body_match = ""
        body_check = ""

    return f"""
{mailbox_setup}
const msgs = mailbox.messages;
const query = {query_js};
const threshold = {threshold};

// Batch fetch properties
const data = MailCore.batchFetch(msgs, {props});

const results = [];
const count = data.id.length;

for (let i = 0; i < count; i++) {{
    const subject = data.subject[i] || '';
    const sender = data.sender[i] || '';
    {content_line}

    // Try matching against fields
    const subjectMatch = MailCore.fuzzyMatch(query, subject);
    const senderMatch = MailCore.fuzzyMatch(query, sender);
    {body_match}

    // Take the best match
    let bestScore = 0;
    let matchedIn = null;
    let matchedText = null;

    if (subjectMatch && subjectMatch.score > bestScore) {{
        bestScore = subjectMatch.score;
        matchedIn = 'subject';
        matchedText = subjectMatch.matched;
    }}
    if (senderMatch && senderMatch.score > bestScore) {{
        bestScore = senderMatch.score;
        matchedIn = 'sender';
        matchedText = senderMatch.matched;
    }}
    {body_check}

    if (bestScore >= threshold) {{
        results.push({{
            id: data.id[i],
            subject: subject,
            sender: sender,
            date_received: MailCore.formatDate(data.dateReceived[i]),
            read: data.readStatus[i],
            flagged: data.flaggedStatus[i],
            score: Math.round(bestScore * 100) / 100,
            matched_in: matchedIn,
            matched_text: matchedText
        }});
    }}
}}

// Sort by score descending, then by date
results.sort((a, b) => {{
    if (b.score !== a.score) return b.score - a.score;
    return new Date(b.date_received) - new Date(a.date_received);
}});

JSON.stringify(results.slice(0, {limit}));
"""


# ========== Helper Functions ==========


def _get_index_manager():
    """Get the IndexManager singleton, lazily imported."""
    from .index import IndexManager

    return IndexManager.get_instance()


def _resolve_account(account: str | None) -> str | None:
    """Resolve account, using default from env if not specified."""
    return account if account is not None else get_default_account()


def _resolve_mailbox(mailbox: str | None) -> str:
    """Resolve mailbox, using default from env if not specified."""
    return mailbox if mailbox is not None else get_default_mailbox()


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
    return await execute_with_core_async(script)


@mcp.tool
async def list_mailboxes(account: str | None = None) -> list[Mailbox]:
    """
    List all mailboxes for an email account.

    Args:
        account: Account name. Uses JXA_MAIL_DEFAULT_ACCOUNT env var or
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
    limit: int = 50,
) -> list[EmailSummary]:
    """
    Get emails from a mailbox.

    Retrieves emails with standard properties: id, subject, sender,
    date_received, read status, and flagged status.

    Args:
        account: Account name. Uses JXA_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses JXA_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.
        limit: Maximum number of emails to return (default: 50)

    Returns:
        List of email dictionaries sorted by date (newest first).

    Example:
        >>> get_emails("Work", "INBOX", limit=10)
        [{"subject": "Meeting tomorrow", "sender": "boss@work.com", ...}, ...]
    """
    query = (
        QueryBuilder()
        .from_mailbox(_resolve_account(account), _resolve_mailbox(mailbox))
        .select("standard")
        .order_by("date_received", descending=True)
        .limit(limit)
    )
    return await execute_query_async(query)


@mcp.tool
async def get_todays_emails(
    account: str | None = None,
    mailbox: str | None = None,
) -> list[EmailSummary]:
    """
    Get all emails received today from a mailbox.

    Args:
        account: Account name. Uses JXA_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses JXA_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.

    Returns:
        List of today's emails sorted by date (newest first).

    Example:
        >>> get_todays_emails("Work")
        [{"subject": "Urgent: Review needed", "sender": "team@work.com", ...}]
    """
    query = (
        QueryBuilder()
        .from_mailbox(_resolve_account(account), _resolve_mailbox(mailbox))
        .select("standard")
        .where("data.dateReceived[i] >= MailCore.today()")
        .order_by("date_received", descending=True)
    )
    return await execute_query_async(query)


@mcp.tool
async def get_unread_emails(
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 50,
) -> list[EmailSummary]:
    """
    Get unread emails from a mailbox.

    Args:
        account: Account name. Uses JXA_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses JXA_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.
        limit: Maximum number of emails to return (default: 50)

    Returns:
        List of unread emails sorted by date (newest first).

    Example:
        >>> get_unread_emails("Work", limit=20)
        [{"subject": "New message", "read": false, ...}, ...]
    """
    query = (
        QueryBuilder()
        .from_mailbox(_resolve_account(account), _resolve_mailbox(mailbox))
        .select("standard")
        .where("data.readStatus[i] === false")
        .order_by("date_received", descending=True)
        .limit(limit)
    )
    return await execute_query_async(query)


@mcp.tool
async def get_flagged_emails(
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 50,
) -> list[EmailSummary]:
    """
    Get flagged emails from a mailbox.

    Args:
        account: Account name. Uses JXA_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses JXA_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.
        limit: Maximum number of emails to return (default: 50)

    Returns:
        List of flagged emails sorted by date (newest first).

    Example:
        >>> get_flagged_emails("Work")
        [{"subject": "Important task", "flagged": true, ...}, ...]
    """
    query = (
        QueryBuilder()
        .from_mailbox(_resolve_account(account), _resolve_mailbox(mailbox))
        .select("standard")
        .where("data.flaggedStatus[i] === true")
        .order_by("date_received", descending=True)
        .limit(limit)
    )
    return await execute_query_async(query)


@mcp.tool
async def search_emails(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 50,
) -> list[EmailSummary]:
    """
    Search for emails matching a query string.

    Searches in both subject and sender fields (case-insensitive).

    Args:
        query: Search term to look for
        account: Account name. Uses JXA_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses JXA_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.
        limit: Maximum number of results (default: 50)

    Returns:
        List of matching emails sorted by date (newest first).

    Example:
        >>> search_emails("invoice", "Work")
        [{"subject": "Invoice #123", "sender": "billing@vendor.com", ...}]
    """
    # Use json.dumps for safe JavaScript string serialization
    safe_query_js = json.dumps(query.lower())

    filter_expr = f"""(
        (data.subject[i] || '').toLowerCase().includes({safe_query_js}) ||
        (data.sender[i] || '').toLowerCase().includes({safe_query_js})
    )"""

    q = (
        QueryBuilder()
        .from_mailbox(_resolve_account(account), _resolve_mailbox(mailbox))
        .select("standard")
        .where(filter_expr)
        .order_by("date_received", descending=True)
        .limit(limit)
    )
    return await execute_query_async(q)


@mcp.tool
async def fuzzy_search_emails(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 20,
    threshold: float = 0.3,
) -> list[FuzzySearchResult]:
    """
    Fuzzy search for emails using trigram + Levenshtein matching.

    Finds emails even with typos or partial matches. Uses trigrams for
    fast candidate selection and Levenshtein distance for accurate ranking.

    Args:
        query: Search term (fuzzy matched against subject and sender)
        account: Account name. Uses JXA_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses JXA_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.
        limit: Maximum number of results (default: 20)
        threshold: Minimum similarity score 0-1 (default: 0.3)

    Returns:
        List of matching emails with similarity scores, sorted by score.

    Example:
        >>> fuzzy_search_emails("joob descrption")  # typos OK
        [{"subject": "Job Description", "score": 0.85, ...}, ...]
    """
    resolved_account = _resolve_account(account)
    resolved_mailbox = _resolve_mailbox(mailbox)

    mailbox_setup = build_mailbox_setup_js(resolved_account, resolved_mailbox)
    script = _build_fuzzy_search_script(
        mailbox_setup=mailbox_setup,
        query_js=json.dumps(query),
        threshold=threshold,
        limit=limit,
        include_body=False,
    )
    return await execute_with_core_async(script)


@mcp.tool
async def search_email_bodies(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 20,
    threshold: float = 0.3,
    use_index: bool = True,
) -> list[FuzzySearchResult]:
    """
    Search within email body content using fuzzy matching.

    Searches the full text content of emails, not just metadata.
    When the FTS5 index is available (built via 'jxa-mail-mcp index'),
    searches are ~100x faster (~50ms vs ~7s).

    Args:
        query: Search term to find in email bodies
        account: Account name. Uses JXA_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses JXA_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.
        limit: Maximum number of results (default: 20)
        threshold: Minimum similarity score 0-1 (default: 0.3)
        use_index: Use FTS5 index if available (default: True)

    Returns:
        List of matching emails with scores, sorted by relevance.
        Includes 'matched_in' field indicating where match was found
        and 'matched_text'/'content_snippet' showing context.

    Example:
        >>> search_email_bodies("project deadline")
        [{"subject": "Re: Updates", "score": 0.95, "matched_in": "body",
          "matched_text": "...project deadline is...", ...}]
    """
    resolved_account = _resolve_account(account)
    resolved_mailbox = _resolve_mailbox(mailbox)

    # Try using the FTS5 index for fast search
    if use_index:
        manager = _get_index_manager()
        if manager.has_index():
            results = manager.search(
                query,
                account=resolved_account,
                mailbox=resolved_mailbox if mailbox else None,
                limit=limit,
            )
            # Convert SearchResult objects to dicts matching JXA output format
            return [
                {
                    "id": r.id,
                    "subject": r.subject,
                    "sender": r.sender,
                    "date_received": r.date_received,
                    "score": r.score,
                    "matched_in": "body",
                    "matched_text": r.content_snippet,
                    "account": r.account,
                    "mailbox": r.mailbox,
                }
                for r in results
            ]

    # Fallback to JXA-based search (slower)
    mailbox_setup = build_mailbox_setup_js(resolved_account, resolved_mailbox)
    script = _build_fuzzy_search_script(
        mailbox_setup=mailbox_setup,
        query_js=json.dumps(query),
        threshold=threshold,
        limit=limit,
        include_body=True,
    )
    return await execute_with_core_async(script)


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

    # Use json.dumps for safe serialization
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
def index_status() -> IndexStatus:
    """
    Get the status of the FTS5 search index.

    Returns information about the email search index including:
    - Whether an index exists
    - Number of indexed emails and mailboxes
    - Database file size
    - Last sync time and staleness

    Use this to check if the index needs rebuilding.

    Returns:
        Dictionary with index statistics or status message if no index.

    Example:
        >>> index_status()
        {"exists": true, "email_count": 5432, "mailbox_count": 15,
         "db_size_mb": 45.2, "last_sync": "2024-01-15T10:30:00", ...}
    """
    manager = _get_index_manager()

    if not manager.has_index():
        return {
            "exists": False,
            "message": "No index found. Run 'jxa-mail-mcp index' to build.",
            "index_path": str(manager.db_path),
        }

    stats = manager.get_stats()

    staleness = None
    if stats.staleness_hours is not None:
        staleness = round(stats.staleness_hours, 1)

    return {
        "exists": True,
        "email_count": stats.email_count,
        "mailbox_count": stats.mailbox_count,
        "db_size_mb": round(stats.db_size_mb, 2),
        "last_sync": stats.last_sync.isoformat() if stats.last_sync else None,
        "staleness_hours": staleness,
        "is_stale": manager.is_stale(),
        "index_path": str(manager.db_path),
    }


@mcp.tool
def sync_index() -> OperationResult:
    """
    Sync the search index with new emails.

    Fetches emails that arrived since the last sync via JXA
    and adds them to the FTS5 index. This is faster than a full
    rebuild but requires an existing index.

    Note: For initial indexing, use 'jxa-mail-mcp index' CLI command
    which reads directly from disk (much faster).

    Returns:
        Dictionary with sync results.

    Example:
        >>> sync_index()
        {"success": true, "new_emails": 23, "message": "Synced 23 new emails"}
    """
    manager = _get_index_manager()

    if not manager.has_index():
        return {
            "success": False,
            "message": "No index. Run 'jxa-mail-mcp index' to build.",
        }

    try:
        count = manager.sync_updates()
        msg = f"Synced {count} new emails" if count else "Index up to date"
        return {
            "success": True,
            "new_emails": count,
            "message": msg,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Sync failed: {e}",
        }


@mcp.tool
def rebuild_index(
    account: str | None = None,
    mailbox: str | None = None,
) -> OperationResult:
    """
    Rebuild the search index from disk.

    Forces a complete rebuild of the FTS5 index by reading .emlx files
    directly from ~/Library/Mail/. This requires Full Disk Access.

    For normal use, prefer 'sync_index()' which only fetches new emails.

    Args:
        account: Optional - only rebuild this account (all if not specified)
        mailbox: Optional - only rebuild this mailbox (requires account)

    Returns:
        Dictionary with rebuild results.

    Example:
        >>> rebuild_index()
        {"success": true, "emails_indexed": 5432, "message": "Rebuilt index..."}
    """
    manager = _get_index_manager()

    try:
        count = manager.rebuild(account=account, mailbox=mailbox)
        return {
            "success": True,
            "emails_indexed": count,
            "message": f"Rebuilt index with {count} emails",
        }
    except PermissionError as e:
        return {
            "success": False,
            "message": (
                f"Permission denied: {e}\n"
                "Grant Full Disk Access to the MCP server process."
            ),
        }
    except FileNotFoundError as e:
        return {
            "success": False,
            "message": f"Mail directory not found: {e}",
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Rebuild failed: {e}",
        }


if __name__ == "__main__":
    mcp.run()
