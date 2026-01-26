import json
import subprocess

from fastmcp import FastMCP

mcp = FastMCP("Apple Mail")


def run_jxa(script: str) -> str:
    """Execute a JXA script and return the output."""
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"JXA script failed: {result.stderr}")
    return result.stdout.strip()


@mcp.tool
def get_todays_emails(
    account: str = "Sesame", mailbox: str = "Inbox"
) -> list[dict]:
    """
    Get all emails received today from a specified Mail account and mailbox.

    Args:
        account: The email account name in Apple Mail (default: "Sesame")
        mailbox: The mailbox to read from (default: "Inbox")

    Returns:
        List of email dictionaries with sender, subject, date, read status,
        and flagged status
    """
    script = f"""
const Mail = Application('Mail');
const today = new Date();
today.setHours(0, 0, 0, 0);

const account = Mail.accounts.byName({json.dumps(account)});
const inbox = account.mailboxes.byName({json.dumps(mailbox)});
const msgs = inbox.messages;

// Batch fetch all properties (fast - single IPC call per property)
const dates = msgs.dateReceived();
const senders = msgs.sender();
const subjects = msgs.subject();
const readStatuses = msgs.readStatus();
const flaggedStatuses = msgs.flaggedStatus();

const results = [];
for (let i = 0; i < dates.length; i++) {{
    if (dates[i] >= today) {{
        results.push({{
            from: senders[i],
            subject: subjects[i],
            date: dates[i].toISOString(),
            read: readStatuses[i],
            flagged: flaggedStatuses[i]
        }});
    }}
}}

JSON.stringify(results);
"""
    output = run_jxa(script)
    return json.loads(output)


@mcp.tool
def search_emails(
    query: str,
    account: str = "Sesame",
    mailbox: str = "Inbox",
    limit: int = 50,
) -> list[dict]:
    """
    Search for emails matching a query in Apple Mail.

    Args:
        query: Search term to look for in email subjects
        account: The email account name in Apple Mail (default: "Sesame")
        mailbox: The mailbox to search in (default: "Inbox")
        limit: Maximum number of results to return (default: 50)

    Returns:
        List of matching email dictionaries
    """
    script = f"""
const Mail = Application('Mail');
const account = Mail.accounts.byName({json.dumps(account)});
const inbox = account.mailboxes.byName({json.dumps(mailbox)});
const msgs = inbox.messages;

const query = {json.dumps(query.lower())};
const limit = {limit};

// Batch fetch properties
const subjects = msgs.subject();
const senders = msgs.sender();
const dates = msgs.dateReceived();
const readStatuses = msgs.readStatus();
const flaggedStatuses = msgs.flaggedStatus();

const results = [];
for (let i = 0; i < subjects.length && results.length < limit; i++) {{
    const subjectLower = (subjects[i] || '').toLowerCase();
    const senderLower = (senders[i] || '').toLowerCase();
    if (subjectLower.includes(query) || senderLower.includes(query)) {{
        results.push({{
            from: senders[i],
            subject: subjects[i],
            date: dates[i].toISOString(),
            read: readStatuses[i],
            flagged: flaggedStatuses[i]
        }});
    }}
}}

JSON.stringify(results);
"""
    output = run_jxa(script)
    return json.loads(output)


if __name__ == "__main__":
    mcp.run()
