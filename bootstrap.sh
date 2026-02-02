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

# Configuration
COMFY_BASE="${COMFY_BASE:-/workspace/comfy}"
COMFY_DIR="$COMFY_BASE/ComfyUI"
VENV_DIR="$COMFY_BASE/.venv"
ARRAKIS_DIR="$COMFY_BASE/arrakis_start"

export DEBIAN_FRONTEND=noninteractive
export HF_HOME="/workspace/.hf"
export HUGGINGFACE_HUB_CACHE="$HF_HOME"
export TRANSFORMERS_CACHE="$HF_HOME"
export TMPDIR="/workspace/.tmp"
export GIT_LFS_SKIP_SMUDGE=1

# Create directories
mkdir -p "$COMFY_BASE" "$HF_HOME" "$TMPDIR"

log_info "========================================="
log_info " Arrakis Start - ComfyUI Deployment"
log_info "========================================="

# 1. Install system dependencies
log_info "[1/5] Installing system dependencies..."

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

# 2. Setup Python environment
log_info "[2/5] Setting up Python environment..."

if [ ! -d "$VENV_DIR/bin" ]; then
    python3 -m venv "$VENV_DIR"
    log_success "Virtual environment created"
else
    log_info "Virtual environment already exists"
fi

source "$VENV_DIR/bin/activate"

python -m pip install -q --upgrade pip wheel setuptools
python -m pip install -q --upgrade "huggingface_hub[cli,hf_transfer]" comfy-cli

log_success "Python environment ready"

# 3. Install ComfyUI
log_info "[3/5] Installing ComfyUI..."

if [ -f "$COMFY_DIR/main.py" ]; then
    log_warn "ComfyUI already exists, skipping installation"
else
    comfy --skip-prompt --workspace "$COMFY_DIR" install --fast-deps --nvidia
    log_success "ComfyUI installed"
fi

# 4. GPU-specific PyTorch configuration
log_info "[4/5] Configuring PyTorch for GPU..."

GPU_INFO=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo "")

if [[ "$GPU_INFO" == *"5090"* ]] || [[ "$GPU_INFO" == *"5080"* ]]; then
    log_warn "RTX 5090/5080 detected - installing PyTorch with CUDA 12.8"
    python -m pip install -q --force-reinstall \
        torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu128
elif [[ "$GPU_INFO" == *"4090"* ]] || [[ "$GPU_INFO" == *"4080"* ]]; then
    log_info "RTX 4090/4080 detected - using default PyTorch"
else
    log_info "GPU: ${GPU_INFO:-Not detected}"
fi

log_success "PyTorch configured"

# 5. Clone/update Arrakis Start
log_info "[5/5] Setting up Arrakis Start..."

if [ -d "$ARRAKIS_DIR/.git" ]; then
    log_info "Updating Arrakis Start..."
    git -C "$ARRAKIS_DIR" pull --ff-only 2>/dev/null || true
else
    log_info "Cloning Arrakis Start..."
    git clone --depth 1 https://github.com/adbrasi/arrakis_start.git "$ARRAKIS_DIR" 2>/dev/null || {
        # Fallback: if repo doesn't exist yet, copy from current directory
        if [ -f "$(dirname "$0")/start.py" ]; then
            cp -r "$(dirname "$0")" "$ARRAKIS_DIR"
        else
            log_error "Could not find Arrakis Start files"
            exit 1
        fi
    }
fi

log_success "Arrakis Start ready"

# Final message
log_info "========================================="
log_success "Bootstrap complete!"
log_info "Starting web selector on port 8090..."
log_info "Access via VastAI/Runpod port forwarding"
log_info "========================================="

# Start Arrakis Start
cd "$ARRAKIS_DIR"
exec python start.py --web-only
