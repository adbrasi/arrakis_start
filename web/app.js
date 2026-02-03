// Arrakis Start v2.0 - Frontend Logic

let selectedPresets = new Set();
let allPresets = [];
let isInstalling = false;
let statusInterval = null;

// Load on page load
document.addEventListener('DOMContentLoaded', () => {
    loadPresets();
    loadStatus();
    setupEventListeners();

    // Poll status every 5 seconds
    statusInterval = setInterval(loadStatus, 5000);
});

function setupEventListeners() {
    document.getElementById('install-btn').addEventListener('click', installSelectedPresets);
    document.getElementById('skip-btn').addEventListener('click', skipAndStartComfyUI);
    document.getElementById('stop-btn').addEventListener('click', stopInstallation);
    document.getElementById('reset-btn').addEventListener('click', resetAndStartOver);

    // ComfyUI controls
    document.getElementById('start-comfy-btn').addEventListener('click', () => controlComfyUI('start'));
    document.getElementById('stop-comfy-btn').addEventListener('click', () => controlComfyUI('stop'));
    document.getElementById('restart-comfy-btn').addEventListener('click', () => controlComfyUI('restart'));
}

async function loadStatus() {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) return;

        const status = await response.json();

        // Update ComfyUI status
        updateComfyUIStatus(status.comfyui);

        // Update installed presets list
        updateInstalledPresets(status.installed_presets);

    } catch (error) {
        console.error('Failed to load status:', error);
    }
}

function updateComfyUIStatus(comfyui) {
    const statusEl = document.getElementById('comfyui-status');
    const dot = statusEl.querySelector('.status-dot');
    const text = statusEl.querySelector('.status-text');
    const url = document.getElementById('comfyui-url');

    // Update status indicator
    dot.className = 'status-dot';
    if (comfyui.status === 'running' && comfyui.is_healthy) {
        dot.classList.add('running');
        text.textContent = `Running on port ${comfyui.port}`;
        url.textContent = `http://localhost:${comfyui.port}`;
        url.href = `http://localhost:${comfyui.port}`;
    } else if (comfyui.status === 'starting') {
        dot.classList.add('starting');
        text.textContent = 'Starting...';
    } else {
        dot.classList.add('stopped');
        text.textContent = 'Stopped';
    }

    // Update button states
    const startBtn = document.getElementById('start-comfy-btn');
    const stopBtn = document.getElementById('stop-comfy-btn');
    const restartBtn = document.getElementById('restart-comfy-btn');

    if (comfyui.is_running) {
        startBtn.disabled = true;
        stopBtn.disabled = false;
        restartBtn.disabled = false;
    } else {
        startBtn.disabled = false;
        stopBtn.disabled = true;
        restartBtn.disabled = true;
    }
}

function updateInstalledPresets(presets) {
    const listEl = document.getElementById('installed-presets-list');

    if (presets.length === 0) {
        listEl.innerHTML = '<p class="empty-text">No presets installed yet</p>';
        return;
    }

    listEl.innerHTML = presets.map(p =>
        `<div class="installed-item">✓ ${p}</div>`
    ).join('');
}

