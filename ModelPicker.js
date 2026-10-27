/**
 * ModelPicker — compact model and mode selector for the chat header.
 *
 * Classic script (no ES module syntax). Requires StateStore.js loaded first.
 *
 * Role: a view. It reads the current model/mode from the store and renders them.
 * It never writes to the store directly. Selections are reported through
 * onSelectModel / onSelectMode so the app can run its own side effects
 * (toasts, right-panel sync, placeholder updates) in one place.
 *
 * Usage:
 *   const picker = new ModelPicker(store, "modelPicker", {
 *     onSelectModel: id => setModel(id),
 *     onSelectMode:  id => setMode(id)
 *   });
 *   picker.render();
 */

class ModelPicker {
  static _stylesInjected = false;

  static _injectStyles() {
    if (ModelPicker._stylesInjected) return;
    ModelPicker._stylesInjected = true;

    const style = document.createElement("style");
    style.id = "model-picker-styles";
    style.textContent = `
      .model-picker { position: relative; display: inline-flex; min-width: 0; }
      .model-picker-trigger { max-width: 100%; }
      .model-picker-trigger[aria-expanded="true"] { border-color: rgba(34,211,238,.45); }
      .mp-menu {
        position: absolute; top: calc(100% + 8px); left: 0; z-index: 90;
        min-width: 250px; max-height: min(460px, 70vh); overflow-y: auto;
        padding: 6px; border-radius: 16px;
        border: 1px solid var(--border-2, rgba(255,255,255,.16));
        background: rgba(14, 14, 27, .94);
        box-shadow: var(--shadow-2, 0 18px 55px rgba(0,0,0,.34));
        backdrop-filter: blur(20px);
        animation: mp-in 160ms ease-out;
      }
      @keyframes mp-in {
        from { opacity: 0; transform: translateY(-6px); }
        to   { opacity: 1; transform: none; }
      }
      .mp-section + .mp-section { margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border, rgba(255,255,255,.085)); }
      .mp-label {
        padding: 6px 10px 4px; color: var(--text-3, #5d5d77);
        font-size: 11px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
      }
      .mp-item {
        display: flex; align-items: center; gap: 10px; width: 100%;
        padding: 10px; border-radius: 11px; color: var(--text-1, #c8c8dd);
        text-align: left; font-size: 13.5px;
        transition: background var(--transition, 180ms ease), color var(--transition, 180ms ease);
      }
      .mp-item:hover, .mp-item:focus-visible { background: var(--glass, rgba(255,255,255,.045)); color: var(--text-0, #f5f5ff); }
      .mp-item.active { background: var(--brand-soft, rgba(99,102,241,.18)); color: var(--text-0, #f5f5ff); }
      .mp-item-icon { width: 22px; text-align: center; flex: none; }
      .mp-item-body { flex: 1; min-width: 0; display: grid; gap: 2px; }
      .mp-item-name { font-weight: 600; }
      .mp-item-meta { color: var(--text-2, #8f8fa8); font-size: 11.5px; }
      .mp-item-check { color: var(--cyan, #22d3ee); font-weight: 700; flex: none; }
    `;
    document.head.appendChild(style);
  }

  constructor(store, containerId, handlers = {}) {
    this.store = store;
    this.containerId = containerId;
    this.onSelectModel = handlers.onSelectModel || (id => store.setModel(id));
    this.onSelectMode = handlers.onSelectMode || (id => store.setMode(id));

    this.container = null;
    this.trigger = null;
    this.menu = null;
    this.nameEl = null;
    this.iconEl = null;
    this.modelList = null;
    this.modeList = null;
    this.isOpen = false;

    this._activeModelId = null;
    this._activeModeId = null;
    this._bound = false;

    ModelPicker._injectStyles();

    // Store changes only refresh the highlighted items; no full re-render.
    this._unsubscribe = store.subscribe(() => this.updateActiveModel());

    this._onDocumentClick = (event) => {
      if (this.isOpen && this.container && !this.container.contains(event.target)) this.close();
    };
    this._onContainerClick = (event) => this._onClick(event);
    this._onKeydown = (event) => {
      if (event.key === "Escape" && this.isOpen) {
        this.close();
        this.trigger?.focus();
      }
    };
    document.addEventListener("click", this._onDocumentClick);
    document.addEventListener("keydown", this._onKeydown);
  }

