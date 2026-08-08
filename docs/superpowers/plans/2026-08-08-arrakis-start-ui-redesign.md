# Arrakis Start UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current preset grid with the approved dense two-panel interface while preserving every proven install, cancel, resume, workflow, lifecycle, and selective-uninstall behavior.

**Architecture:** Keep the production frontend in the existing vanilla `web/index.html`, `web/styles.css`, and `web/app.js` files. Extend the existing preset loader and `/api/presets` response with authoritative server metadata, and continue using `/api/status` plus `progress.py` as the only live progress channel. Treat the generated Design Canvas export as a visual reference, never as runtime code.

**Tech Stack:** Python 3, `unittest`, `http.server`, vanilla HTML/CSS/JavaScript, JSON preset manifests, local Playwright/Google Chrome for the visual smoke, Git.

## Global Constraints

- User-facing UI text and commit messages are in Portuguese (pt-BR); code identifiers, comments, and technical documentation are in English.
- Do not import `Redesign Arrakis Start/support.js` or the generated `.dc.html` runtime.
- Do not add React, Tailwind, a WebSocket, a second log channel, or client-side persistence.
- Keep `/api/status` as the only browser-facing live status/progress channel.
- Keep cancellation resumable, terminal failure states honest, and installed state server-authoritative.
- `DELETAR` opens the newly styled installed-preset manager and calls the existing selective `/api/uninstall`; it never duplicates shutdown.
- Remove the legacy floating delete button, rounded popup, installed-preset chip section, and their visual classes.
- Use latest Git modification time for committed preset JSON files and filesystem `mtime` only when Git has no timestamp for that file.
- The global progress percentage uses `progress.done / progress.total`, never estimated gigabytes.
- No push. The user tests locally before explicitly authorizing publication.
- Preserve the untracked `Redesign Arrakis Start/` reference folder and never stage it.

---

### Task 1: Replace the production UI as one working vertical slice

**Files:**
- Modify: `web/index.html`
- Modify: `web/styles.css`
- Modify: `web/app.js`
- Create: `tests/ui_smoke.cjs`

**Interfaces:**
- Consumes: the current `GET /api/presets` payload and accepts the optional `pinned`, `size_gb`, and `modified_at` fields produced by Task 2.
- Consumes: existing `GET /api/status` fields, including `progress.stages`, `progress.done`, `progress.total`, `progress.active`, and `progress.recent`.
- Preserves: `POST /api/install`, `/api/cancel`, `/api/restart`, `/api/shutdown`, and `/api/uninstall` request bodies and response handling.
- Produces DOM anchors: `pinned-presets`, `recent-presets`, `queue-list`, `queue-count`, `queue-total`, `progress-summary`, `progress-fill`, `activity-list`, `manage-dialog`, and `manage-list`.

- [ ] **Step 1: Write a failing browser behavior test**

Create the first version of `tests/ui_smoke.cjs` with the server and API-mocking helpers from Step 9. Open the current page in Chromium and assert behavior through the rendered DOM: the three pinned presets are visible, the desktop catalog has three columns, the control panel is 300 px wide, selecting a preset updates the queue and total size, and `DELETAR` opens a square native dialog containing the installed presets. Do not inspect source text or CSS declarations.

- [ ] **Step 2: Run the browser behavior test and verify RED**

Run:

```bash
node tests/ui_smoke.cjs
```

Expected: failure at the first missing behavior because the current page still uses the legacy grid, installed chips, floating delete button, and popup.

- [ ] **Step 3: Replace `web/index.html` with the semantic shell**

Build this hierarchy using real buttons, labels, an input, and a native dialog. Preserve the existing endpoint-facing IDs for status, flags, start, cancel, restart, and shutdown; add the new IDs from the contract test:

