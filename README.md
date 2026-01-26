# Apple Mail MCP Server

An MCP (Model Context Protocol) server that provides fast, programmatic access to Apple Mail via optimized JXA (JavaScript for Automation) scripts.

## Features

- **get_todays_emails** - Fetch all emails received today from any account/mailbox
- **search_emails** - Search emails by subject or sender

## Installation

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone <repo>
cd apple-mail-mcp-server
uv sync
```

## Usage

### Run the MCP server

```bash
uv run fastmcp run src/apple_mail_mcp_server/server.py
```

### Add to Claude Code

```bash
claude mcp add apple-mail -- uv run --directory /path/to/apple-mail-mcp-server fastmcp run src/apple_mail_mcp_server/server.py
```

### Test directly

```python
from apple_mail_mcp_server.server import get_todays_emails, search_emails

emails = get_todays_emails(account="iCloud", mailbox="Inbox")
results = search_emails("meeting", limit=10)
```

## Performance Philosophy

### The Problem

Naive AppleScript/JXA iteration is extremely slow when accessing Apple Mail. A typical approach might look like:

```javascript
// SLOW: ~54 seconds for a mailbox with a few hundred messages
for (let msg of inbox.messages()) {
    results.push({
        from: msg.sender(),      // IPC call to Mail.app
        subject: msg.subject(),  // IPC call to Mail.app
        date: msg.dateReceived() // IPC call to Mail.app
    });
}
```

Each property access (`msg.sender()`, `msg.subject()`, etc.) triggers a separate Apple Event IPC round-trip to Mail.app. With hundreds of messages and multiple properties, this results in thousands of IPC calls.

### The Solution: Batch Property Fetching

JXA supports fetching a property from all elements at once, returning an array:

```javascript
// FAST: ~0.6 seconds (87x faster)
const msgs = inbox.messages;

// Single IPC call returns array of ALL senders
const senders = msgs.sender();
// Single IPC call returns array of ALL subjects
const subjects = msgs.subject();
// Single IPC call returns array of ALL dates
const dates = msgs.dateReceived();

// Now filter in pure JavaScript - no more IPC
for (let i = 0; i < dates.length; i++) {
    if (dates[i] >= today) {
        results.push({
            from: senders[i],
            subject: subjects[i],
            date: dates[i]
        });
    }
}
```

### Benchmark Results

| Method | Time | Speedup |
|--------|------|---------|
| AppleScript (per-message iteration) | 54.1s | 1x |
| JXA (per-message iteration) | 53.9s | 1x |
| **JXA (batch property fetching)** | **0.62s** | **87x** |

The bottleneck is not AppleScript vs JavaScript - it's the number of IPC round-trips. Batch fetching reduces thousands of Apple Event calls to just a handful.

### Why Not Direct SQLite Access?

Apple Mail stores its data in SQLite databases at `~/Library/Mail/V10/MailData/Envelope Index`. Direct database access would be even faster (~50ms), but:

1. Requires **Full Disk Access** permission granted to the terminal
2. Database schema is undocumented and may change between macOS versions
3. The scripting bridge is the officially supported API

The batch JXA approach provides an excellent balance: near-instant performance without special permissions or fragile implementation details.

### Key Principles

1. **Minimize IPC calls** - Each Apple Event round-trip has significant overhead
2. **Batch fetch properties** - Use `collection.property()` syntax to get arrays
3. **Filter in JavaScript** - Once data is in memory, JS operations are effectively free
4. **Avoid content fetching** - Email body content is expensive; only fetch when needed

## Development

```bash
# Install with dev dependencies
uv sync

# Run linter
uv run ruff check src/

# Format code
uv run ruff format src/
```

## License

MIT
