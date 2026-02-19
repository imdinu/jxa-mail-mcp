"""Account name ↔ UUID mapping for FTS5 search filters.

The disk indexer stores account UUIDs from filesystem paths
(e.g., "24E569DF-5E45-...") but users pass friendly names
(e.g., "Work") from JXA. This module bridges the gap.

Usage:
    acct_map = AccountMap.get_instance()
    await acct_map.ensure_loaded()
    uuid = acct_map.name_to_uuid("Work")   # → "24E569DF-..."
    name = acct_map.uuid_to_name("24E5...")  # → "Work"
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)

# Cache TTL in seconds (5 minutes)
_CACHE_TTL = 300


class AccountMap:
    """Thread-safe, cached mapping between account names and UUIDs.

    JXA `Mail.accounts.id()` returns the same UUIDs as the
    filesystem folder names under ~/Library/Mail/V10/, so we
    can reliably translate friendly names for FTS5 queries.
    """

    _instance: AccountMap | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._name_to_uuid: dict[str, str] = {}
        self._uuid_to_name: dict[str, str] = {}
        self._loaded_at: float = 0
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> AccountMap:
        """Get the singleton AccountMap instance (thread-safe)."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = AccountMap()
            return cls._instance

    def name_to_uuid(self, name: str) -> str | None:
        """Translate a friendly account name to its UUID.

        Args:
            name: Friendly account name (e.g., "Work")

        Returns:
            UUID string, or None if not found
        """
        with self._lock:
            return self._name_to_uuid.get(name)

    def uuid_to_name(self, uuid: str) -> str:
        """Translate a UUID to its friendly account name.

        Args:
            uuid: Account UUID from the index DB

        Returns:
            Friendly name, or the UUID itself as fallback
        """
        with self._lock:
            return self._uuid_to_name.get(uuid, uuid)

    def load_from_jxa(self, accounts: list[dict]) -> None:
        """Populate the map from listAccounts() output.

        Args:
            accounts: List of {"name": "Work", "id": "UUID"} dicts
        """
        with self._lock:
            self._name_to_uuid.clear()
            self._uuid_to_name.clear()
            for acct in accounts:
                name = acct.get("name", "")
                uid = acct.get("id", "")
                if name and uid:
                    self._name_to_uuid[name] = uid
                    self._uuid_to_name[uid] = name
            self._loaded_at = time.monotonic()
            logger.debug(
                "AccountMap loaded: %d accounts", len(self._name_to_uuid)
            )

    def _is_stale(self) -> bool:
        """Check if the cache needs refreshing."""
        if self._loaded_at == 0:
            return True
        return (time.monotonic() - self._loaded_at) > _CACHE_TTL

    async def ensure_loaded(self) -> None:
        """Ensure the map is populated, fetching via JXA if needed.

        Called from async context (MCP tool handlers). Uses
        execute_with_core_async to avoid blocking the event loop.

        Double-checked locking prevents concurrent callers from
        firing duplicate JXA fetches.
        """
        if not self._is_stale():
            return

        async with self._async_lock:
            # Re-check after acquiring lock — another coroutine
            # may have refreshed while we were waiting.
            if not self._is_stale():
                return

            from ..builders import AccountsQueryBuilder
            from ..executor import execute_with_core_async

            script = AccountsQueryBuilder().list_accounts()
            accounts = await execute_with_core_async(script)
            self.load_from_jxa(accounts)