```html
<main id="app-shell" class="app-shell">
  <section id="preset-catalog" class="preset-catalog" aria-label="Catálogo de presets">
    <header class="section-bar section-bar--pinned">
      <span class="section-tag">FIXADOS</span>
      <span class="section-hint">os que sempre uso</span>
    </header>
    <div id="pinned-presets" class="pinned-grid"></div>

    <header class="section-bar">
      <span class="section-tag section-tag--muted">RECENTES · MODIFICADO</span>
    </header>
    <div id="recent-presets" class="recent-list"></div>
  </section>

  <aside id="control-panel" class="control-panel" aria-label="Controles do Arrakis Start">
    <section class="status-block" aria-live="polite">
      <span id="status-dot" class="status-dot stopped" aria-hidden="true"></span>
      <strong id="status-text">COMFYUI: VERIFICANDO</strong>
      <span id="status-port" class="status-port"></span>
    </section>

    <label class="flags-block" for="extra-flags-input">
      <span class="control-label">FLAGS EXTRAS</span>
      <input id="extra-flags-input" type="text" placeholder="--disable-xformers">
    </label>

    <section class="queue-block" aria-labelledby="queue-title">
      <h2 id="queue-title" class="control-label">FILA · <span id="queue-count">0</span> PRESETS</h2>
      <div id="queue-list" class="queue-list"></div>
      <div class="queue-total"><span>TOTAL</span><strong id="queue-total">0 GB</strong></div>
      <button id="start-btn" class="primary-action" type="button" disabled>SELECIONE UM PRESET</button>
      <button id="cancel-btn" class="cancel-action" type="button" hidden>CANCELAR INSTALAÇÃO</button>
      <div class="overall-progress" aria-live="polite">
        <div id="progress-summary" class="progress-summary">AGUARDANDO</div>
        <div class="progress-track"><span id="progress-fill" class="progress-fill"></span></div>
      </div>
    </section>

    <section class="activity-block" aria-labelledby="activity-title">
      <h2 id="activity-title" class="control-label">ATIVIDADE</h2>
      <div id="activity-list" class="activity-list" aria-live="polite"></div>
    </section>

    <footer class="control-footer">
      <button id="restart-btn" type="button">REINICIAR</button>
      <button id="shutdown-btn" type="button">DESLIGAR</button>
      <button id="manage-btn" type="button">DELETAR</button>
    </footer>
  </aside>
</main>

<dialog id="manage-dialog" class="manage-dialog" aria-labelledby="manage-title">
  <header class="manage-dialog__header">
    <h2 id="manage-title">PRESETS INSTALADOS</h2>
    <button id="manage-close" type="button" aria-label="Fechar gerenciador"></button>
  </header>
  <div id="manage-list" class="manage-list"></div>
</dialog>

<div id="toast-container" class="toast-container" aria-live="assertive"></div>
```

Use inline SVG from one consistent 16/20-pixel icon style for pin, workflow download, restart, power, trash, and close. Do not use emoji characters as icons.

- [ ] **Step 4: Replace `web/styles.css` with the approved visual system**

Define the exact root tokens and base layout first:

```css
@import url("https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap");

:root {
    --page-bg: #0d0b11;
    --catalog-bg: #131118;
    --panel-bg: #0f0d15;
    --section-bg: #18151f;
    --border: #2b2738;
    --row-border: #1e1b28;
    --text: #eae6f2;
    --text-secondary: #87809c;
    --text-soft: #b0a9c4;
    --text-muted: #6b6480;
    --placeholder: #544e68;
    --accent: #a284f2;
    --accent-light: #c4b2f7;
    --online: #7fd4d0;
    --installed: #7fd4a8;
    --workflow: #8bb8f2;
    --destructive: #d178b8;
}

* { box-sizing: border-box; }

body {
    margin: 0;
    min-width: 320px;
    min-height: 100vh;
    background: var(--page-bg);
    color: var(--text);
    font-family: "JetBrains Mono", ui-monospace, monospace;
}

.preset-name {
    font-family: "Space Grotesk", system-ui, sans-serif;
}

.app-shell {
    width: min(100%, 1280px);
    min-height: 100vh;
    margin: 0 auto;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 300px;
    background: var(--catalog-bg);
    border-inline: 2px solid var(--border);
}

.control-panel {
    position: sticky;
    top: 0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    background: var(--panel-bg);
    border-left: 2px solid var(--border);
}

.pinned-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
    padding: 14px 18px;
}
```

Implement square-corner preset cards, recent rows, filled custom checkbox visuals, installed/workflow markers, independently scrollable queue/activity regions, two-pixel structural borders, visible `:focus-visible` outlines, disabled states, toast placement, dialog backdrop, and stable 150-200 ms color/opacity transitions.

Add these responsive contracts exactly:

```css
@media (max-width: 920px) {
    .app-shell { grid-template-columns: 1fr; }
    .control-panel {
        position: static;
        width: 100%;
        height: auto;
        min-height: 520px;
        border-left: 0;
        border-top: 2px solid var(--border);
    }
    .pinned-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 640px) {
    .pinned-grid { grid-template-columns: 1fr; padding-inline: 12px; }
    .recent-row {
        grid-template-columns: 20px minmax(0, 1fr) auto;
        grid-template-areas:
            "check name size"
            "check description description"
            "date stats workflows";
    }
    .control-footer button { min-height: 44px; }
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        scroll-behavior: auto !important;
    }
}
```

- [ ] **Step 5: Rewrite catalog and queue rendering in `web/app.js`**

Keep one state object and ordered selection array:

```javascript
const state = {
    presets: [],
    selectedNames: [],
    isInstalling: false,
    isRestarting: false,
    installedNames: [],
    lastProgress: null,
};
```

Replace `loadPresets()` with the authoritative fetch/rerender flow:

```javascript
async function loadPresets() {
    try {
        const response = await fetch("/api/presets");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        state.presets = Array.isArray(payload.presets) ? payload.presets : [];
        const available = new Set(state.presets.map(preset => preset.name));
        state.selectedNames = state.selectedNames.filter(name => available.has(name));
        state.installedNames = state.presets
            .filter(preset => preset.installed)
            .map(preset => preset.name);
        renderPresetCatalog();
        renderQueue();
        renderManageDialog();
    } catch (error) {
        console.error("Failed to load presets:", error);
        showToast("Falha ao carregar presets.", "error");
    }
}
```

Use these concrete rendering boundaries:

