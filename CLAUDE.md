# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Arrakis Start is a fast, modular ComfyUI deployment system for VastAI/Runpod cloud instances. It installs ComfyUI with preset-based model selection via a web UI, supporting parallel downloads, real-time progress, and Cloudflared tunneling.

## Running

```bash
# Web selector UI only (port 8090)
python start.py --web-only

# Install presets and start ComfyUI
python start.py --presets base qwen-image --start-comfy

# Start ComfyUI with already-installed presets
python start.py --start-comfy

# Production one-liner (cloud instances)
curl -L https://raw.githubusercontent.com/adbrasi/arrakis_start/main/bootstrap.sh | bash
```

No test suite or linter is configured.

## Architecture

| Module | Role |
|---|---|
| `bootstrap.sh` | Cloud entry point — installs ComfyUI, venv, cloudflared, then starts web selector |
| `start.py` | Main orchestrator — loads presets, installs nodes/models/pip deps, launches ComfyUI |
| `downloader.py` | Parallel download manager using aria2c (16 connections), supports HF/Civitai/direct URLs |
| `server.py` | HTTP server (port 8090) serving the web UI and REST API (`/api/presets`, `/api/install`, etc.) |
| `process_manager.py` | ComfyUI lifecycle (start/stop/restart/health check) via comfy-cli + psutil fallback |
| `state.py` | Thread-safe persistent state in JSON (`installed_presets`, `installed_models`, `comfyui_status`, etc.) |
| `websocket_server.py` | Real-time progress/log broadcasting to browser clients |
| `web/` | Frontend UI (vanilla HTML/CSS/JS, Portuguese) — preset selector, install progress, ComfyUI controls |

**Data flow:** Web UI → `server.py` API → `start.py` orchestrator → `downloader.py` + node installer → `state.py` persistence, with `websocket_server.py` streaming progress back to the UI.

## Preset System

Presets are JSON files in `presets/`. Each defines models to download, custom nodes to clone, pip packages to install, ComfyUI flags, and optional workflows. The `base.json` preset contains core nodes required by all configurations.

- **Active presets:** `*.json` files in `presets/`
- **Disabled presets:** renamed to `*.json.ignore`
- **Hidden presets:** prefixed with `.`

Key preset fields: `name`, `description`, `models[]` (url/dir/filename), `nodes[]` (git URLs), `pip_commands[]` (with optional `condition` and `allow_failure`), `comfyui_flags[]`, `use_sage_attention`, `workflow`/`workflow_url`.

## Environment Variables

| Variable | Purpose |
|---|---|
| `HF_TOKEN` | HuggingFace token (required for gated models) |
| `CIVITAI_TOKEN` | Civitai API token |
| `COMFY_BASE` | Base install dir (default: `/workspace/comfy`) |
| `WEB_PORT` / `COMFY_PORT` | Server ports (default: 8090 / 8818) |
| `DOWNLOAD_SPEED_LIMIT` | Bandwidth throttle (e.g. `50M`) |
| `GITHUB_TOKEN` | For private custom node repos |

## Conventions

- UI text and commit messages in Portuguese (pt-BR); code and identifiers in English.
- Token/credential sanitization in all log output — never log secrets.
- Atomic file writes via `tempfile` + `os.replace` in state management.
- Adding a new preset requires only a new JSON file in `presets/` — no code changes needed.
- Workflows go in `workflows/` as plain JSON files, referenced by preset `workflow` field.
