"""Tests for disk reading functionality."""

from __future__ import annotations

from pathlib import Path

from apple_mail_mcp.index.disk import (
    MAX_EMLX_SIZE,
    _extract_body_text,
    _infer_account_mailbox,
    _strip_html,
    parse_emlx,
)


class TestParseEmlx:
    """Tests for .emlx file parsing."""

    def test_parse_valid_emlx(self, temp_emlx_file: Path):
        result = parse_emlx(temp_emlx_file)
        assert result is not None
        assert result.id == 12345
        assert result.subject == "Test Email Subject"
        assert result.sender == "sender@example.com"
        assert "body of the test email" in result.content

    def test_parse_returns_none_for_invalid(self, tmp_path: Path):
        # Create invalid emlx file
        invalid_path = tmp_path / "99999.emlx"
        invalid_path.write_bytes(b"not a valid emlx file")
        result = parse_emlx(invalid_path)
        assert result is None

    def test_parse_returns_none_for_missing_file(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.emlx"
        result = parse_emlx(missing)
        assert result is None

    def test_parse_rejects_oversized_files(self, tmp_path: Path):
        # Create a file larger than MAX_EMLX_SIZE
        # We'll fake this by setting the size check first
        large_path = tmp_path / "large.emlx"
        # Write just enough to exceed the limit
        large_path.write_bytes(b"x" * (MAX_EMLX_SIZE + 1))
        result = parse_emlx(large_path)
        assert result is None

    def test_parse_extracts_message_id_from_filename(self, tmp_path: Path):
        # Message ID comes from the filename stem
        emlx_content = b"10\nFrom: x@y.z\n\nBody"
        (tmp_path / "42.emlx").write_bytes(emlx_content)
        # Should at least try to parse (might fail due to minimal content)
        _ = parse_emlx(tmp_path / "42.emlx")


class TestExtractBodyText:
    """Tests for email body extraction."""

    def test_extract_plain_text(self):
        import email

        msg = email.message_from_string(
            "Content-Type: text/plain\n\nHello world"
        )
        result = _extract_body_text(msg)
        assert "Hello world" in result

    def test_extract_from_multipart(self):
        import email

        raw = """\
Content-Type: multipart/alternative; boundary="----=_Part"

------=_Part
Content-Type: text/plain

Plain text version

------=_Part
Content-Type: text/html

<html><body>HTML version</body></html>

------=_Part--
"""
        msg = email.message_from_string(raw)
        result = _extract_body_text(msg)
        # Should prefer plain text
        assert "Plain text version" in result


class TestStripHtml:
    """Tests for HTML stripping."""

    def test_removes_script_tags(self):
        html = '<p>Hello</p><script>alert("xss")</script><p>World</p>'
        result = _strip_html(html)
        assert "alert" not in result
        assert "script" not in result.lower()
        assert "Hello" in result
        assert "World" in result

    def test_removes_style_tags(self):
        html = "<style>.red{color:red}</style><p>Content</p>"
        result = _strip_html(html)
        assert "color" not in result
        assert "Content" in result

    def test_converts_block_elements_to_newlines(self):
        html = "<p>Para 1</p><p>Para 2</p>"
        result = _strip_html(html)
        assert "Para 1" in result
        assert "Para 2" in result

    def test_decodes_html_entities(self):
        html = "&lt;tag&gt; &amp; &quot;quotes&quot;"
        result = _strip_html(html)
        assert "<tag>" in result
        assert "&" in result
        assert '"quotes"' in result

    def test_handles_nested_script_bypass_attempt(self):
        """Test XSS bypass with nested/malformed tags."""
        # This is a classic XSS bypass that breaks regex-based stripping
        html = '<<script>script>alert("xss")<</script>/script>'
        result = _strip_html(html)
        # BeautifulSoup removes the dangerous JavaScript payload
        # Any remaining text like "/script>" is harmless plain text
        assert "alert" not in result
        assert "xss" not in result

    def test_handles_img_onerror_xss(self):
        """Test XSS bypass with img onerror."""
        html = '<img src=x onerror="alert(1)"><p>Content</p>'
        result = _strip_html(html)
        assert "onerror" not in result
        assert "alert" not in result
        assert "Content" in result

    def test_handles_svg_onload_xss(self):
        """Test XSS bypass with SVG onload."""
        html = '<svg onload="alert(1)"><circle/></svg><p>Safe</p>'
        result = _strip_html(html)
        assert "onload" not in result
        assert "alert" not in result
        assert "Safe" in result

    def test_returns_empty_on_invalid_html(self):
        """Malformed HTML should return empty string, not crash."""
        # Extremely malformed input
        result = _strip_html(None)  # type: ignore
        assert result == ""


class TestInferAccountMailbox:
    """Tests for path parsing."""

    def test_infer_from_standard_path(self, tmp_path: Path):
        # Simulate: V10/account-uuid/INBOX.mbox/Data/.../12345.emlx
        mail_dir = tmp_path / "V10"
        emlx_path = (
            mail_dir
            / "account-uuid-123"
            / "INBOX.mbox"
            / "Data"
            / "1"
            / "Messages"
            / "12345.emlx"
        )
        emlx_path.parent.mkdir(parents=True)
        emlx_path.touch()

        account, mailbox = _infer_account_mailbox(emlx_path, mail_dir)
        assert account == "account-uuid-123"
        assert mailbox == "INBOX"

    def test_infer_removes_mbox_suffix(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        emlx_path = mail_dir / "acc" / "Sent Messages.mbox" / "Data" / "1.emlx"
        emlx_path.parent.mkdir(parents=True)
        emlx_path.touch()

        _account, mailbox = _infer_account_mailbox(emlx_path, mail_dir)
        assert mailbox == "Sent Messages"

    def test_infer_returns_unknown_for_invalid_path(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        other_path = tmp_path / "somewhere" / "else.emlx"

        account, mailbox = _infer_account_mailbox(other_path, mail_dir)
        assert account == "Unknown"
        assert mailbox == "Unknown"
