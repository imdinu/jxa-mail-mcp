"""Shared pytest fixtures for apple-mail-mcp tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from apple_mail_mcp.index.schema import (
    INSERT_EMAIL_SQL,
    SCHEMA_VERSION,
    get_schema_sql,
)


@pytest.fixture
def temp_db():
    """Create an in-memory database with the schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(get_schema_sql())
    conn.execute(
        "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Return a temporary path for a database file."""
    return tmp_path / "test_index.db"


@pytest.fixture
def sample_emails() -> list[dict]:
    """Return sample email data for testing."""
    return [
        {
            "message_id": 1001,
            "account": "test-account-uuid",
            "mailbox": "INBOX",
            "subject": "Meeting tomorrow at 3pm",
            "sender": "boss@company.com",
            "content": "Please review the quarterly report before the meeting.",
            "date_received": "2024-01-15T10:30:00",
        },
        {
            "message_id": 1002,
            "account": "test-account-uuid",
            "mailbox": "INBOX",
            "subject": "Invoice #12345 attached",
            "sender": "billing@vendor.com",
            "content": "Your invoice for January is attached. Total: $500",
            "date_received": "2024-01-14T09:00:00",
        },
        {
            "message_id": 1003,
            "account": "test-account-uuid",
            "mailbox": "Sent",
            "subject": "Re: Project deadline",
            "sender": "me@company.com",
            "content": "The project deadline has been extended to Friday.",
            "date_received": "2024-01-13T14:22:00",
        },
        {
            "message_id": 1001,  # Same ID as first, different mailbox
            "account": "test-account-uuid",
            "mailbox": "Archive",
            "subject": "Archived: Old meeting notes",
            "sender": "archive@company.com",
            "content": "These are archived meeting notes from last year.",
            "date_received": "2023-06-01T08:00:00",
        },
    ]


@pytest.fixture
def populated_db(temp_db: sqlite3.Connection, sample_emails: list[dict]):
    """Database with sample emails inserted.

    Uses INSERT_EMAIL_SQL from schema.py to ensure consistency with
    production code. The sample_emails fixture uses 'message_id' key
    while email_to_row() expects 'id', so we adapt here.
    """
    for email in sample_emails:
        # Adapt fixture format to match email_to_row() expectations
        row = (
            email["message_id"],  # message_id
            email["account"],  # account
            email["mailbox"],  # mailbox
            email["subject"],  # subject
            email["sender"],  # sender
            email["content"],  # content
            email["date_received"],  # date_received
            None,  # emlx_path (not used in test fixtures)
        )
        temp_db.execute(INSERT_EMAIL_SQL, row)
    temp_db.commit()

    # Rebuild FTS index
    temp_db.execute("INSERT INTO emails_fts(emails_fts) VALUES('rebuild')")
    temp_db.commit()

    return temp_db


@pytest.fixture
def sample_emlx_content() -> bytes:
    """Return sample .emlx file content."""
    mime_content = b"""\
From: sender@example.com
To: recipient@example.com
Subject: Test Email Subject
Date: Mon, 15 Jan 2024 10:30:00 -0500
Content-Type: text/plain; charset="utf-8"

This is the body of the test email.
It has multiple lines.
"""
    byte_count = len(mime_content)
    plist_footer = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN">
<plist version="1.0">
<dict>
    <key>date-received</key>
    <real>727531800</real>
</dict>
</plist>
"""
    return f"{byte_count}\n".encode() + mime_content + plist_footer


@pytest.fixture
def temp_emlx_file(tmp_path: Path, sample_emlx_content: bytes) -> Path:
    """Create a temporary .emlx file."""
    emlx_path = tmp_path / "12345.emlx"
    emlx_path.write_bytes(sample_emlx_content)
    return emlx_path
