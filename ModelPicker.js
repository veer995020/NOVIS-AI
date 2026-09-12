/**
 * ModelPicker — Optimized
 * Compact model selection component for chat interface
 * 
 * Optimizations:
 * - Single style injection per app
 * - Efficient DOM caching
 * - Event delegation
 * - Minimal re-renders
 * - No global picker registry
 */

class ModelPicker {
    // Static style injection (once per page)
    static _styleInjected = false;
    static _injectStyles() {
        if (ModelPicker._styleInjected) return;
        
        const style = document.createElement('style');
        style.textContent = `
            .model-picker { position: relative; }
            .model-picker-trigger {
                display: flex; align-items: center; gap: 8px; padding: 8px 12px;
                background: var(--bg-2); border: 1px solid var(--line);
                border-radius: var(--radius-s); color: var(--ink-0);
                font-weight: 600; font-size: 0.9rem; cursor: pointer;
                transition: all var(--fast); min-width: 140px; justify-content: space-between;
            }
            .model-picker-trigger:hover { border-color: var(--cyan); }
            .model-picker-trigger.open { border-color: var(--cyan); background: var(--bg-1); }
            .model-icon { font-size: 1rem; }
            .model-name { flex: 1; text-align: left; }
            .picker-arrow {
                font-size: 0.7rem; opacity: 0.6; transition: transform var(--fast);
            }
            .model-picker-trigger.open .picker-arrow { transform: rotate(180deg); }
            .model-picker-menu {
                position: absolute; top: calc(100% + 4px); left: 0;
                background: var(--bg-1); border: 1px solid var(--line);
                border-radius: var(--radius-m); box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
                z-index: 100; min-width: 220px; max-height: 400px; overflow-y: auto;
                animation: slideDown var(--fast) ease-out;
            }
            @keyframes slideDown {
                from { opacity: 0; transform: translateY(-8px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .menu-section { padding: var(--sp-2) 0; }
            .menu-label {
                font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
                color: var(--ink-2); letter-spacing: 0.05em;
                padding: 0 var(--sp-3); margin-bottom: var(--sp-2);
            }
            .menu-item {
                display: flex; align-items: center; gap: var(--sp-2);
                padding: 10px var(--sp-3); cursor: pointer; transition: background var(--fast);
            }
            .menu-item:hover { background: var(--bg-2); }
            .menu-item.active {
                background: var(--bg-2); border-left: 2px solid var(--cyan);
                padding-left: calc(var(--sp-3) - 2px);
            }
            .menu-item-icon { font-size: 1.1rem; min-width: 24px; text-align: center; }
            .menu-item-content { flex: 1; }
            .menu-item-name { font-weight: 500; font-size: 0.9rem; color: var(--ink-0); }
            .menu-item-meta { font-size: 0.75rem; color: var(--ink-2); margin-top: 2px; }
            .menu-item-check { color: var(--cyan); font-weight: 700; font-size: 1rem; }
            .menu-divider { height: 1px; background: var(--line); margin: 0; }
            ::-webkit-scrollbar { width: 6px; }
            ::-webkit-scrollbar-track { background: transparent; }
            ::-webkit-scrollbar-thumb { background: var(--line); border-radius: 3px; }
            ::-webkit-scrollbar-thumb:hover { background: var(--ink-2); }
        `;
        document.head.appendChild(style);
        ModelPicker._styleInjected = true;
    }

    constructor(store, containerId) {
        this.store = store;
        this.containerId = containerId;
        this.isOpen = false;
        
        // Cache DOM elements
        this.container = null;
        this.trigger = null;
        this.menu = null;
        this.nameEl = null;
        this.iconEl = null;
        
        // Inject styles once
        ModelPicker._injectStyles();
        
        // Subscribe to store changes
        this.unsubscribe = store.subscribe(() => {
            this.updateActiveModel();
        });

        // Bind handler for document click
        this._handleDocumentClick = (e) => {
            if (this.isOpen && this.container && !this.container.contains(e.target)) {
                this.close();
            }
        };
    }

