"""FTS5 full-text search for indexed emails.

Provides:
- search_fts(): Search indexed emails with BM25 ranking
- sanitize_fts_query(): Escape special FTS5 syntax characters

FTS5 query syntax supported:
- Simple terms: "meeting notes"
- Phrases: '"exact phrase"'
- Boolean: "meeting OR notes"
- Prefix: "meet*"
- Column filter: "subject:urgent"
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

# Characters that have special meaning in FTS5 and need escaping
FTS5_SPECIAL_CHARS = re.compile(r'(["\'\-\*\(\)\:\^])')


def add_account_mailbox_filter(
    sql: str,
    params: list,
    account: str | None,
    mailbox: str | None,
    table_alias: str = "e",
) -> str:
    """
    Add account/mailbox WHERE clauses to a SQL query.

    This helper reduces repetition when building filtered queries.
    Modifies params in-place and returns the updated SQL string.

    Args:
        sql: Base SQL query string
        params: List of query parameters (modified in-place)
        account: Optional account filter
        mailbox: Optional mailbox filter
        table_alias: Table alias prefix (default: "e")

    Returns:
        Updated SQL string with added WHERE clauses

    Example:
        >>> sql = "SELECT * FROM emails e WHERE 1=1"
        >>> params = []
        >>> sql = add_account_mailbox_filter(sql, params, "Work", "INBOX")
        >>> sql
        "SELECT * FROM emails e WHERE 1=1 AND e.account = ? AND e.mailbox = ?"
        >>> params
        ["Work", "INBOX"]
    """
    if account:
        sql += f" AND {table_alias}.account = ?"
        params.append(account)
    if mailbox:
        sql += f" AND {table_alias}.mailbox = ?"
        params.append(mailbox)
    return sql


@dataclass
class SearchResult:
    """A single search result with ranking info."""

    id: int
    account: str
    mailbox: str
    subject: str
    sender: str
    content_snippet: str
    date_received: str
    score: float


def sanitize_fts_query(query: str) -> str:
    """
    Sanitize a query string for safe FTS5 use.

    Escapes special characters to prevent syntax errors.
    Boolean operators (OR, AND, NOT) are preserved since they
    don't contain special characters.

    Args:
        query: Raw user query

    Returns:
        Sanitized query safe for FTS5
    """
    if not query:
        return ""

    # Remove leading/trailing whitespace
    query = query.strip()

    # Escape all FTS5 special characters
    # Boolean operators (OR, AND, NOT) are unaffected since they
    # don't contain any of these chars: " ' - * ( ) : ^
    sanitized = FTS5_SPECIAL_CHARS.sub(r"\\\1", query)

    return sanitized


def _extract_snippet(content: str, max_length: int = 150) -> str:
    """Extract a snippet from content for display."""
    if not content:
        return ""

    # Remove excessive whitespace
    text = " ".join(content.split())

    if len(text) <= max_length:
        return text

    # Truncate and add ellipsis
    return text[:max_length].rsplit(" ", 1)[0] + "..."


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 20,
    *,
    _is_retry: bool = False,
) -> list[SearchResult]:
    """
    Search indexed emails using FTS5 with BM25 ranking.

    Args:
        conn: Database connection
        query: Search query (supports FTS5 syntax)
        account: Optional account filter
        mailbox: Optional mailbox filter
        limit: Maximum results (default: 20)

    Returns:
        List of SearchResult ordered by relevance (BM25 score)
    """
    if not query or not query.strip():
        return []

    # Sanitize query for FTS5 (skip on retry to avoid double-escaping)
    safe_query = query if _is_retry else sanitize_fts_query(query)

    if not safe_query:
        return []

    # Build the SQL query with optional filters
    # BM25 returns negative scores (more negative = better match)
    # We negate it for intuitive positive scores
    # Note: FTS5 content_rowid='rowid' links to emails.rowid
    sql = """
        SELECT
            e.message_id,
            e.account,
            e.mailbox,
            e.subject,
            e.sender,
            e.content,
            e.date_received,
            -bm25(emails_fts, 1.0, 0.5, 2.0) as score
        FROM emails_fts
        JOIN emails e ON emails_fts.rowid = e.rowid
        WHERE emails_fts MATCH ?
    """

    params: list = [safe_query]
    sql = add_account_mailbox_filter(sql, params, account, mailbox)
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(limit)

    try:
        cursor = conn.execute(sql, params)
        results = []

        for row in cursor:
            results.append(
                SearchResult(
                    id=row[0],
                    account=row[1],
                    mailbox=row[2],
                    subject=row[3] or "",
                    sender=row[4] or "",
                    content_snippet=_extract_snippet(row[5]),
                    date_received=row[6] or "",
                    score=round(row[7], 3),
                )
            )

        return results

    except sqlite3.OperationalError as e:
        # FTS5 syntax error - try with phrase search as fallback
        if "fts5: syntax error" in str(e).lower() and not _is_retry:
            # Wrap entire query in quotes as a phrase search
            escaped_query = '"' + query.replace('"', '""') + '"'
            return search_fts(
                conn,
                escaped_query,
                account=account,
                mailbox=mailbox,
                limit=limit,
                _is_retry=True,
            )
        raise


def search_fts_highlight(
    conn: sqlite3.Connection,
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
    limit: int = 20,
) -> list[SearchResult]:
    """
    Search with highlighted snippets showing match context.

    Similar to search_fts but uses FTS5 highlight() function
    to mark matched terms in the content.

    Args:
        conn: Database connection
        query: Search query
        account: Optional account filter
        mailbox: Optional mailbox filter
        limit: Maximum results

    Returns:
        List of SearchResult with highlighted content_snippet
    """
    if not query or not query.strip():
        return []

    safe_query = sanitize_fts_query(query)
    if not safe_query:
        return []

    # Use highlight() to mark matches with ** markers
    sql = """
        SELECT
            e.message_id,
            e.account,
            e.mailbox,
            highlight(emails_fts, 0, '**', '**') as subject_hl,
            e.sender,
            snippet(emails_fts, 2, '**', '**', '...', 32) as content_snippet,
            e.date_received,
            -bm25(emails_fts, 1.0, 0.5, 2.0) as score
        FROM emails_fts
        JOIN emails e ON emails_fts.rowid = e.rowid
        WHERE emails_fts MATCH ?
    """

    params: list = [safe_query]
    sql = add_account_mailbox_filter(sql, params, account, mailbox)
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(limit)

    try:
        cursor = conn.execute(sql, params)
        results = []

        for row in cursor:
            results.append(
                SearchResult(
                    id=row[0],
                    account=row[1],
                    mailbox=row[2],
                    subject=row[3] or "",
                    sender=row[4] or "",
                    content_snippet=row[5] or "",
                    date_received=row[6] or "",
                    score=round(row[7], 3),
                )
            )

        return results

    except sqlite3.OperationalError:
        # Fall back to basic search
        return search_fts(conn, query, account, mailbox, limit)


def count_matches(
    conn: sqlite3.Connection,
    query: str,
    account: str | None = None,
    mailbox: str | None = None,
) -> int:
    """
    Count total matches for a query without returning results.

    Useful for pagination or showing "X results found".

    Args:
        conn: Database connection
        query: Search query
        account: Optional account filter
        mailbox: Optional mailbox filter

    Returns:
        Total number of matching emails
    """
    if not query or not query.strip():
        return 0

    safe_query = sanitize_fts_query(query)
    if not safe_query:
        return 0

    sql = """
        SELECT COUNT(*)
        FROM emails_fts
        JOIN emails e ON emails_fts.rowid = e.rowid
        WHERE emails_fts MATCH ?
    """

    params: list = [safe_query]
    sql = add_account_mailbox_filter(sql, params, account, mailbox)

    try:
        cursor = conn.execute(sql, params)
        return cursor.fetchone()[0]
    except sqlite3.OperationalError:
        return 0
