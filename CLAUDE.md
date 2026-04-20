# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Arrakis Start is a fast, modular ComfyUI deployment system for VastAI/Runpod cloud instances. It installs ComfyUI with preset-based model selection via a web UI, supporting parallel downloads, real-time progress, and Cloudflared tunneling.

## Running

On cloud instances the code lives at `/workspace/comfy/arrakis_start` and the Python venv at `/workspace/comfy/.venv`. Activate it before invoking `start.py`:

```bash
cd /workspace/comfy/arrakis_start
source /workspace/comfy/.venv/bin/activate

# Web selector UI only (port 8090)
python start.py --web-only

# Install presets and start ComfyUI
python start.py --presets base qwen-image --start-comfy

# Start ComfyUI with already-installed presets
python start.py --start-comfy

# Production one-liner (fresh cloud instance)
curl -L https://raw.githubusercontent.com/adbrasi/arrakis_start/main/bootstrap.sh | bash
```

No test suite or linter is configured.

## Architecture

| Module | Role |
|---|---|
| `bootstrap.sh` | Cloud entry point — installs ComfyUI, venv, cloudflared, then starts web selector. Optionally cleans a pre-existing template `/workspace/ComfyUI` before installing. |
| `start.py` | Main orchestrator — loads presets, installs nodes/models/pip deps, launches ComfyUI. Drives the runtime-stack decision (standard torch vs SageAttention installer). |
| `downloader.py` | Parallel download manager using aria2c (default 16 connections; 8 for HuggingFace LFS), with `hf_hub_download` fallback and Civitai/direct URL support. |
| `server.py` | HTTP server (port 8090) serving the web UI and REST API (`/api/presets`, `/api/install`, etc.). |
| `process_manager.py` | ComfyUI lifecycle (start/stop/restart/health check) via comfy-cli with psutil fallback. |
| `state.py` | Thread-safe persistent state in JSON (`installed_presets`, `installed_models`, `comfyui_status`, etc.) written atomically via `tempfile` + `os.replace`. |
| `websocket_server.py` | Real-time progress/log broadcasting to browser clients. |
| `web/` | Frontend UI (vanilla HTML/CSS/JS, Portuguese) — preset selector, install progress, ComfyUI controls. |

**Data flow:** Web UI → `server.py` API → `start.py` orchestrator → `downloader.py` + node installer → `state.py` persistence, with `websocket_server.py` streaming progress back to the UI.

**Runtime stack selection:** When any active preset sets `use_sage_attention: true`, `start.py` runs the unified SageAttention installer (`SAGEATTENTION_INSTALLER_URL`) and passes `--use-sage-attention` to ComfyUI. Otherwise it installs the standard torch wheel from `TORCH_INDEX_URL` (default CUDA 12.8).

## Preset System

Presets are JSON files in `presets/`. Each defines models to download, custom nodes to clone, pip packages to install, ComfyUI flags, and optional workflows. The `base.json` preset contains core nodes required by all configurations.

- **Active presets:** `*.json` files in `presets/`
- **Disabled presets:** renamed to `*.json.ignore`
- **Hidden presets:** prefixed with `.`

Key preset fields: `name`, `description`, `models[]` (`url`/`dir`/`filename`), `nodes[]` (git URLs), `pip_commands[]` (each with optional `condition` — e.g. `cuda_available` — and `allow_failure`), `comfyui_flags[]`, `use_sage_attention`, `workflow` (local file in `workflows/`) or `workflow_url` (external link).

When both workflow keys are present, the local `workflow` file wins. The web UI auto-detects new JSON files — adding a preset requires no code changes.

## Environment Variables

| Variable | Purpose |
|---|---|
| `HF_TOKEN` | HuggingFace token (required for gated models). `HUGGING_FACE_HUB_TOKEN` is also accepted. |
| `CIVITAI_TOKEN` | Civitai API token (`CIVITAI_API_KEY` and `~/.civitai/config` file also checked). |
| `GITHUB_TOKEN` / `GH_TOKEN` | Auth for private custom-node repos. |
| `COMFY_BASE` | Base install dir (default: `/workspace/comfy`). |
| `WEB_PORT` / `COMFY_PORT` | Server ports (default: 8090 / 8818). |
| `COMFY_STARTUP_TIMEOUT` | Seconds to wait for ComfyUI healthcheck (default: 120). |
| `DOWNLOAD_SPEED_LIMIT` | aria2c bandwidth throttle (e.g. `50M`; default off). |
| `ARIA2_CONNECTIONS` / `ARIA2_HF_CONNECTIONS` | Parallel connections per download (defaults: 16 / 8). |
| `HF_XET_HIGH_PERFORMANCE` | Toggle HF Xet high-perf mode; auto-disabled below `HF_XET_HP_MIN_RAM_GB` (default 48). |
| `TORCH_INDEX_URL` | Torch wheel index (default: CUDA 12.8 build). |
| `DISABLE_TEMPLATE_COMFY` / `TEMPLATE_COMFY_DIR` | Bootstrap cleanup of pre-existing template ComfyUI at `/workspace/ComfyUI` (enabled by default). |

## Conventions

- UI text and commit messages in Portuguese (pt-BR); code and identifiers in English.
- Token/credential sanitization in all log output — never log secrets.
- Atomic file writes via `tempfile` + `os.replace` in state management.
- Adding a new preset requires only a new JSON file in `presets/` — no code changes.
- Workflows go in `workflows/` as plain JSON files, referenced by the preset `workflow` field.
