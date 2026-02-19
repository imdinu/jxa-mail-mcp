# Installation

## With pipx (Recommended)

```bash
pipx install apple-mail-mcp
```

A persistent install is recommended because the FTS5 search index (`~/.apple-mail-mcp/index.db`) is built once and reused across sessions. Ephemeral runners like `pipx run` or `uvx` work but won't benefit from the cached index.

## With uv

```bash
uv tool install apple-mail-mcp
```

## With pip

```bash
pip install apple-mail-mcp
```

## From Source

For development or to run the latest unreleased version:

```bash
git clone https://github.com/imdinu/apple-mail-mcp
cd apple-mail-mcp
uv sync
```

Run with:

```bash
uv run apple-mail-mcp
```

## Prerelease Versions

To install a prerelease (e.g., `v0.2.0a1`):

```bash
pipx install apple-mail-mcp --pip-args='--pre'
# or
uv tool install apple-mail-mcp --prerelease=allow
```

## Verify Installation

```bash
apple-mail-mcp status
```

This prints the index status. If you see output (even "no index found"), the installation is working.

## Requirements

| Requirement | Version |
|-------------|---------|
| **macOS** | Ventura or later |
| **Python** | 3.11+ |
| **Apple Mail** | Configured with ≥1 account |

!!! note
    Apple Mail MCP is macOS-only. It requires Apple Mail and the `osascript` runtime for JXA execution.
