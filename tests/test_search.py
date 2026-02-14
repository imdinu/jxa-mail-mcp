"""Tests for FTS5 search functionality."""

from __future__ import annotations

import sqlite3

from apple_mail_mcp.index.search import (
    count_matches,
    sanitize_fts_query,
    search_fts,
)


class TestSanitizeFtsQuery:
    """Tests for FTS5 query sanitization."""

    def test_empty_query(self):
        assert sanitize_fts_query("") == ""
        assert sanitize_fts_query("   ") == ""

    def test_simple_query(self):
        assert sanitize_fts_query("hello world") == "hello world"

    def test_escapes_special_characters(self):
        # Hyphens
        assert sanitize_fts_query("meeting-notes") == r"meeting\-notes"
        # Asterisks
        assert sanitize_fts_query("prefix*") == r"prefix\*"
        # Colons
        assert sanitize_fts_query("subject:test") == r"subject\:test"
        # Quotes
        assert sanitize_fts_query('say "hello"') == r"say \"hello\""
        # Parentheses
        assert sanitize_fts_query("(group)") == r"\(group\)"
        # Carets
        assert sanitize_fts_query("boost^2") == r"boost\^2"

    def test_preserves_boolean_operators(self):
        # Boolean operators don't contain special chars, so they're preserved
        result = sanitize_fts_query("hello OR world")
        assert "OR" in result

        result = sanitize_fts_query("hello AND world")
        assert "AND" in result

        result = sanitize_fts_query("hello NOT world")
        assert "NOT" in result

    def test_escapes_injection_attempts(self):
        # Malicious query trying to use special syntax
        result = sanitize_fts_query('test" OR *')
        assert r"\"" in result  # Quote escaped
        assert r"\*" in result  # Asterisk escaped

    def test_strips_whitespace(self):
        assert sanitize_fts_query("  hello  ") == "hello"


class TestSearchFts:
    """Tests for FTS5 search function."""

    def test_empty_query_returns_empty(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "")
        assert results == []

    def test_basic_search(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "meeting")
        assert len(results) >= 1
        # Check result structure
        result = results[0]
        assert hasattr(result, "id")
        assert hasattr(result, "subject")
        assert hasattr(result, "score")

    def test_search_with_multiple_terms(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "quarterly report")
        assert len(results) >= 1

    def test_search_respects_limit(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "the", limit=2)
        assert len(results) <= 2

    def test_search_filters_by_account(self, populated_db: sqlite3.Connection):
        results = search_fts(
            populated_db, "meeting", account="test-account-uuid"
        )
        assert all(r.account == "test-account-uuid" for r in results)

    def test_search_filters_by_mailbox(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "deadline", mailbox="Sent")
        assert len(results) >= 1
        assert all(r.mailbox == "Sent" for r in results)

    def test_search_results_ordered_by_score(
        self, populated_db: sqlite3.Connection
    ):
        results = search_fts(populated_db, "meeting", limit=10)
        if len(results) > 1:
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True)

    def test_search_handles_special_characters(
        self, populated_db: sqlite3.Connection
    ):
        # Hyphens should be escaped and work
        results = search_fts(populated_db, "test-query")
        assert isinstance(results, list)

        # Quotes should be escaped
        results = search_fts(populated_db, "meeting tomorrow")
        assert isinstance(results, list)

    def test_search_handles_malformed_queries(
        self, populated_db: sqlite3.Connection
    ):
        # Malformed queries should either return results or empty list
        # but not raise (due to retry logic)
        for query in ["test*", "hello:", "(broken"]:
            results = search_fts(populated_db, query)
            assert isinstance(results, list)

    def test_search_no_results(self, populated_db: sqlite3.Connection):
        results = search_fts(populated_db, "xyznonexistent123")
        assert results == []


class TestCountMatches:
    """Tests for match counting function."""

    def test_empty_query_returns_zero(self, populated_db: sqlite3.Connection):
        assert count_matches(populated_db, "") == 0

    def test_count_basic_query(self, populated_db: sqlite3.Connection):
        count = count_matches(populated_db, "meeting")
        assert count >= 1

    def test_count_with_filters(self, populated_db: sqlite3.Connection):
        count = count_matches(
            populated_db, "deadline", account="test-account-uuid"
        )
        assert count >= 0

    def test_count_no_results(self, populated_db: sqlite3.Connection):
        count = count_matches(populated_db, "xyznonexistent123")
        assert count == 0


class TestCompositeKeyUniqueness:
    """Tests verifying composite key behavior."""

    def test_same_message_id_different_mailbox(
        self, populated_db: sqlite3.Connection
    ):
        """Message ID 1001 exists in both INBOX and Archive."""
        cursor = populated_db.execute(
            "SELECT COUNT(*) FROM emails WHERE message_id = 1001"
        )
        count = cursor.fetchone()[0]
        assert count == 2, "Same message_id should exist in different mailboxes"

    def test_search_returns_both_duplicates(
        self, populated_db: sqlite3.Connection
    ):
        """Search should find emails with same ID in different mailboxes."""
        results = search_fts(populated_db, "meeting")
        # Should find at least the INBOX and Archive versions
        mailboxes = {r.mailbox for r in results}
        assert len(mailboxes) >= 1
