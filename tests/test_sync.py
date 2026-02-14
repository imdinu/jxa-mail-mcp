"""Tests for disk-based sync functionality (v0.4.0)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from apple_mail_mcp.index.disk import get_disk_inventory
from apple_mail_mcp.index.schema import get_schema_sql, SCHEMA_VERSION
from apple_mail_mcp.index.sync import (
    SyncResult,
    get_db_inventory,
    sync_from_disk,
)


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_total_changes(self):
        result = SyncResult(added=5, deleted=3, moved=2, errors=1)
        assert result.total_changes == 10  # 5 + 3 + 2

    def test_zero_changes(self):
        result = SyncResult(added=0, deleted=0, moved=0, errors=0)
        assert result.total_changes == 0


class TestGetDbInventory:
    """Tests for database inventory function."""

    def test_empty_database(self, temp_db: sqlite3.Connection):
        inventory = get_db_inventory(temp_db)
        assert inventory == {}

    def test_returns_email_paths(self, temp_db: sqlite3.Connection):
        # Insert emails with paths
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, emlx_path)
               VALUES (1, 'acc1', 'INBOX', 'Test', '/path/to/1.emlx')"""
        )
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, emlx_path)
               VALUES (2, 'acc1', 'INBOX', 'Test2', '/path/to/2.emlx')"""
        )
        temp_db.commit()

        inventory = get_db_inventory(temp_db)
        assert len(inventory) == 2
        assert inventory[("acc1", "INBOX", 1)] == "/path/to/1.emlx"
        assert inventory[("acc1", "INBOX", 2)] == "/path/to/2.emlx"

    def test_handles_null_paths(self, temp_db: sqlite3.Connection):
        # Insert email without path (legacy data)
        temp_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject)
               VALUES (1, 'acc1', 'INBOX', 'Test')"""
        )
        temp_db.commit()

        inventory = get_db_inventory(temp_db)
        assert inventory[("acc1", "INBOX", 1)] == ""


class TestGetDiskInventory:
    """Tests for disk inventory scanning."""

    def test_empty_directory(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        mail_dir.mkdir()
        inventory = get_disk_inventory(mail_dir)
        assert inventory == {}

    def test_finds_emlx_files(self, tmp_path: Path):
        # Create mail directory structure
        mail_dir = tmp_path / "V10"
        mbox = mail_dir / "account-uuid" / "INBOX.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True)

        # Create emlx files
        (mbox / "12345.emlx").write_bytes(b"test")
        (mbox / "67890.emlx").write_bytes(b"test")

        inventory = get_disk_inventory(mail_dir)
        assert len(inventory) == 2
        assert ("account-uuid", "INBOX", 12345) in inventory
        assert ("account-uuid", "INBOX", 67890) in inventory

    def test_skips_partial_files(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        mbox = mail_dir / "acc" / "INBOX.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True)

        # Create normal and partial files
        (mbox / "12345.emlx").write_bytes(b"test")
        (mbox / "12345.partial.emlx").write_bytes(b"partial")

        inventory = get_disk_inventory(mail_dir)
        assert len(inventory) == 1
        assert ("acc", "INBOX", 12345) in inventory

    def test_handles_nested_mbox_structure(self, tmp_path: Path):
        mail_dir = tmp_path / "V10"
        # Deep nesting: acc/Folder.mbox/Subfolder.mbox/Data/Messages/
        mbox = (
            mail_dir
            / "acc"
            / "Folder.mbox"
            / "Subfolder.mbox"
            / "Data"
            / "Messages"
        )
        mbox.mkdir(parents=True)
        (mbox / "1.emlx").write_bytes(b"test")

        inventory = get_disk_inventory(mail_dir)
        assert len(inventory) == 1


class TestSyncFromDisk:
    """Tests for disk-based state reconciliation."""

    @pytest.fixture
    def sync_db(self, tmp_path: Path):
        """Create a database for sync testing."""
        db_path = tmp_path / "sync_test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(get_schema_sql())
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def mail_dir(self, tmp_path: Path) -> Path:
        """Create a mock mail directory."""
        mail_dir = tmp_path / "Mail" / "V10"
        mail_dir.mkdir(parents=True)
        return mail_dir

    def _create_emlx(self, mail_dir: Path, account: str, mailbox: str,
                     msg_id: int) -> Path:
        """Helper to create a valid emlx file."""
        mbox = mail_dir / account / f"{mailbox}.mbox" / "Data" / "Messages"
        mbox.mkdir(parents=True, exist_ok=True)

        # Create minimal valid emlx
        emlx_path = mbox / f"{msg_id}.emlx"
        content = b"100\nFrom: test@test.com\nSubject: Test\n\nBody text"
        emlx_path.write_bytes(content)
        return emlx_path

    def test_sync_empty_both(self, sync_db: sqlite3.Connection,
                              mail_dir: Path):
        """Sync with empty DB and empty disk should be no-op."""
        result = sync_from_disk(sync_db, mail_dir)
        assert result.added == 0
        assert result.deleted == 0
        assert result.moved == 0

    def test_sync_detects_new_emails(self, sync_db: sqlite3.Connection,
                                      mail_dir: Path):
        """New files on disk should be added to DB."""
        self._create_emlx(mail_dir, "acc1", "INBOX", 1001)
        self._create_emlx(mail_dir, "acc1", "INBOX", 1002)

        result = sync_from_disk(sync_db, mail_dir)
        assert result.added == 2
        assert result.deleted == 0

        # Verify in DB
        cursor = sync_db.execute("SELECT COUNT(*) FROM emails")
        assert cursor.fetchone()[0] == 2

    def test_sync_detects_deleted_emails(self, sync_db: sqlite3.Connection,
                                          mail_dir: Path):
        """Emails in DB but not on disk should be deleted."""
        # Pre-populate DB with an email that doesn't exist on disk
        sync_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, emlx_path)
               VALUES (999, 'ghost', 'INBOX', 'Deleted', '/gone.emlx')"""
        )
        sync_db.commit()

        result = sync_from_disk(sync_db, mail_dir)
        assert result.deleted == 1

        # Verify removed from DB
        cursor = sync_db.execute("SELECT COUNT(*) FROM emails")
        assert cursor.fetchone()[0] == 0

    def test_sync_detects_moved_emails(self, sync_db: sqlite3.Connection,
                                        mail_dir: Path):
        """Emails with changed paths should be updated."""
        # Create file at new location
        new_path = self._create_emlx(mail_dir, "acc1", "Archive", 1001)

        # Pre-populate DB with old path
        sync_db.execute(
            """INSERT INTO emails
               (message_id, account, mailbox, subject, emlx_path)
               VALUES (1001, 'acc1', 'Archive', 'Moved', '/old/path.emlx')"""
        )
        sync_db.commit()

        result = sync_from_disk(sync_db, mail_dir)
        assert result.moved == 1

        # Verify path updated
        cursor = sync_db.execute(
            "SELECT emlx_path FROM emails WHERE message_id = 1001"
        )
        assert str(new_path) in cursor.fetchone()[0]
