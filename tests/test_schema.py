"""Tests for SQLite schema and database initialization."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from apple_mail_mcp.index.schema import (
    SCHEMA_VERSION,
    init_database,
    optimize_fts_index,
    rebuild_fts_index,
)


class TestSchemaSQL:
    """Tests for schema SQL generation."""

    @pytest.mark.parametrize("table", ["emails", "emails_fts", "sync_state"])
    def test_schema_creates_required_tables(
        self, temp_db: sqlite3.Connection, table
    ):
        """Schema creates all required tables."""
        cursor = temp_db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name=?",
            (table,),
        )
        assert cursor.fetchone() is not None

    def test_schema_creates_triggers(self, temp_db: sqlite3.Connection):
        cursor = temp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
        triggers = {row[0] for row in cursor}
        assert "emails_ai" in triggers  # After insert
        assert "emails_ad" in triggers  # After delete
        assert "emails_au" in triggers  # After update


class TestCompositeKeyConstraint:
    """Tests for composite unique constraint on emails."""

    def test_allows_same_message_id_different_mailbox(
        self, temp_db: sqlite3.Connection
    ):
        """Same message_id is allowed in different mailboxes."""
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject)
               VALUES (1, 'acc', 'INBOX', 'Test 1')"""
        )
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject)
               VALUES (1, 'acc', 'Archive', 'Test 2')"""
        )
        temp_db.commit()

        cursor = temp_db.execute(
            "SELECT COUNT(*) FROM emails WHERE message_id = 1"
        )
        assert cursor.fetchone()[0] == 2

    def test_rejects_duplicate_composite_key(self, temp_db: sqlite3.Connection):
        """Same (account, mailbox, message_id) should fail."""
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject)
               VALUES (1, 'acc', 'INBOX', 'Original')"""
        )

        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute(
                """INSERT INTO emails
                   (message_id, account, mailbox, subject)
                   VALUES (1, 'acc', 'INBOX', 'Duplicate')"""
            )


class TestFtsTriggers:
    """Tests for FTS sync triggers."""

    def test_insert_trigger_syncs_to_fts(self, temp_db: sqlite3.Connection):
        """Insert into emails should auto-insert into emails_fts."""
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, content)
               VALUES (1, 'acc', 'INBOX', 'Test', 'searchable')"""
        )
        temp_db.commit()

        # Search should find it
        cursor = temp_db.execute(
            "SELECT rowid FROM emails_fts WHERE emails_fts MATCH 'searchable'"
        )
        result = cursor.fetchone()
        assert result is not None

    def test_delete_trigger_removes_from_fts(self, temp_db: sqlite3.Connection):
        """Delete from emails should remove from emails_fts."""
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, content)
               VALUES (1, 'acc', 'INBOX', 'Test', 'uniqueword987')"""
        )
        temp_db.commit()

        # Verify it's searchable
        cursor = temp_db.execute(
            "SELECT rowid FROM emails_fts "
            "WHERE emails_fts MATCH 'uniqueword987'"
        )
        assert cursor.fetchone() is not None

        # Delete
        temp_db.execute(
            "DELETE FROM emails WHERE message_id = 1 AND account = 'acc'"
        )
        temp_db.commit()

        # Should no longer be searchable
        cursor = temp_db.execute(
            "SELECT rowid FROM emails_fts "
            "WHERE emails_fts MATCH 'uniqueword987'"
        )
        assert cursor.fetchone() is None

    def test_update_trigger_reindexes(self, temp_db: sqlite3.Connection):
        """Update should re-index the content."""
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, content)
               VALUES (1, 'acc', 'INBOX', 'Orig', 'oldword123')"""
        )
        temp_db.commit()

        # Update content
        temp_db.execute(
            """UPDATE emails SET content = 'newword456'
               WHERE message_id = 1 AND account = 'acc'"""
        )
        temp_db.commit()

        # Old content should not be findable
        cursor = temp_db.execute(
            "SELECT rowid FROM emails_fts WHERE emails_fts MATCH 'oldword123'"
        )
        assert cursor.fetchone() is None

        # New content should be findable
        cursor = temp_db.execute(
            "SELECT rowid FROM emails_fts WHERE emails_fts MATCH 'newword456'"
        )
        assert cursor.fetchone() is not None


class TestInitDatabase:
    """Tests for database initialization."""

    def test_creates_database_file(self, temp_db_path: Path):
        conn = init_database(temp_db_path)
        assert temp_db_path.exists()
        conn.close()

    def test_creates_parent_directories(self, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "c" / "index.db"
        conn = init_database(deep_path)
        assert deep_path.exists()
        conn.close()

    def test_sets_wal_mode(self, temp_db_path: Path):
        conn = init_database(temp_db_path)
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"
        conn.close()

    def test_stores_schema_version(self, temp_db_path: Path):
        conn = init_database(temp_db_path)
        cursor = conn.execute("SELECT version FROM schema_version")
        version = cursor.fetchone()[0]
        assert version == SCHEMA_VERSION
        conn.close()

    def test_sets_secure_permissions(self, tmp_path: Path):
        """New database files should have 0600 permissions (owner only)."""
        db_path = tmp_path / "secure_test.db"
        conn = init_database(db_path)
        conn.close()

        # Check file permissions
        mode = os.stat(db_path).st_mode
        # Extract just the permission bits (last 9 bits)
        perms = stat.S_IMODE(mode)
        # Should be 0600 (owner read/write only)
        assert perms == 0o600, f"Expected 0600, got {oct(perms)}"


class TestFtsOperations:
    """Tests for FTS maintenance operations."""

    def test_rebuild_fts_index(self, populated_db: sqlite3.Connection):
        # Should not raise
        rebuild_fts_index(populated_db)

        # Search should still work
        cursor = populated_db.execute(
            "SELECT rowid FROM emails_fts WHERE emails_fts MATCH 'meeting'"
        )
        assert cursor.fetchone() is not None

    def test_optimize_fts_index(self, populated_db: sqlite3.Connection):
        # Should not raise
        optimize_fts_index(populated_db)
