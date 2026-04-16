#!/usr/bin/env bash
# Arrakis Start - Bootstrap Script
# One-liner entry point for ComfyUI deployment on VastAI/Runpod

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }

run_with_progress() {
    local label="$1"
    shift

    local interval="${LOG_HEARTBEAT_INTERVAL:-25}"
    local start_ts now elapsed next_log_at
    start_ts="$(date +%s)"
    next_log_at=$((start_ts + interval))

    "$@" &
    local cmd_pid=$!

    while kill -0 "$cmd_pid" >/dev/null 2>&1; do
        sleep 1
        now="$(date +%s)"
        if [ "$now" -ge "$next_log_at" ] && kill -0 "$cmd_pid" >/dev/null 2>&1; then
            elapsed=$((now - start_ts))
            log_info "$label... ainda executando (${elapsed}s)"
            next_log_at=$((now + interval))
        fi
    done

    if wait "$cmd_pid"; then
        now="$(date +%s)"
        elapsed=$((now - start_ts))
        log_success "$label concluido (${elapsed}s)"
    else
        local exit_code=$?
        now="$(date +%s)"
        elapsed=$((now - start_ts))
        log_error "$label falhou apos ${elapsed}s (exit code $exit_code)"
        return "$exit_code"
    fi
}

path_real() {
    local path="$1"
    readlink -f "$path" 2>/dev/null || printf '%s' "$path"
}

paths_match() {
    local left right
    left="$(path_real "$1")"
    right="$(path_real "$2")"
    [ "$left" = "$right" ]
}

requirements_hash() {
    local req_file="$1"
    sha256sum "$req_file" | awk '{print $1}'
}

is_requirements_synced() {
    local req_file="$1"
    local marker_file="$2"

    [ -f "$marker_file" ] || return 1
    [ -f "$req_file" ] || return 1

    local current_hash
    current_hash="$(requirements_hash "$req_file")"
    local saved_hash
    saved_hash="$(cat "$marker_file" 2>/dev/null || true)"

    [ "$current_hash" = "$saved_hash" ]
}

mark_requirements_synced() {
    local req_file="$1"
    local marker_file="$2"
    requirements_hash "$req_file" > "$marker_file"
}

stop_template_comfy_processes() {
    local pattern="$1"
    if pgrep -f "$pattern" >/dev/null 2>&1; then
        log_warn "Stopping template ComfyUI process(es): $pattern"
        pkill -TERM -f "$pattern" >/dev/null 2>&1 || true
        sleep 2
        pkill -KILL -f "$pattern" >/dev/null 2>&1 || true
    fi
}

template_comfy_is_still_running() {
    local template_dir="$1"
    pgrep -f "$template_dir/main.py" >/dev/null 2>&1 && return 0
    pgrep -f "comfy.*--workspace $template_dir" >/dev/null 2>&1 && return 0
    return 1
}

cleanup_template_comfyui() {
    local template_dir="$1"
    local target_dir="$2"
    local template_supervisor_conf="$3"

    log_info "Checking template-managed ComfyUI conflicts..."

    # User requested: only cleanup template when /workspace/ComfyUI exists.
    if [ ! -d "$template_dir" ]; then
        log_info "Template ComfyUI directory not found at $template_dir; skipping template cleanup."
        return 0
    fi

    # 1) Stop/disable supervisor-managed comfyui from template images.
    if command -v supervisorctl >/dev/null 2>&1; then
        if timeout 10 supervisorctl status comfyui >/dev/null 2>&1; then
            log_warn "Template supervisor service 'comfyui' detected; stopping..."
            timeout 15 supervisorctl stop comfyui >/dev/null 2>&1 || true
        fi

        if [ -f "$template_supervisor_conf" ]; then
            local disabled_conf="${template_supervisor_conf}.arrakis-disabled"
            if [ ! -f "$disabled_conf" ]; then
                mv "$template_supervisor_conf" "$disabled_conf"
                log_success "Disabled template supervisor config: $template_supervisor_conf"
            else
                rm -f "$template_supervisor_conf"
                log_info "Template supervisor config already disabled"
            fi
            timeout 10 supervisorctl reread >/dev/null 2>&1 || true
            timeout 10 supervisorctl update >/dev/null 2>&1 || true
        fi
    fi

    # 2) Stop leftover processes that may still hold 8818.
    stop_template_comfy_processes "$template_dir/main.py"
    stop_template_comfy_processes "python.*$template_dir/main.py"
    stop_template_comfy_processes "comfy.*--workspace $template_dir"

    # 3) Remove template ComfyUI folder only when it's not our target install dir.
    if paths_match "$template_dir" "$target_dir"; then
        log_warn "Template ComfyUI path equals target path ($target_dir); skipping removal."
    else
        log_warn "Removing template ComfyUI folder: $template_dir"
        rm -rf --one-file-system "$template_dir"
        log_success "Template ComfyUI folder removed"
    fi

    # 4) Soft validation: warn if cleanup was partial, but keep bootstrap running.
    if template_comfy_is_still_running "$template_dir"; then
        log_warn "Template ComfyUI process still running after cleanup attempt: $template_dir"
    fi

    if [ -d "$template_dir" ] && ! paths_match "$template_dir" "$target_dir"; then
        log_warn "Template ComfyUI directory still exists after cleanup attempt: $template_dir"
    fi

    if [ -f "$template_supervisor_conf" ]; then
        log_warn "Template supervisor config still active after cleanup attempt: $template_supervisor_conf"
    fi

    log_success "Template ComfyUI cleanup attempt completed"
}

