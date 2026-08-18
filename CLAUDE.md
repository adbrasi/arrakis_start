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

Tests: `PYTHONPATH=. python3 -m unittest tests.test_downloader` from the repo root. No linter is configured.

## Architecture

| Module | Role |
|---|---|
| `bootstrap.sh` | Cloud entry point — installs ComfyUI, venv, cloudflared, then starts web selector. Optionally cleans a pre-existing template `/workspace/ComfyUI` before installing. |
| `start.py` | Main orchestrator — loads presets, installs nodes/models/pip deps, launches ComfyUI. Drives the runtime-stack decision (standard torch vs SageAttention installer). |
| `downloader.py` | Parallel download manager. HuggingFace chain: XET worker → aria2c (8 connections) → `hf_hub_download` HTTP → wget; other URLs use aria2c (16 connections) → wget. Civitai/direct URL support. |
| `server.py` | HTTP server (port 8090) serving the web UI and REST API (`/api/presets`, `/api/install`, etc.). |
| `process_manager.py` | ComfyUI lifecycle (start/stop/restart/health check) via comfy-cli with psutil fallback. |
| `state.py` | Thread-safe persistent state in JSON (`installed_presets`, `installed_models`, `comfyui_status`, etc.) written atomically via `tempfile` + `os.replace`. |
| `progress.py` | In-process progress registry (downloads, phases) served through `/api/status`, which the web UI polls. |
| `web/` | Frontend UI (vanilla HTML/CSS/JS, Portuguese) — preset selector, install progress, ComfyUI controls. |

**Data flow:** Web UI → `server.py` API → `start.py` orchestrator → `downloader.py` + node installer → `state.py` persistence. Progress flows back the other way through `progress.py`, which `/api/status` serves to the polling UI — cloud hosts expose only the web port, so a second socket could not reach the browser.

**Runtime stack selection:** When any active preset sets `use_sage_attention: true`, `start.py` runs the unified SageAttention installer (`SAGEATTENTION_INSTALLER_URL`) and passes `--use-sage-attention` to ComfyUI. If the downloaded wheel cannot import against the active PyTorch ABI, it rebuilds SageAttention from source and publishes the compatible wheel when `HF_TOKEN` is available. Otherwise it installs the standard torch wheel selected for the detected driver.

## Preset System

Presets are JSON files in `presets/`. Each defines models to download, custom nodes to clone, pip packages to install, ComfyUI flags, and optional workflows. The `base.json` preset contains core nodes required by all configurations.

- **Active presets:** `*.json` files in `presets/`
- **Disabled presets:** renamed to `*.json.ignore`
- **Hidden presets:** prefixed with `.`

Key preset fields: `name`, `description`, `pinned`, `size_gb`, `models[]` (`url`/`dir`/`filename`), `nodes[]` (git URLs), `pip_commands[]` (each with optional `condition` — e.g. `cuda_available` — and `allow_failure`), `comfyui_flags[]`, `use_sage_attention`, `workflows[]` (list of `{label, file|url}` — `file` is a local file in `workflows/` and wins over `url`). The legacy single `workflow`/`workflow_url` pair is still accepted and becomes a one-item list.

```json
{
  "pinned": false,
  "size_gb": 15
}
```

`pinned` selects whether the preset appears in the large-card section. `size_gb` is an author-maintained estimate of the full payload. Committed presets are ordered by their latest modifying commit rather than the commit that added them.

Two presets must never map different URLs to the same `dir` + `filename`: one path holds one file, so the first entry wins and the other is reported as a configuration conflict (a preset-data problem to fix, never a download failure — it does not block ComfyUI). Presets sharing a file must therefore share its exact URL.

The web UI auto-detects new JSON files — adding a preset requires no code changes. Committed presets are listed by their latest modifying commit (file mtime for uncommitted ones — a fresh clone flattens mtimes, so git is the source of truth on cloud instances).

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
| `XET_NO_PROGRESS_SECONDS` | Abandon XET for the aria2c fallback after this long with no delivered bytes at all (default 240). A slow-but-growing warm-up never trips it. |
| `HF_MIN_BYTES_PER_SEC` / `HF_RATE_GRACE_SECONDS` | Sliding-window throughput floor for XET and hub-HTTP transfers (defaults 10 MB/s after 180 s), divided by the number of concurrently active transfers so bandwidth sharing never trips it. A tripped transfer falls through to multi-connection aria2c instead of crawling for hours. |
| `DOWNLOAD_OVERALL_STALL_SECONDS` | Batch backstop: abort the whole download batch after this long with no new bytes from ANY file (default 900). Progress-based — a huge file transferring at full speed for 30+ minutes never trips it. |
| `NODE_PIP_STALL_SECONDS` | Kill a custom-node `pip install` after this long with no output, CPU or I/O (default 300). This is the real liveness guard — a pip starved of bandwidth by concurrent model downloads is slow, not hung. |
| `NODE_PIP_TIMEOUT_SECONDS` | Wall-clock backstop for the same command (default 1800), for a child that spins forever without ever going quiet. |
| `TORCH_INDEX_URL` | Torch wheel index (default: CUDA 12.8 build). |
| `DISABLE_TEMPLATE_COMFY` / `TEMPLATE_COMFY_DIR` | Bootstrap cleanup of pre-existing template ComfyUI at `/workspace/ComfyUI` (enabled by default). |
| `TEMPLATE_COMFY_EXTRA_DIRS` / `TEMPLATE_COMFY_PORTS` | Extra template ComfyUI dirs/ports to clean, `:`- or newline-separated (whitespace splitting cannot represent a path containing a space). Defaults cover RunPod comfyui-base (`/workspace/runpod-slim/ComfyUI`, port `8188`). Set to empty to disable. A directory is only removed when it proves to be a template (sentinel, matching supervisor conf, or no `.git`) and its `models/` holds no large files; ports are only freed when the listener is actually ComfyUI. |

## Conventions

- UI text and commit messages in Portuguese (pt-BR); code and identifiers in English.
- Token/credential sanitization in all log output — never log secrets.
- Atomic file writes via `tempfile` + `os.replace` in state management.
- Adding a new preset requires only a new JSON file in `presets/` — no code changes.
- Workflows go in `workflows/` as plain JSON files, referenced by the preset `workflow` field.
