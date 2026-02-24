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

# Characters that make a bare FTS5 token dangerous
# (hyphens = NOT, colons = column filter, parens = grouping, etc.)
_HAS_SPECIAL = re.compile(r"['\-\(\)\:\^]")

# FTS5 boolean operators that should be passed through
_FTS5_OPERATORS = {"OR", "AND", "NOT"}


def _tokenize_fts_query(query: str) -> list[str]:
    """Split query into phrase blocks and bare tokens.

    Balanced double-quoted segments are kept intact (including quotes).
    Unbalanced quotes are dropped.

    Returns:
        List of tokens — quoted phrases and individual bare words.
    """
    tokens: list[str] = []
    i = 0
    n = len(query)

    while i < n:
        # Skip whitespace
        if query[i].isspace():
            i += 1
            continue

        # Check for opening double quote
        if query[i] == '"':
            # Look for closing quote
            end = query.find('"', i + 1)
            if end != -1:
                # Balanced phrase — keep as-is
                tokens.append(query[i : end + 1])
                i = end + 1
            else:
                # Unbalanced quote — skip it
                i += 1
        else:
            # Bare token — collect until whitespace or quote
            start = i
            while i < n and not query[i].isspace() and query[i] != '"':
                i += 1
            tokens.append(query[start:i])

    return tokens


def _sanitize_bare_token(token: str) -> str:
    """Sanitize a single bare FTS5 token.

    FTS5 escaping works by wrapping in double quotes — backslash
    escaping is NOT supported. Tokens containing special characters
    are wrapped in ``"..."`` to prevent operator interpretation.

    Preserves:
    - Trailing ``*`` (prefix search)
    - Boolean operators (OR, AND, NOT)
    """
    # Preserve boolean operators
    if token in _FTS5_OPERATORS:
        return token

    # Check for trailing wildcard
    has_wildcard = token.endswith("*") and len(token) > 1
    core = token[:-1] if has_wildcard else token

    # If the core contains special FTS5 chars, wrap in double quotes
    if _HAS_SPECIAL.search(core):
        # Escape internal double quotes by doubling them
        safe_core = '"' + core.replace('"', '""') + '"'
        # Wildcard must go outside the quotes for FTS5
        return safe_core + "*" if has_wildcard else safe_core

    return token


def _escape_all_special(query: str) -> str:
    """Aggressively quote ALL special tokens as last-resort fallback.

    Used when the first search attempt raises a syntax error.
    Each term is individually quoted to preserve multi-term semantics
    (unlike wrapping in one big phrase, which changes OR → phrase).
    """
    words = query.split()
    escaped: list[str] = []
    for word in words:
        if word in _FTS5_OPERATORS:
            escaped.append(word)
        else:
            # Wrap in double quotes (FTS5's escaping mechanism)
            safe = '"' + word.replace('"', '""') + '"'
            escaped.append(safe)
    return " ".join(escaped)


def add_account_mailbox_filter(
    sql: str,
    params: list,
    account: str | None,
    mailbox: str | None,
    table_alias: str = "e",
    exclude_mailboxes: list[str] | None = None,
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
        exclude_mailboxes: Optional list of mailboxes to exclude

    Returns:
        Updated SQL string with added WHERE clauses
    """
    if account:
        sql += f" AND {table_alias}.account = ?"
        params.append(account)
    if mailbox:
        sql += f" AND {table_alias}.mailbox = ?"
        params.append(mailbox)
    if exclude_mailboxes:
        placeholders = ", ".join("?" for _ in exclude_mailboxes)
        sql += f" AND {table_alias}.mailbox NOT IN ({placeholders})"
        params.extend(exclude_mailboxes)
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
    """Sanitize a query string for safe FTS5 use.

    Preserves:
    - Balanced double-quoted phrases: ``"exact phrase"``
    - Trailing ``*`` for prefix search: ``meet*``
    - Boolean operators: ``OR``, ``AND``, ``NOT``

    Escapes:
    - Unbalanced quotes, colons, carets, parentheses, single quotes

    Args:
        query: Raw user query

    Returns:
        Sanitized query safe for FTS5
    """
    if not query or not query.strip():
        return ""

    query = query.strip()
    tokens = _tokenize_fts_query(query)

    sanitized_parts: list[str] = []
    for token in tokens:
        if token.startswith('"') and token.endswith('"'):
            # Already a balanced phrase — pass through
            sanitized_parts.append(token)
        else:
            sanitized_parts.append(_sanitize_bare_token(token))

    return " ".join(sanitized_parts)


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
    exclude_mailboxes: list[str] | None = None,
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
    sql = add_account_mailbox_filter(
        sql,
        params,
        account,
        mailbox,
        exclude_mailboxes=exclude_mailboxes,
    )
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(limit)

    try:
        cursor = conn.execute(sql, params)
        results = []

        for row in cursor:
            results.append(
                SearchResult(
                    id=row["message_id"],
                    account=row["account"],
                    mailbox=row["mailbox"],
                    subject=row["subject"] or "",
                    sender=row["sender"] or "",
                    content_snippet=_extract_snippet(row["content"]),
                    date_received=row["date_received"] or "",
                    score=round(row["score"], 3),
                )
            )

        return results

    except sqlite3.OperationalError as e:
        # FTS5 syntax error — retry with aggressive per-term escaping
        # (preserves multi-term semantics unlike wrapping in quotes)
        if "fts5: syntax error" in str(e).lower() and not _is_retry:
            escaped_query = _escape_all_special(query)
            return search_fts(
                conn,
                escaped_query,
                account=account,
                mailbox=mailbox,
                limit=limit,
                exclude_mailboxes=exclude_mailboxes,
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