torch_runtime_is_ready() {
    "$COMFY_PYTHON" - <<'PY' >/dev/null 2>&1
import importlib
import sys

required = ("torch", "torchvision", "torchaudio")
for module_name in required:
    try:
        importlib.import_module(module_name)
    except Exception:
        raise SystemExit(1)

import torch
cuda_version = (getattr(torch.version, "cuda", None) or "").strip()
# Accept CUDA 12.8+ and any CUDA 13.x.
# - 12.8 is the minimum for Blackwell sm_120 support in stable PyTorch
# - 13.x is the new stable default on PyPI (PyTorch 2.11+)
# - A stricter pin can be set via TORCH_CUDA_PIN_PREFIX env var
import os
pin = os.environ.get("TORCH_CUDA_PIN_PREFIX", "").strip()
if pin:
    if not cuda_version.startswith(pin):
        raise SystemExit(2)
else:
    if not (cuda_version.startswith("12.8") or cuda_version.startswith("13.")):
        raise SystemExit(2)
PY
}

# Configuration
COMFY_BASE="${COMFY_BASE:-/workspace/comfy}"
COMFY_DIR="$COMFY_BASE/ComfyUI"
ARRAKIS_DIR="$COMFY_BASE/arrakis_start"
COMFY_VENV_DIR="$COMFY_BASE/.venv"
ARRAKIS_VENV_DIR="$ARRAKIS_DIR/.venv"
COMFY_PYTHON="$COMFY_VENV_DIR/bin/python"
COMFY_CLI="$COMFY_VENV_DIR/bin/comfy"
ARRAKIS_PYTHON="$ARRAKIS_VENV_DIR/bin/python"
COMFY_REQ_MARKER="$COMFY_VENV_DIR/.arrakis_comfy_requirements.sha256"
TEMPLATE_COMFY_DIR="${TEMPLATE_COMFY_DIR:-/workspace/ComfyUI}"
TEMPLATE_COMFY_SUPERVISOR_CONF="${TEMPLATE_COMFY_SUPERVISOR_CONF:-/etc/supervisor/conf.d/comfyui.conf}"
DISABLE_TEMPLATE_COMFY="${DISABLE_TEMPLATE_COMFY:-1}"

export DEBIAN_FRONTEND=noninteractive
export GIT_TERMINAL_PROMPT=0
export PIP_ROOT_USER_ACTION=ignore
export HF_HOME="/workspace/.hf"
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
# TRANSFORMERS_CACHE is deprecated in Transformers v5+; prefer HF_HOME only
unset TRANSFORMERS_CACHE || true
export TMPDIR="/workspace/.tmp"
export GIT_LFS_SKIP_SMUDGE=1
export MAX_JOBS="${MAX_JOBS:-32}"
export HF_HUB_ENABLE_HF_TRANSFER=1
export HF_TRANSFER_CONCURRENCY="${HF_TRANSFER_CONCURRENCY:-16}"
export NVCC_APPEND_FLAGS="${NVCC_APPEND_FLAGS:---threads 8}"
# PyTorch 2.9+ renamed PYTORCH_CUDA_ALLOC_CONF to PYTORCH_ALLOC_CONF (backend-agnostic).
# Export both so old and new torch builds work without warnings.
export PYTORCH_ALLOC_CONF="${PYTORCH_ALLOC_CONF:-${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-$PYTORCH_ALLOC_CONF}"
# Create directories
mkdir -p "$COMFY_BASE" "$HF_HOME" "$TMPDIR"

