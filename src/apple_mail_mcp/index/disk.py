"""Direct disk reading of Apple Mail .emlx files.

This module reads emails directly from ~/Library/Mail/V10/ for fast indexing.
Requires Full Disk Access permission for the terminal.

Mail.app storage structure:
    ~/Library/Mail/V10/
    ├── [Account-UUID]/
    │   └── [Mailbox].mbox/
    │       └── Data/x/y/Messages/
    │           ├── 12345.emlx
    │           └── 12346.emlx
    └── MailData/
        └── Envelope Index    # SQLite with metadata

.emlx file format:
    1255                      ← Byte count of MIME content
    From: sender@example.com  ← RFC 5322 headers + body
    Subject: Hello
    ...
    <?xml version="1.0"?>     ← Plist metadata footer
    <plist>...</plist>
"""

from __future__ import annotations

import email
import re
import sqlite3
import warnings
from dataclasses import dataclass
from email.header import decode_header, make_header
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# Mail.app version folder (V10 for macOS Catalina+)
MAIL_VERSION = "V10"

# Maximum email file size to prevent OOM from malformed/huge files (25 MB)
MAX_EMLX_SIZE = 25 * 1024 * 1024


@dataclass
class EmlxEmail:
    """Parsed email from .emlx file."""

    id: int
    subject: str
    sender: str
    content: str
    date_received: str
    emlx_path: Path


def find_mail_directory() -> Path:
    """
    Find the Apple Mail data directory.

    Returns:
        Path to ~/Library/Mail/V10/

    Raises:
        FileNotFoundError: If directory doesn't exist
        PermissionError: If Full Disk Access is not granted
    """
    mail_dir = Path.home() / "Library" / "Mail" / MAIL_VERSION

    if not mail_dir.exists():
        raise FileNotFoundError(
            f"Mail directory not found: {mail_dir}\n"
            "Ensure Apple Mail has been used on this Mac."
        )

    # Test access by trying to list contents
    try:
        next(mail_dir.iterdir(), None)
    except PermissionError as e:
        raise PermissionError(
            f"Cannot access {mail_dir}\n"
            "Grant Full Disk Access to Terminal:\n"
            "  System Settings → Privacy & Security → Full Disk Access"
        ) from e

    return mail_dir


def find_envelope_index(mail_dir: Path) -> Path:
    """
    Find the Envelope Index SQLite database.

    Args:
        mail_dir: Path to ~/Library/Mail/V10/

    Returns:
        Path to the Envelope Index database

    Raises:
        FileNotFoundError: If database not found
    """
    # The Envelope Index is in MailData directory
    envelope_path = mail_dir.parent / "MailData" / "Envelope Index"

    if not envelope_path.exists():
        raise FileNotFoundError(
            f"Envelope Index not found: {envelope_path}\n"
            "Ensure Apple Mail has synced email."
        )

    return envelope_path


