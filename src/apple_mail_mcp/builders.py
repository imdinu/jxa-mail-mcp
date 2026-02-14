"""
JXA Script Builders for Apple Mail operations.

These builders generate optimized JXA scripts that use batch property
fetching for maximum performance.
"""

import json
from dataclasses import dataclass, field

# Standard email properties available for batch fetching
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
    "source": "source",  # Raw email source - expensive!
}

# Shorthand aliases for common property sets
PROPERTY_SETS = {
    "minimal": ["id", "subject", "sender", "date_received"],
    "standard": [
        "id",
        "subject",
        "sender",
        "date_received",
        "read",
        "flagged",
    ],
    "full": [
        "id",
        "subject",
        "sender",
        "date_received",
        "date_sent",
        "read",
        "flagged",
        "reply_to",
        "message_id",
    ],
}


@dataclass
class QueryBuilder:
    """
    Builder for constructing optimized email query scripts.

    Uses batch property fetching for fast execution. Supports filtering,
    limiting, and property selection.

    Example:
        query = (QueryBuilder()
            .from_mailbox("Work", "INBOX")
            .select("sender", "subject", "date_received", "read")
            .where("data.dateReceived[i] >= MailCore.today()")
            .limit(50)
            .build())
    """

    _account: str | None = None
    _mailbox: str = "INBOX"
    _properties: list[str] = field(default_factory=list)
    _filter_expr: str | None = None
    _limit: int | None = None
    _order_by: str | None = None
    _descending: bool = True

    def from_mailbox(
        self, account: str | None = None, mailbox: str = "INBOX"
    ) -> "QueryBuilder":
        """
        Set the source mailbox for the query.

        Args:
            account: Account name (None for first/default account)
            mailbox: Mailbox name (default: "INBOX")
        """
        self._account = account
        self._mailbox = mailbox
        return self

    def select(self, *props: str) -> "QueryBuilder":
        """
        Select properties to fetch.

        Use property names like: id, subject, sender, date_received,
        read, flagged, etc. Or use a preset: "minimal", "standard", "full".

        Args:
            props: Property names or preset names
        """
        for prop in props:
            if prop in PROPERTY_SETS:
                self._properties.extend(PROPERTY_SETS[prop])
            elif prop in EMAIL_PROPERTIES:
                self._properties.append(prop)
            else:
                raise ValueError(
                    f"Unknown property: {prop}. "
                    f"Valid: {list(EMAIL_PROPERTIES.keys())}"
                )
        return self

    def where(self, js_expression: str) -> "QueryBuilder":
        """
        Add a filter expression (JavaScript).

        The expression has access to:
        - `data`: Object with arrays of fetched properties
        - `i`: Current index in the loop
        - `MailCore`: The MailCore utilities

        Example:
            .where("data.dateReceived[i] >= MailCore.today()")
            .where("data.subject[i].toLowerCase().includes('urgent')")

        Args:
            js_expression: JavaScript boolean expression
        """
        self._filter_expr = js_expression
        return self

    def limit(self, n: int) -> "QueryBuilder":
        """Limit the number of results."""
        self._limit = n
        return self

    def order_by(self, prop: str, descending: bool = True) -> "QueryBuilder":
        """
        Order results by a property.

        Args:
            prop: Property name to sort by
            descending: Sort descending (default: True, newest first)
        """
        if prop not in EMAIL_PROPERTIES:
            raise ValueError(f"Unknown property for ordering: {prop}")
        self._order_by = prop
        self._descending = descending
        return self

    def build(self) -> str:
        """
        Generate the JXA script.

        Returns:
            JavaScript code that uses MailCore and returns JSON
        """
        if not self._properties:
            # Default to standard properties
            self._properties = PROPERTY_SETS["standard"].copy()

        # Remove duplicates while preserving order
        props = list(dict.fromkeys(self._properties))

        # Map Python property names to JXA property names
        jxa_props = [EMAIL_PROPERTIES[p] for p in props]

        # Build the script
        account_json = json.dumps(self._account)
        mailbox_json = json.dumps(self._mailbox)
        props_json = json.dumps(jxa_props)

        lines = [
            "// Setup",
            f"const account = MailCore.getAccount({account_json});",
            f"const mailbox = MailCore.getMailbox(account, {mailbox_json});",
            "const msgs = mailbox.messages;",
            "",
            "// Batch fetch (optimized - single IPC per property)",
            f"const data = MailCore.batchFetch(msgs, {props_json});",
            "",
            "// Build results",
            "const results = [];",
            f"const len = data.{jxa_props[0]}.length;",
            "",
        ]

        # Loop with optional limit
        if self._limit:
            loop_cond = f"i < len && results.length < {self._limit}"
            lines.append(f"for (let i = 0; {loop_cond}; i++) {{")
        else:
            lines.append("for (let i = 0; i < len; i++) {")

        # Optional filter
        if self._filter_expr:
            lines.append(f"    if (!({self._filter_expr})) continue;")

        # Build result object
        lines.append("    results.push({")
        for py_name, jxa_name in zip(props, jxa_props, strict=True):
            if jxa_name in ("dateReceived", "dateSent"):
                fmt = f"MailCore.formatDate(data.{jxa_name}[i])"
                lines.append(f"        {py_name}: {fmt},")
            else:
                lines.append(f"        {py_name}: data.{jxa_name}[i],")
        lines.append("    });")
        lines.append("}")

        # Optional sorting (in JS after collection)
        if self._order_by:
            direction = -1 if self._descending else 1
            lines.append("")
            lines.append("// Sort results")
            lines.append("results.sort((a, b) => {")
            lines.append(f"    const va = a.{self._order_by};")
            lines.append(f"    const vb = b.{self._order_by};")
            lines.append(f"    if (va < vb) return {-direction};")
            lines.append(f"    if (va > vb) return {direction};")
            lines.append("    return 0;")
            lines.append("});")

        lines.append("")
        lines.append("JSON.stringify(results);")

        return "\n".join(lines)


@dataclass
class AccountsQueryBuilder:
    """Builder for listing accounts and mailboxes."""

    def list_accounts(self) -> str:
        """Generate script to list all mail accounts."""
        return "JSON.stringify(MailCore.listAccounts());"

    def list_mailboxes(self, account: str | None = None) -> str:
        """Generate script to list mailboxes for an account."""
        account_json = json.dumps(account)
        return f"""
const account = MailCore.getAccount({account_json});
JSON.stringify(MailCore.listMailboxes(account));
"""
