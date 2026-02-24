"""Tests for MCP server tools.

Tests the 5 MCP tools exposed by server.py:
- list_accounts
- list_mailboxes
- get_emails
- get_email
- search

Uses mocking to avoid actual JXA execution (which requires macOS + Mail.app).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestListAccounts:
    """Tests for list_accounts() tool."""

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_with_core_async")
    async def test_returns_account_list(self, mock_exec):
        """list_accounts returns list of account dicts."""
        mock_exec.return_value = [
            {"name": "Work", "id": "abc123"},
            {"name": "Personal", "id": "def456"},
        ]

        from apple_mail_mcp.server import list_accounts

        result = await list_accounts()

        assert len(result) == 2
        assert result[0]["name"] == "Work"
        assert result[1]["name"] == "Personal"
        mock_exec.assert_called_once()

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_with_core_async")
    async def test_returns_empty_list_when_no_accounts(self, mock_exec):
        """list_accounts handles empty account list."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import list_accounts

        result = await list_accounts()

        assert result == []


class TestListMailboxes:
    """Tests for list_mailboxes() tool."""

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_with_core_async")
    async def test_returns_mailbox_list(self, mock_exec):
        """list_mailboxes returns list of mailbox dicts."""
        mock_exec.return_value = [
            {"name": "INBOX", "unreadCount": 5},
            {"name": "Sent", "unreadCount": 0},
        ]

        from apple_mail_mcp.server import list_mailboxes

        result = await list_mailboxes("Work")

        assert len(result) == 2
        assert result[0]["name"] == "INBOX"
        assert result[0]["unreadCount"] == 5

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_with_core_async")
    async def test_uses_default_account_when_none(self, mock_exec):
        """list_mailboxes uses default account when not specified."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import list_mailboxes

        await list_mailboxes(None)

        # Should still call execute - the script handles None account
        mock_exec.assert_called_once()


class TestGetEmails:
    """Tests for get_emails() tool."""

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_filter_all_returns_emails(self, mock_exec):
        """get_emails with filter='all' returns all emails."""
        mock_exec.return_value = [
            {
                "id": 1,
                "subject": "Test",
                "sender": "test@example.com",
                "date_received": "2024-01-15T10:00:00",
                "read": True,
                "flagged": False,
            }
        ]

        from apple_mail_mcp.server import get_emails

        result = await get_emails(filter="all")

        assert len(result) == 1
        assert result[0]["subject"] == "Test"

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_filter_unread_adds_read_status_condition(self, mock_exec):
        """get_emails with filter='unread' adds appropriate filter."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import get_emails

        await get_emails(filter="unread")

        # Verify the query was built with the unread filter
        call_args = mock_exec.call_args[0][0]  # First positional arg (query)
        script = call_args.build()
        assert "readStatus[i] === false" in script

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_filter_flagged_adds_flagged_condition(self, mock_exec):
        """get_emails with filter='flagged' adds flagged filter."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import get_emails

        await get_emails(filter="flagged")

        call_args = mock_exec.call_args[0][0]
        script = call_args.build()
        assert "flaggedStatus[i] === true" in script

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_filter_today_uses_mailcore_today(self, mock_exec):
        """get_emails with filter='today' uses MailCore.today()."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import get_emails

        await get_emails(filter="today")

        call_args = mock_exec.call_args[0][0]
        script = call_args.build()
        assert "MailCore.today()" in script

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_filter_this_week_uses_days_ago(self, mock_exec):
        """get_emails with filter='this_week' uses MailCore.daysAgo(7)."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import get_emails

        await get_emails(filter="this_week")

        call_args = mock_exec.call_args[0][0]
        script = call_args.build()
        assert "MailCore.daysAgo(7)" in script

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_respects_limit_parameter(self, mock_exec):
        """get_emails respects the limit parameter."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import get_emails

        await get_emails(limit=10)

        call_args = mock_exec.call_args[0][0]
        script = call_args.build()
        # The limit appears in the loop condition
        assert "results.length < 10" in script

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_uses_specified_account_and_mailbox(self, mock_exec):
        """get_emails uses specified account and mailbox."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import get_emails

        await get_emails(account="Work", mailbox="INBOX")

        call_args = mock_exec.call_args[0][0]
        script = call_args.build()
        assert '"Work"' in script
        assert '"INBOX"' in script


class TestGetEmail:
    """Tests for get_email() tool."""

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_with_core_async")
    async def test_returns_full_email(self, mock_exec):
        """get_email returns complete email with content."""
        mock_exec.return_value = {
            "id": 12345,
            "subject": "Meeting notes",
            "sender": "boss@company.com",
            "content": "Here are the notes from today's meeting...",
            "date_received": "2024-01-15T10:00:00",
            "date_sent": "2024-01-15T09:58:00",
            "read": True,
            "flagged": False,
            "reply_to": "boss@company.com",
            "message_id": "<abc123@mail.example.com>",
        }

        from apple_mail_mcp.server import get_email

        result = await get_email(12345)

        assert result["id"] == 12345
        assert result["subject"] == "Meeting notes"
        assert "notes from today" in result["content"]

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_with_core_async")
    async def test_includes_message_id_in_script(self, mock_exec):
        """get_email includes message_id in the JXA script."""
        mock_exec.return_value = {"id": 99999}

        from apple_mail_mcp.server import get_email

        await get_email(99999, account="Work", mailbox="INBOX")

        call_args = mock_exec.call_args[0][0]  # First positional arg
        assert "99999" in call_args
        assert "targetId" in call_args

    @pytest.mark.asyncio
    async def test_get_email_uses_index_for_fallback(self):
        """B1: Strategy 2 uses index lookup when strategy 1 fails."""
        call_count = 0

        async def mock_exec_side_effect(script):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Not found in specified mailbox")
            if call_count == 2:
                # Strategy 2 succeeds
                return {
                    "id": 42,
                    "subject": "Found via index",
                    "sender": "a@b.com",
                    "content": "Body",
                    "date_received": "2024-01-01",
                    "date_sent": "2024-01-01",
                    "read": True,
                    "flagged": False,
                    "reply_to": "",
                    "message_id": "<x>",
                }
            return {}

        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_conn = MagicMock()
        mock_row = {"account": "uuid-123", "mailbox": "Archive"}
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row
        mock_conn.execute.return_value = mock_cursor
        mock_manager._get_conn.return_value = mock_conn

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.uuid_to_name.return_value = "Work"

        with (
            patch(
                "apple_mail_mcp.server.execute_with_core_async",
                side_effect=mock_exec_side_effect,
            ),
            patch("apple_mail_mcp.server._get_index_manager") as mock_get_mgr,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
        ):
            mock_get_mgr.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import get_email

            result = await get_email(42)

            assert result["subject"] == "Found via index"
            assert call_count == 2  # Strategy 1 failed, 2 succeeded


class TestSearch:
    """Tests for search() tool."""

    @pytest.mark.asyncio
    async def test_uses_fts_when_index_available(self, populated_db):
        """search uses FTS5 path when index exists."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True

        mock_result = MagicMock()
        mock_result.id = 1001
        mock_result.subject = "Invoice #12345"
        mock_result.sender = "billing@vendor.com"
        mock_result.date_received = "2024-01-14T09:00:00"
        mock_result.score = 2.5
        mock_result.content_snippet = "Your invoice..."
        mock_result.account = "test-account"
        mock_result.mailbox = "INBOX"
        mock_manager.search.return_value = [mock_result]

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.name_to_uuid.return_value = None
        mock_acct_map.uuid_to_name.side_effect = lambda x: x

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
        ):
            mock_get.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            result = await search("invoice")

            assert len(result) == 1
            assert result[0]["subject"] == "Invoice #12345"
            # S1: matched_in is now detected dynamically
            assert "body" in result[0]["matched_in"]
            mock_manager.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_fts_translates_account_name_to_uuid(self):
        """search(account="Work") translates to UUID for FTS5."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.search.return_value = []

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.name_to_uuid.return_value = "UUID-WORK-123"

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
        ):
            mock_get.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            await search("invoice", account="Work")

            # Verify manager.search received the UUID, not "Work"
            call_kwargs = mock_manager.search.call_args[1]
            assert call_kwargs["account"] == "UUID-WORK-123"

    @pytest.mark.asyncio
    async def test_fts_results_show_friendly_account_name(self):
        """FTS5 results translate UUID back to friendly name."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True

        mock_result = MagicMock()
        mock_result.id = 1
        mock_result.subject = "Test"
        mock_result.sender = "a@b.com"
        mock_result.date_received = "2024-01-01"
        mock_result.score = 1.0
        mock_result.content_snippet = "..."
        mock_result.account = "UUID-WORK-123"
        mock_result.mailbox = "INBOX"
        mock_manager.search.return_value = [mock_result]

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.name_to_uuid.return_value = None
        mock_acct_map.uuid_to_name.return_value = "Work"

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
        ):
            mock_get.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            result = await search("test")

            # Result should show "Work", not "UUID-WORK-123"
            assert result[0]["account"] == "Work"

    @pytest.mark.asyncio
    async def test_fts_account_filter_falls_back_to_raw_value(
        self,
    ):
        """If name isn't in AccountMap, pass it through as-is."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.search.return_value = []

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.name_to_uuid.return_value = None  # Not found

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
        ):
            mock_get.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            await search("test", account="RAW-UUID-ABC")

            # Should pass through the raw value as fallback
            call_kwargs = mock_manager.search.call_args[1]
            assert call_kwargs["account"] == "RAW-UUID-ABC"

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_falls_back_to_jxa_when_no_index(self, mock_exec):
        """search falls back to JXA when no FTS5 index exists."""
        mock_exec.return_value = [
            {
                "id": 1,
                "subject": "Test Invoice",
                "sender": "test@example.com",
                "date_received": "2024-01-15T10:00:00",
                "read": True,
                "flagged": False,
            }
        ]

        mock_manager = MagicMock()
        mock_manager.has_index.return_value = False

        with patch("apple_mail_mcp.server._get_index_manager") as mock_get:
            mock_get.return_value = mock_manager

            from apple_mail_mcp.server import search

            result = await search("invoice")

            # Should use JXA path
            mock_exec.assert_called_once()
            assert len(result) == 1

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_scope_subject_uses_jxa(self, mock_exec):
        """search with scope='subject' uses JXA path."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import search

        await search("urgent", scope="subject")

        call_args = mock_exec.call_args[0][0]
        script = call_args.build()
        # Subject-only search in JXA
        assert "subject[i]" in script.lower()
        assert "toLowerCase().includes" in script

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_scope_sender_uses_jxa(self, mock_exec):
        """search with scope='sender' uses JXA path."""
        mock_exec.return_value = []

        from apple_mail_mcp.server import search

        await search("john@example.com", scope="sender")

        call_args = mock_exec.call_args[0][0]
        script = call_args.build()
        assert "sender[i]" in script.lower()

    @pytest.mark.asyncio
    async def test_scope_body_uses_fts(self):
        """search with scope='body' uses FTS5 path when available."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.search.return_value = []

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.name_to_uuid.return_value = None

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
        ):
            mock_get.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            await search("meeting notes", scope="body")

            mock_manager.search.assert_called_once()

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.server.execute_query_async")
    async def test_respects_limit(self, mock_exec):
        """search respects limit parameter."""
        mock_exec.return_value = []

        mock_manager = MagicMock()
        mock_manager.has_index.return_value = False

        with patch("apple_mail_mcp.server._get_index_manager") as mock_get:
            mock_get.return_value = mock_manager

            from apple_mail_mcp.server import search

            await search("test", limit=5)

            call_args = mock_exec.call_args[0][0]
            script = call_args.build()
            assert "results.length < 5" in script


class TestHelperFunctions:
    """Tests for helper functions in server.py."""

    def test_resolve_account_returns_provided_account(self):
        """_resolve_account returns provided account when given."""
        from apple_mail_mcp.server import _resolve_account

        result = _resolve_account("Work")
        assert result == "Work"

    def test_resolve_account_returns_none_when_no_default(self):
        """_resolve_account returns None when no default is set."""
        from apple_mail_mcp.server import _resolve_account

        with patch("apple_mail_mcp.server.get_default_account") as mock:
            mock.return_value = None
            result = _resolve_account(None)
            assert result is None

    def test_resolve_mailbox_returns_provided_mailbox(self):
        """_resolve_mailbox returns provided mailbox when given."""
        from apple_mail_mcp.server import _resolve_mailbox

        result = _resolve_mailbox("INBOX")
        assert result == "INBOX"

    def test_resolve_mailbox_returns_default_when_none(self):
        """_resolve_mailbox returns default when None provided."""
        from apple_mail_mcp.server import _resolve_mailbox

        with patch("apple_mail_mcp.server.get_default_mailbox") as mock:
            mock.return_value = "Inbox"
            result = _resolve_mailbox(None)
            assert result == "Inbox"


class TestDetectMatchedColumns:
    """Tests for S1: accurate matched_in detection."""

    def test_detects_subject_match(self):
        from apple_mail_mcp.server import _detect_matched_columns

        result = MagicMock()
        result.subject = "Meeting tomorrow"
        result.sender = "boss@company.com"
        result.content_snippet = "Please review..."

        matched = _detect_matched_columns("meeting", result)
        assert "subject" in matched
        assert "body" in matched

    def test_detects_sender_match(self):
        from apple_mail_mcp.server import _detect_matched_columns

        result = MagicMock()
        result.subject = "Hello"
        result.sender = "john@example.com"
        result.content_snippet = "Hi there"

        matched = _detect_matched_columns("john", result)
        assert "sender" in matched

    def test_body_always_included(self):
        from apple_mail_mcp.server import _detect_matched_columns

        result = MagicMock()
        result.subject = "Other topic"
        result.sender = "other@test.com"
        result.content_snippet = "Some content"

        matched = _detect_matched_columns("xyzunknown", result)
        assert "body" in matched


class TestSearchFtsAccountFiltering:
    """Tests for S5: FTS5 None account means all."""

    @pytest.mark.asyncio
    async def test_search_fts_none_account_means_all(self):
        """When account=None, FTS5 path should NOT resolve a default."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.is_stale.return_value = False
        mock_manager.search.return_value = []

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.name_to_uuid.return_value = None

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
        ):
            mock_get.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            await search("test", account=None)

            # account should be None → search all
            call_kwargs = mock_manager.search.call_args[1]
            assert call_kwargs["account"] is None


