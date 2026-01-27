"""Configuration for JXA Mail MCP server."""

import os


def get_default_account() -> str | None:
    """
    Get the default account from environment variable.

    Set JXA_MAIL_DEFAULT_ACCOUNT to use a specific account by default.
    If not set, the first account in Apple Mail will be used.

    Returns:
        Account name or None to use first account.
    """
    return os.environ.get("JXA_MAIL_DEFAULT_ACCOUNT")


def get_default_mailbox() -> str:
    """
    Get the default mailbox from environment variable.

    Set JXA_MAIL_DEFAULT_MAILBOX to use a specific mailbox by default.
    Defaults to "INBOX".

    Returns:
        Mailbox name.
    """
    return os.environ.get("JXA_MAIL_DEFAULT_MAILBOX", "Inbox")
