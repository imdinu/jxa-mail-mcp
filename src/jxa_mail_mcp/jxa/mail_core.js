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

    // ========== Fuzzy Search Utilities ==========

    /**
     * Extract trigrams (3-character sequences) from a string.
     * @param {string} str - Input string
     * @returns {Set<string>} Set of trigrams
     */
    trigrams(str) {
        const s = (str || "").toLowerCase().trim();
        const result = new Set();
        if (s.length < 3) {
            result.add(s);
            return result;
        }
        for (let i = 0; i <= s.length - 3; i++) {
            result.add(s.substring(i, i + 3));
        }
        return result;
    },

    /**
     * Calculate trigram similarity (Jaccard index).
     * @param {Set<string>} set1 - First trigram set
     * @param {Set<string>} set2 - Second trigram set
     * @returns {number} Similarity score 0-1
     */
    trigramSimilarity(set1, set2) {
        if (set1.size === 0 && set2.size === 0) return 1;
        if (set1.size === 0 || set2.size === 0) return 0;
        let intersection = 0;
        for (const t of set1) {
            if (set2.has(t)) intersection++;
        }
        const union = set1.size + set2.size - intersection;
        return intersection / union;
    },

    /**
     * Calculate Levenshtein edit distance between two strings.
     * @param {string} a - First string
     * @param {string} b - Second string
     * @returns {number} Edit distance
     */
    levenshtein(a, b) {
        const s1 = (a || "").toLowerCase();
        const s2 = (b || "").toLowerCase();
        if (s1 === s2) return 0;
        if (s1.length === 0) return s2.length;
        if (s2.length === 0) return s1.length;

        // Use two rows instead of full matrix for memory efficiency
        let prev = [];
        let curr = [];
        for (let j = 0; j <= s2.length; j++) prev[j] = j;

        for (let i = 1; i <= s1.length; i++) {
            curr[0] = i;
            for (let j = 1; j <= s2.length; j++) {
                const cost = s1[i - 1] === s2[j - 1] ? 0 : 1;
                curr[j] = Math.min(
                    prev[j] + 1,      // deletion
                    curr[j - 1] + 1,  // insertion
                    prev[j - 1] + cost // substitution
                );
            }
            [prev, curr] = [curr, prev];
        }
        return prev[s2.length];
    },

    /**
     * Calculate normalized Levenshtein similarity (0-1).
     * @param {string} a - First string
     * @param {string} b - Second string
     * @returns {number} Similarity score 0-1
     */
    levenshteinSimilarity(a, b) {
        const maxLen = Math.max((a || "").length, (b || "").length);
        if (maxLen === 0) return 1;
        return 1 - this.levenshtein(a, b) / maxLen;
    },

    /**
     * Fuzzy match a query against text.
     * Uses trigrams for fast candidate filtering, Levenshtein for scoring.
     * @param {string} query - Search query
     * @param {string} text - Text to search in
     * @param {number} trigramThreshold - Min trigram similarity (default 0.2)
     * @returns {object|null} {score, matched} or null if no match
     */
    fuzzyMatch(query, text, trigramThreshold = 0.2) {
        const q = (query || "").toLowerCase().trim();
        const t = (text || "").toLowerCase();

        if (!q || !t) return null;

        // Exact substring match is best
        if (t.includes(q)) {
            return { score: 1.0, matched: q };
        }

        // Split text into words for word-level matching
        const words = t.split(/\s+/);
        const queryTrigrams = this.trigrams(q);

        let bestScore = 0;
        let bestMatch = null;

        for (const word of words) {
            // Quick trigram filter
            const wordTrigrams = this.trigrams(word);
            const trigramSim = this.trigramSimilarity(
                queryTrigrams,
                wordTrigrams
            );

            if (trigramSim >= trigramThreshold) {
                // Candidate found, calculate precise score
                const levSim = this.levenshteinSimilarity(q, word);
                if (levSim > bestScore) {
                    bestScore = levSim;
                    bestMatch = word;
                }
            }
        }

        // Also check multi-word phrases (sliding window)
        const queryWords = q.split(/\s+/).length;
        if (queryWords > 1 && words.length >= queryWords) {
            for (let i = 0; i <= words.length - queryWords; i++) {
                const phrase = words.slice(i, i + queryWords).join(" ");
                const phraseTrigrams = this.trigrams(phrase);
                const trigramSim = this.trigramSimilarity(
                    queryTrigrams,
                    phraseTrigrams
                );

                if (trigramSim >= trigramThreshold) {
                    const levSim = this.levenshteinSimilarity(q, phrase);
                    if (levSim > bestScore) {
                        bestScore = levSim;
                        bestMatch = phrase;
                    }
                }
            }
        }

        if (bestScore > 0) {
            return { score: bestScore, matched: bestMatch };
        }
        return null;
    },
};
