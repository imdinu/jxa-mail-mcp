"""JXA script resources for Apple Mail automation."""

from pathlib import Path

# Path to the mail_core.js library
MAIL_CORE_JS = (Path(__file__).parent / "mail_core.js").read_text()

__all__ = ["MAIL_CORE_JS"]
