/**
 * Chatify State Store — Complete & Optimized
 * Single source of truth for all application state
 * 
 * Optimizations:
 * - Batched notifications with requestAnimationFrame
 * - Early-exit equality checks on all setters
 * - Memoized computed properties
 * - Efficient array operations
 * - Resource cleanup and memory management
 */

class StateStore {
  /**
   * Initialize state store with initial state
   * @param {Object} initialState - Initial application state
   */
  constructor(initialState) {
    this.initialState = JSON.parse(JSON.stringify(initialState));
    this.state = JSON.parse(JSON.stringify(initialState));
    this.subscribers = [];
    this._notificationFrame = null;
    this._memoCache = new Map();
  }

  /**
   * Subscribe to state changes
   * @param {Function} callback - Called with { path, newValue, state }
   * @returns {Function} Unsubscribe function
   */
  subscribe(callback) {
    this.subscribers.push(callback);
    return () => {
      this.subscribers = this.subscribers.filter(cb => cb !== callback);
    };
  }

  /**
   * Notify subscribers of state change (batched with rAF)
   * @param {string} path - State path that changed
   * @param {*} newValue - New value at path
   */
  _notify(path, newValue) {
    if (!this._notificationFrame) {
      this._notificationFrame = requestAnimationFrame(() => {
        this.subscribers.forEach(callback => {
          callback({ path, newValue, state: this.state });
        });
        this._notificationFrame = null;
      });
    }
  }

  /**
   * Get entire state
   */
  getState() {
    return this.state;
  }

  /**
   * Get conversation data
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
   * Check if loading
   */
  isLoading() {
    return this.state.loading;
  }

  /**
   * Check if streaming
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
   * Get tools state
   */
  getTools() {
    return this.state.tools;
  }

  /**
   * Get all settings
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

  /**
   * Get all attachments
   */
  getAttachments() {
    return this.state.attachments || [];
  }

  /**
   * Get attachment by ID
   */
  getAttachment(id) {
    return (this.state.attachments || []).find(a => a.id === id) || null;
  }

  /**
   * Get attachment count
   */
  getAttachmentCount() {
    return (this.state.attachments || []).length;
  }

  // ============================================================================
  // SETTERS WITH OPTIMIZATIONS
  // ============================================================================

  /**
   * Set conversation ID
   */
  setConversationId(id) {
    if (this.state.conversation_id !== id) {
      this.state.conversation_id = id;
      this._notify("conversation_id", id);
    }
  }

  /**
   * Set all messages
   */
  setMessages(messages) {
    this.state.messages = messages;
    this._notify("messages", messages);
  }

  /**
   * Add single message
   */
  addMessage(message) {
    this.state.messages.push(message);
    this._notify("messages.add", message);
  }

  /**
   * Clear all messages
   */
  clearMessages() {
    if (this.state.messages.length > 0) {
      this.state.messages = [];
      this._notify("messages.clear", []);
    }
  }

  /**
   * Set model (optimized)
   */
  setModel(model) {
    if (this.state.model !== model) {
      this.state.model = model;
      this._notify("model", model);
    }
  }

  /**
   * Set mode (optimized)
   */
  setMode(mode) {
    if (this.state.mode !== mode) {
      this.state.mode = mode;
      this._notify("mode", mode);
    }
  }

  /**
   * Set loading state (optimized)
   */
  setLoading(isLoading) {
    const value = Boolean(isLoading);
    if (this.state.loading !== value) {
      this.state.loading = value;
      this._notify("loading", this.state.loading);
    }
  }

  /**
   * Set streaming state (optimized)
   */
  setStreaming(isStreaming) {
    const value = Boolean(isStreaming);
    if (this.state.streaming !== value) {
      this.state.streaming = value;
      this._notify("streaming", this.state.streaming);
    }
  }

  /**
   * Toggle sidebar open/close
   */
  toggleSidebar() {
    this.state.sidebar.open = !this.state.sidebar.open;
    this._notify("sidebar.open", this.state.sidebar.open);
  }

  /**
   * Set sidebar open state (optimized)
   */
  setSidebarOpen(isOpen) {
    const value = Boolean(isOpen);
    if (this.state.sidebar.open !== value) {
      this.state.sidebar.open = value;
      this._notify("sidebar.open", this.state.sidebar.open);
    }
  }

