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

    @pytest.mark.parametrize(
        "preset, expected, unexpected",
        [
            ("minimal", ["sender", "subject", "dateReceived"], ["readStatus"]),
            (
                "standard",
                ["sender", "dateReceived", "readStatus", "flaggedStatus"],
                [],
            ),
            ("full", ["replyTo", "messageId", "sender"], []),
        ],
    )
    def test_preset_includes_expected_fields(
        self, preset, expected, unexpected
    ):
        """select() presets include the correct JXA property names."""
        js = QueryBuilder().from_mailbox(None, "INBOX").select(preset).build()
        for field in expected:
            assert field in js
        for field in unexpected:
            assert field not in js

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

    @pytest.mark.parametrize("descending", [True, False])
    def test_order_by_generates_sort_block(self, descending):
        """order_by generates a sort block regardless of direction."""
        q = (
            QueryBuilder()
            .from_mailbox(None, "INBOX")
            .order_by("date_received", descending=descending)
        )
        js = q.build()

        assert "results.sort" in js
        assert "date_received" in js

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

    @pytest.mark.parametrize(
        "account, expected_fragment",
        [
            ("Work", '"Work"'),
            (None, "null"),
            ('Account "Special"', "Account"),
        ],
    )
    def test_list_mailboxes_generates_script(self, account, expected_fragment):
        """list_mailboxes generates correct script for various inputs."""
        js = AccountsQueryBuilder().list_mailboxes(account)

        assert expected_fragment in js
        assert "MailCore.listMailboxes" in js


class TestPropertySets:
    """Tests for PROPERTY_SETS constants."""

    @pytest.mark.parametrize(
        "preset, required_keys",
        [
            ("minimal", ["id", "subject"]),
            ("standard", ["read", "flagged"]),
            ("full", ["reply_to", "message_id"]),
        ],
    )
    def test_property_set_contains_expected_keys(self, preset, required_keys):
        """Each PROPERTY_SETS preset contains its required keys."""
        assert preset in PROPERTY_SETS
        for key in required_keys:
            assert key in PROPERTY_SETS[preset]