class TestSearchAutoSync:
    """Tests for S2: auto-sync stale index."""

    @pytest.mark.asyncio
    async def test_search_auto_syncs_when_stale(self):
        """Search triggers sync when index is stale."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.is_stale.return_value = True
        mock_manager.search.return_value = []
        mock_manager.sync_updates.return_value = 5

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.name_to_uuid.return_value = None

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
            patch(
                "apple_mail_mcp.server.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_thread,
        ):
            mock_get.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            await search("test")

            mock_thread.assert_called_once_with(mock_manager.sync_updates)


class TestSearchExcludeMailboxes:
    """Tests for S3: draft exclusion in search."""

    @pytest.mark.asyncio
    async def test_search_excludes_drafts_by_default(self):
        """Search passes exclude_mailboxes=["Drafts"] by default."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_manager.is_stale.return_value = False
        mock_manager.search.return_value = []

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.name_to_uuid.return_value = None

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
        ):
            mock_get.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            await search("test")

            call_kwargs = mock_manager.search.call_args[1]
            assert call_kwargs["exclude_mailboxes"] == ["Drafts"]


class TestGetAttachment:
    """Tests for A4: get_attachment tool."""

    @pytest.mark.asyncio
    async def test_get_attachment_returns_base64(self):
        """get_attachment returns base64-encoded content."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_row = {"emlx_path": "/fake/path/42.emlx"}
        mock_cursor.fetchone.return_value = mock_row
        mock_conn.execute.return_value = mock_cursor
        mock_manager._get_conn.return_value = mock_conn

        fake_bytes = b"fake pdf content"
        fake_result = (fake_bytes, "application/pdf")

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch(
                "apple_mail_mcp.server.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_thread,
        ):
            mock_get.return_value = mock_manager
            mock_thread.return_value = fake_result

            from apple_mail_mcp.server import get_attachment

            result = await get_attachment(42, "invoice.pdf")

            assert result["filename"] == "invoice.pdf"
            assert result["mime_type"] == "application/pdf"
            assert result["size"] == len(fake_bytes)
            assert "content_base64" in result

    @pytest.mark.asyncio
    async def test_get_attachment_raises_for_missing(self):
        """get_attachment raises ValueError for missing attachment."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_row = {"emlx_path": "/fake/path/42.emlx"}
        mock_cursor.fetchone.return_value = mock_row
        mock_conn.execute.return_value = mock_cursor
        mock_manager._get_conn.return_value = mock_conn

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch(
                "apple_mail_mcp.server.asyncio.to_thread",
                new_callable=AsyncMock,
            ) as mock_thread,
        ):
            mock_get.return_value = mock_manager
            mock_thread.return_value = None

            from apple_mail_mcp.server import get_attachment

            with pytest.raises(ValueError, match="not found"):
                await get_attachment(42, "missing.pdf")