    render() {
        this.container = document.getElementById(this.containerId);
        if (!this.container) return;

        const html = `
            <div class="model-picker">
                <button class="model-picker-trigger">
                    <span class="model-icon"></span>
                    <span class="model-name"></span>
                    <span class="picker-arrow">▼</span>
                </button>
                <div class="model-picker-menu" style="display: none;">
                    <div class="menu-section">
                        <div class="menu-label">Models</div>
                        <div class="model-list"></div>
                    </div>
                    <div class="menu-divider"></div>
                    <div class="menu-section">
                        <div class="menu-label">Modes</div>
                        <div class="mode-list"></div>
                    </div>
                </div>
            </div>
        `;

        this.container.innerHTML = html;

        // Cache DOM references
        this.trigger = this.container.querySelector('.model-picker-trigger');
        this.menu = this.container.querySelector('.model-picker-menu');
        this.nameEl = this.container.querySelector('.model-name');
        this.iconEl = this.container.querySelector('.model-icon');
        const modelListEl = this.container.querySelector('.model-list');
        const modeListEl = this.container.querySelector('.mode-list');

        // Render items once
        modelListEl.innerHTML = this._renderModelList();
        modeListEl.innerHTML = this._renderModeList();

        // Setup event handlers
        this.trigger.addEventListener('click', () => this.toggle());
        
        // Event delegation for menu items
        this.menu.addEventListener('click', (e) => {
            const modelBtn = e.target.closest('[data-model-id]');
            if (modelBtn) {
                this.selectModel(modelBtn.dataset.modelId);
                return;
            }
            
            const modeBtn = e.target.closest('[data-mode-id]');
            if (modeBtn) {
                this.selectMode(modeBtn.dataset.modeId);
            }
        });

        // Document click handler
        document.addEventListener('click', this._handleDocumentClick, { passive: true });

        this.updateActiveModel();
    }

    _renderModelList() {
        return this.store.getModels()
            .map(model => `
                <div class="menu-item ${model.id === this.store.getModel().id ? 'active' : ''}" data-model-id="${model.id}">
                    <div class="menu-item-icon">${model.icon}</div>
                    <div class="menu-item-content">
                        <div class="menu-item-name">${model.name}</div>
                        <div class="menu-item-meta">${model.tier}</div>
                    </div>
                    ${model.id === this.store.getModel().id ? '<div class="menu-item-check">✓</div>' : ''}
                </div>
            `)
            .join('');
    }

    _renderModeList() {
        return this.store.getModes()
            .map(mode => `
                <div class="menu-item ${mode.id === this.store.getMode().id ? 'active' : ''}" data-mode-id="${mode.id}">
                    <div class="menu-item-icon">${mode.icon}</div>
                    <div class="menu-item-content">
                        <div class="menu-item-name">${mode.name}</div>
                    </div>
                    ${mode.id === this.store.getMode().id ? '<div class="menu-item-check">✓</div>' : ''}
                </div>
            `)
            .join('');
    }

    updateActiveModel() {
        if (!this.nameEl || !this.iconEl) return;
        
        const model = this.store.getModel();
        this.nameEl.textContent = model.name;
        this.iconEl.textContent = model.icon;

        // Update active states
        if (this.container) {
            this.container.querySelectorAll('[data-model-id]').forEach(el => {
                el.classList.toggle('active', el.dataset.modelId === model.id);
            });
        }
    }

    selectModel(modelId) {
        this.store.setModel(modelId);
        this.close();
    }

    selectMode(modeId) {
        this.store.setMode(modeId);
        this.close();
    }

    toggle() {
        if (this.isOpen) this.close();
        else this.open();
    }

    open() {
        if (!this.trigger || !this.menu) return;
        this.trigger.classList.add('open');
        this.menu.style.display = 'block';
        this.isOpen = true;
    }

    close() {
        if (!this.trigger || !this.menu) return;
        this.trigger.classList.remove('open');
        this.menu.style.display = 'none';
        this.isOpen = false;
    }

    destroy() {
        this.unsubscribe();
        document.removeEventListener('click', this._handleDocumentClick);
        this.container = null;
        this.trigger = null;
        this.menu = null;
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = ModelPicker;
}