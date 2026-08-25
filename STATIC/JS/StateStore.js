/**
 * Chatify State Store
 * Single source of truth for all application state
 * 
 * Rules:
 * - No duplicate state
 * - Modular updates only via setter methods
 * - Subscribers notified on changes
 * - Lightweight & efficient
 */

class StateStore {
    constructor() {
        // Single source of truth
        this.state = {
            // Conversation
            conversation_id: null,
            messages: [],
            
            // AI Settings
            model: {
                id: 'SONET',
                name: 'Sonet',
                tier: 'Default',
                tags: ['Balanced', 'Productivity', 'Learning'],
                description: 'Balanced performance for everyday tasks.'
            },
            mode: {
                id: 'GENERAL',
                name: 'General',
                icon: '💬',
                description: 'Everyday assistance and general questions.'
            },
            
            // Loading states
            loading: false,
            streaming: false,
            
            // UI State
            sidebar: {
                open: true,
                searchQuery: ''
            },
            tools: {
                open: false
            },
            
            // Settings
            settings: {
                markdown: true,
                codeHighlight: true,
                autoScroll: true,
                streaming: false  // Disabled - local only
            }
        };
        
        // Subscribers for reactive updates
        this.subscribers = [];
    }

    /**
     * Subscribe to state changes
     * @param {Function} callback - Called with changed state
     * @returns {Function} Unsubscribe function
     */
    subscribe(callback) {
        this.subscribers.push(callback);
        return () => {
            this.subscribers = this.subscribers.filter(cb => cb !== callback);
        };
    }

    /**
     * Notify all subscribers of state change
     * @param {string} path - State path that changed (e.g., 'model.id')
     * @param {*} newValue - New value
     */
    _notify(path, newValue) {
        this.subscribers.forEach(callback => {
            callback({ path, newValue, state: this.state });
        });
    }

    // ============================================================================
    // GETTERS - Read-only access to state
    // ============================================================================

    /**
     * Get entire state
     */
    getState() {
        return this.state;
    }

    /**
     * Get conversation state
     */
    getConversation() {
        return {
            id: this.state.conversation_id,
            messages: this.state.messages
        };
    }

    /**
     * Get all messages
     */
    getMessages() {
        return this.state.messages;
    }

    /**
     * Get current conversation ID
     */
    getConversationId() {
        return this.state.conversation_id;
    }

    /**
     * Get current model
     */
    getModel() {
        return this.state.model;
    }

    /**
     * Get current mode
     */
    getMode() {
        return this.state.mode;
    }

    /**
     * Get loading state
     */
    isLoading() {
        return this.state.loading;
    }

    /**
     * Get streaming state
     */
    isStreaming() {
        return this.state.streaming;
    }

    /**
     * Get sidebar state
     */
    getSidebar() {
        return this.state.sidebar;
    }

    /**
     * Get tools panel state
     */
    getTools() {
        return this.state.tools;
    }

    /**
     * Get settings
     */
    getSettings() {
        return this.state.settings;
    }

    /**
     * Get specific setting
     */
    getSetting(key) {
        return this.state.settings[key];
    }

    // ============================================================================
    // SETTERS - Modular updates
    // ============================================================================

    /**
     * Set conversation ID
     */
    setConversationId(id) {
        this.state.conversation_id = id;
        this._notify('conversation_id', id);
    }

    /**
     * Set all messages
     */
    setMessages(messages) {
        this.state.messages = messages;
        this._notify('messages', messages);
    }

    /**
     * Add single message
     */
    addMessage(message) {
        this.state.messages.push(message);
        this._notify('messages.add', message);
    }

    /**
     * Clear all messages
     */
    clearMessages() {
        this.state.messages = [];
        this._notify('messages.clear', []);
    }

    /**
     * Set model
     */
    setModel(model) {
        this.state.model = model;
        this._notify('model', model);
    }

    /**
     * Set mode
     */
    setMode(mode) {
        this.state.mode = mode;
        this._notify('mode', mode);
    }

    /**
     * Set loading state
     */
    setLoading(isLoading) {
        this.state.loading = isLoading;
        this._notify('loading', isLoading);
    }

    /**
     * Set streaming state
     */
    setStreaming(isStreaming) {
        this.state.streaming = isStreaming;
        this._notify('streaming', isStreaming);
    }

    /**
     * Toggle sidebar open/close
     */
    toggleSidebar() {
        this.state.sidebar.open = !this.state.sidebar.open;
        this._notify('sidebar.open', this.state.sidebar.open);
    }

    /**
     * Set sidebar open state
     */
    setSidebarOpen(isOpen) {
        this.state.sidebar.open = isOpen;
        this._notify('sidebar.open', isOpen);
    }

    /**
     * Set sidebar search query
     */
    setSidebarSearch(query) {
        this.state.sidebar.searchQuery = query;
        this._notify('sidebar.searchQuery', query);
    }

    /**
     * Toggle tools panel open/close
     */
    toggleTools() {
        this.state.tools.open = !this.state.tools.open;
        this._notify('tools.open', this.state.tools.open);
    }

    /**
     * Set tools panel open state
     */
    setToolsOpen(isOpen) {
        this.state.tools.open = isOpen;
        this._notify('tools.open', isOpen);
    }

    /**
     * Toggle setting
     */
    toggleSetting(key) {
        if (typeof this.state.settings[key] === 'boolean') {
            this.state.settings[key] = !this.state.settings[key];
            this._notify(`settings.${key}`, this.state.settings[key]);
        }
    }

    /**
     * Set setting
     */
    setSetting(key, value) {
        this.state.settings[key] = value;
        this._notify(`settings.${key}`, value);
    }

    /**
     * Update multiple settings at once
     */
    updateSettings(updates) {
        Object.keys(updates).forEach(key => {
            if (key in this.state.settings) {
                this.state.settings[key] = updates[key];
            }
        });
        this._notify('settings', this.state.settings);
    }

    // ============================================================================
    // UTILITY METHODS
    // ============================================================================

    /**
     * Reset all state to defaults
     */
    reset() {
        this.state.conversation_id = null;
        this.state.messages = [];
        this.state.model = {
            id: 'SONET',
            name: 'Sonet',
            tier: 'Default',
            tags: ['Balanced', 'Productivity', 'Learning'],
            description: 'Balanced performance for everyday tasks.'
        };
        this.state.mode = {
            id: 'GENERAL',
            name: 'General',
            icon: '💬',
            description: 'Everyday assistance and general questions.'
        };
        this.state.loading = false;
        this.state.streaming = false;
        this.state.sidebar.open = true;
        this.state.sidebar.searchQuery = '';
        this.state.tools.open = false;
        this.state.settings = {
            markdown: true,
            codeHighlight: true,
            autoScroll: true,
            streaming: false
        };
        this._notify('reset', this.state);
    }

    /**
     * Get state snapshot (deep copy)
     */
    snapshot() {
        return JSON.parse(JSON.stringify(this.state));
    }

    /**
     * Batch update multiple state values
     */
    batchUpdate(updates) {
        Object.keys(updates).forEach(key => {
            if (key in this.state) {
                this.state[key] = updates[key];
            }
        });
        this._notify('batch', updates);
    }

    /**
     * Debug: Log current state
     */
    debug() {
        console.log('📊 State Store:', this.snapshot());
    }
}

// Export singleton instance
const store = new StateStore();

// For browser console debugging
if (typeof window !== 'undefined') {
    window.__chatifyStore = store;
}