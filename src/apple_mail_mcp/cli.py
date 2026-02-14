"""Command-line interface for apple-mail-mcp.

Provides commands for:
- index: Build search index from disk (requires Full Disk Access)
- status: Show index statistics
- rebuild: Force rebuild the index
- serve: Run the MCP server (default)

Usage:
    apple-mail-mcp            # Run MCP server (default)
    apple-mail-mcp serve      # Run MCP server explicitly
    apple-mail-mcp --watch    # Run with real-time index updates
    apple-mail-mcp index      # Build index from disk
    apple-mail-mcp status     # Show index status
    apple-mail-mcp rebuild    # Force rebuild index
"""

import sys
import time
from typing import Annotated

import cyclopts

from .config import get_index_path

app = cyclopts.App(
    name="apple-mail-mcp",
    help="Fast MCP server for Apple Mail with FTS5 search index.",
)


def _format_size(size_mb: float) -> str:
    """Format file size for display."""
    if size_mb < 1:
        return f"{size_mb * 1024:.1f} KB"
    return f"{size_mb:.1f} MB"


def _format_time(seconds: float) -> str:
    """Format duration for display."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.1f}s"


def _progress_bar(current: int, total: int | None, width: int = 40) -> str:
    """Create a progress bar string."""
    if total is None or total == 0:
        # Indeterminate progress
        return f"[{'=' * (current % width)}>]"

    pct = min(current / total, 1.0)
    filled = int(width * pct)
    bar = "=" * filled + "-" * (width - filled)
    return f"[{bar}] {pct * 100:.0f}%"


def _run_serve(watch: bool = False) -> None:
    """Internal function to run the MCP server."""
    # Perform startup sync if index exists (disk-based, fast)
    from .index import IndexManager
    from .server import mcp

    manager = IndexManager.get_instance()

    if manager.has_index():
        try:
            print("Syncing index...", file=sys.stderr, flush=True)
            start = time.time()
            count = manager.sync_updates()
            elapsed = time.time() - start
            if count > 0:
                print(
                    f"Synced {count} changes in {_format_time(elapsed)}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Index up to date ({_format_time(elapsed)})",
                    file=sys.stderr,
                )
        except Exception as e:
            print(f"Warning: Index sync failed: {e}", file=sys.stderr)

        # Start file watcher if requested
        if watch:
            try:

                def on_update(added: int, removed: int) -> None:
                    if added or removed:
                        print(
                            f"Index updated: +{added} -{removed}",
                            file=sys.stderr,
                        )

                if manager.start_watcher(on_update=on_update):
                    print("File watcher started", file=sys.stderr)
                else:
                    print(
                        "Warning: Could not start file watcher",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(f"Warning: File watcher failed: {e}", file=sys.stderr)

    mcp.run()


@app.command
def serve(
    watch: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--watch", "-w"],
            help="Watch for new emails and update index in real-time",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--verbose", "-v"],
            help="Enable verbose output",
        ),
    ] = False,
) -> None:
    """
    Run the MCP server.

    This is the default command when no subcommand is specified.
    The server provides email search and access tools to MCP clients.

    At startup, the index is automatically synced with disk (fast, <5s).
    Use --watch to enable real-time index updates when emails arrive.
    Requires Full Disk Access for the terminal.
    """
    _run_serve(watch=watch)


@app.command
def index(
    verbose: Annotated[
        bool,
        cyclopts.Parameter(name=["--verbose", "-v"], help="Show progress"),
    ] = False,
) -> None:
    """
    Build the search index from disk.

    Reads .emlx files directly from ~/Library/Mail/V10/ for fast indexing.
    This is much faster than fetching via JXA (~30x faster).

    IMPORTANT: Requires Full Disk Access permission for Terminal.
    Grant access in System Settings → Privacy & Security → Full Disk Access.
    """
    from .index import IndexManager

    print("Building search index from disk...")
    print(f"Index location: {get_index_path()}")
    print()

    manager = IndexManager()
    start = time.time()
    last_report = start

    def progress(current: int, total: int | None, message: str) -> None:
        nonlocal last_report
        now = time.time()

        # Throttle updates to avoid spam
        if now - last_report < 0.5 and total is None:
            return
        last_report = now

        if verbose:
            if total:
                bar = _progress_bar(current, total)
                print(f"\r{bar} {message}", end="", flush=True)
            else:
                print(f"\r{message}", end="", flush=True)

    try:
        callback = progress if verbose else None
        count = manager.build_from_disk(progress_callback=callback)
        elapsed = time.time() - start

        if verbose:
            print()  # Newline after progress

        print()
        print(f"✓ Indexed {count:,} emails in {_format_time(elapsed)}")

        stats = manager.get_stats()
        print(f"  Mailboxes: {stats.mailbox_count}")
        print(f"  Database size: {_format_size(stats.db_size_mb)}")

    except PermissionError as e:
        print(f"\n✗ Permission denied: {e}", file=sys.stderr)
        print("\nTo fix this:", file=sys.stderr)
        print("  1. Open System Settings", file=sys.stderr)
        print("  2. Privacy & Security → Full Disk Access", file=sys.stderr)
        print("  3. Add and enable Terminal.app", file=sys.stderr)
        print("  4. Restart terminal and try again", file=sys.stderr)
        sys.exit(1)

    except FileNotFoundError as e:
        print(f"\n✗ Not found: {e}", file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


@app.command
def status(
    verbose: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--verbose", "-v"],
            help="Enable verbose output",
        ),
    ] = False,
) -> None:
    """
    Show index statistics.

    Displays:
    - Email count and mailbox count
    - Last sync time and staleness
    - Database file size
    """
    from .index import IndexManager

    manager = IndexManager()

    if not manager.has_index():
        print("No index found.")
        print(f"Expected location: {get_index_path()}")
        print()
        print("Run 'apple-mail-mcp index' to build the index.")
        sys.exit(1)

    stats = manager.get_stats()

    print("Apple Mail MCP Index Status")
    print("=" * 40)
    print(f"Location:     {get_index_path()}")
    print(f"Emails:       {stats.email_count:,}")
    print(f"Mailboxes:    {stats.mailbox_count}")
    print(f"Database:     {_format_size(stats.db_size_mb)}")
    print()

    if stats.last_sync:
        print(f"Last sync:    {stats.last_sync.strftime('%Y-%m-%d %H:%M:%S')}")
        if stats.staleness_hours is not None:
            if stats.staleness_hours < 1:
                staleness = f"{stats.staleness_hours * 60:.0f} minutes ago"
            elif stats.staleness_hours < 24:
                staleness = f"{stats.staleness_hours:.1f} hours ago"
            else:
                staleness = f"{stats.staleness_hours / 24:.1f} days ago"
            print(f"Staleness:    {staleness}")

            if manager.is_stale():
                print()
                print(
                    "⚠ Index is stale. Run 'apple-mail-mcp index' to refresh."
                )
    else:
        print("Last sync:    Never")
        print()
        print("⚠ No sync recorded. Run 'apple-mail-mcp index' to build.")


@app.command
def rebuild(
    account: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--account", "-a"],
            help="Rebuild only this account (all if not specified)",
        ),
    ] = None,
    mailbox: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--mailbox", "-m"],
            help="Rebuild only this mailbox (requires --account)",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        cyclopts.Parameter(name=["--verbose", "-v"], help="Show progress"),
    ] = False,
) -> None:
    """
    Force rebuild the search index.

    Clears existing data and rebuilds from disk.
    Optionally scope to a specific account or mailbox.
    """
    if mailbox and not account:
        print("Error: --mailbox requires --account", file=sys.stderr)
        sys.exit(1)

    from .index import IndexManager

    scope = "entire index"
    if account and mailbox:
        scope = f"{account}/{mailbox}"
    elif account:
        scope = f"account {account}"

    print(f"Rebuilding {scope}...")

    manager = IndexManager()
    start = time.time()

    def progress(current: int, total: int | None, message: str) -> None:
        if verbose:
            print(f"\r{message}", end="", flush=True)

    try:
        count = manager.rebuild(
            account=account,
            mailbox=mailbox,
            progress_callback=progress if verbose else None,
        )
        elapsed = time.time() - start

        if verbose:
            print()

        print(f"✓ Rebuilt {count:,} emails in {_format_time(elapsed)}")

    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


@app.default
def default_handler(
    watch: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--watch", "-w"],
            help="Watch for new emails and update index in real-time",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--verbose", "-v"],
            help="Enable verbose output",
        ),
    ] = False,
) -> None:
    """Run the MCP server (default when no command specified)."""
    _run_serve(watch=watch)


def main() -> None:
    """Entry point for the CLI."""
    app()
