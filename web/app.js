const state = {
    presets: [],
    selectedNames: [],
    isInstalling: false,
    isRestarting: false,
    installedNames: [],
    lastProgress: null,
    statusReachable: true,
    isShuttingDown: false,
};

let statusPollTimer = null;
const statusPolling = {
    cadenceMs: 5000,
    inFlight: false,
    requestId: 0,
    latestAppliedRequestId: 0,
    lifecycleGeneration: 0,
    pendingLifecycleMutations: 0,
    restartRestoreTimer: null,
};

function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
}

function createDownloadIcon() {
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 20 20");
    icon.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M9 2h2v9l3-3 1.4 1.4-5.4 5.4-5.4-5.4L6 8l3 3V2Zm-6 14h14v2H3v-2Z");
    icon.appendChild(path);
    return icon;
}

function createPinIcon() {
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.classList.add("preset-pin");
    icon.setAttribute("viewBox", "0 0 20 20");
    icon.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "m13.7 2.5 3.8 3.8-2.2 2.2v3.1l1.3 1.3v1.4H11l-5.2 5.2-1.4-1.4 5.2-5.2V7.3h1.4l1.3 1.3h3.1l2.2-2.2-3.8-3.8Z");
    icon.appendChild(path);
    return icon;
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

function renderPresetCatalog() {
    const pinned = state.presets.filter(preset => preset.pinned === true);
    const recent = state.presets.filter(preset => preset.pinned !== true);
    const pinnedContainer = document.getElementById("pinned-presets");
    const recentContainer = document.getElementById("recent-presets");
    pinnedContainer.replaceChildren(...pinned.map(renderPinnedPreset));
    if (state.presets.length === 0) {
        const empty = document.createElement("p");
        empty.className = "catalog-empty";
        empty.textContent = "Nenhum preset disponível.";
        recentContainer.replaceChildren(empty);
        return;
    }
    recentContainer.replaceChildren(...recent.map(renderRecentPreset));
}

function renderPinnedPreset(preset) {
    const entry = createPresetEntry(preset, "preset-card pinned-card");
    entry.prepend(createPinIcon());
    return entry;
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
    checkbox.disabled = state.isInstalling || !state.statusReachable;
    checkbox.addEventListener("change", () => togglePresetSelection(preset.name));
    const name = document.createElement("strong");
    name.className = "preset-name";
    name.textContent = preset.name;
    selection.append(checkbox, name);

    const description = document.createElement("p");
    description.className = "preset-description";
    description.textContent = preset.description || "Sem descrição.";
    entry.append(selection, description, createPresetMeta(preset));
    entry.addEventListener("click", event => {
        if (
            state.isInstalling
            || !state.statusReachable
            || event.target.closest(".preset-select, .workflow-link")
        ) return;
        togglePresetSelection(preset.name);
    });
    return entry;
}

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
        anchor.append(createDownloadIcon(), document.createTextNode(workflow.label || "Workflow"));
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

function togglePresetSelection(name) {
    if (state.isInstalling) return;
    state.selectedNames = state.selectedNames.includes(name)
        ? state.selectedNames.filter(selected => selected !== name)
        : [...state.selectedNames, name];
    renderPresetCatalog();
    renderQueue();
}

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
    startButton.disabled = selected.length === 0 || state.isInstalling || !state.statusReachable;
    startButton.textContent = state.isInstalling
        ? "INSTALANDO..."
        : selected.length === 0
            ? "SELECIONE UM PRESET"
            : `INICIAR COM ${selected.length} PRESET${selected.length === 1 ? "" : "S"}`;
}

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

function appendActivityLine(container, message, type) {
    const line = document.createElement("p");
    line.className = `activity-line activity-line--${type}`;
    line.textContent = message;
    container.appendChild(line);
}