```javascript
function renderPresetCatalog() {
    const pinned = state.presets.filter(preset => preset.pinned === true);
    const recent = state.presets.filter(preset => preset.pinned !== true);
    document.getElementById("pinned-presets").replaceChildren(
        ...pinned.map(renderPinnedPreset),
    );
    document.getElementById("recent-presets").replaceChildren(
        ...recent.map(renderRecentPreset),
    );
}

function renderPinnedPreset(preset) {
    return createPresetEntry(preset, "preset-card pinned-card");
}

function renderRecentPreset(preset) {
    return createPresetEntry(preset, "recent-row");
}

function createPresetEntry(preset, className) {
    const entry = document.createElement("article");
    entry.className = className;
    entry.classList.toggle("selected", state.selectedNames.includes(preset.name));

    const selection = document.createElement("label");
    selection.className = "preset-select";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "preset-checkbox";
    checkbox.checked = state.selectedNames.includes(preset.name);
    checkbox.disabled = state.isInstalling;
    checkbox.addEventListener("change", () => togglePresetSelection(preset.name));
    const name = document.createElement("strong");
    name.className = "preset-name";
    name.textContent = preset.name;
    selection.append(checkbox, name);

    const description = document.createElement("p");
    description.className = "preset-description";
    description.textContent = preset.description || "Sem descrição.";
    entry.append(selection, description, createPresetMeta(preset));
    return entry;
}

function togglePresetSelection(name) {
    if (state.isInstalling) return;
    state.selectedNames = state.selectedNames.includes(name)
        ? state.selectedNames.filter(selected => selected !== name)
        : [...state.selectedNames, name];
    renderPresetCatalog();
    renderQueue();
}

function formatModifiedDate(timestamp) {
    if (!Number.isFinite(timestamp) || timestamp <= 0) return "--/--";
    return new Intl.DateTimeFormat("pt-BR", {
        day: "2-digit",
        month: "2-digit",
    }).format(new Date(timestamp * 1000));
}

function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / (1024 ** unit);
    return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}
```

Create the compact metadata row without interpolating API values into HTML:

```javascript
function createPresetMeta(preset) {
    const meta = document.createElement("div");
    meta.className = "preset-meta";

    const date = document.createElement("span");
    date.className = "preset-date";
    date.textContent = formatModifiedDate(preset.modified_at);
    const size = document.createElement("strong");
    size.className = "preset-size";
    size.textContent = Number.isFinite(preset.size_gb) ? `${preset.size_gb} GB` : "-- GB";
    const stats = document.createElement("span");
    stats.className = "preset-stats";
    stats.textContent = `${preset.models_count}m · ${preset.nodes_count}n`;
    meta.append(date, size, stats);

    if (preset.installed) {
        const installed = document.createElement("span");
        installed.className = "installed-marker";
        installed.textContent = "INSTALADO";
        meta.appendChild(installed);
    }

    const workflowGroup = document.createElement("span");
    workflowGroup.className = "workflow-group";
    for (const workflow of preset.workflows || []) {
        const anchor = document.createElement("a");
        anchor.className = "workflow-link";
        anchor.href = workflow.url;
        anchor.textContent = workflow.label || "Workflow";
        anchor.addEventListener("click", event => event.stopPropagation());
        if (workflow.local) {
            anchor.download = workflow.file || "workflow.json";
        } else {
            anchor.target = "_blank";
            anchor.rel = "noopener noreferrer";
        }
        workflowGroup.appendChild(anchor);
    }
    meta.appendChild(workflowGroup);
    return meta;
}
```

Selection rendering must use DOM creation plus `textContent` for preset/API data. Workflow anchors stop propagation and keep current local-download versus external-link behavior. Until Task 2 exposes metadata, missing `pinned` behaves as false, missing `size_gb` produces the honest incomplete-total marker, and missing `modified_at` produces `--/--`. Use an explicit unknown-size rule:

```javascript
function renderQueue() {
    const selected = state.selectedNames
        .map(name => state.presets.find(preset => preset.name === name))
        .filter(Boolean);
    const list = document.getElementById("queue-list");
    list.replaceChildren(...selected.map(createQueueRow));
    document.getElementById("queue-count").textContent = String(selected.length);

    const knownTotal = selected.reduce(
        (sum, preset) => sum + (Number.isFinite(preset.size_gb) ? preset.size_gb : 0),
        0,
    );
    const hasUnknown = selected.some(preset => !Number.isFinite(preset.size_gb));
    document.getElementById("queue-total").textContent = selected.length === 0
        ? "0 GB"
        : `${hasUnknown ? "≥ " : ""}${knownTotal.toLocaleString("pt-BR")} GB`;

    const startButton = document.getElementById("start-btn");
    startButton.disabled = selected.length === 0 || state.isInstalling;
    startButton.textContent = state.isInstalling
        ? "INSTALANDO..."
        : selected.length === 0
            ? "SELECIONE UM PRESET"
            : `INICIAR COM ${selected.length} PRESET${selected.length === 1 ? "" : "S"}`;
}
```

Create queue rows with:

