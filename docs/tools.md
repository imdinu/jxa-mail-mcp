# Tools

Apple Mail MCP provides **5 MCP tools** — a consolidated API designed for AI assistants.

## Overview

| Tool | Purpose | Parameters |
|------|---------|------------|
| `list_accounts()` | List email accounts | — |
| `list_mailboxes()` | List mailboxes | `account?` |
| `get_emails()` | Get emails with filtering | `account?`, `mailbox?`, `filter?`, `limit?` |
| `get_email()` | Get single email with content | `message_id`, `account?`, `mailbox?` |
| `search()` | Search emails | `query`, `account?`, `mailbox?`, `scope?`, `limit?` |

---

## `list_accounts()`

List all configured email accounts in Apple Mail.

**Parameters:** None

**Returns:** List of accounts with `name` and `id` fields.

```python
list_accounts()
# → [{"name": "Work", "id": "abc123"}, {"name": "Personal", "id": "def456"}]
```

---

## `list_mailboxes()`

List all mailboxes for an email account.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `account` | `string?` | env default | Account name |

**Returns:** List of mailboxes with `name` and `unreadCount` fields.

```python
list_mailboxes()
# → [{"name": "INBOX", "unreadCount": 5}, {"name": "Sent", "unreadCount": 0}]

list_mailboxes("Work")
# → [{"name": "INBOX", "unreadCount": 12}, ...]
```

---

## `get_emails()`

Get emails from a mailbox with optional filtering. This is the primary tool for listing emails.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `account` | `string?` | env default | Account name |
| `mailbox` | `string?` | `INBOX` | Mailbox name |
| `filter` | `string?` | `all` | Filter type (see below) |
| `limit` | `int?` | `50` | Max emails to return |

**Filters:**

| Filter | Description |
|--------|-------------|
| `all` | All emails (default) |
| `unread` | Only unread emails |
| `flagged` | Only flagged emails |
| `today` | Emails received today |
| `this_week` | Emails from the last 7 days |

**Returns:** List of email summaries sorted by date (newest first), each with: `id`, `subject`, `sender`, `date_received`, `read`, `flagged`.

```python
get_emails()
# All emails from default mailbox

get_emails(filter="unread", limit=10)
# 10 most recent unread emails

get_emails("Work", "INBOX", filter="today")
# Today's work emails
```

---

## `get_email()`

Get a single email with full content. Use this after finding an email via `get_emails()` or `search()` to read its body.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `message_id` | `int` | *required* | Email ID (from list/search results) |
| `account` | `string?` | env default | Helps find the message faster |
| `mailbox` | `string?` | `INBOX` | Helps find the message faster |

**Returns:** Full email with: `id`, `subject`, `sender`, `content` (full body text), `date_received`, `date_sent`, `read`, `flagged`, `reply_to`, `message_id` (RFC 822 Message-ID header).

```python
get_email(12345)
# → {"id": 12345, "subject": "Meeting notes", "content": "Hi team,...", ...}
```

!!! tip
    If `account` and `mailbox` are not provided, the server searches all mailboxes in the default account to find the message.

---

## `search()`

Search emails with automatic FTS5 optimization. Uses the FTS5 index for fast search (~2ms) when available, falls back to JXA-based search otherwise.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `string` | *required* | Search term or phrase |
| `account` | `string?` | env default | Account filter |
| `mailbox` | `string?` | `INBOX` | Mailbox filter |
| `scope` | `string?` | `all` | Search scope (see below) |
| `limit` | `int?` | `20` | Max results |

**Scopes:**

| Scope | Searches | Engine |
|-------|----------|--------|
| `all` | Subject + sender + body | FTS5 (if indexed) |
| `subject` | Subject line only | JXA |
| `sender` | Sender field only | JXA |
| `body` | Body content only | FTS5 (if indexed) |

**Returns:** List of results sorted by relevance (FTS5) or date (JXA fallback), each with: `id`, `subject`, `sender`, `date_received`, `score`, `matched_in`, and optionally `content_snippet`, `account`, `mailbox`.

```python
search("invoice")
# Search everywhere — uses FTS5 for instant results

search("john@example.com", scope="sender")
# Find emails from a specific sender

search("meeting notes", scope="body")
# Search body content only

search("deadline", limit=5)
# Top 5 results
```
