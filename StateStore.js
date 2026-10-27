/**
 * Novis / Novis — application state store
 *
 * Single source of truth for UI state. Classic script (no ES module syntax).
 *
 * Catalog ownership: the store resolves model and mode ids to catalog entries,
 * so callers may pass either an id ("sonet") or an entry ({ id: "sonet", ... }).
 * Unknown ids fall back to the configured defaults.
 *
 * Notifications: subscribers are called at most once per animation frame with
 * { changes, path, newValue, state }. `changes` lists every path touched in that
 * frame; prefer re-reading `state` over relying on a single path.
 *
 * Usage:
 *   const store = new StateStore(buildInitialState(), {
 *     models: MODELS, modes: MODES,
 *     defaultModelId: "rapid", defaultModeId: "general"
 *   });
 */

class StateStore {
  /**
   * @param {Object} initialState  starting state (ids allowed for model/mode)
   * @param {Object} catalog
   *   models, modes             arrays of entries with a unique `id`
   *   defaultModelId/ModeId     ids used when a lookup misses
   */
  constructor(initialState = {}, catalog = {}) {
    this._models = Object.freeze([...(catalog.models || [])]);
    this._modes = Object.freeze([...(catalog.modes || [])]);
    this._modelIndex = new Map(this._models.map(m => [m.id, m]));
    this._modeIndex = new Map(this._modes.map(m => [m.id, m]));
    this._defaultModelId = catalog.defaultModelId;
    this._defaultModeId = catalog.defaultModeId;

    this.subscribers = new Set();
    this._pendingPaths = new Set();
    this._pendingValue = undefined;
    this._pendingPath = null;
    this._frame = null;

    this.initialState = initialState;
    this.state = this._hydrate(StateStore._clone(initialState));
  }

  /* ----------------------------------------------------------------------
     INTERNALS
  ---------------------------------------------------------------------- */

  static _clone(value) {
    return typeof structuredClone === "function"
      ? structuredClone(value)
      : JSON.parse(JSON.stringify(value));
  }

  _hydrate(state) {
    state.model = this.resolveModel(state.model);
    state.mode = this.resolveMode(state.mode);
    return state;
  }

  _lookup(index, fallbackId, value) {
    const id = value && typeof value === "object" ? value.id : value;
    return index.get(id) || index.get(fallbackId) || null;
  }

  _notify(path, value) {
    if (this.subscribers.size === 0) return; // nothing listening: skip the work

    this._pendingPath = path;
    this._pendingValue = value;
    this._pendingPaths.add(path);
    if (this._frame !== null) return; // already scheduled for this frame

    this._frame = StateStore._schedule(() => {
      const changes = Array.from(this._pendingPaths);
      const event = {
        changes,
        path: this._pendingPath,
        newValue: this._pendingValue,
        state: this.state
      };
      this._pendingPaths.clear();
      this._frame = null;
      this.subscribers.forEach(callback => {
        try {
          callback(event);
        } catch (err) {
          console.error("StateStore subscriber failed:", err);
        }
      });
    });
  }

  static _schedule(fn) {
    return typeof requestAnimationFrame === "function"
      ? { id: requestAnimationFrame(fn), cancel: cancelAnimationFrame }
      : { id: setTimeout(fn, 16), cancel: clearTimeout };
  }

  /* ----------------------------------------------------------------------
     SUBSCRIPTION
  ---------------------------------------------------------------------- */

  /** @returns {Function} unsubscribe */
  subscribe(callback) {
    this.subscribers.add(callback);
    return () => this.subscribers.delete(callback);
  }

  destroy() {
    if (this._frame) {
      this._frame.cancel(this._frame.id);
      this._frame = null;
    }
    this._pendingPaths.clear();
    this.subscribers.clear();
  }

  /* ----------------------------------------------------------------------
     CATALOG
  ---------------------------------------------------------------------- */

  getModels() { return this._models; }
  getModes() { return this._modes; }

  /** Resolve an id or entry to a model entry, falling back to the default. */
  resolveModel(value) {
    return this._lookup(this._modelIndex, this._defaultModelId, value);
  }

  /** Resolve an id or entry to a mode entry, falling back to the default. */
  resolveMode(value) {
    return this._lookup(this._modeIndex, this._defaultModeId, value);
  }

  /* ----------------------------------------------------------------------
     READ
  ---------------------------------------------------------------------- */

  getState() { return this.state; }

  /** Deep copy of state, safe to serialize. */
  snapshot() { return StateStore._clone(this.state); }

  getConversationId() { return this.state.conversation_id; }
  getMessages() { return this.state.messages; }
  getModel() { return this.state.model; }
  getMode() { return this.state.mode; }
  isLoading() { return this.state.loading; }
  isStreaming() { return this.state.streaming; }
  getConnection() { return this.state.connection; }
  getSidebar() { return this.state.sidebar; }
  getTools() { return this.state.tools; }
  getSettings() { return this.state.settings; }
  getSetting(key) { return this.state.settings[key]; }
  getAttachments() { return this.state.attachments; }
  getAttachment(id) { return this.state.attachments.find(a => a.id === id) || null; }
  getAttachmentCount() { return this.state.attachments.length; }