async function controlComfyUI(action) {
    const statusMessage = document.getElementById('status-message');

    try {
        const response = await fetch(`/api/comfyui/${action}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (result.success) {
            statusMessage.className = 'status-message success';
            statusMessage.textContent = `✓ ${result.message}`;
        } else {
            statusMessage.className = 'status-message error';
            statusMessage.textContent = `✗ ${result.message}`;
        }

        // Refresh status immediately
        setTimeout(loadStatus, 1000);

    } catch (error) {
        console.error(`ComfyUI ${action} error:`, error);
        statusMessage.className = 'status-message error';
        statusMessage.textContent = `✗ Failed to ${action} ComfyUI`;
    }
}

async function loadPresets() {
    const container = document.getElementById('presets-container');

    try {
        const response = await fetch('/api/presets');
        if (!response.ok) throw new Error('Failed to load presets');

        allPresets = await response.json();

        if (allPresets.length === 0) {
            container.innerHTML = '<p class="no-presets">No presets found.</p>';
            return;
        }

        // Filter out "Base" preset (auto-included)
        const visiblePresets = allPresets.filter(p => p.name !== 'Base');

        if (visiblePresets.length === 0) {
            container.innerHTML = '<p class="no-presets">Only base preset available.</p>';
            return;
        }

        // Render preset cards
        container.innerHTML = '';
        visiblePresets.forEach(preset => {
            const card = createPresetCard(preset);
            container.appendChild(card);
        });

    } catch (error) {
        console.error('Error loading presets:', error);
        container.innerHTML = `<p class="error">Failed to load presets: ${error.message}</p>`;
    }
}

function createPresetCard(preset) {
    const card = document.createElement('div');
    card.className = 'preset-card';

    // Add installed class if already installed
    if (preset.installed) {
        card.classList.add('installed');
    }

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `preset-${preset.name}`;
    checkbox.className = 'preset-checkbox';
    checkbox.addEventListener('change', (e) => {
        if (e.target.checked) {
            selectedPresets.add(preset.name);
            card.classList.add('selected');
        } else {
            selectedPresets.delete(preset.name);
            card.classList.remove('selected');
        }
        updateInstallButton();
    });

    const label = document.createElement('label');
    label.htmlFor = `preset-${preset.name}`;

    const installedBadge = preset.installed ? '<span class="installed-badge">✓ Installed</span>' : '';

    label.innerHTML = `
        ${installedBadge}
        <h3>${preset.name}</h3>
        <p class="description">${preset.description}</p>
        <div class="stats">
            <span class="stat">📦 ${preset.models_count} models</span>
            <span class="stat">🔧 ${preset.nodes_count} nodes</span>
        </div>
    `;

    card.appendChild(checkbox);
    card.appendChild(label);

    // Make entire card clickable
    card.addEventListener('click', (e) => {
        if (e.target !== checkbox && !isInstalling) {
            checkbox.checked = !checkbox.checked;
            checkbox.dispatchEvent(new Event('change'));
        }
    });

    return card;
}

function updateInstallButton() {
    const installBtn = document.getElementById('install-btn');
    const count = selectedPresets.size;

    if (count > 0 && !isInstalling) {
        installBtn.disabled = false;
        installBtn.textContent = `Install ${count} Preset${count > 1 ? 's' : ''} + Base`;
    } else if (!isInstalling) {
        installBtn.disabled = true;
        installBtn.textContent = 'Install Selected Presets';
    }
}

function showControlButtons(show) {
    const controlButtons = document.getElementById('control-buttons');
    const installBtn = document.getElementById('install-btn');
    const skipBtn = document.getElementById('skip-btn');

    if (show) {
        controlButtons.style.display = 'flex';
        installBtn.style.display = 'none';
        skipBtn.style.display = 'none';
    } else {
        controlButtons.style.display = 'none';
        installBtn.style.display = 'block';
        skipBtn.style.display = 'block';
    }
}

async function stopInstallation() {
    const statusMessage = document.getElementById('status-message');
    const stopBtn = document.getElementById('stop-btn');

    stopBtn.disabled = true;
    stopBtn.textContent = 'Stopping...';

    try {
        const response = await fetch('/api/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        if (response.ok) {
            statusMessage.className = 'status-message warning';
            statusMessage.textContent = '⚠️ Installation stopped. Click Reset to start over.';
            isInstalling = false;
        }
    } catch (error) {
        console.error('Stop error:', error);
        statusMessage.className = 'status-message error';
        statusMessage.textContent = `Failed to stop: ${error.message}`;
    }

    stopBtn.disabled = false;
    stopBtn.textContent = '⏹️ Stop Installation';
}

async function resetAndStartOver() {
    const statusMessage = document.getElementById('status-message');
    const progressSection = document.getElementById('progress-section');
    const progressFill = document.getElementById('progress-fill');

    // Reset UI state
    isInstalling = false;
    selectedPresets.clear();

    // Reset all checkboxes
    document.querySelectorAll('.preset-checkbox').forEach(cb => {
        cb.checked = false;
    });
    document.querySelectorAll('.preset-card').forEach(card => {
        card.classList.remove('selected');
    });

    // Hide progress and control buttons
    progressSection.style.display = 'none';
    progressFill.style.width = '0%';
    showControlButtons(false);

    // Reset status message
    statusMessage.className = 'status-message';
    statusMessage.textContent = '';

    // Reset install button
    updateInstallButton();

    // Reload presets to update installed status
    await loadPresets();
    await loadStatus();
}

async function skipAndStartComfyUI() {
    const skipBtn = document.getElementById('skip-btn');
    const statusMessage = document.getElementById('status-message');
    const progressSection = document.getElementById('progress-section');
    const progressText = document.getElementById('progress-text');

    skipBtn.disabled = true;
    skipBtn.textContent = 'Starting...';
    document.getElementById('install-btn').disabled = true;

    progressSection.style.display = 'block';
    progressText.textContent = 'Starting ComfyUI without presets...';

    try {
        const response = await fetch('/api/comfyui/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (result.success) {
            statusMessage.className = 'status-message success';
            statusMessage.textContent = '✓ ComfyUI started! Access at http://localhost:8818';
            progressText.textContent = 'ComfyUI is running (no models installed)';

            // Refresh status
            setTimeout(loadStatus, 1000);
        } else {
            throw new Error(result.message);
        }

    } catch (error) {
        console.error('Start error:', error);
        statusMessage.className = 'status-message error';
        statusMessage.textContent = `✗ Failed to start ComfyUI: ${error.message}`;
        skipBtn.disabled = false;
        skipBtn.textContent = 'Skip & Start ComfyUI Only';
        document.getElementById('install-btn').disabled = selectedPresets.size === 0;
        progressSection.style.display = 'none';
    }
}

async function installSelectedPresets() {
    if (selectedPresets.size === 0) return;

    const installBtn = document.getElementById('install-btn');
    const statusMessage = document.getElementById('status-message');
    const progressSection = document.getElementById('progress-section');
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');

    // Set installing state
    isInstalling = true;

    // Update UI
    installBtn.disabled = true;
    document.getElementById('skip-btn').disabled = true;
    showControlButtons(true);

    // Show progress section
    progressSection.style.display = 'block';
    progressFill.style.width = '10%';
    progressText.textContent = 'Starting installation (Base + selected presets)...';

    try {
        const response = await fetch('/api/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                presets: Array.from(selectedPresets)
            })
        });

        if (!response.ok) throw new Error('Installation request failed');

        const result = await response.json();

        // Simulate progress (TODO: replace with WebSocket)
        simulateProgress();

        statusMessage.className = 'status-message success';
        statusMessage.textContent = '✓ Installation started! Check terminal for real-time progress. ComfyUI will auto-start when ready.';

        // Poll status more frequently during installation
        const installCheck = setInterval(async () => {
            await loadStatus();
            await loadPresets();
        }, 3000);

        // Stop polling after 5 minutes
        setTimeout(() => clearInterval(installCheck), 300000);

    } catch (error) {
        console.error('Installation error:', error);
        statusMessage.className = 'status-message error';
        statusMessage.textContent = `✗ Installation failed: ${error.message}`;
        isInstalling = false;
        showControlButtons(false);
        installBtn.disabled = false;
        document.getElementById('skip-btn').disabled = false;
        progressSection.style.display = 'none';
    }
}

function simulateProgress() {
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');

    let progress = 10;
    const interval = setInterval(() => {
        if (!isInstalling) {
            clearInterval(interval);
            return;
        }

        progress += Math.random() * 10;
        if (progress >= 95) {
            progress = 95;
            clearInterval(interval);
            progressText.textContent = 'Finalizing installation...';
        }

        progressFill.style.width = `${progress}%`;

        if (progress < 30) {
            progressText.textContent = 'Downloading models (check terminal for speeds)...';
        } else if (progress < 60) {
            progressText.textContent = 'Installing custom nodes...';
        } else if (progress < 90) {
            progressText.textContent = 'Configuring ComfyUI...';
        }
    }, 2000);
}
