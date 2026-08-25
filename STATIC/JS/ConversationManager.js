/**
 * ConversationManager
 * In-memory conversation state management
 * No database, no persistence, ephemeral storage
 */

class ConversationManager {
    constructor() {
        this.conversations = new Map();
        this.currentConversationId = null;
        this.listeners = [];
    }

    /**
     * Create a new conversation
     */
    createConversation() {
        const id = this._generateId();
        const now = new Date();
        
        const conversation = {
            id,
            title: 'New Chat',
            messages: [],
            created_at: now,
            updated_at: now,
            pinned: false
        };

        this.conversations.set(id, conversation);
        this.currentConversationId = id;
        this._notify();
        return id;
    }

    /**
     * Get current conversation
     */
    getCurrentConversation() {
        if (!this.currentConversationId) return null;
        return this.conversations.get(this.currentConversationId);
    }

    /**
     * Open/switch to a conversation
     */
    openConversation(id) {
        if (!this.conversations.has(id)) return false;
        this.currentConversationId = id;
        this._notify();
        return true;
    }

    /**
     * Add message to current conversation
     */
    addMessage(role, content, model = 'Sonet') {
        const conv = this.getCurrentConversation();
        if (!conv) return false;

        conv.messages.push({
            role,
            content,
            model,
            timestamp: new Date()
        });

        conv.updated_at = new Date();

        // Auto-generate title from first user message
        if (conv.title === 'New Chat' && role === 'user' && conv.messages.length === 1) {
            conv.title = this._generateTitleFromMessage(content);
        }

        this._notify();
        return true;
    }

    /**
     * Get messages from current conversation
     */
    getMessages() {
        const conv = this.getCurrentConversation();
        return conv ? conv.messages : [];
    }

    /**
     * Rename a conversation
     */
    renameConversation(id, newTitle) {
        if (!this.conversations.has(id)) return false;
        
        const conv = this.conversations.get(id);
        conv.title = newTitle.trim() || 'Untitled Chat';
        conv.updated_at = new Date();
        
        this._notify();
        return true;
    }

    /**
     * Delete a conversation
     */
    deleteConversation(id) {
        if (!this.conversations.has(id)) return false;

        this.conversations.delete(id);

        // If deleted conversation is current, switch to most recent
        if (this.currentConversationId === id) {
            const remaining = Array.from(this.conversations.values())
                .sort((a, b) => b.updated_at - a.updated_at);
            this.currentConversationId = remaining.length > 0 ? remaining[0].id : null;
        }

        this._notify();
        return true;
    }

    /**
     * Toggle pin status
     */
    togglePin(id) {
        if (!this.conversations.has(id)) return false;
        
        const conv = this.conversations.get(id);
        conv.pinned = !conv.pinned;
        conv.updated_at = new Date();
        
        this._notify();
        return true;
    }

    /**
     * Search conversations by title or message content
     */
    searchConversations(query) {
        const q = query.toLowerCase();
        return Array.from(this.conversations.values()).filter(conv => 
            conv.title.toLowerCase().includes(q) ||
            conv.messages.some(msg => msg.content.toLowerCase().includes(q))
        );
    }

    /**
     * Get all conversations grouped by date
     */
    getGroupedConversations() {
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today.getTime() - 86400000);
        const twoDaysAgo = new Date(yesterday.getTime() - 86400000);

        const all = Array.from(this.conversations.values());
        
        // Separate pinned and unpinned
        const pinned = all.filter(c => c.pinned)
            .sort((a, b) => b.updated_at - a.updated_at);

        const unpinned = all.filter(c => !c.pinned)
            .sort((a, b) => b.updated_at - a.updated_at);

        // Group unpinned by date
        const grouped = { pinned: [], today: [], yesterday: [], older: [] };

        grouped.pinned = pinned;

        unpinned.forEach(conv => {
            const convDate = new Date(
                conv.updated_at.getFullYear(),
                conv.updated_at.getMonth(),
                conv.updated_at.getDate()
            );

            if (convDate.getTime() === today.getTime()) {
                grouped.today.push(conv);
            } else if (convDate.getTime() === yesterday.getTime()) {
                grouped.yesterday.push(conv);
            } else {
                grouped.older.push(conv);
            }
        });

        return grouped;
    }

    /**
     * Get all conversations count
     */
    getCount() {
        return this.conversations.size;
    }

    /**
     * Clear all conversations
     */
    clearAll() {
        this.conversations.clear();
        this.currentConversationId = null;
        this._notify();
    }

    /**
     * Subscribe to changes
     */
    subscribe(callback) {
        this.listeners.push(callback);
        return () => {
            this.listeners = this.listeners.filter(l => l !== callback);
        };
    }

    /**
     * Notify all listeners of state change
     */
    _notify() {
        this.listeners.forEach(callback => callback());
    }

    /**
     * Generate unique ID
     */
    _generateId() {
        return `conv_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    /**
     * Generate title from first message (first 50 chars or first sentence)
     */
    _generateTitleFromMessage(content) {
        let title = content.trim();
        
        // Get first sentence or first 50 chars
        const sentences = title.match(/[^.!?]+[.!?]+/);
        if (sentences) {
            title = sentences[0].trim();
        } else if (title.length > 50) {
            title = title.substring(0, 50).trim() + '...';
        }

        // Remove markdown and special chars for cleaner title
        title = title
            .replace(/\*\*/g, '')
            .replace(/`/g, '')
            .replace(/\n/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();

        return title || 'New Chat';
    }
}

// Export for use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ConversationManager;
}