```javascript
function createQueueRow(preset) {
    const row = document.createElement("div");
    row.className = "queue-row";
    const name = document.createElement("span");
    name.textContent = preset.name;
    const size = document.createElement("span");
    size.textContent = Number.isFinite(preset.size_gb) ? `${preset.size_gb} GB` : "-- GB";
    row.append(name, size);
    return row;
}
```

If the API reload removes or renames a preset, prune it from `state.selectedNames` before rerendering.

- [ ] **Step 6: Move status and progress into the control panel**

Preserve the existing status precedence and implement:

```javascript
function renderActivity(progress) {
    const activity = document.getElementById("activity-list");
    activity.replaceChildren();

    for (const [lane, detail] of Object.entries(progress?.stages || {})) {
        appendActivityLine(activity, `${lane}: ${detail}`, "stage");
    }
    for (const file of progress?.active || []) {
        const total = file.total > 0 ? ` / ${formatBytes(file.total)}` : "";
        const speed = file.speed_bps > 0 ? ` · ${formatBytes(file.speed_bps)}/s` : "";
        appendActivityLine(
            activity,
            `${file.filename}: ${formatBytes(file.current)}${total}${speed}`,
            "active",
        );
    }
    for (const file of (progress?.recent || []).slice(-6).reverse()) {
        appendActivityLine(
            activity,
            `${file.ok ? "concluído" : "falhou"}: ${file.filename}`,
            file.ok ? "success" : "error",
        );
    }
}

function appendActivityLine(container, message, type) {
    const line = document.createElement("p");
    line.className = `activity-line activity-line--${type}`;
    line.textContent = message;
    container.appendChild(line);
}
```

Set overall progress from model counts only:

```javascript
const done = Number(progress?.done || 0);
const total = Number(progress?.total || 0);
const percent = total > 0 ? Math.min(100, (done / total) * 100) : 0;
progressFill.style.width = `${percent}%`;
progressSummary.textContent = total > 0
    ? `${done}/${total} MODELOS · ${Math.round(percent)}%`
    : "AGUARDANDO";
```

Continue polling every five seconds. Installation, terminal failure, completed-with-failures, unreachable, running, starting, and stopped states retain their current semantic distinctions. Display the real `status.port` next to the status label.

Whenever `updateStatusUI(data)` changes `state.isInstalling`, call `renderPresetCatalog()`, `renderQueue()`, and `renderManageDialog()` so checkboxes, install/cancel actions, restart, and every remove action change state together. On successful status responses, set `state.lastProgress = data.progress || state.lastProgress` and call `renderActivity(state.lastProgress)`. An unreachable response preserves the last known activity instead of replacing it with a false empty state.

- [ ] **Step 7: Preserve all lifecycle actions and rebuild preset management**

Keep the request/response logic from `startWithPresets()`, `cancelInstall()`, `restartComfyUI()`, `shutdownArrakis()`, and `removePreset()`, changing only their DOM targets and visual state updates.

Implement the new manager boundary:

```javascript
function renderManageDialog() {
    const list = document.getElementById("manage-list");
    list.replaceChildren();
    if (state.installedNames.length === 0) {
        const empty = document.createElement("p");
        empty.className = "manage-empty";
        empty.textContent = "Nenhum preset instalado.";
        list.appendChild(empty);
        return;
    }
    for (const name of state.installedNames) {
        list.appendChild(createManageRow(name));
    }
}

function openManageDialog() {
    renderManageDialog();
    document.getElementById("manage-dialog").showModal();
}
```

Create manager rows with:

```javascript
function createManageRow(name) {
    const row = document.createElement("div");
    row.className = "manage-row";
    const label = document.createElement("span");
    label.className = "manage-row__name";
    label.textContent = name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "manage-remove";
    remove.textContent = "REMOVER";
    remove.disabled = state.isInstalling;
    remove.addEventListener("click", () => removePreset(name, remove));
    row.append(label, remove);
    return row;
}
```

Keep the existing confirmation text and result summary inside `removePreset()`. The server's 409 remains the final concurrency guard.

Wire the footer `DELETAR` button to `openManageDialog()` and `manage-close` to `dialog.close()`. Remove every `manage-fab`, `manage-popup`, `toggleManagePopup`, outside-popup click, installed-chip, and old order-badge code path.

Use one initialization boundary:

```javascript
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("start-btn").addEventListener("click", startWithPresets);
    document.getElementById("cancel-btn").addEventListener("click", cancelInstall);
    document.getElementById("restart-btn").addEventListener("click", restartComfyUI);
    document.getElementById("shutdown-btn").addEventListener("click", shutdownArrakis);
    document.getElementById("manage-btn").addEventListener("click", openManageDialog);
    document.getElementById("manage-close").addEventListener("click", () => {
        document.getElementById("manage-dialog").close();
    });
    loadPresets();
    startStatusPolling();
});
```

- [ ] **Step 8: Run focused syntax verification**

Run:

```bash
node --check web/app.js
```

Expected: Node reports no syntax error. Browser behavior remains the contract and is verified in Step 9.

- [ ] **Step 9: Add and run the browser smoke at desktop and mobile widths**

Create `tests/ui_smoke.cjs`. It uses the already-installed workspace Playwright and `/usr/bin/google-chrome`; it is a local verification harness, not a production dependency:

