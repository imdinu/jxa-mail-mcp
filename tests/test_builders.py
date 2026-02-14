"""Tests for QueryBuilder classes.

Tests the JXA script builders:
- QueryBuilder for email queries
- AccountsQueryBuilder for account/mailbox listing

These builders generate JavaScript code that runs via osascript.
We test the generated script content, not execution.
"""

from __future__ import annotations

import pytest

from apple_mail_mcp.builders import (
    PROPERTY_SETS,
    AccountsQueryBuilder,
    QueryBuilder,
)


class TestQueryBuilderFromMailbox:
    """Tests for from_mailbox() method."""

    def test_sets_account_and_mailbox(self):
        """from_mailbox sets account and mailbox in script."""
        q = QueryBuilder().from_mailbox("Work", "INBOX")
        js = q.build()

        assert '"Work"' in js
        assert '"INBOX"' in js

    def test_none_account_uses_null(self):
        """from_mailbox with None account uses null in script."""
        q = QueryBuilder().from_mailbox(None, "INBOX")
        js = q.build()

        assert "null" in js
        assert '"INBOX"' in js

    def test_special_chars_are_escaped(self):
        """from_mailbox escapes special characters in names."""
        # Test with quotes and backslashes
        q = QueryBuilder().from_mailbox('Test "Account"', "Mail\\Box")
        js = q.build()

        # json.dumps should escape these
        assert '\\"' in js or "\\u0022" in js  # Escaped quote
        assert "\\\\" in js  # Escaped backslash


class TestQueryBuilderSelect:
    """Tests for select() method."""

    def test_standard_includes_required_fields(self):
        """select('standard') includes standard fields."""
        q = QueryBuilder().from_mailbox(None, "INBOX").select("standard")
        js = q.build()

        # Standard includes: id, subject, sender, date_received, read, flagged
        assert "sender" in js
        assert "subject" in js
        assert "dateReceived" in js
        assert "readStatus" in js
        assert "flaggedStatus" in js

    def test_minimal_includes_basic_fields(self):
        """select('minimal') includes minimal fields."""
        q = QueryBuilder().from_mailbox(None, "INBOX").select("minimal")
        js = q.build()

        # Minimal includes: id, subject, sender, date_received
        assert "sender" in js
        assert "subject" in js
        assert "dateReceived" in js
        # Should not include read/flagged
        assert "readStatus" not in js

    def test_full_includes_all_fields(self):
        """select('full') includes extended fields."""
        q = QueryBuilder().from_mailbox(None, "INBOX").select("full")
        js = q.build()

        # Full includes reply_to, message_id, etc.
        assert "replyTo" in js
        assert "messageId" in js

    def test_individual_properties(self):
        """select() accepts individual property names."""
        q = QueryBuilder().from_mailbox(None, "INBOX").select("id", "subject")
        js = q.build()

        assert "subject" in js
        # Should not include sender since we didn't select it
        # (but id is always first, used for length)

    def test_unknown_property_raises(self):
        """select() raises ValueError for unknown property."""
        with pytest.raises(ValueError, match="Unknown property"):
            QueryBuilder().select("unknown_field")

    def test_default_properties_when_none_selected(self):
        """build() uses standard properties when none selected."""
        q = QueryBuilder().from_mailbox(None, "INBOX")
        js = q.build()

        # Should default to standard properties
        assert "sender" in js
        assert "subject" in js


class TestQueryBuilderWhere:
    """Tests for where() filter method."""

    def test_adds_filter_condition(self):
        """where() adds filter condition to script."""
        q = (
            QueryBuilder()
            .from_mailbox(None, "INBOX")
            .where("data.readStatus[i] === false")
        )
        js = q.build()

        assert "readStatus[i] === false" in js
        assert "if (!(" in js  # Inverted condition for continue

    def test_multiple_where_replaces_previous(self):
        """Calling where() multiple times replaces the previous filter."""
        q = (
            QueryBuilder()
            .from_mailbox(None, "INBOX")
            .where("data.readStatus[i] === false")
            .where("data.flaggedStatus[i] === true")
        )
        js = q.build()

        # Only the last where should be present
        assert "flaggedStatus[i] === true" in js
        assert "readStatus[i] === false" not in js