function renderActivity(progress) {
    const activity = document.getElementById("activity-list");
    activity.replaceChildren();

    for (const [lane, detail] of Object.entries(progress?.stages || {})) {
        appendActivityLine(activity, `${lane}: ${detail}`, "stage");
    }
    for (const file of progress?.active || []) {
        const total = file.total > 0 ? ` / ${formatBytes(file.total)}` : "";
        const speed = file.speed_bps > 0 ? ` · ${formatBytes(file.speed_bps)}/s` : "";
        const backend = file.backend ? ` · ${file.backend}` : "";
        appendActivityLine(
            activity,
            `${file.filename}: ${formatBytes(file.current)}${total}${speed}${backend}`,
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

function renderProgress(progress) {
    const progressFill = document.getElementById("progress-fill");
    const progressSummary = document.getElementById("progress-summary");
    const done = Number(progress?.done || 0);
    const total = Number(progress?.total || 0);
    const percent = total > 0 ? Math.min(100, (done / total) * 100) : 0;
    progressFill.style.width = `${percent}%`;
    progressSummary.textContent = total > 0
        ? `${done}/${total} MODELOS · ${Math.round(percent)}%`
        : "AGUARDANDO";
}

async function pollStatus() {
    if (statusPolling.inFlight) return;
    const requestId = ++statusPolling.requestId;
    const lifecycleGeneration = statusPolling.lifecycleGeneration;
    const beganDuringLifecycleMutation = statusPolling.pendingLifecycleMutations > 0;
    statusPolling.inFlight = true;
    try {
        const response = await fetch("/api/status");
        if (!canApplyStatusPoll(lifecycleGeneration, beganDuringLifecycleMutation)) return;
        if (!response.ok) {
            if (requestId >= statusPolling.latestAppliedRequestId) {
                statusPolling.latestAppliedRequestId = requestId;
                updateStatusUI({ status: "unreachable" }, { authoritative: false });
            }
            return;
        }
        const data = await response.json();
        if (!canApplyStatusPoll(lifecycleGeneration, beganDuringLifecycleMutation)) return;
        if (requestId >= statusPolling.latestAppliedRequestId) {
            statusPolling.latestAppliedRequestId = requestId;
            updateStatusUI(data, { authoritative: true });
        }
    } catch {
        if (!canApplyStatusPoll(lifecycleGeneration, beganDuringLifecycleMutation)) return;
        if (requestId >= statusPolling.latestAppliedRequestId) {
            statusPolling.latestAppliedRequestId = requestId;
            updateStatusUI({ status: "unreachable" }, { authoritative: false });
        }
    } finally {
        statusPolling.inFlight = false;
    }
}

function canApplyStatusPoll(lifecycleGeneration, beganDuringLifecycleMutation) {
    return !beganDuringLifecycleMutation
        && statusPolling.pendingLifecycleMutations === 0
        && lifecycleGeneration === statusPolling.lifecycleGeneration;
}

function beginLifecycleMutation() {
    statusPolling.lifecycleGeneration += 1;
    statusPolling.pendingLifecycleMutations += 1;
}

function settleLifecycleMutation() {
    statusPolling.lifecycleGeneration += 1;
    statusPolling.pendingLifecycleMutations = Math.max(0, statusPolling.pendingLifecycleMutations - 1);
}

function setShuttingDown(isShuttingDown) {
    state.isShuttingDown = isShuttingDown;
    const button = document.getElementById("shutdown-btn");
    button.disabled = isShuttingDown;
    button.lastChild.textContent = isShuttingDown ? " DESLIGANDO..." : " DESLIGAR";
}

function updateStatusUI(data, { authoritative } = { authoritative: true }) {
    const dot = document.getElementById("status-dot");
    const text = document.getElementById("status-text");
    const port = document.getElementById("status-port");
    const restartButton = document.getElementById("restart-btn");
    const cancelButton = document.getElementById("cancel-btn");
    const shutdownButton = document.getElementById("shutdown-btn");
    const manageButton = document.getElementById("manage-btn");
    const flagsInput = document.getElementById("extra-flags-input");
    const wasInstalling = state.isInstalling;
    const hasInstalling = Object.prototype.hasOwnProperty.call(data, "installing")
        && typeof data.installing === "boolean";
    if (authoritative && hasInstalling) state.isInstalling = data.installing;
    const installingChanged = wasInstalling !== state.isInstalling;
    state.statusReachable = authoritative;

    dot.classList.remove("running", "stopped", "starting", "error");
    if (authoritative) port.textContent = data.port ? `PORTA ${data.port}` : "";

    if (!authoritative) {
        dot.classList.add("error");
        text.textContent = "SERVIDOR INACESSÍVEL";
        restartButton.disabled = true;
        cancelButton.hidden = !state.isInstalling;
        cancelButton.disabled = true;
    } else if (state.isInstalling) {
        dot.classList.add("starting");
        text.textContent = "INSTALANDO...";
        restartButton.disabled = true;
        cancelButton.hidden = false;
        cancelButton.disabled = false;
    } else if (data.running) {
        dot.classList.add("running");
        text.textContent = "COMFYUI: RODANDO";
        restartButton.disabled = state.isRestarting;
        cancelButton.hidden = true;
    } else if (data.status === "starting") {
        dot.classList.add("starting");
        text.textContent = "COMFYUI: INICIANDO...";
        restartButton.disabled = true;
        cancelButton.hidden = true;
    } else if (data.status === "error") {
        dot.classList.add("error");
        text.textContent = "COMFYUI: ERRO";
        restartButton.disabled = state.isRestarting;
        cancelButton.hidden = true;
    } else if (data.status === "unreachable") {
        dot.classList.add("error");
        text.textContent = "SERVIDOR INACESSÍVEL";
        restartButton.disabled = true;
        cancelButton.hidden = true;
    } else {
        dot.classList.add("stopped");
        text.textContent = "COMFYUI: PARADO";
        restartButton.disabled = state.isRestarting;
        cancelButton.hidden = true;
    }

    const controlsLocked = state.isInstalling || !state.statusReachable;
    shutdownButton.disabled = state.isShuttingDown;
    manageButton.disabled = controlsLocked;
    flagsInput.disabled = controlsLocked;

    if (authoritative) {
        state.lastProgress = data.progress || state.lastProgress;
        renderProgress(state.lastProgress);
        renderActivity(state.lastProgress);
    }
    renderPresetCatalog();
    renderQueue();
    renderManageDialog();
    if (authoritative && wasInstalling && installingChanged && !state.isInstalling) {
        handleInstallationFinished(data);
    }
}

function handleInstallationFinished(data) {
    if (["cancelled", "failed", "start_failed"].includes(data.install_status)) {
        showToast(
            data.install_status === "cancelled"
                ? "Instalação cancelada. Você pode selecionar e instalar novamente para retomar."
                : data.install_status === "start_failed"
                    ? "Arquivos instalados, mas o ComfyUI não iniciou. Consulte os logs."
                    : "A instalação falhou. Consulte os logs e tente novamente.",
            data.install_status === "cancelled" ? "info" : "error",
        );
        return;
    }
    if (!data.running) return;
    state.selectedNames = [];
    renderQueue();
    loadPresets();
    if (data.install_status === "completed_with_failures") {
        showToast(
            "ComfyUI iniciado, mas alguns itens não baixaram. Veja os erros no log e execute novamente para retomar só o que falta.",
            "info",
        );
    }
}

function startStatusPolling() {
    setStatusPollingCadence(5000, true);
}

function setStatusPollingCadence(cadenceMs, pollImmediately = false) {
    statusPolling.cadenceMs = cadenceMs;
    if (statusPollTimer) clearInterval(statusPollTimer);
    statusPollTimer = setInterval(pollStatus, statusPolling.cadenceMs);
    if (pollImmediately) pollStatus();
}

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
    remove.setAttribute("aria-label", `Remover preset ${name}`);
    remove.disabled = state.isInstalling || !state.statusReachable;
    remove.addEventListener("click", () => removePreset(name, remove));
    row.append(label, remove);
    return row;
}

function openManageDialog() {
    if (state.isInstalling || !state.statusReachable) return;
    renderManageDialog();
    const dialog = document.getElementById("manage-dialog");
    if (!dialog.open) dialog.showModal();
}

async function removePreset(presetName, button) {
    if (state.isInstalling || !state.statusReachable) {
        showToast("Aguarde a instalação terminar antes de remover.", "error");
        return;
    }
    const confirmed = confirm(
        `Remover modelos do preset "${presetName}"?\n\n`
        + "- Apenas modelos exclusivos serão apagados (compartilhados com outros presets ficam).\n"
        + "- Custom nodes não serão removidos.\n"
        + "- Modelos sem nome fixo (ex.: Civitai) podem ficar no disco e precisam ser removidos manualmente.",
    );
    if (!confirmed) return;

    button.disabled = true;
    try {
        const response = await fetch("/api/uninstall", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ preset: presetName }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success) {
            showToast(result.error || `Falha ao remover (HTTP ${response.status})`, "error");
            return;
        }
        const parts = [
            `${result.deleted?.length || 0} arquivo(s) removido(s)`,
            `${formatBytes(result.bytes_freed || 0)} liberados`,
        ];
        if (result.shared_kept) parts.push(`${result.shared_kept} mantido(s) por compartilhamento`);
        if (result.civitai_skipped) parts.push(`${result.civitai_skipped} sem nome (Civitai)`);
        if (result.errors?.length) parts.push(`${result.errors.length} erro(s)`);
        showToast(`✓ ${presetName}: ${parts.join(" • ")}`, "success");
        await loadPresets();
    } catch (error) {
        console.error("Erro ao remover preset:", error);
        showToast("Falha ao remover preset (rede/servidor).", "error");
    } finally {
        button.disabled = state.isInstalling;
    }
}

async function cancelInstall() {
    if (!state.statusReachable) {
        showToast("Não foi possível confirmar o estado da instalação.", "error");
        return;
    }
    if (!confirm(
        "Cancelar a instalação?\n\n"
        + "Arquivos concluídos serão preservados e downloads parciais poderão ser retomados ao instalar novamente.",
    )) return;
    const button = document.getElementById("cancel-btn");
    button.disabled = true;
    try {
        beginLifecycleMutation();
        const response = await fetch("/api/cancel", { method: "POST" });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            showToast(result.error || `Falha ao solicitar cancelamento (HTTP ${response.status}).`, "error");
            return;
        }
        showToast(
            result.cancelled ? "Cancelamento solicitado — preservando o que já foi concluído." : "Nenhuma instalação ativa.",
            "info",
        );
    } catch {
        showToast("Falha ao solicitar cancelamento.", "error");
    } finally {
        settleLifecycleMutation();
        button.disabled = false;
    }
}

