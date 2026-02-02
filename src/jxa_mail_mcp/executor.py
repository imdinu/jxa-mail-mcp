"""JXA script execution utilities.

Provides:
- run_jxa() / run_jxa_async(): Execute raw JXA scripts
- execute_with_core() / execute_with_core_async(): Execute with MailCore
- execute_query() / execute_query_async(): Execute a QueryBuilder
- build_account_js(): Build JXA code to get an account reference
- build_mailbox_setup_js(): Build JXA code to set up account + mailbox

The async versions use asyncio.create_subprocess_exec to avoid blocking
the event loop, which is important for MCP server responsiveness.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import TYPE_CHECKING, Any

from .jxa import MAIL_CORE_JS

if TYPE_CHECKING:
    from .builders import QueryBuilder


class JXAError(Exception):
    """Raised when a JXA script fails to execute."""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


# ========== JXA Code Building Helpers ==========


def build_account_js(account: str | None) -> str:
    """
    Build JXA expression to get an account reference.

    Uses json.dumps() for safe string serialization to prevent injection.

    Args:
        account: Account name, or None for first/default account

    Returns:
        JXA expression string like: MailCore.getAccount("Work")

    Example:
        >>> build_account_js("Work")
        'MailCore.getAccount("Work")'
        >>> build_account_js(None)
        'MailCore.getAccount(null)'
    """
    account_json = json.dumps(account)
    return f"MailCore.getAccount({account_json})"


def build_mailbox_setup_js(
    account: str | None,
    mailbox: str,
    account_var: str = "account",
    mailbox_var: str = "mailbox",
) -> str:
    """
    Build JXA code to set up account and mailbox variables.

    Uses json.dumps() for safe string serialization to prevent injection.

    Args:
        account: Account name, or None for first/default account
        mailbox: Mailbox name
        account_var: Variable name for account (default: "account")
        mailbox_var: Variable name for mailbox (default: "mailbox")

    Returns:
        JXA code declaring account and mailbox variables

    Example:
        >>> build_mailbox_setup_js("Work", "INBOX")
        'const account = MailCore.getAccount("Work");
        const mailbox = MailCore.getMailbox(account, "INBOX");'
    """
    account_json = json.dumps(account)
    mailbox_json = json.dumps(mailbox)
    return f"""const {account_var} = MailCore.getAccount({account_json});
const {mailbox_var} = MailCore.getMailbox({account_var}, {mailbox_json});"""


# ========== Script Execution ==========


def run_jxa(script: str, timeout: int = 120) -> str:
    """
    Execute a raw JXA script and return the output.

    Args:
        script: JavaScript code to execute via osascript
        timeout: Maximum execution time in seconds

    Returns:
        The script's stdout output (stripped)

    Raises:
        JXAError: If the script fails to execute
        subprocess.TimeoutExpired: If execution exceeds timeout
    """
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise JXAError(f"JXA script failed: {result.stderr}", result.stderr)
    return result.stdout.strip()


def execute_with_core(script_body: str, timeout: int = 120) -> Any:
    """
    Execute a JXA script with MailCore library injected.

    The script should use MailCore utilities and end with a
    JSON.stringify() call to return data.

    Args:
        script_body: JavaScript code that uses MailCore
        timeout: Maximum execution time in seconds

    Returns:
        Parsed JSON result from the script

    Raises:
        JXAError: If execution fails or output isn't valid JSON
    """
    full_script = f"{MAIL_CORE_JS}\n\n{script_body}"
    output = run_jxa(full_script, timeout)

    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        # Truncate long output for the error message
        preview = output[:500] + "..." if len(output) > 500 else output
        raise JXAError(
            f"Failed to parse JXA output as JSON: {e}\nOutput: {preview}",
            stderr=output,
        ) from e


def execute_query(query: QueryBuilder, timeout: int = 120) -> list[dict]:
    """
    Execute a QueryBuilder and return results.

    Args:
        query: A configured QueryBuilder instance
        timeout: Maximum execution time in seconds

    Returns:
        List of email dictionaries matching the query
    """
    script = query.build()
    return execute_with_core(script, timeout)


# ========== Async Script Execution ==========


async def run_jxa_async(script: str, timeout: int = 120) -> str:
    """
    Execute a raw JXA script asynchronously.

    Uses asyncio.create_subprocess_exec to avoid blocking the event loop.
    This is preferred for MCP server tools to maintain responsiveness.

    Args:
        script: JavaScript code to execute via osascript
        timeout: Maximum execution time in seconds

    Returns:
        The script's stdout output (stripped)

    Raises:
        JXAError: If the script fails to execute
        asyncio.TimeoutError: If execution exceeds timeout
    """
    process = await asyncio.create_subprocess_exec(
        "osascript",
        "-l",
        "JavaScript",
        "-e",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise

    if process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace")
        raise JXAError(f"JXA script failed: {stderr_text}", stderr_text)

    return stdout.decode("utf-8", errors="replace").strip()


async def execute_with_core_async(script_body: str, timeout: int = 120) -> Any:
    """
    Execute a JXA script with MailCore library injected (async version).

    The script should use MailCore utilities and end with a
    JSON.stringify() call to return data.

    Args:
        script_body: JavaScript code that uses MailCore
        timeout: Maximum execution time in seconds

    Returns:
        Parsed JSON result from the script

    Raises:
        JXAError: If execution fails or output isn't valid JSON
    """
    full_script = f"{MAIL_CORE_JS}\n\n{script_body}"
    output = await run_jxa_async(full_script, timeout)

    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        # Truncate long output for the error message
        preview = output[:500] + "..." if len(output) > 500 else output
        raise JXAError(
            f"Failed to parse JXA output as JSON: {e}\nOutput: {preview}",
            stderr=output,
        ) from e


async def execute_query_async(
    query: QueryBuilder, timeout: int = 120
) -> list[dict]:
    """
    Execute a QueryBuilder asynchronously and return results.

    Args:
        query: A configured QueryBuilder instance
        timeout: Maximum execution time in seconds

    Returns:
        List of email dictionaries matching the query
    """
    script = query.build()
    return await execute_with_core_async(script, timeout)
