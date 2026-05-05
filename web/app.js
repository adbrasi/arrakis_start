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
        const data = await response.json();
        updateStatusUI(data);
    } catch {
        updateStatusUI({ running: false, status: 'unknown' });
    }
}

function updateStatusUI(data) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const restartBtn = document.getElementById('restart-btn');

    // Remove all status classes
    dot.classList.remove('running', 'stopped', 'starting', 'error');

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
    } else {
        dot.classList.add('stopped');
        text.textContent = 'ComfyUI: Parado';
        restartBtn.disabled = isRestarting;
    }
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
        data.presets.forEach(preset => {
            const card = createPresetCard(preset);
            container.appendChild(card);
        });

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

    if (preset.workflow_url) {
        const wfLink = document.createElement('a');
        wfLink.className = 'workflow-link';
        wfLink.href = preset.workflow_url;
        wfLink.title = 'Baixar workflow (arraste para o ComfyUI)';
        wfLink.innerHTML = `<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M8 12l-4-4h2.5V2h3v6H12L8 12zm-6 2h12v1.5H2V14z"/></svg> Workflow`;
        if (preset.workflow_local) {
            wfLink.download = preset.workflow_file || 'workflow.json';
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
// Install & Start
// ============================================
async function startWithPresets() {
    if (selectedPresets.length === 0 || isInstalling) return;

    const startBtn = document.getElementById('start-btn');
    isInstalling = true;
    startBtn.disabled = true;
    startBtn.textContent = 'Instalando...';

    showToast('Instalando presets e iniciando ComfyUI...', 'info');

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
                    if (statusData.running) {
                        clearInterval(installPoll);
                        await loadPresets();
                        selectedPresets = [];
                        isInstalling = false;
                        updateStartButton();
                    }
                } catch {
                    // Keep polling on network errors
                }
            }, 3000);
        } else {
            throw new Error('Falha na requisicao de instalacao');
        }

    } catch (error) {
        console.error('Erro na instalacao:', error);
        showToast('Instalacao falhou. Verifique o console para detalhes.', 'error');
        isInstalling = false;
        startBtn.disabled = false;
        startBtn.textContent = 'Iniciar com Presets Selecionados';
    }
}

// ============================================
// Shutdown
// ============================================
async function shutdownArrakis() {
    if (!confirm('Tem certeza que deseja desligar o Arrakis Start e o ComfyUI?')) return;
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
});