async function startWithPresets() {
    if (state.selectedNames.length === 0 || state.isInstalling || !state.statusReachable) return;
    beginLifecycleMutation();
    state.isInstalling = true;
    renderPresetCatalog();
    renderQueue();
    renderManageDialog();
    document.getElementById("cancel-btn").hidden = false;
    showToast("Instalando presets e iniciando ComfyUI...", "info");

    try {
        const flagsInput = document.getElementById("extra-flags-input");
        const extraFlags = flagsInput.value.trim().split(/\s+/).filter(Boolean);
        const response = await fetch("/api/install", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ presets: state.selectedNames, extra_flags: extraFlags }),
        });
        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || "Falha na requisição de instalação");
        }
        settleLifecycleMutation();
        showToast("Instalação iniciada! ComfyUI será iniciado quando estiver pronto.", "success");
    } catch (error) {
        settleLifecycleMutation();
        console.error("Erro na instalação:", error);
        state.isInstalling = false;
        renderPresetCatalog();
        renderQueue();
        renderManageDialog();
        document.getElementById("cancel-btn").hidden = true;
        showToast(error.message || "Instalação falhou. Verifique o console para detalhes.", "error");
    }
}

async function restartComfyUI() {
    if (state.isRestarting || state.isInstalling || !state.statusReachable) return;
    const button = document.getElementById("restart-btn");
    state.isRestarting = true;
    button.disabled = true;
    showToast("Reiniciando ComfyUI...", "info");
    try {
        beginLifecycleMutation();
        const response = await fetch("/api/restart", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) throw new Error("Falha no restart");
        settleLifecycleMutation();
        showToast("Reiniciando... aguarde o ComfyUI voltar.", "success");
        setStatusPollingCadence(2000, true);
        if (statusPolling.restartRestoreTimer) clearTimeout(statusPolling.restartRestoreTimer);
        statusPolling.restartRestoreTimer = setTimeout(() => {
            state.isRestarting = false;
            setStatusPollingCadence(5000, true);
        }, 30000);
    } catch (error) {
        settleLifecycleMutation();
        console.error("Erro no restart:", error);
        state.isRestarting = false;
        button.disabled = false;
        showToast("Falha ao reiniciar ComfyUI.", "error");
    }
}

async function shutdownArrakis() {
    if (state.isShuttingDown) return;
    if (!confirm("Desligar o Arrakis Start e o ComfyUI? Downloads incompletos de modelos serão apagados.")) return;
    setShuttingDown(true);
    beginLifecycleMutation();
    try {
        const response = await fetch("/api/shutdown", { method: "POST" });
        if (!response.ok) {
            setShuttingDown(false);
            showToast("Falha ao desligar.", "error");
            return;
        }
        showToast("Arrakis Start desligando...", "success");
    } catch {
        showToast("Arrakis Start desligado.", "success");
    } finally {
        settleLifecycleMutation();
    }
}

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