def read_envelope_index(mail_dir: Path) -> dict[int, dict]:
    """
    Read the Envelope Index database to get message metadata.

    The Envelope Index contains:
    - Message IDs and their file paths
    - Account and mailbox information
    - Basic metadata (subject, sender, dates)

    Args:
        mail_dir: Path to ~/Library/Mail/V10/

    Returns:
        Dict mapping message ID to metadata dict with:
        - account: Account name
        - mailbox: Mailbox name
        - emlx_path: Path to .emlx file (relative)
        - subject: Email subject
        - sender: Sender address
        - date_received: ISO date string
    """
    envelope_path = find_envelope_index(mail_dir)

    # Connect in read-only mode to avoid locking issues
    conn = sqlite3.connect(f"file:{envelope_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    result: dict[int, dict] = {}

    try:
        # Query the messages table joined with mailboxes
        # Schema varies by macOS version, so we use a flexible approach
        cursor = conn.execute("""
            SELECT
                m.ROWID as id,
                m.subject,
                m.sender,
                m.date_received,
                m.mailbox as mailbox_id,
                mb.url as mailbox_url
            FROM messages m
            LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
            ORDER BY m.date_received DESC
        """)

        for row in cursor:
            msg_id = row["id"]

            # Parse mailbox URL to get account and mailbox name
            # Format: mailbox://[account-uuid]/[mailbox-name]
            mailbox_url = row["mailbox_url"] or ""
            account, mailbox = _parse_mailbox_url(mailbox_url)

            result[msg_id] = {
                "account": account,
                "mailbox": mailbox,
                "subject": row["subject"] or "",
                "sender": row["sender"] or "",
                "date_received": _format_timestamp(row["date_received"]),
            }

    except sqlite3.OperationalError as e:
        # Schema might be different, try alternative approach
        if "no such table" in str(e).lower():
            # Fallback to scanning .emlx files directly
            pass
        else:
            raise
    finally:
        conn.close()

    return result


def _parse_mailbox_url(url: str) -> tuple[str, str]:
    """
    Parse a mailbox URL to extract account and mailbox names.

    Args:
        url: mailbox://account-uuid/mailbox-name

    Returns:
        (account_name, mailbox_name) tuple
    """
    if not url:
        return ("Unknown", "Unknown")

    # Remove mailbox:// prefix
    path = url.replace("mailbox://", "")

    # Split by /
    parts = path.split("/", 1)

    if len(parts) >= 2:
        account = parts[0] or "Unknown"
        mailbox = parts[1] or "Unknown"
        return (account, mailbox)

    return (parts[0] if parts else "Unknown", "Unknown")


def _format_timestamp(timestamp: float | int | None) -> str:
    """Convert Core Data timestamp to ISO string."""
    if timestamp is None:
        return ""

    # Core Data timestamps are seconds since Jan 1, 2001
    # Convert to Unix timestamp (seconds since Jan 1, 1970)
    import datetime

    CORE_DATA_EPOCH = 978307200  # Jan 1, 2001 in Unix time

    try:
        unix_ts = timestamp + CORE_DATA_EPOCH
        dt = datetime.datetime.fromtimestamp(unix_ts, tz=datetime.UTC)
        return dt.isoformat()
    except (OSError, ValueError, OverflowError):
        return ""


def parse_emlx(path: Path) -> EmlxEmail | None:
    """
    Parse a single .emlx file.

    .emlx format:
    1. First line: byte count of MIME content
    2. MIME message (RFC 5322)
    3. XML plist footer with Apple metadata

    Args:
        path: Path to .emlx file

    Returns:
        EmlxEmail with parsed content, or None if parsing fails
    """
    try:
        # Check file size to prevent OOM from huge/malformed files
        if path.stat().st_size > MAX_EMLX_SIZE:
            return None

        content = path.read_bytes()

        # Find the byte count on first line
        newline_idx = content.find(b"\n")
        if newline_idx == -1:
            return None

        try:
            byte_count = int(content[:newline_idx].strip())
        except ValueError:
            return None

        # Extract MIME content
        mime_start = newline_idx + 1
        mime_end = mime_start + byte_count
        mime_content = content[mime_start:mime_end]

        # Parse MIME message
        msg = email.message_from_bytes(mime_content)

        # Extract subject with proper decoding
        subject = ""
        if msg["Subject"]:
            try:
                subject = str(make_header(decode_header(msg["Subject"])))
            except (UnicodeDecodeError, LookupError):
                subject = msg["Subject"] or ""

        # Extract sender
        sender = msg["From"] or ""
        if sender:
            try:
                sender = str(make_header(decode_header(sender)))
            except (UnicodeDecodeError, LookupError):
                pass

        # Extract date and convert from RFC 2822 to ISO 8601
        date_received = ""
        if msg["Date"]:
            try:
                from email.utils import parsedate_to_datetime

                dt = parsedate_to_datetime(msg["Date"])
                date_received = dt.isoformat()
            except (ValueError, TypeError):
                date_received = msg["Date"]

        # Extract body text
        body = _extract_body_text(msg)

        # Extract message ID from filename
        msg_id = int(path.stem)

        return EmlxEmail(
            id=msg_id,
            subject=subject,
            sender=sender,
            content=body,
            date_received=date_received,
            emlx_path=path,
        )

    except Exception:
        # Skip malformed files
        return None


def _extract_body_text(msg: email.message.Message) -> str:
    """
    Extract plain text body from email message.

    Handles multipart messages, preferring text/plain over text/html.
    """
    if msg.is_multipart():
        text_parts = []
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        decoded = payload.decode(charset, errors="replace")
                        text_parts.append(decoded)
                    except (UnicodeDecodeError, LookupError):
                        decoded = payload.decode("utf-8", errors="replace")
                        text_parts.append(decoded)
        if text_parts:
            return "\n".join(text_parts)

        # Fallback to HTML if no plain text
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html = payload.decode(charset, errors="replace")
                        return _strip_html(html)
                    except (UnicodeDecodeError, LookupError):
                        pass
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    return _strip_html(text)
                return text
            except (UnicodeDecodeError, LookupError):
                return payload.decode("utf-8", errors="replace")
        return ""


def _strip_html(html: str) -> str:
    """
    Robust HTML to text conversion using BeautifulSoup.

    Uses a proper HTML parser instead of regex to prevent XSS bypass
    attacks from malformed HTML like <<script> or nested tags.
    """
    try:
        from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(html, "html.parser")

        # Remove script and style elements completely
        for element in soup(["script", "style"]):
            element.decompose()

        # Get text with newlines as separators
        text = soup.get_text(separator="\n", strip=True)

        # Collapse multiple newlines
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = re.sub(r" +", " ", text)

        return text.strip()

    except Exception:
        # Fallback: return empty string if parsing fails entirely
        # This is safer than returning potentially malicious content
        return ""


def scan_emlx_files(mail_dir: Path) -> Iterator[Path]:
    """
    Find all .emlx files in the Mail directory.

    Args:
        mail_dir: Path to ~/Library/Mail/V10/

    Yields:
        Paths to .emlx files
    """
    # .emlx files are in: account-uuid/mailbox.mbox/Data/x/y/Messages/
    for emlx_path in mail_dir.rglob("*.emlx"):
        # Skip partial downloads
        if ".partial.emlx" in emlx_path.name:
            continue
        yield emlx_path


def scan_all_emails(mail_dir: Path) -> Iterator[dict]:
    """
    Scan all emails from the Mail directory.

    This combines the Envelope Index metadata with .emlx file content
    for comprehensive email data.

    Args:
        mail_dir: Path to ~/Library/Mail/V10/

    Yields:
        Email dicts with: id, account, mailbox, subject, sender,
        content, date_received, emlx_path
    """
    # First, try to read metadata from Envelope Index
    try:
        metadata = read_envelope_index(mail_dir)
    except (FileNotFoundError, sqlite3.Error):
        metadata = {}

    # Scan .emlx files and combine with metadata
    for emlx_path in scan_emlx_files(mail_dir):
        parsed = parse_emlx(emlx_path)
        if not parsed:
            continue

        msg_id = parsed.id

        # Get metadata from Envelope Index if available
        meta = metadata.get(msg_id, {})

        # Infer account/mailbox from path if not in metadata
        if not meta:
            account, mailbox = _infer_account_mailbox(emlx_path, mail_dir)
            meta = {"account": account, "mailbox": mailbox}

        yield {
            "id": msg_id,
            "account": meta.get("account", "Unknown"),
            "mailbox": meta.get("mailbox", "Unknown"),
            "subject": parsed.subject or meta.get("subject", ""),
            "sender": parsed.sender or meta.get("sender", ""),
            "content": parsed.content,
            "date_received": meta.get("date_received") or parsed.date_received,
            "emlx_path": str(emlx_path),
        }


def get_disk_inventory(mail_dir: Path) -> dict[tuple[str, str, int], str]:
    """
    Fast inventory of all emails on disk WITHOUT parsing content.

    This walks the filesystem and extracts (account, mailbox, message_id)
    from file paths. Much faster than scan_all_emails() since it doesn't
    read file content.

    Path structure:
        V10/[account-uuid]/[mailbox].mbox/Data/.../Messages/[id].emlx

    Args:
        mail_dir: Path to ~/Library/Mail/V10/

    Returns:
        Dict mapping (account, mailbox, msg_id) -> emlx_path string
    """
    inventory: dict[tuple[str, str, int], str] = {}

    for emlx_path in scan_emlx_files(mail_dir):
        try:
            # Extract message ID from filename (e.g., "12345.emlx" -> 12345)
            msg_id = int(emlx_path.stem)

            # Infer account/mailbox from path
            account, mailbox = _infer_account_mailbox(emlx_path, mail_dir)

            inventory[(account, mailbox, msg_id)] = str(emlx_path)

        except (ValueError, AttributeError):
            # Skip files with non-numeric names
            continue

    return inventory


def _infer_account_mailbox(emlx_path: Path, mail_dir: Path) -> tuple[str, str]:
    """
    Infer account and mailbox from .emlx file path.

    Path structure: V10/account-uuid/mailbox.mbox/Data/.../Messages/id.emlx
    """
    try:
        relative = emlx_path.relative_to(mail_dir)
        parts = relative.parts

        # First part is account UUID
        account = parts[0] if parts else "Unknown"

        # Second part is mailbox.mbox
        mailbox = "Unknown"
        if len(parts) > 1:
            mbox_part = parts[1]
            if mbox_part.endswith(".mbox"):
                mailbox = mbox_part[:-5]  # Remove .mbox suffix
            else:
                mailbox = mbox_part

        return (account, mailbox)
    except ValueError:
        return ("Unknown", "Unknown")
