# JXA Mail MCP - Project Instructions

## Project Overview

Fast MCP server for Apple Mail using optimized JXA scripts with batch property fetching for 87x faster performance than naive iteration.

## Project Structure

```
src/jxa_mail_mcp/
├── __init__.py         # Exports mcp instance and main()
├── server.py           # FastMCP server and MCP tools
├── builders.py         # QueryBuilder, AccountsQueryBuilder
├── executor.py         # run_jxa(), execute_with_core(), execute_query()
└── jxa/
    ├── __init__.py     # Exports MAIL_CORE_JS
    └── mail_core.js    # Shared JXA utilities (MailCore object)
```

## Architecture

### Layer Separation

1. **server.py** - MCP tool definitions, uses builders
2. **builders.py** - Constructs JXA scripts from Python, type-safe
3. **executor.py** - Runs scripts via osascript, handles JSON parsing
4. **jxa/mail_core.js** - Shared JS utilities injected into all scripts

### Data Flow

```
MCP Tool → QueryBuilder.build() → executor.execute_query()
                                        ↓
                           MAIL_CORE_JS + script body
                                        ↓
                              osascript -l JavaScript
                                        ↓
                              JSON.parse(stdout)
```

## Coding Standards

- **Python 3.13+**, type hints required
- **Formatter**: `uv run ruff format src/`
- **Linter**: `uv run ruff check src/`
- Line length: 80 characters

## Adding New Query Tools

Use `QueryBuilder` for read operations:

```python
from .builders import QueryBuilder
from .executor import execute_query

@mcp.tool
def get_recent_from_sender(
    sender: str,
    account: str | None = None,
    limit: int = 20
) -> list[dict]:
    """Get recent emails from a specific sender."""
    safe_sender = sender.lower().replace("'", "\\'")
    query = (
        QueryBuilder()
        .from_mailbox(account, "INBOX")
        .select("standard")  # id, subject, sender, date_received, read, flagged
        .where(f"(data.sender[i] || '').toLowerCase().includes('{safe_sender}')")
        .order_by("date_received", descending=True)
        .limit(limit)
    )
    return execute_query(query)
```

### Available Properties

```python
EMAIL_PROPERTIES = {
    "id": "id",
    "subject": "subject",
    "sender": "sender",
    "date_received": "dateReceived",
    "date_sent": "dateSent",
    "read": "readStatus",
    "flagged": "flaggedStatus",
    "deleted": "deletedStatus",
    "junk": "junkMailStatus",
    "reply_to": "replyTo",
    "message_id": "messageId",
    "source": "source",  # Raw email - expensive!
}

# Presets
PROPERTY_SETS = {
    "minimal": ["id", "subject", "sender", "date_received"],
    "standard": ["id", "subject", "sender", "date_received", "read", "flagged"],
    "full": [...all except source...]
}
```

### QueryBuilder Methods

```python
QueryBuilder()
    .from_mailbox(account, mailbox)  # Set source
    .select("standard")               # Choose properties
    .select("subject", "sender")      # Or individual props
    .where("JS expression")           # Filter (uses data.prop[i])
    .order_by("date_received")        # Sort results
    .limit(50)                        # Cap results
    .build()                          # → JXA script string
```

## Adding Non-Query Tools

For operations that don't fit `QueryBuilder` (compose, mutate, attachments), use `execute_with_core()` directly:

```python
from .executor import execute_with_core

@mcp.tool
def mark_as_read(message_id: int) -> dict:
    """Mark a message as read."""
    script = f"""
const msg = Mail.messages.byId({message_id});
msg.readStatus = true;
JSON.stringify({{success: true, id: {message_id}}});
"""
    return execute_with_core(script)
```

## Extending mail_core.js

Add utilities to `jxa/mail_core.js` for reusable JS logic:

```javascript
const MailCore = {
    // ... existing methods ...

    /**
     * Your new utility.
     */
    newUtility(param) {
        // Implementation
    },
};
```

Then use in Python-generated scripts:

```python
script = "JSON.stringify(MailCore.newUtility('value'));"
result = execute_with_core(script)
```

## Testing

```bash
# Import test
uv run python -c "from jxa_mail_mcp import mcp; print('OK')"

# Test specific tool
uv run python -c "
from jxa_mail_mcp.server import get_todays_emails
print(get_todays_emails())
"

# Test QueryBuilder output
uv run python -c "
from jxa_mail_mcp.builders import QueryBuilder
print(QueryBuilder().from_mailbox('Work').select('minimal').build())
"

# Run MCP server
jxa-mail-mcp
# or
uv run jxa-mail-mcp
```

## Critical: JXA Performance

**ALWAYS use batch property fetching.** Never iterate messages individually:

```javascript
// WRONG - 87x slower
for (let msg of inbox.messages()) {
    results.push({ from: msg.sender() });  // IPC per message
}

// RIGHT - Use MailCore.batchFetch
const data = MailCore.batchFetch(msgs, ["sender", "subject"]);
for (let i = 0; i < data.sender.length; i++) {
    results.push({ from: data.sender[i] });
}
```

## Dependencies

- **fastmcp**: MCP server framework
- **cyclopts**: CLI argument parsing (future CLI features)

Dev: ruff (linting/formatting)
