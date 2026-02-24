"""Configuration for Apple Mail MCP server."""

import os
from pathlib import Path

# Default index location
DEFAULT_INDEX_PATH = Path.home() / ".apple-mail-mcp" / "index.db"


def get_default_account() -> str | None:
    """
    Get the default account from environment variable.

    Set APPLE_MAIL_DEFAULT_ACCOUNT to use a specific account by default.
    If not set, the first account in Apple Mail will be used.

    Returns:
        Account name or None to use first account.
    """
    return os.environ.get("APPLE_MAIL_DEFAULT_ACCOUNT")


def get_default_mailbox() -> str:
    """
    Get the default mailbox from environment variable.

    Set APPLE_MAIL_DEFAULT_MAILBOX to use a specific mailbox by default.
    Defaults to "INBOX".

    Returns:
        Mailbox name.
    """
    return os.environ.get("APPLE_MAIL_DEFAULT_MAILBOX", "INBOX")


# ========== Index Configuration ==========


def get_index_path() -> Path:
    """
    Get the FTS5 index database path.

    Set APPLE_MAIL_INDEX_PATH to customize the location.
    Defaults to ~/.apple-mail-mcp/index.db

    Returns:
        Path to the index database file.
    """
    env_path = os.environ.get("APPLE_MAIL_INDEX_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_INDEX_PATH


def get_index_max_emails() -> int:
    """
    Get the maximum number of emails to index per mailbox.

    Set APPLE_MAIL_INDEX_MAX_EMAILS to customize.
    Defaults to 5000 emails per mailbox.

    Returns:
        Maximum emails per mailbox.
    """
    return int(os.environ.get("APPLE_MAIL_INDEX_MAX_EMAILS", "5000"))


def get_index_exclude_mailboxes() -> set[str]:
    """
    Get mailboxes to exclude from indexing.

    Set APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES to a comma-separated list.
    Defaults to "Drafts".

    Returns:
        Set of mailbox names to exclude.
    """
    env_val = os.environ.get("APPLE_MAIL_INDEX_EXCLUDE_MAILBOXES")
    if env_val is not None:
        return {m.strip() for m in env_val.split(",") if m.strip()}
    return {"Drafts"}


def get_index_staleness_hours() -> float:
    """
    Get the staleness threshold for the index.

    After this many hours without a sync, the index is considered stale
    and should be refreshed.

    Set APPLE_MAIL_INDEX_STALENESS_HOURS to customize.
    Defaults to 24 hours.

    Returns:
        Staleness threshold in hours.
    """
    return float(os.environ.get("APPLE_MAIL_INDEX_STALENESS_HOURS", "24"))
