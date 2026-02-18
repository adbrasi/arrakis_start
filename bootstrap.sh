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
if not cuda_version.startswith("12.8"):
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

export DEBIAN_FRONTEND=noninteractive
export GIT_TERMINAL_PROMPT=0
export PIP_ROOT_USER_ACTION=ignore
export HF_HOME="/workspace/.hf"
export HUGGINGFACE_HUB_CACHE="$HF_HOME"
# TRANSFORMERS_CACHE is deprecated in Transformers v5+; prefer HF_HOME only
unset TRANSFORMERS_CACHE || true
export TMPDIR="/workspace/.tmp"
export GIT_LFS_SKIP_SMUDGE=1
export MAX_JOBS="${MAX_JOBS:-32}"
export HF_HUB_ENABLE_HF_TRANSFER=1
export HF_TRANSFER_CONCURRENCY="${HF_TRANSFER_CONCURRENCY:-16}"
export NVCC_APPEND_FLAGS="${NVCC_APPEND_FLAGS:---threads 8}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
# Create directories
mkdir -p "$COMFY_BASE" "$HF_HOME" "$TMPDIR"

log_info "========================================="
log_info " Arrakis Start - ComfyUI Deployment"
log_info "========================================="

# 1. Install system dependencies
log_info "[1/4] Installing system dependencies..."

apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    python3-venv \
    python3-pip \
    aria2 \
    git \
    wget \
    curl \
    2>/dev/null

# Install Cloudflared
if ! command -v cloudflared &>/dev/null; then
    log_info "Installing Cloudflared..."
    mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | tee /etc/apt/sources.list.d/cloudflared.list
    apt-get update -qq && apt-get install -y cloudflared
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
    "$COMFY_PYTHON" -m pip install -q --upgrade pip wheel setuptools comfy-cli
elif [ ! -x "$COMFY_CLI" ]; then
    log_warn "comfy-cli não encontrado no venv; instalando..."
    "$COMFY_PYTHON" -m pip install -q --upgrade comfy-cli
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
    "$COMFY_CLI" --skip-prompt --workspace "$COMFY_DIR" install --fast-deps --nvidia
    log_success "ComfyUI installed"
fi

# Ensure ComfyUI Python dependencies are present even if ComfyUI folder already existed.
# This is required when /workspace/comfy/.venv is recreated from scratch.
if [ -f "$COMFY_DIR/requirements.txt" ]; then
    if [ "$COMFY_VENV_CREATED" -eq 1 ] || ! is_requirements_synced "$COMFY_DIR/requirements.txt" "$COMFY_REQ_MARKER"; then
        log_info "Syncing ComfyUI core requirements..."
        "$COMFY_PYTHON" -m pip install -q --upgrade -r "$COMFY_DIR/requirements.txt"
        mark_requirements_synced "$COMFY_DIR/requirements.txt" "$COMFY_REQ_MARKER"
        log_success "ComfyUI core requirements synced"
    else
        log_info "ComfyUI core requirements já sincronizados; pulando"
    fi
else
    log_warn "ComfyUI requirements.txt not found, skipping dependency sync"
fi

# Keep PyTorch nightly cu128 in ComfyUI runtime (Blackwell/RTX 50xx compatibility)
if torch_runtime_is_ready; then
    log_info "PyTorch nightly cu128 já está correto no runtime; pulando reinstall"
else
    log_info "PyTorch ausente/incompatível; instalando nightly cu128 no runtime..."
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

if [ -d "$ARRAKIS_DIR/.git" ]; then
    log_info "Updating Arrakis Start..."
    if timeout 45 git -C "$ARRAKIS_DIR" pull --ff-only; then
        log_success "Arrakis Start atualizado"
    else
        log_warn "Update pulado (timeout, rede ou bloqueio de git). Continuando com versão local."
    fi
else
    log_info "Cloning Arrakis Start..."
    if timeout 45 git clone --depth 1 https://github.com/adbrasi/arrakis_start.git "$ARRAKIS_DIR"; then
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

"$ARRAKIS_PYTHON" -m pip install -q --upgrade pip wheel setuptools
# HF CLI/XET live in orchestrator venv (isolated from ComfyUI runtime deps)
"$ARRAKIS_PYTHON" -m pip install -q --upgrade "huggingface_hub[cli]>=1.3.0,<2.0" hf_xet
"$ARRAKIS_PYTHON" -m pip install -q --upgrade -r "$ARRAKIS_DIR/requirements.txt"
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
exec "$ARRAKIS_PYTHON" start.py --web-only
