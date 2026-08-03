// Arrakis Start - UI Logic

// Selection is an ordered array (not a Set) to track order
let selectedPresets = [];
let isInstalling = false;
let isRestarting = false;
let statusPollTimer = null;

// ============================================
// Toast Notifications
// ============================================
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 5000);
}

// ============================================
// Status Polling
// ============================================
async function pollStatus() {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) {
            // A server error is not the same as "ComfyUI stopped" — say so.
            updateStatusUI({ running: false, status: 'unreachable' });
            return;
        }
        const data = await response.json();
        updateStatusUI(data);
    } catch {
        updateStatusUI({ running: false, status: 'unreachable' });
    }
}

function formatBytes(bytes) {
    if (!bytes || bytes < 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

function renderProgress(progress) {
    const panel = document.getElementById('progress-panel');
    if (!panel) return;

    const stages = progress?.stages || {};
    const stageNames = Object.keys(stages);
    if (!progress || (!progress.active?.length && !progress.total && !stageNames.length)) {
        panel.hidden = true;
        return;
    }
    panel.hidden = false;

    const stageEl = document.getElementById('progress-stage');
    if (stageEl) {
        // Lanes run concurrently (models download while nodes install), so each
        // gets its own line rather than competing for one.
        stageEl.textContent = '';
        if (progress.total) {
            const counts = document.createElement('div');
            counts.textContent = `Modelos: ${progress.done}/${progress.total}`;
            stageEl.appendChild(counts);
        }
        for (const lane of stageNames) {
            const line = document.createElement('div');
            line.textContent = `${lane}: ${stages[lane]}`;
            stageEl.appendChild(line);
        }
    }

    const list = document.getElementById('progress-files');
    if (!list) return;
    list.textContent = '';
    for (const file of progress.active || []) {
        const row = document.createElement('div');
        row.className = 'progress-row';

        const name = document.createElement('span');
        name.className = 'progress-name';
        name.textContent = file.filename + (file.backend ? ` [${file.backend}]` : '');
        row.appendChild(name);

        const bar = document.createElement('div');
        bar.className = 'progress-bar';
        const fill = document.createElement('div');
        fill.className = 'progress-fill';
        // No total means no percentage — show bytes rather than a fake 0%.
        if (file.total > 0) {
            const pct = Math.min(100, (file.current / file.total) * 100);
            fill.style.width = `${pct}%`;
        } else {
            fill.style.width = '0%';
            bar.classList.add('indeterminate');
        }
        bar.appendChild(fill);
        row.appendChild(bar);

        const stats = document.createElement('span');
        stats.className = 'progress-stats';
        const speed = file.speed_bps ? ` @ ${formatBytes(file.speed_bps)}/s` : '';
        stats.textContent = file.total > 0
            ? `${formatBytes(file.current)} / ${formatBytes(file.total)}${speed}`
            : `${formatBytes(file.current)}${speed}`;
        row.appendChild(stats);

        list.appendChild(row);
    }
}

function updateStatusUI(data) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const restartBtn = document.getElementById('restart-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    const startBtn = document.getElementById('start-btn');

    // Remove all status classes
    dot.classList.remove('running', 'stopped', 'starting', 'error');

    // An install in progress is authoritative and survives a page reload, so it
    // is checked before the ComfyUI state: module flags reset on reload, which
    // used to show "Parado" with the Install button live during a running install.
    if (data.installing) {
        isInstalling = true;
        dot.classList.add('starting');
        text.textContent = 'Instalando...';
        restartBtn.disabled = true;
        if (startBtn) startBtn.disabled = true;
        if (cancelBtn) cancelBtn.hidden = false;
    } else {
        if (isInstalling) {
            // The install finished while this tab was not the one that started it.
            isInstalling = false;
            if (startBtn) startBtn.disabled = false;
            if (cancelBtn) cancelBtn.hidden = true;
        }

        if (data.running) {
            dot.classList.add('running');
            text.textContent = `ComfyUI: Rodando (porta ${data.port || 8818})`;
            restartBtn.disabled = isRestarting;
        } else if (data.status === 'starting') {
            dot.classList.add('starting');
            text.textContent = 'ComfyUI: Iniciando...';
            restartBtn.disabled = true;
        } else if (data.status === 'error') {
            dot.classList.add('error');
            text.textContent = 'ComfyUI: Erro';
            restartBtn.disabled = isRestarting;
        } else if (data.status === 'unreachable') {
            dot.classList.add('error');
            text.textContent = 'Servidor inacessível';
            restartBtn.disabled = true;
        } else {
            dot.classList.add('stopped');
            text.textContent = 'ComfyUI: Parado';
            restartBtn.disabled = isRestarting;
        }
    }

    renderProgress(data.progress);
}

function startStatusPolling() {
    pollStatus();
    statusPollTimer = setInterval(pollStatus, 5000);
}

// ============================================
// Restart ComfyUI
// ============================================
async function restartComfyUI() {
    if (isRestarting || isInstalling) return;

    const restartBtn = document.getElementById('restart-btn');
    isRestarting = true;
    restartBtn.disabled = true;
    restartBtn.classList.add('restarting');

    showToast('Reiniciando ComfyUI...', 'info');

    try {
        const response = await fetch('/api/restart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        if (response.ok) {
            showToast('Reiniciando... aguarde o ComfyUI voltar.', 'success');
            // Poll more frequently during restart
            const fastPoll = setInterval(pollStatus, 2000);
            setTimeout(() => {
                clearInterval(fastPoll);
                isRestarting = false;
                restartBtn.classList.remove('restarting');
                restartBtn.disabled = false;
            }, 30000);
        } else {
            throw new Error('Falha no restart');
        }
    } catch (error) {
        console.error('Erro no restart:', error);
        showToast('Falha ao reiniciar ComfyUI.', 'error');
        isRestarting = false;
        restartBtn.classList.remove('restarting');
        restartBtn.disabled = false;
    }
}

// ============================================
// Load Presets
// ============================================
async function loadPresets() {
    try {
        const response = await fetch('/api/presets');
        const data = await response.json();

        const container = document.getElementById('presets-container');
        const installedList = document.getElementById('installed-presets-list');

        if (!data.presets || data.presets.length === 0) {
            container.innerHTML = '<div class="loading">Nenhum preset disponivel</div>';
            return;
        }

        // Update installed presets list (chips only \u2014 delete UX lives in the popup)
        const installed = data.presets.filter(p => p.installed).map(p => p.name);
        if (installed.length === 0) {
            installedList.innerHTML = '<p class="empty-text">Nenhum preset instalado ainda</p>';
        } else {
            installedList.innerHTML = '';
            installed.forEach(name => {
                const item = document.createElement('div');
                item.className = 'installed-item';
                item.textContent = '\u2713 ' + name;
                installedList.appendChild(item);
            });
        }
        renderManageList(installed);

        // Render preset cards
        container.innerHTML = '';
        // Drop selections whose preset no longer exists (e.g. it was deleted or
        // renamed), otherwise the button counts presets that are not on screen.
        const availableNames = new Set(data.presets.map(p => p.name));
        selectedPresets = selectedPresets.filter(name => availableNames.has(name));
        data.presets.forEach(preset => {
            const card = createPresetCard(preset);
            container.appendChild(card);
        });
        // Re-rendering rebuilds the DOM, so restore the selection state that
        // lives in `selectedPresets` — otherwise the checkboxes render unchecked
        // while the array still drives the install.
        restoreSelectionUI();
        updateAllOrderBadges();
        updateStartButton();

    } catch (error) {
        console.error('Falha ao carregar presets:', error);
        showToast('Falha ao carregar presets', 'error');
    }
}

function createPresetCard(preset) {
    const card = document.createElement('div');
    card.className = 'preset-card';

    // Selection order badge
    const orderBadge = document.createElement('div');
    orderBadge.className = 'selection-order';
    orderBadge.dataset.presetName = preset.name;
    card.appendChild(orderBadge);

    // Header: checkbox + title + installed badge
    const header = document.createElement('div');
    header.className = 'card-header';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `preset-${preset.name}`;
    checkbox.className = 'preset-checkbox';
    checkbox.addEventListener('change', (e) => {
        e.stopPropagation();
        if (e.target.checked) {
            selectedPresets.push(preset.name);
            card.classList.add('selected');
        } else {
            selectedPresets = selectedPresets.filter(n => n !== preset.name);
            card.classList.remove('selected');
        }
        updateAllOrderBadges();
        updateStartButton();
    });

    const title = document.createElement('span');
    title.className = 'card-title';
    title.textContent = preset.name;

    header.appendChild(checkbox);
    header.appendChild(title);

    if (preset.installed) {
        const badge = document.createElement('span');
        badge.className = 'installed-badge';
        badge.textContent = '\u2713 Instalado';
        header.appendChild(badge);
    }

    // Body: description
    const body = document.createElement('div');
    body.className = 'card-body';
    const desc = document.createElement('p');
    desc.className = 'description';
    desc.textContent = preset.description;
    body.appendChild(desc);

    // Footer: stats + workflow link
    const footer = document.createElement('div');
    footer.className = 'card-footer';

    const stats = document.createElement('div');
    stats.className = 'stats';
    stats.innerHTML = `<span class="stat">\uD83D\uDCE6 ${preset.models_count} models</span><span class="stat">\uD83D\uDD27 ${preset.nodes_count} nodes</span>`;
    footer.appendChild(stats);

    for (const wf of preset.workflows || []) {
        const wfLink = document.createElement('a');
        wfLink.className = 'workflow-link';
        wfLink.href = wf.url;
        wfLink.title = 'Baixar workflow (arraste para o ComfyUI)';
        wfLink.innerHTML = `<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M8 12l-4-4h2.5V2h3v6H12L8 12zm-6 2h12v1.5H2V14z"/></svg>`;
        wfLink.appendChild(document.createTextNode(` ${wf.label || 'Workflow'}`));
        if (wf.local) {
            wfLink.download = wf.file || 'workflow.json';
        } else {
            wfLink.target = '_blank';
            wfLink.rel = 'noopener';
        }
        wfLink.addEventListener('click', (e) => e.stopPropagation());
        footer.appendChild(wfLink);
    }

    card.appendChild(header);
    card.appendChild(body);
    card.appendChild(footer);

    // Make entire card clickable (except checkbox and links)
    card.addEventListener('click', (e) => {
        if (e.target === checkbox || e.target.closest('.workflow-link') || isInstalling) return;
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change'));
    });

    return card;
}

function restoreSelectionUI() {
    // `selectedPresets` is the source of truth; the DOM is rebuilt on every
    // render and must be re-synced to it.
    for (const name of selectedPresets) {
        const checkbox = document.getElementById(`preset-${name}`);
        if (!checkbox) continue;
        checkbox.checked = true;
        checkbox.closest('.preset-card')?.classList.add('selected');
    }
}

function updateAllOrderBadges() {
    document.querySelectorAll('.selection-order').forEach(badge => {
        const name = badge.dataset.presetName;
        const idx = selectedPresets.indexOf(name);
        if (idx >= 0) {
            badge.textContent = idx + 1;
        } else {
            badge.textContent = '';
        }
    });
}

function updateStartButton() {
    const startBtn = document.getElementById('start-btn');
    const count = selectedPresets.length;

    if (count > 0 && !isInstalling) {
        startBtn.disabled = false;
        startBtn.textContent = `Iniciar com ${count} Preset${count > 1 ? 's' : ''}`;
    } else if (!isInstalling) {
        startBtn.disabled = true;
        startBtn.textContent = 'Iniciar com Presets Selecionados';
    }
}

// ============================================
// Manage Popup (delete installed presets)
// ============================================
function renderManageList(installedNames) {
    const list = document.getElementById('manage-list');
    if (!list) return;

    if (!installedNames || installedNames.length === 0) {
        list.innerHTML = '<p class="empty-text">Nenhum preset instalado ainda.</p>';
        return;
    }

    list.innerHTML = '';
    installedNames.forEach(name => {
        const row = document.createElement('div');
        row.className = 'manage-row';

        const label = document.createElement('span');
        label.className = 'row-label';
        label.title = name;
        label.textContent = name;

        const del = document.createElement('button');
        del.className = 'row-delete';
        del.type = 'button';
        del.textContent = 'Deletar';
        del.setAttribute('aria-label', `Deletar modelos do preset ${name}`);
        del.addEventListener('click', () => removePreset(name, del));

        row.appendChild(label);
        row.appendChild(del);
        list.appendChild(row);
    });
}

function toggleManagePopup(force) {
    const popup = document.getElementById('manage-popup');
    if (!popup) return;
    const willOpen = typeof force === 'boolean' ? force : popup.hasAttribute('hidden');
    if (willOpen) {
        popup.removeAttribute('hidden');
    } else {
        popup.setAttribute('hidden', '');
    }
}

// Close popup when clicking outside (or pressing Esc)
document.addEventListener('click', (e) => {
    const popup = document.getElementById('manage-popup');
    const fab = document.getElementById('manage-fab');
    if (!popup || popup.hasAttribute('hidden')) return;
    if (popup.contains(e.target) || fab.contains(e.target)) return;
    toggleManagePopup(false);
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') toggleManagePopup(false);
});

// ============================================
// Remove Preset
// ============================================
function formatBytes(bytes) {
    if (!bytes || bytes < 1024) return `${bytes || 0} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let size = bytes / 1024;
    let i = 0;
    while (size >= 1024 && i < units.length - 1) {
        size /= 1024;
        i++;
    }
    return `${size.toFixed(2)} ${units[i]}`;
}

async function removePreset(presetName, btn) {
    if (isInstalling) {
        showToast('Aguarde a instalação terminar antes de remover.', 'error');
        return;
    }
    const ok = confirm(
        `Remover modelos do preset "${presetName}"?\n\n` +
        `- Apenas modelos exclusivos serão apagados (compartilhados com outros presets ficam).\n` +
        `- Custom nodes não serão removidos.\n` +
        `- Modelos sem nome fixo (ex.: Civitai) podem ficar no disco e precisam ser removidos manualmente.`
    );
    if (!ok) return;

    if (btn) {
        btn.disabled = true;
        btn.classList.add('removing');
    }

    try {
        const response = await fetch('/api/uninstall', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preset: presetName })
        });

        const result = await response.json().catch(() => ({}));

        if (!response.ok || !result.success) {
            const msg = result.error || `Falha ao remover (HTTP ${response.status})`;
            showToast(msg, 'error');
            return;
        }

        const parts = [
            `${result.deleted?.length || 0} arquivo(s) removido(s)`,
            `${formatBytes(result.bytes_freed || 0)} liberados`
        ];
        if (result.shared_kept) parts.push(`${result.shared_kept} mantido(s) por compartilhamento`);
        if (result.civitai_skipped) parts.push(`${result.civitai_skipped} sem nome (Civitai)`);
        if (result.errors?.length) parts.push(`${result.errors.length} erro(s)`);
        showToast(`✓ ${presetName}: ` + parts.join(' • '), 'success');

        await loadPresets();
    } catch (error) {
        console.error('Erro ao remover preset:', error);
        showToast('Falha ao remover preset (rede/servidor).', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.classList.remove('removing');
        }
    }
}

// ============================================
// Cancel install
// ============================================
async function cancelInstall() {
    if (!confirm(
        'Cancelar a instalação?\n\n' +
        'Arquivos concluídos serão preservados e downloads parciais poderão ser retomados ao instalar novamente.'
    )) return;
    const btn = document.getElementById('cancel-btn');
    if (btn) btn.disabled = true;
    try {
        const r = await fetch('/api/cancel', { method: 'POST' });
        const d = await r.json().catch(() => ({}));
        showToast(
            d.cancelled ? 'Cancelamento solicitado — preservando o que já foi concluído.' : 'Nenhuma instalação ativa.',
            'info'
        );
    } catch {
        showToast('Falha ao solicitar cancelamento.', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ============================================
// Install & Start
// ============================================
async function startWithPresets() {
    if (selectedPresets.length === 0 || isInstalling) return;

    const startBtn = document.getElementById('start-btn');
    isInstalling = true;
    startBtn.disabled = true;
    startBtn.textContent = 'Instalando...';

    showToast('Instalando presets e iniciando ComfyUI...', 'info');

    const cancelBtn = document.getElementById('cancel-btn');
    if (cancelBtn) { cancelBtn.hidden = false; cancelBtn.disabled = false; }

    try {
        const extraFlagsStr = document.getElementById('extra-flags-input').value.trim();
        const extraFlags = extraFlagsStr ? extraFlagsStr.split(/\s+/).filter(Boolean) : [];

        const response = await fetch('/api/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                presets: selectedPresets,
                extra_flags: extraFlags
            })
        });

        if (response.ok) {
            showToast('Instalacao iniciada! ComfyUI sera iniciado quando estiver pronto.', 'success');

            // Poll status until ComfyUI is running again (installation complete)
            const installPoll = setInterval(async () => {
                try {
                    const statusResp = await fetch('/api/status');
                    const statusData = await statusResp.json();
                    pollStatus(); // Update UI
                    const terminalFailure = !statusData.installing &&
                        ['cancelled', 'failed', 'start_failed'].includes(statusData.install_status);
                    if (terminalFailure) {
                        clearInterval(installPoll);
                        isInstalling = false;
                        const cb = document.getElementById('cancel-btn');
                        if (cb) cb.hidden = true;
                        updateStartButton();
                        showToast(
                            statusData.install_status === 'cancelled'
                                ? 'Instalação cancelada. Você pode selecionar e instalar novamente para retomar.'
                                : statusData.install_status === 'start_failed'
                                    ? 'Arquivos instalados, mas o ComfyUI não iniciou. Consulte os logs.'
                                    : 'A instalação falhou. Consulte os logs e tente novamente.',
                            statusData.install_status === 'cancelled' ? 'info' : 'error'
                        );
                    } else if (statusData.running && !statusData.installing) {
                        clearInterval(installPoll);
                        await loadPresets();
                        selectedPresets = [];
                        isInstalling = false;
                        const cb = document.getElementById('cancel-btn');
                        if (cb) cb.hidden = true;
                        updateStartButton();
                        // Launchable but incomplete: ComfyUI is up, and the
                        // preset stays pending so a re-run resumes only what
                        // is missing.
                        if (statusData.install_status === 'completed_with_failures') {
                            showToast(
                                'ComfyUI iniciado, mas alguns itens não baixaram. ' +
                                'Veja os erros no log e execute novamente para retomar só o que falta.',
                                'info'
                            );
                        }
                    }
                } catch {
                    // Keep polling on network errors
                }
            }, 3000);
        } else {
            // Surface the server's own reason (e.g. the 409 for a concurrent
            // install) instead of a generic failure the user cannot act on.
            let serverMessage = '';
            try {
                const body = await response.json();
                serverMessage = body.error || '';
            } catch {
                // Non-JSON body; fall through to the generic message.
            }
            throw new Error(serverMessage || 'Falha na requisicao de instalacao');
        }

    } catch (error) {
        console.error('Erro na instalacao:', error);
        showToast(error.message || 'Instalacao falhou. Verifique o console para detalhes.', 'error');
        isInstalling = false;
        const cb = document.getElementById('cancel-btn');
        if (cb) cb.hidden = true;
        startBtn.disabled = false;
        startBtn.textContent = 'Iniciar com Presets Selecionados';
    }
}

// ============================================
// Shutdown
// ============================================
async function shutdownArrakis() {
    if (!confirm(
        'Desligar o Arrakis Start e o ComfyUI? Downloads incompletos de modelos serão apagados.'
    )) return;
    try {
        const response = await fetch('/api/shutdown', { method: 'POST' });
        if (response.ok) {
            showToast('Arrakis Start desligando...', 'success');
            const shutdownBtn = document.getElementById('shutdown-btn');
            shutdownBtn.disabled = true;
            const textNode = Array.from(shutdownBtn.childNodes).find(n => n.nodeType === Node.TEXT_NODE);
            if (textNode) {
                textNode.textContent = ' Desligando...';
            }
        } else {
            showToast('Falha ao desligar.', 'error');
        }
    } catch (error) {
        showToast('Arrakis Start desligado.', 'success');
    }
}

// ============================================
// Initialize
// ============================================
document.addEventListener('DOMContentLoaded', () => {
    loadPresets();
    startStatusPolling();
    document.getElementById('start-btn').addEventListener('click', startWithPresets);
    document.getElementById('restart-btn').addEventListener('click', restartComfyUI);
    document.getElementById('shutdown-btn').addEventListener('click', shutdownArrakis);
    document.getElementById('manage-fab').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleManagePopup();
    });
    document.getElementById('manage-close').addEventListener('click', () => toggleManagePopup(false));

    // Cancel install
    document.getElementById('cancel-btn').addEventListener('click', cancelInstall);
});
