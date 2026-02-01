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


@mcp.tool
def fuzzy_search_emails(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 20,
    threshold: float = 0.3,
) -> list[dict]:
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
    safe_query = query.replace("\\", "\\\\").replace("'", "\\'")
    resolved_account = _resolve_account(account)
    resolved_mailbox = _resolve_mailbox(mailbox)

    # Build account reference
    if resolved_account:
        safe_account = resolved_account.replace("'", "\\'")
        account_js = f"MailCore.getAccount('{safe_account}')"
    else:
        account_js = "MailCore.getAccount(null)"

    safe_mailbox = resolved_mailbox.replace("'", "\\'")

    script = f"""
const account = {account_js};
const mailbox = MailCore.getMailbox(account, '{safe_mailbox}');
const msgs = mailbox.messages;
const query = '{safe_query}';
const threshold = {threshold};

// Batch fetch properties
const data = MailCore.batchFetch(msgs, [
    'id', 'subject', 'sender', 'dateReceived', 'readStatus', 'flaggedStatus'
]);

const results = [];
const count = data.id.length;

for (let i = 0; i < count; i++) {{
    const subject = data.subject[i] || '';
    const sender = data.sender[i] || '';

    // Try matching against subject and sender
    const subjectMatch = MailCore.fuzzyMatch(query, subject);
    const senderMatch = MailCore.fuzzyMatch(query, sender);

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
    return execute_with_core(script)


@mcp.tool
def search_email_bodies(
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 20,
    threshold: float = 0.3,
) -> list[dict]:
    """
    Search within email body content using fuzzy matching.

    Searches the full text content of emails, not just metadata.
    Uses a fast two-tier approach: exact substring matching first,
    then trigram similarity for typo tolerance.

    Note: Slower than metadata-only search due to fetching email bodies.
    Use search_emails() or fuzzy_search_emails() for faster metadata search.

    Args:
        query: Search term to find in email bodies
        account: Account name. Uses JXA_MAIL_DEFAULT_ACCOUNT env var or
                 first account if not specified.
        mailbox: Mailbox name. Uses JXA_MAIL_DEFAULT_MAILBOX env var or
                 "Inbox" if not specified.
        limit: Maximum number of results (default: 20)
        threshold: Minimum similarity score 0-1 (default: 0.3)

    Returns:
        List of matching emails with scores, sorted by relevance.
        Includes 'matched_in' field indicating where match was found
        (subject, sender, or body) and 'matched_text' showing the match.

    Example:
        >>> search_email_bodies("project deadline")
        [{"subject": "Re: Updates", "score": 0.95, "matched_in": "body",
          "matched_text": "...project deadline is...", ...}]
    """
    safe_query = query.replace("\\", "\\\\").replace("'", "\\'")
    resolved_account = _resolve_account(account)
    resolved_mailbox = _resolve_mailbox(mailbox)

    # Build account reference
    if resolved_account:
        safe_account = resolved_account.replace("'", "\\'")
        account_js = f"MailCore.getAccount('{safe_account}')"
    else:
        account_js = "MailCore.getAccount(null)"

    safe_mailbox = resolved_mailbox.replace("'", "\\'")

    script = f"""
const account = {account_js};
const mailbox = MailCore.getMailbox(account, '{safe_mailbox}');
const msgs = mailbox.messages;
const query = '{safe_query}';
const threshold = {threshold};

// Batch fetch properties INCLUDING content
const data = MailCore.batchFetch(msgs, [
    'id', 'subject', 'sender', 'content',
    'dateReceived', 'readStatus', 'flaggedStatus'
]);

const results = [];
const count = data.id.length;

for (let i = 0; i < count; i++) {{
    const subject = data.subject[i] || '';
    const sender = data.sender[i] || '';
    const content = data.content[i] || '';

    // Try matching against subject, sender, and body
    const subjectMatch = MailCore.fuzzyMatch(query, subject);
    const senderMatch = MailCore.fuzzyMatch(query, sender);
    const bodyMatch = MailCore.fuzzyMatchBody(query, content);

    // Take the best match across all fields
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
    if (bodyMatch && bodyMatch.score > bestScore) {{
        bestScore = bodyMatch.score;
        matchedIn = 'body';
        matchedText = bodyMatch.matched;
    }}

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
    return execute_with_core(script)


@mcp.tool
def get_email(
    message_id: int,
    account: str | None = None,
    mailbox: str | None = None,
) -> dict:
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

    # Build account reference
    if resolved_account:
        safe_account = resolved_account.replace("'", "\\'")
        account_js = f"MailCore.getAccount('{safe_account}')"
    else:
        account_js = "MailCore.getAccount(null)"

    safe_mailbox = resolved_mailbox.replace("'", "\\'")

    script = f"""
const targetId = {message_id};

// Try to find the message in the specified mailbox first
let msg = null;
const account = {account_js};
const mailbox = MailCore.getMailbox(account, '{safe_mailbox}');

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
    return execute_with_core(script)


if __name__ == "__main__":
    mcp.run()