log_info "========================================="
log_info " Arrakis Start - ComfyUI Deployment"
log_info "========================================="

if [ "$DISABLE_TEMPLATE_COMFY" = "1" ]; then
    cleanup_template_comfyui "$TEMPLATE_COMFY_DIR" "$COMFY_DIR" "$TEMPLATE_COMFY_SUPERVISOR_CONF"
else
    log_warn "DISABLE_TEMPLATE_COMFY=0, skipping template ComfyUI cleanup"
fi

# 1. Install system dependencies
log_info "[1/4] Installing system dependencies..."

# Fix conflicting APT sources from template images (e.g. VastAI templates
# that ship with duplicate MEGA repo entries using different Signed-By keys).
# This causes "Conflicting values set for option Signed-By" and makes
# apt-get update fail with exit 100, aborting the entire bootstrap.
if ! apt-get update -qq 2>/dev/null; then
    log_warn "apt-get update falhou — verificando sources conflitantes..."
    # Remove duplicate/conflicting MEGA repo entries
    conflicting_sources=()
    while IFS= read -r f; do
        conflicting_sources+=("$f")
    done < <(grep -rl 'mega\.nz' /etc/apt/sources.list.d/ 2>/dev/null || true)

    if [ ${#conflicting_sources[@]} -gt 0 ]; then
        log_warn "Removendo ${#conflicting_sources[@]} source(s) conflitante(s) do MEGA:"
        for src in "${conflicting_sources[@]}"; do
            log_warn "  → $src"
            rm -f "$src"
        done
    fi

    # Also check for other common conflicts: duplicate Signed-By for any repo
    # Try again after cleanup
    run_with_progress "Atualizando indices do APT (apos limpeza)" apt-get update -qq
else
    log_success "APT indices atualizados"
fi
run_with_progress "Instalando dependencias de sistema" apt-get install -y -qq --no-install-recommends \
    python3-venv \
    python3-pip \
    aria2 \
    git \
    wget \
    curl

# Install Cloudflared
if ! command -v cloudflared &>/dev/null; then
    log_info "Installing Cloudflared..."
    mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | tee /etc/apt/sources.list.d/cloudflared.list
    run_with_progress "Atualizando indices para instalar cloudflared" apt-get update -qq
    run_with_progress "Instalando cloudflared" apt-get install -y cloudflared
fi

apt-get clean
rm -rf /var/lib/apt/lists/*

log_success "System dependencies installed"

# 2. Setup ComfyUI Python environment
log_info "[2/5] Setting up ComfyUI Python environment..."

if [ ! -d "$COMFY_VENV_DIR/bin" ]; then
    python3 -m venv "$COMFY_VENV_DIR"
    COMFY_VENV_CREATED=1
    log_success "ComfyUI virtual environment created"
else
    COMFY_VENV_CREATED=0
    log_info "ComfyUI virtual environment already exists"
fi

if [ "$COMFY_VENV_CREATED" -eq 1 ]; then
    run_with_progress "Instalando tooling base do venv ComfyUI (pip/wheel/setuptools/comfy-cli)" \
        "$COMFY_PYTHON" -m pip install --progress-bar on --upgrade pip wheel setuptools comfy-cli
elif [ ! -x "$COMFY_CLI" ]; then
    log_warn "comfy-cli não encontrado no venv; instalando..."
    run_with_progress "Instalando comfy-cli no venv ComfyUI" \
        "$COMFY_PYTHON" -m pip install --progress-bar on --upgrade comfy-cli
else
    log_info "ComfyUI venv já pronto; pulando upgrade de tooling Python"
fi

# Configure hf_xet for MAXIMUM download speed (100x+ faster than default)
# HF_XET_HIGH_PERFORMANCE: saturates network/CPU for fastest downloads
# HF_XET_NUM_CONCURRENT_RANGE_GETS: increases parallel chunk reads (24-32 for fast SSD)
export HF_XET_HIGH_PERFORMANCE=1
export HF_XET_NUM_CONCURRENT_RANGE_GETS=32
export HF_HUB_DOWNLOAD_TIMEOUT=60

log_success "ComfyUI Python environment ready"

# 3. Install ComfyUI
log_info "[3/5] Installing ComfyUI..."

if [ -f "$COMFY_DIR/main.py" ]; then
    log_warn "ComfyUI already exists, skipping installation"
else
    run_with_progress "Instalando ComfyUI (comfy-cli)" \
        "$COMFY_CLI" --skip-prompt --workspace "$COMFY_DIR" install --fast-deps --nvidia
    log_success "ComfyUI installed"
fi

# Ensure ComfyUI Python dependencies are present even if ComfyUI folder already existed.
# This is required when /workspace/comfy/.venv is recreated from scratch.
if [ -f "$COMFY_DIR/requirements.txt" ]; then
    if [ "$COMFY_VENV_CREATED" -eq 1 ] || ! is_requirements_synced "$COMFY_DIR/requirements.txt" "$COMFY_REQ_MARKER"; then
        log_info "Syncing ComfyUI core requirements..."
        run_with_progress "Instalando dependencias core do ComfyUI" \
            "$COMFY_PYTHON" -m pip install --progress-bar on --upgrade -r "$COMFY_DIR/requirements.txt"
        mark_requirements_synced "$COMFY_DIR/requirements.txt" "$COMFY_REQ_MARKER"
        log_success "ComfyUI core requirements synced"
    else
        log_info "ComfyUI core requirements já sincronizados; pulando"
    fi
else
    log_warn "ComfyUI requirements.txt not found, skipping dependency sync"
fi

# Install ComfyUI-Manager v4+ pip package into workspace venv.
# comfy-cli v1.7+ installs the Manager as a pip package (comfyui_manager) rather
# than cloning it into custom_nodes/.  We ensure it lives in OUR venv so the
# runtime Python can find it.
if [ -f "$COMFY_DIR/manager_requirements.txt" ]; then
    if ! "$COMFY_PYTHON" -c 'import comfyui_manager' 2>/dev/null; then
        log_info "Installing ComfyUI-Manager pip package into workspace venv..."
        run_with_progress "Instalando comfyui-manager pip" \
            "$COMFY_PYTHON" -m pip install --progress-bar on -r "$COMFY_DIR/manager_requirements.txt"
        log_success "ComfyUI-Manager pip package installed"
    else
        log_info "ComfyUI-Manager pip package already present in workspace venv"
    fi
fi

# Keep PyTorch nightly cu128 in ComfyUI runtime (Blackwell/RTX 50xx compatibility)
if torch_runtime_is_ready; then
    log_info "PyTorch nightly cu128 já está correto no runtime; pulando reinstall"
else
    log_info "PyTorch ausente/incompatível; instalando nightly cu128 no runtime..."
    run_with_progress "Instalando PyTorch nightly cu128 (pode demorar)" \
        "$COMFY_PYTHON" -m pip install --pre --upgrade --force-reinstall \
        torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/nightly/cu128

    if torch_runtime_is_ready; then
        log_success "PyTorch nightly cu128 installed"
    else
        log_error "PyTorch install completed but validation failed (torch/torchvision/torchaudio + CUDA 12.8)"
        exit 1
    fi
fi

# 4. Clone/update Arrakis Start
log_info "[4/5] Setting up Arrakis Start..."

# Build authenticated GitHub URL if GITHUB_TOKEN is set
GITHUB_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
if [ -n "$GITHUB_TOKEN" ]; then
    ARRAKIS_CLONE_URL="https://${GITHUB_TOKEN}@github.com/adbrasi/arrakis_start.git"
    log_info "GitHub token detected — using authenticated clone URL"
else
    ARRAKIS_CLONE_URL="https://github.com/adbrasi/arrakis_start.git"
fi

if [ -d "$ARRAKIS_DIR/.git" ]; then
    log_info "Updating Arrakis Start..."
    # Configure remote URL with token (if available) before pulling
    git -C "$ARRAKIS_DIR" remote set-url origin "$ARRAKIS_CLONE_URL" 2>/dev/null || true
    if run_with_progress "Atualizando repositorio Arrakis Start (git pull)" \
        timeout 45 git -C "$ARRAKIS_DIR" pull --ff-only; then
        log_success "Arrakis Start atualizado"
    else
        log_warn "Update pulado (timeout, rede ou bloqueio de git). Continuando com versão local."
    fi
else
    log_info "Cloning Arrakis Start..."
    if run_with_progress "Clonando repositorio Arrakis Start" \
        timeout 45 git clone --depth 1 "$ARRAKIS_CLONE_URL" "$ARRAKIS_DIR"; then
        log_success "Arrakis Start clonado"
    else
        # Fallback: if repo doesn't exist yet, copy from current directory
        if [ -f "$(dirname "$0")/start.py" ]; then
            cp -r "$(dirname "$0")" "$ARRAKIS_DIR"
            log_warn "Usando fallback local para Arrakis Start"
        else
            log_error "Could not find Arrakis Start files"
            exit 1
        fi
    fi
fi

log_success "Arrakis Start ready"

# 5. Setup Arrakis orchestrator Python environment
log_info "[5/5] Setting up Arrakis orchestrator environment..."

if [ ! -d "$ARRAKIS_VENV_DIR/bin" ]; then
    python3 -m venv "$ARRAKIS_VENV_DIR"
    log_success "Arrakis virtual environment created"
else
    log_info "Arrakis virtual environment already exists"
fi

run_with_progress "Atualizando tooling base do venv Arrakis (pip/wheel/setuptools)" \
    "$ARRAKIS_PYTHON" -m pip install --progress-bar on --upgrade pip wheel setuptools
# HF CLI/XET live in orchestrator venv (isolated from ComfyUI runtime deps)
run_with_progress "Instalando huggingface_hub[cli] + hf_xet no venv Arrakis" \
    "$ARRAKIS_PYTHON" -m pip install --progress-bar on --upgrade "huggingface_hub[cli]>=1.3.0,<2.0" hf_xet

# Store HF token so hf_xet backend and gated model downloads work correctly.
# hf auth login caches the token at $HF_HOME/token, which hf_xet reads directly
# (it does NOT rely on the HF_TOKEN env var for auth in all code paths).
HF_TOKEN="${HF_TOKEN:-}"
if [ -n "$HF_TOKEN" ]; then
    HF_CLI="$ARRAKIS_VENV_DIR/bin/hf"
    if [ -x "$HF_CLI" ]; then
        if "$HF_CLI" auth login --token "$HF_TOKEN" --add-to-git-credential 2>&1; then
            log_success "HuggingFace token stored via hf auth login (gated models enabled)"
        else
            log_warn "hf auth login failed, writing token file directly as fallback"
            mkdir -p "$HF_HOME"
            printf '%s' "$HF_TOKEN" > "$HF_HOME/token"
            chmod 600 "$HF_HOME/token"
            log_success "HuggingFace token stored at $HF_HOME/token (fallback)"
        fi
    else
        # hf CLI not available yet — write token file directly
        mkdir -p "$HF_HOME"
        printf '%s' "$HF_TOKEN" > "$HF_HOME/token"
        chmod 600 "$HF_HOME/token"
        log_success "HuggingFace token stored at $HF_HOME/token (gated models enabled)"
    fi
    # Verify token is actually stored
    if [ -f "$HF_HOME/token" ]; then
        STORED_TAIL=$(tail -c 6 "$HF_HOME/token")
        log_info "HF token stored OK (tail: ...${STORED_TAIL})"
    else
        log_error "HF token file NOT found at $HF_HOME/token after login — gated models will fail!"
    fi
else
    log_warn "HF_TOKEN not set — gated model downloads will fail. Set HF_TOKEN in your environment."
fi

run_with_progress "Instalando requirements do Arrakis" \
    "$ARRAKIS_PYTHON" -m pip install --progress-bar on --upgrade -r "$ARRAKIS_DIR/requirements.txt"
log_success "Arrakis orchestrator environment ready (hf_xet enabled)"

log_info "Runtime stack (torch / sageattention) será configurada por preset na instalação."

# Final message
log_info "========================================="
log_success "Bootstrap complete!"
log_info "Starting web selector on port 8090..."
log_info "Access via VastAI/Runpod port forwarding"
log_info "========================================="

# Start Arrakis Start
cd "$ARRAKIS_DIR"
export COMFY_PYTHON="$COMFY_PYTHON"
export COMFY_CLI="$COMFY_CLI"
# Ensure the workspace venv is the active virtualenv for all child processes.
# Without this, cloud templates may have /venv/main on PATH and comfy-cli launch
# would pick up the wrong Python (with stale PyTorch / missing node deps).
export VIRTUAL_ENV="$COMFY_VENV_DIR"
export PATH="$COMFY_VENV_DIR/bin:$PATH"
exec "$ARRAKIS_PYTHON" start.py --web-only
