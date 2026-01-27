# JXA Mail MCP

A fast MCP (Model Context Protocol) server for Apple Mail, using optimized JXA (JavaScript for Automation) scripts with batch property fetching for **87x faster** performance.

## Features

- **list_accounts** - List all configured email accounts
- **list_mailboxes** - List mailboxes for an account
- **get_emails** - Fetch emails from any mailbox with pagination
- **get_todays_emails** - Fetch all emails received today
- **get_unread_emails** - Fetch unread emails
- **get_flagged_emails** - Fetch flagged emails
- **search_emails** - Search emails by subject or sender

## Installation

### With pipx (recommended)

```bash
pipx install jxa-mail-mcp
```

### From source

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/imdinu/jxa-mail-mcp
cd jxa-mail-mcp
uv sync
```

## Usage

### Add to Claude Code

After installing with pipx:

```json
{
  "mcpServers": {
    "mail": {
      "command": "jxa-mail-mcp"
    }
  }
}
```

Or from source:

```json
{
  "mcpServers": {
    "mail": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/jxa-mail-mcp", "jxa-mail-mcp"]
    }
  }
}
```

### Run directly

```bash
jxa-mail-mcp
```

### Configuration

Set default account and mailbox via environment variables:

```bash
export JXA_MAIL_DEFAULT_ACCOUNT="Work"
export JXA_MAIL_DEFAULT_MAILBOX="Inbox"
```

Or in Claude Code config:

```json
{
  "mcpServers": {
    "mail": {
      "command": "jxa-mail-mcp",
      "env": {
        "JXA_MAIL_DEFAULT_ACCOUNT": "Work"
      }
    }
  }
}
```

### Test in Python

```python
from jxa_mail_mcp.server import get_todays_emails, search_emails

emails = get_todays_emails(account="iCloud", mailbox="Inbox")
results = search_emails("meeting", account="Work", limit=10)
```

## Architecture

```
src/jxa_mail_mcp/
├── __init__.py         # Exports mcp instance and main()
├── server.py           # FastMCP server and MCP tools
├── config.py           # Environment variable configuration
├── builders.py         # QueryBuilder for constructing JXA scripts
├── executor.py         # JXA script execution utilities
└── jxa/
    ├── __init__.py     # Exports MAIL_CORE_JS
    └── mail_core.js    # Shared JXA utilities library
```

### Design Principles

1. **Separation of concerns**: Python handles logic/types, JavaScript handles Mail.app interaction
2. **Builder pattern**: `QueryBuilder` constructs optimized JXA scripts programmatically
3. **Shared JS library**: `mail_core.js` provides reusable utilities injected into all scripts
4. **Type safety**: Python type hints ensure correct usage

## Performance

### The Problem

Naive AppleScript/JXA iteration is extremely slow:

```javascript
// SLOW: ~54 seconds for a few hundred messages
for (let msg of inbox.messages()) {
    results.push({
        from: msg.sender(),      // IPC call to Mail.app
        subject: msg.subject(),  // IPC call to Mail.app
    });
}
```

Each property access triggers a separate Apple Event IPC round-trip.

### The Solution: Batch Property Fetching

JXA supports fetching a property from all elements at once:

```javascript
// FAST: ~0.6 seconds (87x faster)
const msgs = inbox.messages;
const senders = msgs.sender();   // Single IPC call returns array
const subjects = msgs.subject(); // Single IPC call returns array

for (let i = 0; i < senders.length; i++) {
    results.push({ from: senders[i], subject: subjects[i] });
}
```

### Benchmark Results

| Method | Time | Speedup |
|--------|------|---------|
| AppleScript (per-message) | 54.1s | 1x |
| JXA (per-message) | 53.9s | 1x |
| **JXA (batch fetching)** | **0.62s** | **87x** |

## Development

```bash
uv sync
uv run ruff check src/
uv run ruff format src/

# Test
uv run python -c "
from jxa_mail_mcp.server import list_accounts, get_todays_emails
print('Accounts:', len(list_accounts()))
print('Today:', len(get_todays_emails()))
"
```

## License

GPL-3.0-or-later
