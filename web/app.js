// Arrakis Start - Minimal UI Logic

let selectedPresets = new Set();
let isInstalling = false;

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
// Load Presets
// ============================================
async function loadPresets() {
    try {
        const response = await fetch('/api/presets');
        const data = await response.json();

        const container = document.getElementById('presets-container');
        const installedList = document.getElementById('installed-presets-list');

        if (!data.presets || data.presets.length === 0) {
            container.innerHTML = '<div class="loading">No presets available</div>';
            return;
        }

        // Update installed presets list
        const installed = data.presets.filter(p => p.installed).map(p => p.name);
        if (installed.length === 0) {
            installedList.innerHTML = '<p class="empty-text">No presets installed yet</p>';
        } else {
            installedList.innerHTML = installed.map(name =>
                `<div class="installed-item">✓ ${name}</div>`
            ).join('');
        }

        // Render preset cards
        container.innerHTML = '';
        data.presets.forEach(preset => {
            const card = createPresetCard(preset);
            container.appendChild(card);
        });

    } catch (error) {
        console.error('Failed to load presets:', error);
        showToast('Failed to load presets', 'error');
    }
}

function createPresetCard(preset) {
    const card = document.createElement('div');
    card.className = 'preset-card';

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
        updateStartButton();
    });

    const label = document.createElement('label');
    label.htmlFor = `preset-${preset.name}`;

    const installedBadge = preset.installed ? '<div class="installed-badge">✓ Installed</div>' : '';

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

function updateStartButton() {
    const startBtn = document.getElementById('start-btn');
    const count = selectedPresets.size;

    if (count > 0 && !isInstalling) {
        startBtn.disabled = false;
        startBtn.textContent = `Start with ${count} Preset${count > 1 ? 's' : ''}`;
    } else if (!isInstalling) {
        startBtn.disabled = true;
        startBtn.textContent = 'Start with Selected Presets';
    }
}

// ============================================
// Install & Start
// ============================================
async function startWithPresets() {
    if (selectedPresets.size === 0 || isInstalling) return;

    const startBtn = document.getElementById('start-btn');
    isInstalling = true;
    startBtn.disabled = true;
    startBtn.textContent = 'Installing...';

    showToast('Installing presets and starting ComfyUI...', 'info');

    try {
        const response = await fetch('/api/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                presets: Array.from(selectedPresets)
            })
        });

        if (response.ok) {
            showToast('Installation started! ComfyUI will start when ready.', 'success');

            // Wait a bit then reload presets to show updated installed status
            setTimeout(async () => {
                await loadPresets();
                selectedPresets.clear();
                isInstalling = false;
                updateStartButton();
            }, 3000);
        } else {
            throw new Error('Installation request failed');
        }

    } catch (error) {
        console.error('Installation error:', error);
        showToast('Installation failed. Check console for details.', 'error');
        isInstalling = false;
        startBtn.disabled = false;
        startBtn.textContent = 'Start with Selected Presets';
    }
}

// ============================================
// Initialize
// ============================================
document.addEventListener('DOMContentLoaded', () => {
    loadPresets();
    document.getElementById('start-btn').addEventListener('click', startWithPresets);
});
