"""FTS5 search index for fast email body search.

This module provides:
- IndexManager: Main interface for building, syncing, and searching the index
- IndexWatcher: Real-time file watcher for automatic index updates
- Pre-indexing from disk via CLI (requires Full Disk Access)
- Incremental sync via JXA for new emails
- FTS5 full-text search with BM25 ranking
"""

from .manager import IndexManager, IndexStats, SearchResult
from .watcher import IndexWatcher

__all__ = ["IndexManager", "IndexStats", "IndexWatcher", "SearchResult"]