```javascript
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { chromium } = require("playwright");

const installing = process.env.UI_INSTALLING === "1";
const presets = [
    {
        name: "Anima 3 Studio",
        description: "Preset completo para edit e inpaint",
        models_count: 33,
        nodes_count: 37,
        installed: true,
        workflows: [{
            label: "Workflow",
            url: "/api/workflows/anima.json",
            local: true,
            file: "anima.json",
        }],
        pinned: true,
        size_gb: 42,
        modified_at: 1786147200,
    },
    {
        name: "Krea 2 Full",
        description: "Krea com stack completa",
        models_count: 19,
        nodes_count: 5,
        installed: true,
        workflows: [],
        pinned: true,
        size_gb: 35,
        modified_at: 1786060800,
    },
    {
        name: "MiniMax H3 5090",
        description: "Vídeo com áudio estéreo nativo",
        models_count: 6,
        nodes_count: 1,
        installed: false,
        workflows: [],
        pinned: true,
        size_gb: 91,
        modified_at: 1785974400,
    },
    {
        name: "Qwen Image Edit 2511",
        description: "Edição de imagem",
        models_count: 8,
        nodes_count: 2,
        installed: false,
        workflows: [],
        pinned: false,
        size_gb: 15,
        modified_at: 1785888000,
    },
];
const status = {
    running: !installing,
    status: installing ? "starting" : "running",
    port: 8818,
    installing,
    install_status: installing ? "running" : "completed",
    installed_presets: ["Anima 3 Studio", "Krea 2 Full"],
    progress: {
        stages: { models: "fila pronta" },
        done: 2,
        total: 6,
        active: [{
            filename: "model.safetensors",
            current: 1073741824,
            total: 4294967296,
            speed_bps: 104857600,
            backend: "xet",
        }],
        recent: [{ filename: "vae.safetensors", ok: true }],
    },
};

function delay(milliseconds) {
    return new Promise(resolve => setTimeout(resolve, milliseconds));
}

async function waitForServer() {
    for (let attempt = 0; attempt < 30; attempt += 1) {
        try {
            const response = await fetch("http://127.0.0.1:8091/");
            if (response.ok) return;
        } catch {}
        await delay(100);
    }
    throw new Error("Static UI server did not start on port 8091");
}

async function mockApi(page) {
    await page.route("**/api/presets", route => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ presets }),
    }));
    await page.route("**/api/status", route => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(status),
    }));
    await page.route("**/api/**", route => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ success: true }),
    }));
}

async function main() {
    const staticServer = spawn(
        "python3",
        ["-m", "http.server", "8091", "--directory", "web"],
        { stdio: "ignore" },
    );
    let browser;
    try {
        await waitForServer();
        browser = await chromium.launch({
            executablePath: "/usr/bin/google-chrome",
            headless: true,
        });

        const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
        const consoleErrors = [];
        desktop.on("console", message => {
            if (message.type() === "error") consoleErrors.push(message.text());
        });
        await mockApi(desktop);
        await desktop.goto("http://127.0.0.1:8091/", { waitUntil: "networkidle" });
        await desktop.waitForSelector(".pinned-card");

        const desktopLayout = await desktop.evaluate(() => ({
            columns: getComputedStyle(document.getElementById("pinned-presets"))
                .gridTemplateColumns.split(" ").length,
            panelWidth: document.getElementById("control-panel").getBoundingClientRect().width,
        }));
        assert.equal(desktopLayout.columns, 3);
        assert.ok(desktopLayout.panelWidth >= 298 && desktopLayout.panelWidth <= 302);

        if (installing) {
            assert.equal(await desktop.locator("#start-btn").textContent(), "INSTALANDO...");
            assert.equal(await desktop.locator("#start-btn").isDisabled(), true);
            assert.equal(await desktop.locator("#cancel-btn").isVisible(), true);
            assert.equal(await desktop.locator("#restart-btn").isDisabled(), true);
            assert.equal(await desktop.locator("#progress-summary").textContent(), "2/6 MODELOS · 33%");
            const activity = await desktop.locator("#activity-list").textContent();
            assert.match(activity, /fila pronta/);
            assert.match(activity, /model\.safetensors/);
            assert.match(activity, /vae\.safetensors/);
        } else {
            await desktop.locator(".pinned-card .preset-checkbox").first().check();
            assert.equal(await desktop.locator("#queue-count").textContent(), "1");
            assert.equal(await desktop.locator("#queue-total").textContent(), "42 GB");
        }

        await desktop.locator("#manage-btn").click();
        assert.equal(await desktop.locator("#manage-dialog").evaluate(dialog => dialog.open), true);
        assert.equal(await desktop.locator(".manage-remove").count(), 2);
        assert.equal(
            await desktop.locator("#manage-dialog").evaluate(dialog => getComputedStyle(dialog).borderRadius),
            "0px",
        );
        if (installing) {
            const disabled = await desktop.locator(".manage-remove").evaluateAll(
                buttons => buttons.every(button => button.disabled),
            );
            assert.equal(disabled, true);
        }

        assert.deepEqual(consoleErrors, []);
        await desktop.screenshot({ path: "/tmp/arrakis-ui-desktop.png", fullPage: true });

        const mobile = await browser.newPage({ viewport: { width: 375, height: 812 } });
        await mockApi(mobile);
        await mobile.goto("http://127.0.0.1:8091/", { waitUntil: "networkidle" });
        await mobile.waitForSelector(".recent-row");
        const widths = await mobile.evaluate(() => ({
            page: document.documentElement.scrollWidth,
            viewport: window.innerWidth,
        }));
        assert.equal(widths.page, widths.viewport);
        await mobile.screenshot({ path: "/tmp/arrakis-ui-mobile.png", fullPage: true });
    } finally {
        if (browser) await browser.close();
        staticServer.kill("SIGTERM");
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
```