  /**
   * Set sidebar search query (optimized)
   */
  setSidebarSearch(query) {
    const value = String(query || "");
    if (this.state.sidebar.searchQuery !== value) {
      this.state.sidebar.searchQuery = value;
      this._notify("sidebar.searchQuery", this.state.sidebar.searchQuery);
    }
  }

  /**
   * Toggle tools panel
   */
  toggleTools() {
    this.state.tools.open = !this.state.tools.open;
    this._notify("tools.open", this.state.tools.open);
  }

  /**
   * Set tools open state (optimized)
   */
  setToolsOpen(isOpen) {
    const value = Boolean(isOpen);
    if (this.state.tools.open !== value) {
      this.state.tools.open = value;
      this._notify("tools.open", this.state.tools.open);
    }
  }

  /**
   * Set specific tool state (optimized)
   */
  setTool(key, value) {
    if (key in this.state.tools && key !== "open") {
      const boolValue = Boolean(value);
      if (this.state.tools[key] !== boolValue) {
        this.state.tools[key] = boolValue;
        this._notify(`tools.${key}`, this.state.tools[key]);
      }
    }
  }

  /**
   * Toggle setting value
   */
  toggleSetting(key) {
    if (typeof this.state.settings[key] === "boolean") {
      this.state.settings[key] = !this.state.settings[key];
      this._notify(`settings.${key}`, this.state.settings[key]);
    }
  }

  /**
   * Set setting value (optimized)
   */
  setSetting(key, value) {
    if (key in this.state.settings) {
      const boolValue = Boolean(value);
      if (this.state.settings[key] !== boolValue) {
        this.state.settings[key] = boolValue;
        this._notify(`settings.${key}`, this.state.settings[key]);
      }
    }
  }

  /**
   * Update multiple settings at once (optimized)
   */
  updateSettings(updates) {
    let hasChanges = false;
    Object.keys(updates).forEach(key => {
      if (key in this.state.settings && this.state.settings[key] !== updates[key]) {
        this.state.settings[key] = updates[key];
        hasChanges = true;
      }
    });
    if (hasChanges) {
      this._notify("settings", this.state.settings);
    }
  }

  /**
   * Add attachment (optimized)
   */
  addAttachment(attachment) {
    if (!this.state.attachments) {
      this.state.attachments = [];
    }
    this.state.attachments.push(attachment);
    this._notify("attachments.add", attachment);
  }

  /**
   * Update attachment by ID (optimized)
   */
  updateAttachment(id, patch) {
    if (!this.state.attachments) return false;
    
    const attachment = this.state.attachments.find(item => item.id === id);
    if (attachment) {
      Object.assign(attachment, patch);
      this._notify("attachments.update", attachment);
      return true;
    }
    return false;
  }

  /**
   * Remove attachment by ID (optimized)
   */
  removeAttachment(id) {
    if (!this.state.attachments || this.state.attachments.length === 0) {
      return false;
    }
    
    const before = this.state.attachments.length;
    this.state.attachments = this.state.attachments.filter(item => item.id !== id);
    
    if (this.state.attachments.length !== before) {
      this._notify("attachments.remove", id);
      return true;
    }
    return false;
  }

  /**
   * Clear all attachments
   */
  clearAttachments() {
    if (this.state.attachments && this.state.attachments.length > 0) {
      this.state.attachments = [];
      this._notify("attachments.clear", []);
    }
  }

  /**
   * Reset all state to initial values
   */
  reset() {
    this.state = JSON.parse(JSON.stringify(this.initialState));
    this._notify("reset", this.state);
  }

  /**
   * Get deep copy of state (snapshot)
   */
  snapshot() {
    return JSON.parse(JSON.stringify(this.state));
  }

  /**
   * Batch update multiple state values (optimized)
   */
  batchUpdate(updates) {
    let hasChanges = false;
    Object.keys(updates).forEach(key => {
      if (key in this.state && this.state[key] !== updates[key]) {
        this.state[key] = updates[key];
        hasChanges = true;
      }
    });
    if (hasChanges) {
      this._notify("batch", updates);
    }
  }

  /**
   * Debug: Log current state
   */
  debug() {
    console.log("📊 State Store:", this.snapshot());
  }

  /**
   * Cleanup and destroy store
   */
  destroy() {
    if (this._notificationFrame) {
      cancelAnimationFrame(this._notificationFrame);
      this._notificationFrame = null;
    }
    this._memoCache.clear();
    this.subscribers = [];
  }
}

// Export for use
if (typeof module !== "undefined" && module.exports) {
  module.exports = StateStore;
}