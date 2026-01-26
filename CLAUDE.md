# Apple Mail MCP Server - Project Instructions

## Project Overview

This is an MCP (Model Context Protocol) server that provides programmatic access to Apple Mail via optimized JXA (JavaScript for Automation) scripts. The server is built with FastMCP and designed for use with Claude Code and other MCP-compatible clients.

## Project Structure

```
apple-mail-mcp-server/
├── src/
│   └── apple_mail_mcp_server/
│       ├── __init__.py          # Exports mcp instance
│       └── server.py            # FastMCP server and tools
├── pyproject.toml               # Project config (uv, ruff, hatch)
├── README.md                    # User documentation
└── CLAUDE.md                    # This file
```

## Coding Standards

### Python
- **Python 3.13+** required
- **Formatter**: ruff format (line length 80)
- **Linter**: ruff check with rules: ASYNC, B, E, F, I, RUF, UP, W
- **Imports**: Use `src` layout - imports are `from apple_mail_mcp_server.server import ...`

### Code Style
- Type hints required for all function signatures
- Docstrings required for all public functions (Google style)
- No bare `except:` clauses - always specify exception types

### Commands
```bash
uv run ruff check src/     # Lint
uv run ruff format src/    # Format
uv sync                    # Install dependencies
```

## JXA Script Conventions

**Critical**: All JXA scripts interacting with Apple Mail MUST use batch property fetching for performance.

### DO - Batch fetch (fast):
```javascript
const msgs = inbox.messages;
const senders = msgs.sender();      // Single IPC call, returns array
const subjects = msgs.subject();    // Single IPC call, returns array

for (let i = 0; i < senders.length; i++) {
    results.push({ from: senders[i], subject: subjects[i] });
}
```

### DON'T - Per-message iteration (87x slower):
```javascript
for (let msg of inbox.messages()) {
    results.push({
        from: msg.sender(),    // IPC call per message
        subject: msg.subject() // IPC call per message
    });
}
```

### JXA Script Template
When adding new tools that query Mail, follow this pattern:

```python
@mcp.tool
def new_mail_tool(account: str = "Default", mailbox: str = "Inbox") -> list[dict]:
    """Tool description."""
    script = f"""
const Mail = Application('Mail');
const account = Mail.accounts.byName({json.dumps(account)});
const mbox = account.mailboxes.byName({json.dumps(mailbox)});
const msgs = mbox.messages;

// Batch fetch ALL needed properties upfront
const prop1 = msgs.property1();
const prop2 = msgs.property2();

// Filter/process in pure JavaScript
const results = [];
for (let i = 0; i < prop1.length; i++) {{
    // Your logic here
    results.push({{ ... }});
}}

JSON.stringify(results);
"""
    return json.loads(run_jxa(script))
```

## Adding New Tools

1. Define the tool function with `@mcp.tool` decorator
2. Use descriptive docstrings - they become the tool's MCP description
3. Follow the JXA batch pattern above
4. Test with: `uv run python -c "from apple_mail_mcp_server.server import your_tool; print(your_tool())"`

## Testing

```bash
# Test server loads
uv run python -c "from apple_mail_mcp_server import mcp; print('OK')"

# Test individual tools
uv run python -c "from apple_mail_mcp_server.server import get_todays_emails; print(get_todays_emails())"

# Run MCP server
uv run fastmcp run src/apple_mail_mcp_server/server.py
```

## Dependencies

- **fastmcp**: MCP server framework
- **cyclopts**: CLI argument parsing (for future CLI features)

Dev dependencies are managed via `[dependency-groups]` in pyproject.toml.
