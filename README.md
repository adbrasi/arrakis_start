# Arrakis Start

🚀 **Fast, modular ComfyUI deployment system for VastAI/Runpod**

Deploy ComfyUI with preset-based model selection in minutes. No more downloading everything - install only what you need.

---

## Quick Start

### One-Liner Installation

```bash
export HF_TOKEN="your_hf_token_here"
export CIVITAI_TOKEN="your_civitai_token_here"
curl -L https://raw.githubusercontent.com/adbrasi/arrakis_start/main/bootstrap.sh | bash

```

This will:
1. ✅ Install ComfyUI core (~3 min)
2. ✅ Prepare Python/ComfyUI base
3. ✅ Install Cloudflared tunnel
4. ✅ Start web selector on port **8090** (listening on `0.0.0.0`)

### Access Web Selector

After bootstrap completes, access the preset selector via VastAI/Runpod port forwarding:

```
http://<your-instance-ip>:8090
```

Select your presets and click **Install**. ComfyUI will auto-start on port **8818** with Cloudflared tunnel.

---

## Available Presets

### 🎯 Base
Core models and nodes for all workflows:
- Upscalers (AnimeSharp, UltraSharp)
- Detection models (YOLO, SAM)
- Essential custom nodes (KJNodes, Impact-Pack, etc.)

### 🎨 Qwen Image Edit 2511
Latest Qwen image editing models:
- Diffusion models (fp8 + GGUF)
- Text encoder + VAE
- Lightning LoRAs (4-step, 8-step)
- ComfyUI-GGUF support

### 🌸 SDXL Anime
SDXL/Illustrious checkpoints for anime generation:
- Checkpoints (perfectxl, ChenkinNoob-XL, Hentai_Anime_RX)
- ControlNet Union SDXL
- Curated anime LoRAs
- Your custom nodes

### 🎬 wan base
WAN video-focused preset:
- WanVideo/NSFW checkpoints and LoRAs
- MMAudio models
- WAN text encoders + VAE + clip vision
- Uses `"use_sage_attention": true` to run unified SageAttention installer
- Rebuilds SageAttention when a prebuilt wheel does not match the active PyTorch ABI and publishes it when `HF_TOKEN` is available
- Auto adds `--use-sage-attention` only when `use_sage_attention=true`
- Optional preset-specific pip installs (CUDA-aware)

---

## Manual Usage

### Install Specific Presets via CLI

```bash
cd /workspace/comfy/arrakis_start
source /workspace/comfy/.venv/bin/activate

# Install base + qwen-image
python start.py --presets base qwen-image --start-comfy

# Install all presets
python start.py --presets base qwen-image sdxl-anime --start-comfy
```

### Start Web Selector Only

```bash
python start.py --web-only
```

---

## Adding Custom Presets

Create a new JSON file in `presets/` directory:

```json
{
  "name": "My Custom Preset",
  "description": "Description of what this preset includes",
  "use_sage_attention": false,
  "comfyui_flags": ["--highvram"],
  "pip_commands": [
    {
      "description": "Install optional CUDA package",
      "condition": "cuda_available",
      "command": ["install", "my-package"],
      "allow_failure": true
    }
  ],
  "models": [
    {
      "url": "https://huggingface.co/repo/model.safetensors",
      "dir": "checkpoints",
      "filename": "model.safetensors"
    }
  ],
  "nodes": [
    "https://github.com/user/ComfyUI-CustomNode"
  ]
}
```

The web UI will automatically detect and display new presets.

---

## Workflows por Preset

Alguns presets têm um workflow ComfyUI recomendado. Você pode disponibilizá-lo para download direto na UI do Arrakis Start.

### Como adicionar um workflow a um preset

1. Coloque o arquivo `.json` do workflow na pasta `workflows/`:

```
arrakis_start/
└── workflows/
    └── ltx-lip-sync.json
```

2. Adicione a chave `"workflow"` no preset correspondente:

```json
{
  "name": "ltx lip sync",
  "workflow": "ltx-lip-sync.json",
  ...
}
```

Na UI, o card do preset exibirá um botão **Workflow** — ao clicar, o arquivo é baixado direto para o computador do usuário. Basta arrastar o `.json` para o ComfyUI.

> **Nota:** a chave `"workflow_url"` (URL externa) ainda é suportada para links de workflow hospedados externamente. A chave `"workflow"` (arquivo local) tem prioridade quando ambas estiverem presentes.