  /** Escape text for safe insertion into HTML. */
  static _esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /** @returns {boolean} true if the mount point was found and rendered */
  render() {
    this.container = document.getElementById(this.containerId);
    if (!this.container) return false;

    this.container.innerHTML = `
      <div class="model-picker">
        <button type="button" class="model-picker-trigger chip chip-model" aria-haspopup="menu" aria-expanded="false"
                title="Change model" aria-label="Change model">
          <span class="model-picker-icon" aria-hidden="true"></span>
          <span class="model-picker-name"></span>
          <svg class="icon chevron" aria-hidden="true"><use href="#i-chevron-down"></use></svg>
        </button>
        <div class="mp-menu" role="menu" hidden>
          <div class="mp-section">
            <div class="mp-label">Models</div>
            <div class="mp-model-list"></div>
          </div>
          <div class="mp-section">
            <div class="mp-label">Modes</div>
            <div class="mp-mode-list"></div>
          </div>
        </div>
      </div>`;

    this.trigger = this.container.querySelector(".model-picker-trigger");
    this.menu = this.container.querySelector(".mp-menu");
    this.nameEl = this.container.querySelector(".model-picker-name");
    this.iconEl = this.container.querySelector(".model-picker-icon");
    this.modelList = this.container.querySelector(".mp-model-list");
    this.modeList = this.container.querySelector(".mp-mode-list");

    this.modelList.innerHTML = this._renderItems(this.store.getModels(), "model-id", m => m.id, m => m.icon, m => m.name, m => m.tier);
    this.modeList.innerHTML = this._renderItems(this.store.getModes(), "mode-id", m => m.id, m => m.icon, m => m.name, () => "");

    // Delegated handlers live on the container, which persists across renders.
    if (!this._bound) {
      this.container.addEventListener("click", this._onContainerClick);
      this._bound = true;
    }

    this._activeModelId = null;
    this._activeModeId = null;
    this.updateActiveModel();
    return true;
  }

  _renderItems(items, attr, idOf, iconOf, nameOf, metaOf) {
    return items.map(item => {
      const meta = metaOf(item);
      return `
        <button type="button" class="mp-item" role="menuitemradio" aria-checked="false" data-${attr}="${ModelPicker._esc(idOf(item))}">
          <span class="mp-item-icon" aria-hidden="true">${ModelPicker._esc(iconOf(item) || "•")}</span>
          <span class="mp-item-body">
            <span class="mp-item-name">${ModelPicker._esc(nameOf(item))}</span>
            ${meta ? `<span class="mp-item-meta">${ModelPicker._esc(meta)}</span>` : ""}
          </span>
          <span class="mp-item-check" aria-hidden="true"></span>
        </button>`;
    }).join("");
  }

  _onClick(event) {
    if (event.target.closest(".model-picker-trigger")) {
      this.toggle();
      return;
    }
    const modelItem = event.target.closest("[data-model-id]");
    if (modelItem) {
      this.selectModel(modelItem.dataset.modelId);
      return;
    }
    const modeItem = event.target.closest("[data-mode-id]");
    if (modeItem) this.selectMode(modeItem.dataset.modeId);
  }

  /** Refresh the trigger label and item highlights. Cheap; runs on every store change. */
  updateActiveModel() {
    if (!this.trigger) return;

    const model = this.store.getModel();
    const mode = this.store.getMode();

    if (model && this._activeModelId !== model.id) {
      this._activeModelId = model.id;
      this.nameEl.textContent = model.name;
      this.iconEl.textContent = model.icon || "";
      this._markActive(this.modelList, "data-model-id", model.id);
    }
    if (mode && this._activeModeId !== mode.id) {
      this._activeModeId = mode.id;
      this._markActive(this.modeList, "data-mode-id", mode.id);
    }
  }

  _markActive(root, attr, activeId) {
    root.querySelectorAll(`[${attr}]`).forEach(el => {
      const on = el.getAttribute(attr) === activeId;
      el.classList.toggle("active", on);
      el.setAttribute("aria-checked", String(on));
      el.querySelector(".mp-item-check").textContent = on ? "✓" : "";
    });
  }

  selectModel(modelId) {
    this.close();
    this.onSelectModel(modelId);
  }

  selectMode(modeId) {
    this.close();
    this.onSelectMode(modeId);
  }

  toggle() {
    if (this.isOpen) this.close();
    else this.open();
  }

  open() {
    if (!this.trigger) return;
    this.menu.hidden = false;
    this.trigger.setAttribute("aria-expanded", "true");
    this.trigger.classList.add("open");
    this.isOpen = true;
    this.menu.querySelector(".mp-item.active")?.focus({ preventScroll: true });
  }

  close() {
    if (!this.trigger || !this.isOpen) return;
    this.menu.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
    this.trigger.classList.remove("open");
    this.isOpen = false;
  }

  destroy() {
    this._unsubscribe();
    document.removeEventListener("click", this._onDocumentClick);
    document.removeEventListener("keydown", this._onKeydown);
    if (this.container) {
      this.container.removeEventListener("click", this._onContainerClick);
      this.container.innerHTML = "";
    }
    this.container = this.trigger = this.menu = null;
    this.nameEl = this.iconEl = this.modelList = this.modeList = null;
    this._bound = false;
    this.isOpen = false;
  }
}

// Node / test harness support. Harmless in the browser.
if (typeof module !== "undefined" && module.exports) {
  module.exports = ModelPicker;
}