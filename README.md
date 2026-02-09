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
2. ✅ Configure GPU-specific PyTorch (5090/4090/etc.)
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
- Auto adds `--use-sage-attention`
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

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `HF_TOKEN` | HuggingFace authentication token | Yes (for HF downloads) |
| `CIVITAI_TOKEN` | Civitai API token | Yes (for Civitai downloads) |
| `COMFY_BASE` | Base directory for ComfyUI | No (default: `/workspace/comfy`) |
| `WEB_PORT` | Web selector port | No (default: `8090`) |
| `COMFY_PORT` | ComfyUI server port | No (default: `8818`) |

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
└── web/                  # Selector UI
    ├── index.html
    ├── app.js
    └── styles.css
```

---

## Performance Optimizations

- ✅ **Parallel downloads** via aria2c (2 connections per file)
- ✅ **Smart caching** - skips existing files
- ✅ **GPU-specific PyTorch** - auto-detects 5090/4090/etc.
- ✅ **Modular installation** - download only what you need
- ✅ **Resume support** - continues interrupted downloads

---

## Troubleshooting

### Web selector not accessible
- Check VastAI/Runpod port forwarding is enabled for port 8090
- Verify firewall allows incoming connections

### Downloads failing
- Ensure `HF_TOKEN` and `CIVITAI_TOKEN` are set
- Check network connectivity
- Verify disk space is sufficient

### ComfyUI not starting
- Check logs in terminal
- Verify PyTorch is installed correctly: `python -c "import torch; print(torch.cuda.is_available())"`