---

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `HF_TOKEN` | HuggingFace authentication token | Yes (for HF downloads) |
| `CIVITAI_TOKEN` | Civitai API token | Yes (for Civitai downloads) |
| `COMFY_BASE` | Base directory for ComfyUI | No (default: `/workspace/comfy`) |
| `DISABLE_TEMPLATE_COMFY` | Auto-clean template ComfyUI (`/workspace/ComfyUI`) before install | No (default: `1`) |
| `TEMPLATE_COMFY_DIR` | Template ComfyUI directory to clean when present | No (default: `/workspace/ComfyUI`) |
| `WEB_PORT` | Web selector port | No (default: `8090`) |
| `COMFY_PORT` | ComfyUI server port | No (default: `8818`) |
| `DOWNLOAD_PARALLELISM` | Concurrent model files | No (default: `3`) |
| `ARIA2_CONNECTIONS` | Connections per generic/Civitai file | No (default: `16`) |
| `ARIA2_HF_CONNECTIONS` | Connections per Hugging Face fallback | No (default: `8`) |
| `ARIA2_STALL_TIMEOUT_SECONDS` | Seconds without new bytes before changing backend | No (default: `120`) |
| `ARRAKIS_HF_PARTIAL_DIR` | Persistent HF resume cache | No (default: `ComfyUI/.arrakis-hf-partials`) |

---

## Architecture

```
arrakis_start/
├── bootstrap.sh          # Entry point
├── start.py              # Main orchestrator
├── downloader.py         # Parallel download manager
├── server.py             # Web server
├── presets/              # Preset definitions
│   ├── base.json
│   ├── qwen-image.json
│   └── sdxl-anime.json
├── workflows/            # ComfyUI workflow files (download via UI)
│   └── *.json
└── web/                  # Selector UI
    ├── index.html
    ├── app.js
    └── styles.css
```

---

## Performance Optimizations

- ✅ **Parallel downloads** (3 files at once; up to 16 aria2c connections per generic file and 8 for HF fallback)
- ✅ **Smart caching** - skips existing files
- ✅ **Preset-driven runtime stack** - standard torch or SageAttention installer
- ✅ **Modular installation** - download only what you need
- ✅ **Resume support** - continues interrupted downloads

---

## Cancel, resume, and run again

Use the **Cancel installation** button in the web UI. Arrakis stops active
downloads, clones, and `pip` installs, preserves completed files, and keeps
partial payloads under staging names/directories. A cancelled preset is not
marked as installed.

After cancellation, select the same presets and install again:

- completed models are skipped;
- HF downloads resume from a private per-file cache;
- aria2c/wget resume the `.arrakis.part` file;
- cloned custom nodes resume `requirements.txt` when needed;
- no second final copy of a model is created.

Running `bootstrap.sh` again is also safe. Existing venvs and the checkout are
reused, ComfyUI is not cloned again when `main.py` exists, and synchronized
requirements are skipped. Bootstrap may update packages and the checkout, but
that replaces previous versions instead of creating a parallel installation.

`Ctrl+C`/`SIGTERM` on the Arrakis process now requests the same safe cancellation
before shutdown. Prefer the UI button because it keeps the page alive until a
terminal status is confirmed.

---

## Troubleshooting

### Web selector not accessible
- Check VastAI/Runpod port forwarding is enabled for port 8090
- Verify firewall allows incoming connections

### Downloads failing
- Ensure `HF_TOKEN` and `CIVITAI_TOKEN` are set
- Check network connectivity
- Verify disk space is sufficient

### Stall warnings and progress

- `sem bytes novos ... há 30s` is a warning; the backend is interrupted only at
  the full timeout (120 seconds by default).
- When the timeout is reached, Arrakis preserves the partial and tries the HTTP
  backend.
- HF progress is isolated per file and capped at 100%; parallel files no longer
  contribute to each other's byte count.
- If a model or custom node fails, ComfyUI may still start, but that preset stays
  pending in the UI so a later run can resume it.

### ComfyUI not starting
- Check logs in terminal
- Verify PyTorch is installed correctly: `python -c "import torch; print(torch.cuda.is_available())"`

### Template already has ComfyUI preinstalled
- `bootstrap.sh` now checks `/workspace/ComfyUI` and only then:
  - stops/disables template `supervisor` ComfyUI service
  - removes template folder
  - continues with standard Arrakis install in `/workspace/comfy`
- If `/workspace/ComfyUI` does not exist, no template cleanup action is performed.