Run:

```bash
node tests/ui_smoke.cjs
```

Expected: exit code zero and screenshots at `/tmp/arrakis-ui-desktop.png` and `/tmp/arrakis-ui-mobile.png`. Inspect both images and reject the checkpoint if text overlaps, recent rows overflow, the mobile page scrolls horizontally, the footer actions disappear, or the dialog uses legacy rounded styling.

- [ ] **Step 10: Commit the complete UI vertical slice**

```bash
git add web/index.html web/styles.css web/app.js tests/ui_smoke.cjs
git commit -m "Implementa nova interface do Arrakis Start"
```

---

### Task 2: Make preset modification metadata authoritative

**Files:**
- Modify: `start.py:270-330`
- Modify: `server.py:118-185`
- Create: `tests/test_web_ui.py`

**Interfaces:**
- Produces: `start.preset_modified_timestamps() -> Dict[str, float]`.
- Produces: `start.load_presets() -> List[Dict]`, with private `_modified_at: float` on every loaded preset.
- Produces: `server.serialize_presets(presets: List[Dict], installed_presets: set[str]) -> List[Dict]`.
- Produces API fields: `pinned: bool`, `size_gb: int | float | None`, and `modified_at: int`.
- Preserves API fields: `name`, `description`, `models_count`, `nodes_count`, `installed`, and `workflows`.

- [ ] **Step 1: Write failing modification-order tests**

Create `tests/test_web_ui.py` with the exact imports shown below, then add the test classes that follow:

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server
import start


class PresetModificationTests(unittest.TestCase):
    def test_git_history_keeps_latest_touch_for_each_preset(self):
        history = "\n".join([
            "200",
            "presets/newer.json",
            "presets/shared.json",
            "",
            "100",
            "presets/shared.json",
            "presets/older.json",
        ])
        completed = Mock(returncode=0, stdout=history)

        with patch("start.subprocess.run", return_value=completed):
            timestamps = start.preset_modified_timestamps()

        self.assertEqual(timestamps, {
            "newer.json": 200.0,
            "shared.json": 200.0,
            "older.json": 100.0,
        })

    def test_load_presets_orders_by_modified_time_then_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            preset_dir = Path(directory)
            for filename, name in [
                ("bravo.json", "Bravo"),
                ("alpha.json", "Alpha"),
                ("newest.json", "Newest"),
            ]:
                (preset_dir / filename).write_text(
                    json.dumps({"name": name, "models": [], "nodes": []}),
                    encoding="utf-8",
                )

            with patch.object(start, "PRESETS_DIR", preset_dir), patch(
                "start.preset_modified_timestamps",
                return_value={
                    "alpha.json": 100.0,
                    "bravo.json": 100.0,
                    "newest.json": 200.0,
                },
            ):
                presets = start.load_presets()

        self.assertEqual(
            [preset["_filename"] for preset in presets],
            ["newest.json", "alpha.json", "bravo.json"],
        )
        self.assertEqual(
            [preset["_modified_at"] for preset in presets],
            [200.0, 100.0, 100.0],
        )

    def test_untracked_preset_uses_filesystem_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            preset_dir = Path(directory)
            preset_file = preset_dir / "local.json"
            preset_file.write_text(
                json.dumps({"name": "Local", "models": [], "nodes": []}),
                encoding="utf-8",
            )
            expected = preset_file.stat().st_mtime

            with patch.object(start, "PRESETS_DIR", preset_dir), patch(
                "start.preset_modified_timestamps", return_value={}
            ):
                preset = start.load_presets()[0]

        self.assertEqual(preset["_modified_at"], expected)
