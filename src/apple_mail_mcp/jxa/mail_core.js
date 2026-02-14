/**
 * Apple Mail JXA Core Library
 *
 * Shared utilities for fast, batch-optimized Mail.app automation.
 * This library is injected into all JXA scripts to provide consistent
 * error handling, account/mailbox resolution, and batch fetching.
 */

const Mail = Application("Mail");

const MailCore = {
    /**
     * Get an account by name, or the first account if name is null/empty.
     * @param {string|null} name - Account name or null for default
     * @returns {Account} Mail account object
     */
    getAccount(name) {
        if (name) {
            return Mail.accounts.byName(name);
        }
        const accounts = Mail.accounts();
        if (accounts.length === 0) {
            throw new Error("No mail accounts configured");
        }
        return accounts[0];
    },

    /**
     * Get a mailbox from an account.
     * @param {Account} account - Mail account object
     * @param {string} name - Mailbox name (e.g., "INBOX", "Sent")
     * @returns {Mailbox} Mailbox object
     */
    getMailbox(account, name) {
        return account.mailboxes.byName(name);
    },

    /**
     * Batch fetch multiple properties from a messages collection.
     * This is THE critical optimization - one IPC call per property
     * instead of one per message.
     *
     * @param {Messages} msgs - Messages collection from a mailbox
     * @param {string[]} props - Property names to fetch
     * @returns {Object} Map of property name to array of values
     */
    batchFetch(msgs, props) {
        const result = {};
        for (const prop of props) {
            result[prop] = msgs[prop]();
        }
        return result;
    },

    /**
     * Get message IDs for referencing specific messages later.
     * @param {Messages} msgs - Messages collection
     * @returns {string[]} Array of message IDs
     */
    getMessageIds(msgs) {
        return msgs.id();
    },

    /**
     * Get a specific message by ID.
     * @param {string} messageId - The message ID
     * @returns {Message} Message object
     */
    getMessageById(messageId) {
        // Messages are referenced by ID across all accounts
        return Mail.messages.byId(messageId);
    },

    /**
     * Wrap an operation with error handling.
     * @param {Function} fn - Function to execute
     * @returns {Object} {ok: true, data: ...} or {ok: false, error: ...}
     */
    safely(fn) {
        try {
            return { ok: true, data: fn() };
        } catch (e) {
            return { ok: false, error: String(e) };
        }
    },

    /**
     * Get today's date at midnight for filtering.
     * @returns {Date} Today at 00:00:00
     */
    today() {
        const d = new Date();
        d.setHours(0, 0, 0, 0);
        return d;
    },

    /**
     * Get a date N days ago at midnight for filtering.
     * @param {number} days - Number of days ago
     * @returns {Date} Date at 00:00:00 N days ago
     */
    daysAgo(days) {
        const d = new Date();
        d.setDate(d.getDate() - days);
        d.setHours(0, 0, 0, 0);
        return d;
    },

    /**
     * Format a date for JSON output.
     * @param {Date} date - Date to format
     * @returns {string} ISO string or null if invalid
     */
    formatDate(date) {
        if (!date || !(date instanceof Date)) return null;
        return date.toISOString();
    },

    /**
     * List all accounts.
     * @returns {Object[]} Array of {name, id} objects
     */
    listAccounts() {
        const accounts = Mail.accounts();
        const names = Mail.accounts.name();
        const ids = Mail.accounts.id();
        const results = [];
        for (let i = 0; i < accounts.length; i++) {
            results.push({ name: names[i], id: ids[i] });
        }
        return results;
    },

    /**
     * List mailboxes for an account.
     * Note: messageCount is not available via batch fetch, only unreadCount.
     * @param {Account} account - Mail account
     * @returns {Object[]} Array of {name, unreadCount}
     */
    listMailboxes(account) {
        const mboxes = account.mailboxes();
        const names = account.mailboxes.name();
        const unread = account.mailboxes.unreadCount();
        const results = [];
        for (let i = 0; i < mboxes.length; i++) {
            results.push({
                name: names[i],
                unreadCount: unread[i],
            });
        }
        return results;
    },
};
