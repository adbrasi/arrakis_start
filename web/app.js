// Arrakis Start - Frontend Logic

let selectedPresets = new Set();
let allPresets = [];

// Load presets on page load
document.addEventListener('DOMContentLoaded', () => {
    loadPresets();
    setupEventListeners();
});

function setupEventListeners() {
    const installBtn = document.getElementById('install-btn');
    const skipBtn = document.getElementById('skip-btn');

    installBtn.addEventListener('click', installSelectedPresets);
    skipBtn.addEventListener('click', skipAndStartComfyUI);
}

async function loadPresets() {
    const container = document.getElementById('presets-container');

    try {
        const response = await fetch('/api/presets');
        if (!response.ok) throw new Error('Failed to load presets');

        allPresets = await response.json();

        if (allPresets.length === 0) {
            container.innerHTML = '<p class="no-presets">No presets found. Add JSON files to the presets/ directory.</p>';
            return;
        }

        // Filter out "Base" preset (it's auto-included)
        const visiblePresets = allPresets.filter(p => p.name !== 'Base');

        if (visiblePresets.length === 0) {
            container.innerHTML = '<p class="no-presets">Only base preset available. Click "Skip & Start ComfyUI Only" to proceed.</p>';
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
    label.innerHTML = `
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
        if (e.target !== checkbox) {
            checkbox.checked = !checkbox.checked;
            checkbox.dispatchEvent(new Event('change'));
        }
    });

    return card;
}

function updateInstallButton() {
    const installBtn = document.getElementById('install-btn');
    const count = selectedPresets.size;

    if (count > 0) {
        installBtn.disabled = false;
        installBtn.textContent = `Install ${count} Preset${count > 1 ? 's' : ''} + Base`;
    } else {
        installBtn.disabled = true;
        installBtn.textContent = 'Install Selected Presets';
    }
}

async function skipAndStartComfyUI() {
    const skipBtn = document.getElementById('skip-btn');
    const statusMessage = document.getElementById('status-message');
    const progressSection = document.getElementById('progress-section');
    const progressText = document.getElementById('progress-text');

    // Disable button
    skipBtn.disabled = true;
    skipBtn.textContent = 'Starting...';

    // Show progress
    progressSection.style.display = 'block';
    progressText.textContent = 'Starting ComfyUI without presets...';

    try {
        const response = await fetch('/api/start-comfy', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) throw new Error('Failed to start ComfyUI');

        const result = await response.json();

        statusMessage.className = 'status-message success';
        statusMessage.textContent = '✓ ComfyUI started! Access at http://localhost:8818';
        progressText.textContent = 'ComfyUI is running (no models installed)';

    } catch (error) {
        console.error('Start error:', error);
        statusMessage.className = 'status-message error';
        statusMessage.textContent = `✗ Failed to start ComfyUI: ${error.message}`;
        skipBtn.disabled = false;
        skipBtn.textContent = 'Skip & Start ComfyUI Only';
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

    // Disable buttons
    installBtn.disabled = true;
    document.getElementById('skip-btn').disabled = true;
    installBtn.textContent = 'Installing...';

    // Show progress section
    progressSection.style.display = 'block';
    progressFill.style.width = '10%';
    progressText.textContent = 'Starting installation (Base + selected presets)...';

    try {
        const response = await fetch('/api/install', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                presets: Array.from(selectedPresets)
            })
        });

        if (!response.ok) throw new Error('Installation request failed');

        const result = await response.json();

        // Simulate progress (since actual progress tracking would require WebSocket)
        simulateProgress();

        statusMessage.className = 'status-message success';
        statusMessage.textContent = '✓ Installation started! Check terminal for real-time download progress. ComfyUI will auto-start when ready.';

    } catch (error) {
        console.error('Installation error:', error);
        statusMessage.className = 'status-message error';
        statusMessage.textContent = `✗ Installation failed: ${error.message}`;
        installBtn.disabled = false;
        document.getElementById('skip-btn').disabled = false;
        installBtn.textContent = 'Retry Installation';
        progressSection.style.display = 'none';
    }
}

function simulateProgress() {
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');

    let progress = 10;
    const interval = setInterval(() => {
        progress += Math.random() * 15;
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
    }, 1000);
}