class TestSearchAttachments:
    """Tests for A5: search by attachment filename."""

    @pytest.mark.asyncio
    async def test_search_scope_attachments(self):
        """search(scope='attachments') queries attachments table."""
        mock_manager = MagicMock()
        mock_manager.has_index.return_value = True
        mock_conn = MagicMock()
        mock_manager._get_conn.return_value = mock_conn

        # Mock cursor with one result row
        mock_row = {
            "message_id": 1,
            "account": "UUID-123",
            "mailbox": "INBOX",
            "subject": "Invoice attached",
            "sender": "billing@co.com",
            "date_received": "2024-01-15",
            "filename": "invoice.pdf",
        }
        mock_conn.execute.return_value = [mock_row]

        mock_acct_map = MagicMock()
        mock_acct_map.ensure_loaded = AsyncMock()
        mock_acct_map.uuid_to_name.return_value = "Work"

        with (
            patch("apple_mail_mcp.server._get_index_manager") as mock_get,
            patch("apple_mail_mcp.server._get_account_map") as mock_get_map,
        ):
            mock_get.return_value = mock_manager
            mock_get_map.return_value = mock_acct_map

            from apple_mail_mcp.server import search

            results = await search("invoice", scope="attachments")

            assert len(results) == 1
            assert results[0]["matched_in"] == "attachment: invoice.pdf"
            assert results[0]["account"] == "Work"
