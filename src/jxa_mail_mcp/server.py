"""
JXA Mail MCP Server

Provides MCP tools for interacting with Apple Mail via optimized JXA scripts.
Uses batch property fetching for 87x faster performance.
"""

from fastmcp import FastMCP

from .builders import AccountsQueryBuilder, QueryBuilder
from .config import get_default_account, get_default_mailbox
from .executor import execute_query, execute_with_core

mcp = FastMCP("JXA Mail")


def _resolve_account(account: str | None) -> str | None:
    """Resolve account, using default from env if not specified."""
    return account if account is not None else get_default_account()


def _resolve_mailbox(mailbox: str | None) -> str:
    """Resolve mailbox, using default from env if not specified."""
    return mailbox if mailbox is not None else get_default_mailbox()


@mcp.tool
def list_accounts() -> list[dict]:
    """
    List all configured email accounts in Apple Mail.

    Returns:
        List of account dictionaries with 'name' and 'id' fields.

    Example:
        >>> list_accounts()
        [{"name": "Work", "id": "abc123"}, {"name": "Personal", "id": "def456"}]
    """
    script = AccountsQueryBuilder().list_accounts()
    return execute_with_core(script)


@mcp.tool
def list_mailboxes(account: str | None = None) -> list[dict]:
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
    return execute_with_core(script)


@mcp.tool
def get_emails(
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 50,
) -> list[dict]:
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
    return execute_query(query)


@mcp.tool
def get_todays_emails(
    account: str | None = None,
    mailbox: str | None = None,
) -> list[dict]:
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
    return execute_query(query)


@mcp.tool
def get_unread_emails(
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 50,
) -> list[dict]:
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
    return execute_query(query)


@mcp.tool
def get_flagged_emails(
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 50,
) -> list[dict]:
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
    return execute_query(query)


@mcp.tool
def search_emails(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 50,
) -> list[dict]:
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
    # Escape the query for safe JavaScript string interpolation
    safe_query = query.lower().replace("\\", "\\\\").replace("'", "\\'")

    filter_expr = f"""(
        (data.subject[i] || '').toLowerCase().includes('{safe_query}') ||
        (data.sender[i] || '').toLowerCase().includes('{safe_query}')
    )"""

    q = (
        QueryBuilder()
        .from_mailbox(_resolve_account(account), _resolve_mailbox(mailbox))
        .select("standard")
        .where(filter_expr)
        .order_by("date_received", descending=True)
        .limit(limit)
    )
    return execute_query(q)


if __name__ == "__main__":
    mcp.run()