  /* ----------------------------------------------------------------------
     WRITE — every setter is a no-op when the value is unchanged
  ---------------------------------------------------------------------- */

  setConversationId(id) {
    if (this.state.conversation_id === id) return false;
    this.state.conversation_id = id;
    this._notify("conversation_id", id);
    return true;
  }

  setMessages(messages) {
    this.state.messages = messages;
    this._notify("messages", messages);
  }

  addMessage(message) {
    this.state.messages.push(message);
    this._notify("messages.add", message);
  }

  clearMessages() {
    if (this.state.messages.length === 0) return false;
    this.state.messages = [];
    this._notify("messages.clear", []);
    return true;
  }

  /** Accepts a model id or entry. Returns true if the model changed. */
  setModel(value) {
    const next = this.resolveModel(value);
    if (!next || this.state.model === next) return false;
    this.state.model = next;
    this._notify("model", next);
    return true;
  }

  /** Accepts a mode id or entry. Returns true if the mode changed. */
  setMode(value) {
    const next = this.resolveMode(value);
    if (!next || this.state.mode === next) return false;
    this.state.mode = next;
    this._notify("mode", next);
    return true;
  }

  setLoading(value) {
    const next = Boolean(value);
    if (this.state.loading === next) return false;
    this.state.loading = next;
    this._notify("loading", next);
    return true;
  }

  setStreaming(value) {
    const next = Boolean(value);
    if (this.state.streaming === next) return false;
    this.state.streaming = next;
    this._notify("streaming", next);
    return true;
  }

  /** @param {"checking"|"online"|"offline"} value */
  setConnection(value) {
    if (this.state.connection === value) return false;
    this.state.connection = value;
    this._notify("connection", value);
    return true;
  }

  setSidebarOpen(value) {
    const next = Boolean(value);
    if (this.state.sidebar.open === next) return false;
    this.state.sidebar.open = next;
    this._notify("sidebar.open", next);
    return true;
  }

  toggleSidebar() { return this.setSidebarOpen(!this.state.sidebar.open); }

  setSidebarSearch(query) {
    const next = String(query || "");
    if (this.state.sidebar.searchQuery === next) return false;
    this.state.sidebar.searchQuery = next;
    this._notify("sidebar.searchQuery", next);
    return true;
  }

  setToolsOpen(value) {
    const next = Boolean(value);
    if (this.state.tools.open === next) return false;
    this.state.tools.open = next;
    this._notify("tools.open", next);
    return true;
  }

  toggleTools() { return this.setToolsOpen(!this.state.tools.open); }

  /** Sets a boolean tool flag. The panel-open flag is not a tool. */
  setTool(key, value) {
    if (key === "open" || !(key in this.state.tools)) return false;
    const next = Boolean(value);
    if (this.state.tools[key] === next) return false;
    this.state.tools[key] = next;
    this._notify(`tools.${key}`, next);
    return true;
  }

  /** Sets a known boolean setting. Unknown keys are ignored. */
  setSetting(key, value) {
    if (!(key in this.state.settings)) return false;
    const next = Boolean(value);
    if (this.state.settings[key] === next) return false;
    this.state.settings[key] = next;
    this._notify(`settings.${key}`, next);
    return true;
  }

  toggleSetting(key) {
    if (typeof this.state.settings[key] !== "boolean") return false;
    return this.setSetting(key, !this.state.settings[key]);
  }

  /** Applies several known settings in one notification. */
  updateSettings(updates) {
    let changed = false;
    for (const [key, value] of Object.entries(updates)) {
      if (!(key in this.state.settings) || this.state.settings[key] === value) continue;
      this.state.settings[key] = value;
      changed = true;
    }
    if (changed) this._notify("settings", this.state.settings);
    return changed;
  }

  addAttachment(attachment) {
    this.state.attachments.push(attachment);
    this._notify("attachments.add", attachment);
  }

  /** Patches an attachment in place. Returns the updated item, or null if missing. */
  updateAttachment(id, patch) {
    const item = this.getAttachment(id);
    if (!item) return null;
    Object.assign(item, patch);
    this._notify("attachments.update", item);
    return item;
  }

  removeAttachment(id) {
    const before = this.state.attachments.length;
    this.state.attachments = this.state.attachments.filter(a => a.id !== id);
    const removed = this.state.attachments.length !== before;
    if (removed) this._notify("attachments.remove", id);
    return removed;
  }

  clearAttachments() {
    if (this.state.attachments.length === 0) return false;
    this.state.attachments = [];
    this._notify("attachments.clear", []);
    return true;
  }

  /** Applies several top-level keys in one notification. Unknown keys are ignored. */
  batchUpdate(updates) {
    let changed = false;
    for (const [key, value] of Object.entries(updates)) {
      if (!(key in this.state) || this.state[key] === value) continue;
      this.state[key] = key === "model" ? this.resolveModel(value)
        : key === "mode" ? this.resolveMode(value)
        : value;
      changed = true;
    }
    if (changed) this._notify("batch", updates);
    return changed;
  }

  /** Restores the initial state. */
  reset() {
    this.state = this._hydrate(StateStore._clone(this.initialState));
    this._notify("reset", this.state);
  }
}

// Node / test harness support. Harmless in the browser.
if (typeof module !== "undefined" && module.exports) {
  module.exports = StateStore;
}