```

- [ ] **Step 2: Run the ordering tests and verify RED**

Run:

```bash
python -m unittest tests.test_web_ui.PresetModificationTests -v
```

Expected: failures because `preset_modified_timestamps()` and `_modified_at` do not exist, and current ordering uses added dates.

- [ ] **Step 3: Implement latest-modification loading**

Rename `preset_added_timestamps()` to `preset_modified_timestamps()`. Remove `--diff-filter=A` from the Git command so the newest log entry that touched each file wins:

```python
def preset_modified_timestamps() -> Dict[str, float]:
    """Map preset filename to the latest committed modification timestamp."""
    timestamps: Dict[str, float] = {}
    try:
        result = subprocess.run(
            [
                "git", "-C", str(SCRIPT_DIR), "log",
                "--format=%ct", "--name-only", "--", "presets",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return timestamps
        current_ts = 0.0
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.isdigit():
                current_ts = float(line)
            else:
                timestamps.setdefault(Path(line).name, current_ts)
    except Exception as exc:
        logger.debug("Could not read preset modification dates from git: %s", exc)
    return timestamps
```

In `load_presets()`, resolve one timestamp per file, sort by `(-timestamp, filename.lower())`, and attach the same value as `preset['_modified_at']` before appending it.

- [ ] **Step 4: Run the ordering tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_web_ui.PresetModificationTests -v
```

Expected: all three tests pass.

- [ ] **Step 5: Write failing API serialization tests**

Append to `tests/test_web_ui.py`:

```python
class PresetSerializationTests(unittest.TestCase):
    def test_serializes_ui_metadata_and_preserves_workflows(self):
        presets = [{
            "name": "Pinned",
            "description": "Dense preset",
            "models": [{"filename": "model.safetensors"}],
            "nodes": ["https://example.com/node"],
            "pinned": True,
            "size_gb": 42,
            "_modified_at": 1786147200.9,
            "workflow": "preset.json",
        }]

        result = server.serialize_presets(presets, {"Pinned"})

        self.assertEqual(result, [{
            "name": "Pinned",
            "description": "Dense preset",
            "models_count": 1,
            "nodes_count": 1,
            "installed": True,
            "workflows": [{
                "label": "Workflow",
                "url": "/api/workflows/preset.json",
                "local": True,
                "file": "preset.json",
            }],
            "pinned": True,
            "size_gb": 42,
            "modified_at": 1786147200,
        }])

    def test_invalid_optional_metadata_becomes_safe_defaults(self):
        presets = [{
            "name": "Unsafe metadata",
            "models": [],
            "nodes": [],
            "pinned": "true",
            "size_gb": "42",
            "_modified_at": "yesterday",
        }]

        result = server.serialize_presets(presets, set())[0]

        self.assertIs(result["pinned"], False)
        self.assertIsNone(result["size_gb"])
        self.assertEqual(result["modified_at"], 0)

    def test_base_preset_remains_hidden(self):
        result = server.serialize_presets(
            [{"name": "Base", "models": [], "nodes": []}],
            set(),
        )
        self.assertEqual(result, [])
```

- [ ] **Step 6: Run the serialization tests and verify RED**

Run:

```bash
python -m unittest tests.test_web_ui.PresetSerializationTests -v
```

Expected: errors because `server.serialize_presets()` does not exist.

- [ ] **Step 7: Extract and use `serialize_presets()`**

Move the current workflow normalization and clean-response construction out of `_handle_get_presets()` into:

```python
def serialize_presets(presets: List[dict], installed_presets: set[str]) -> List[dict]:
    clean_presets = []
    for preset in presets:
        name = preset.get("name", preset.get("_filename", "Unknown"))
        if name.lower() == "base":
            continue

        raw_workflows = preset.get("workflows")
        if not isinstance(raw_workflows, list):
            raw_workflows = [{
                "label": "Workflow",
                "file": preset.get("workflow", ""),
                "url": preset.get("workflow_url", ""),
            }]
        workflows = []
        for workflow in raw_workflows:
            if not isinstance(workflow, dict):
                continue
            filename = workflow.get("file", "")
            url = f"/api/workflows/{filename}" if filename else workflow.get("url", "")
            if url:
                workflows.append({
                    "label": workflow.get("label", "Workflow"),
                    "url": url,
                    "local": bool(filename),
                    "file": filename,
                })

        raw_size = preset.get("size_gb")
        size_gb = (
            raw_size
            if isinstance(raw_size, (int, float))
            and not isinstance(raw_size, bool)
            and raw_size > 0
            else None
        )
        raw_modified = preset.get("_modified_at")
        modified_at = int(raw_modified) if isinstance(raw_modified, (int, float)) else 0
        clean_presets.append({
            "name": name,
            "description": preset.get("description", ""),
            "models_count": len(preset.get("models", [])),
            "nodes_count": len(preset.get("nodes", [])),
            "installed": name in installed_presets,
            "workflows": workflows,
            "pinned": preset.get("pinned") is True,
            "size_gb": size_gb,
            "modified_at": modified_at,
        })
    return clean_presets
```

Reduce `_handle_get_presets()` to obtain the callback data and installed-name set, call `serialize_presets()`, and send the result.

- [ ] **Step 8: Run Task 2 tests and commit**

Run:

```bash
python -m unittest tests.test_web_ui -v
```

Expected: all Task 1 tests pass.

```bash
git add start.py server.py tests/test_web_ui.py
git commit -m "Expõe metadados de ordenação dos presets"
```

---

### Task 3: Add explicit UI metadata to every active preset

**Files:**
- Modify: every active `presets/*.json` file, including `presets/base.json`
- Modify: `tests/test_presets.py`
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: JSON fields `pinned: bool` and `size_gb: number` from Task 2.
- Produces: complete author-maintained metadata for every active preset.
- Preserves: all model URLs, destinations, nodes, workflows, flags, and installer semantics.

- [ ] **Step 1: Write the failing metadata coverage test**

Append to `tests/test_presets.py`:

```python
class PresetUiMetadataTests(unittest.TestCase):
    EXPECTED = {
        "anima3-studio.json": (True, 42),
        "anima3.json": (False, 8),
        "animegen-wan-2.2.json": (False, 91),
        "base.json": (False, 1),
        "flux2-klein-4b-base-full.json": (False, 18),
        "flux2-klein-9b-base.json": (False, 24),
        "gerar_imagens_validacao.json": (False, 9),
        "ideogram4.json": (False, 21),
        "krea2-full.json": (True, 35),
        "krea2.json": (False, 28),
        "ltx-gerador_nsfw.json": (False, 37),
        "ltx-lip-sync-gemma-q4.json": (False, 54),
        "ltx-wan-helper.json": (False, 37),
        "ltx23-anime-production.json": (False, 58),
        "ltx23-gerador_nsfw-10eros.json": (False, 58),
        "ltx23-gerador_nsfw-pinkcherry.json": (False, 39),
        "ltx23-gerador_nsfw-sulphur.json": (False, 39),
        "ltx23-gerador_nsfw.json": (False, 39),
        "ltx23-production-base.json": (False, 58),
        "minimax-h3-5090.json": (True, 91),
        "minimax-h3-6000pro-96gb.json": (False, 190),
        "qwen-image.json": (False, 15),
        "seedvr_tester.json": (False, 2),
        "video-scail-test.json": (False, 33),
    }

    def test_every_active_preset_has_approved_ui_metadata(self):
        presets = {
            preset["_filename"]: preset for preset in start.load_presets()
        }

        self.assertEqual(set(presets), set(self.EXPECTED))
        for filename, (pinned, size_gb) in self.EXPECTED.items():
            with self.subTest(filename=filename):
                self.assertIs(presets[filename]["pinned"], pinned)
                self.assertEqual(presets[filename]["size_gb"], size_gb)
```

- [ ] **Step 2: Run the metadata test and verify RED**

Run:

```bash
python -m unittest tests.test_presets.PresetUiMetadataTests -v
```

Expected: errors on missing `pinned` and `size_gb` keys.

- [ ] **Step 3: Add metadata without touching installer data**

Add the exact `pinned` and `size_gb` values from `EXPECTED` immediately after each preset's `description`. Do not reorder or modify `models`, `nodes`, `pip_commands`, workflows, flags, or URLs.

`base.json` remains hidden from `/api/presets`; its one-gigabyte value documents the shared bootstrap payload and is not added to user queue totals.

- [ ] **Step 4: Update the preset contract documentation**

In `CLAUDE.md` and the preset-authoring section of `README.md`, document:

```json
{
  "pinned": false,
  "size_gb": 15
}
```

State that `pinned` selects the large-card section, `size_gb` is an author-maintained estimate of the full payload, and committed presets are ordered by their latest modifying commit rather than their adding commit.

- [ ] **Step 5: Validate metadata and commit**

Run:

```bash
python -m unittest tests.test_presets.PresetUiMetadataTests -v
```

Expected: the metadata test passes.

Run:

```bash
for preset in presets/*.json; do
  python -m json.tool "$preset" >/dev/null || exit 1
done
```

Expected: every active preset parses successfully.

```bash
git add presets/*.json tests/test_presets.py CLAUDE.md README.md
git commit -m "Adiciona metadados visuais aos presets"
```

---

### Task 4: Run the feature boundary gate and prepare local handoff

**Files:**
- Verify only: `start.py`, `server.py`, `web/`, `presets/`, `tests/`, `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: all outputs from Tasks 1-3.
- Produces: a clean, tested local branch ready for the user's pod/runtime test.

- [ ] **Step 1: Run syntax and data validation once**

Run:

```bash
python -m py_compile start.py server.py progress.py
node --check web/app.js
for file in presets/*.json workflows/*.json; do
  python -m json.tool "$file" >/dev/null || exit 1
done
git diff --check
```

Expected: every command exits successfully with no syntax, JSON, or whitespace error.

- [ ] **Step 2: Run the complete Python test suite once**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all existing and new tests pass.

- [ ] **Step 3: Repeat the mocked browser smoke with installation active**

Run:

```bash
UI_INSTALLING=1 node tests/ui_smoke.cjs
```

Expected: exit code zero. The harness proves that the primary action reads `INSTALANDO...`, cancel is visible, restart and every `REMOVER` action are disabled, progress reads `2/6 MODELOS · 33%`, and activity contains the stage, active transfer, and recent completion.

- [ ] **Step 4: Verify final Git scope**

Run:

```bash
git status --short
git log -4 --oneline
```

Expected: no tracked changes remain; `Redesign Arrakis Start/` is still the only untracked path; the design, UI, server metadata, and preset metadata checkpoints are visible as separate local commits. Do not push.

- [ ] **Step 5: Hand off one concrete local test**

Tell the user to run on their target environment:

```bash
python start.py --web-only
```

Then open the exposed Arrakis Start URL and verify one real preset selection, the `DELETAR` installed-preset dialog, and cancel/resume behavior. Report the local commit hashes and explicitly state that nothing was pushed.
