"""Tests for AccountMap — account name ↔ UUID translation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apple_mail_mcp.index.accounts import _CACHE_TTL, AccountMap

SAMPLE_ACCOUNTS = [
    {"name": "Work", "id": "24E569DF-5E45-4B6A-8E3C-1A2B3C4D5E6F"},
    {"name": "Personal", "id": "F6E5D4C3-B2A1-4321-9876-FEDCBA098765"},
]


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset AccountMap singleton between tests."""
    AccountMap._instance = None
    yield
    AccountMap._instance = None


@pytest.fixture
def loaded_map() -> AccountMap:
    """An AccountMap pre-loaded with sample accounts."""
    m = AccountMap()
    m.load_from_jxa(SAMPLE_ACCOUNTS)
    return m


class TestSingleton:
    """Singleton and instance management."""

    def test_get_instance_returns_same_object(self):
        assert AccountMap.get_instance() is AccountMap.get_instance()


class TestLoadFromJxa:
    """Tests for load_from_jxa() population."""

    def test_populates_both_directions(self, loaded_map):
        """Loading builds both name→UUID and UUID→name maps."""
        assert loaded_map.name_to_uuid("Work") is not None
        assert loaded_map.uuid_to_name(SAMPLE_ACCOUNTS[0]["id"]) == "Work"

    def test_skips_entries_with_missing_fields(self):
        """Entries missing name or id are silently skipped."""
        m = AccountMap()
        m.load_from_jxa([
            {"name": "Good", "id": "UUID-1"},
            {"name": "", "id": "UUID-2"},       # empty name
            {"name": "NoId", "id": ""},          # empty id
            {"id": "UUID-3"},                    # missing name
            {"name": "NoIdKey"},                 # missing id
        ])
        assert m.name_to_uuid("Good") == "UUID-1"
        assert m.name_to_uuid("") is None
        assert m.name_to_uuid("NoId") is None
        assert m.name_to_uuid("NoIdKey") is None

    def test_reload_replaces_old_data(self):
        """Calling load_from_jxa again replaces the previous mapping."""
        m = AccountMap()
        m.load_from_jxa([{"name": "Old", "id": "OLD-UUID"}])
        assert m.name_to_uuid("Old") == "OLD-UUID"

        m.load_from_jxa([{"name": "New", "id": "NEW-UUID"}])
        assert m.name_to_uuid("Old") is None
        assert m.name_to_uuid("New") == "NEW-UUID"


class TestNameToUuid:
    """Tests for name_to_uuid() lookup."""

    def test_returns_uuid_for_known_name(self, loaded_map):
        assert loaded_map.name_to_uuid("Work") == SAMPLE_ACCOUNTS[0]["id"]

    def test_returns_none_for_unknown_name(self, loaded_map):
        assert loaded_map.name_to_uuid("Nonexistent") is None


class TestUuidToName:
    """Tests for uuid_to_name() lookup."""

    def test_returns_name_for_known_uuid(self, loaded_map):
        assert loaded_map.uuid_to_name(SAMPLE_ACCOUNTS[0]["id"]) == "Work"

    def test_returns_uuid_as_fallback_for_unknown(self, loaded_map):
        """Unknown UUIDs fall back to returning the UUID string itself."""
        unknown = "AAAA-BBBB-CCCC"
        assert loaded_map.uuid_to_name(unknown) == unknown


class TestCacheStaleness:
    """Tests for TTL-based cache invalidation."""

    def test_fresh_cache_is_not_stale(self, loaded_map):
        assert not loaded_map._is_stale()

    def test_unloaded_map_is_stale(self):
        assert AccountMap()._is_stale()

    def test_expired_cache_is_stale(self, loaded_map):
        """Cache becomes stale after TTL expires."""
        loaded_map._loaded_at -= _CACHE_TTL + 1
        assert loaded_map._is_stale()


class TestEnsureLoaded:
    """Tests for ensure_loaded() async JXA fetching."""

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.executor.execute_with_core_async")
    async def test_fetches_via_jxa_when_stale(self, mock_exec):
        """ensure_loaded calls JXA when cache is empty/stale."""
        mock_exec.return_value = SAMPLE_ACCOUNTS
        m = AccountMap()

        await m.ensure_loaded()

        mock_exec.assert_called_once()
        assert m.name_to_uuid("Work") == SAMPLE_ACCOUNTS[0]["id"]

    @pytest.mark.asyncio
    @patch("apple_mail_mcp.executor.execute_with_core_async")
    async def test_skips_jxa_when_fresh(self, mock_exec):
        """ensure_loaded is a no-op when cache is still fresh."""
        m = AccountMap()
        m.load_from_jxa(SAMPLE_ACCOUNTS)  # Pre-seed

        await m.ensure_loaded()

        mock_exec.assert_not_called()
