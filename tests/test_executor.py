"""Tests for executor.py - JXA script execution utilities."""

from unittest.mock import patch

import pytest

from apple_mail_mcp.executor import (
    JXAError,
    build_account_js,
    build_mailbox_setup_js,
    execute_with_core,
)


class TestBuildAccountJs:
    """Tests for build_account_js() helper."""

    def test_with_account_name(self):
        """Account name is JSON-serialized safely."""
        result = build_account_js("Work")
        assert result == 'MailCore.getAccount("Work")'

    def test_with_none(self):
        """None becomes null in JavaScript."""
        result = build_account_js(None)
        assert result == "MailCore.getAccount(null)"

    def test_escapes_special_characters(self):
        """Special characters are escaped to prevent injection."""
        result = build_account_js('Test "Account"')
        assert result == 'MailCore.getAccount("Test \\"Account\\"")'


class TestBuildMailboxSetupJs:
    """Tests for build_mailbox_setup_js() helper."""

    def test_basic_setup(self):
        """Generates account and mailbox setup code."""
        result = build_mailbox_setup_js("Work", "INBOX")
        assert 'MailCore.getAccount("Work")' in result
        assert 'MailCore.getMailbox(account, "INBOX")' in result

    def test_with_none_account(self):
        """None account becomes null."""
        result = build_mailbox_setup_js(None, "INBOX")
        assert "MailCore.getAccount(null)" in result

    def test_custom_variable_names(self):
        """Custom variable names are used."""
        result = build_mailbox_setup_js(
            "Work", "INBOX", account_var="acc", mailbox_var="mb"
        )
        assert "const acc = " in result
        assert "const mb = " in result


class TestExecuteWithCoreJsonParsing:
    """Tests for JSON parsing error handling in execute_with_core()."""

    @patch("apple_mail_mcp.executor.run_jxa")
    def test_valid_json_parses_correctly(self, mock_run_jxa):
        """Valid JSON output is parsed correctly."""
        mock_run_jxa.return_value = '{"key": "value"}'
        result = execute_with_core("script")
        assert result == {"key": "value"}

    @patch("apple_mail_mcp.executor.run_jxa")
    def test_invalid_json_raises_jxa_error(self, mock_run_jxa):
        """Invalid JSON raises JXAError with helpful message."""
        mock_run_jxa.return_value = "not valid json"

        with pytest.raises(JXAError) as exc_info:
            execute_with_core("script")

        error = exc_info.value
        assert "Failed to parse JXA output as JSON" in str(error)
        assert "not valid json" in error.stderr

    @patch("apple_mail_mcp.executor.run_jxa")
    def test_json_with_prefix_debug_output_fails(self, mock_run_jxa):
        """Debug output before JSON causes clear error."""
        mock_run_jxa.return_value = 'Debug: starting\n{"data": 123}'

        with pytest.raises(JXAError) as exc_info:
            execute_with_core("script")

        assert "Debug: starting" in exc_info.value.stderr

    @patch("apple_mail_mcp.executor.run_jxa")
    def test_long_output_is_truncated_in_error(self, mock_run_jxa):
        """Long invalid output is truncated in error message."""
        long_output = "x" * 1000
        mock_run_jxa.return_value = long_output

        with pytest.raises(JXAError) as exc_info:
            execute_with_core("script")

        error_msg = str(exc_info.value)
        # Error message should be truncated
        assert "..." in error_msg
        # But full output is in stderr
        assert exc_info.value.stderr == long_output

    @patch("apple_mail_mcp.executor.run_jxa")
    def test_empty_output_raises_clear_error(self, mock_run_jxa):
        """Empty output raises clear error."""
        mock_run_jxa.return_value = ""

        with pytest.raises(JXAError) as exc_info:
            execute_with_core("script")

        assert "Failed to parse JXA output" in str(exc_info.value)
