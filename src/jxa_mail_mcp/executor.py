"""JXA script execution utilities."""

from __future__ import annotations

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
        JXAError: If execution fails
        json.JSONDecodeError: If output isn't valid JSON
    """
    full_script = f"{MAIL_CORE_JS}\n\n{script_body}"
    output = run_jxa(full_script, timeout)
    return json.loads(output)


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