class TestQueryBuilderOrderBy:
    """Tests for order_by() method."""

    def test_descending_order(self):
        """order_by with descending=True sorts newest first."""
        q = (
            QueryBuilder()
            .from_mailbox(None, "INBOX")
            .order_by("date_received", descending=True)
        )
        js = q.build()

        # Sort block should exist
        assert "results.sort" in js
        assert "date_received" in js

    def test_ascending_order(self):
        """order_by with descending=False sorts oldest first."""
        q = (
            QueryBuilder()
            .from_mailbox(None, "INBOX")
            .order_by("date_received", descending=False)
        )
        js = q.build()

        assert "results.sort" in js

    def test_unknown_order_property_raises(self):
        """order_by raises ValueError for unknown property."""
        with pytest.raises(ValueError, match="Unknown property for ordering"):
            QueryBuilder().order_by("unknown_field")


class TestQueryBuilderLimit:
    """Tests for limit() method."""

    def test_limit_caps_results(self):
        """limit() adds result count check to loop."""
        q = QueryBuilder().from_mailbox(None, "INBOX").limit(10)
        js = q.build()

        assert "results.length < 10" in js

    def test_no_limit_iterates_all(self):
        """Without limit, loop iterates all messages."""
        q = QueryBuilder().from_mailbox(None, "INBOX")
        js = q.build()

        # Should have simple loop without length check
        assert "i < len;" in js
        assert "results.length <" not in js


class TestQueryBuilderBuild:
    """Tests for build() script generation."""

    def test_generates_valid_structure(self):
        """build() generates script with expected structure."""
        q = (
            QueryBuilder()
            .from_mailbox("Work", "INBOX")
            .select("standard")
            .limit(50)
        )
        js = q.build()

        # Should have all major sections
        assert "MailCore.getAccount" in js
        assert "MailCore.getMailbox" in js
        assert "MailCore.batchFetch" in js
        assert "results = []" in js
        assert "JSON.stringify(results)" in js

    def test_date_properties_use_format_date(self):
        """Date properties are formatted using MailCore.formatDate."""
        q = QueryBuilder().from_mailbox(None, "INBOX").select("date_received")
        js = q.build()

        assert "MailCore.formatDate(data.dateReceived[i])" in js

    def test_complete_query_example(self):
        """Test a complete realistic query."""
        q = (
            QueryBuilder()
            .from_mailbox("Work", "INBOX")
            .select("standard")
            .where("data.dateReceived[i] >= MailCore.today()")
            .order_by("date_received", descending=True)
            .limit(50)
        )
        js = q.build()

        # All components should be present
        assert '"Work"' in js
        assert '"INBOX"' in js
        assert "MailCore.today()" in js
        assert "results.sort" in js
        assert "results.length < 50" in js


class TestAccountsQueryBuilder:
    """Tests for AccountsQueryBuilder."""

    def test_list_accounts_returns_valid_js(self):
        """list_accounts generates valid script."""
        q = AccountsQueryBuilder()
        js = q.list_accounts()

        assert "MailCore.listAccounts()" in js
        assert "JSON.stringify" in js

    def test_list_mailboxes_with_account(self):
        """list_mailboxes generates script with account name."""
        q = AccountsQueryBuilder()
        js = q.list_mailboxes("Work")

        assert '"Work"' in js
        assert "MailCore.getAccount" in js
        assert "MailCore.listMailboxes" in js

    def test_list_mailboxes_with_null_account(self):
        """list_mailboxes handles None account."""
        q = AccountsQueryBuilder()
        js = q.list_mailboxes(None)

        assert "null" in js
        assert "MailCore.listMailboxes" in js

    def test_list_mailboxes_escapes_special_chars(self):
        """list_mailboxes escapes special characters in account name."""
        q = AccountsQueryBuilder()
        js = q.list_mailboxes('Account "Special"')

        # json.dumps should handle the escaping
        assert "Account" in js
        assert "Special" in js


class TestPropertySets:
    """Tests for PROPERTY_SETS constants."""

    def test_minimal_set_exists(self):
        """PROPERTY_SETS has 'minimal' preset."""
        assert "minimal" in PROPERTY_SETS
        assert "id" in PROPERTY_SETS["minimal"]
        assert "subject" in PROPERTY_SETS["minimal"]

    def test_standard_set_exists(self):
        """PROPERTY_SETS has 'standard' preset."""
        assert "standard" in PROPERTY_SETS
        assert "read" in PROPERTY_SETS["standard"]
        assert "flagged" in PROPERTY_SETS["standard"]

    def test_full_set_exists(self):
        """PROPERTY_SETS has 'full' preset."""
        assert "full" in PROPERTY_SETS
        assert "reply_to" in PROPERTY_SETS["full"]
        assert "message_id" in PROPERTY_SETS["full"]
