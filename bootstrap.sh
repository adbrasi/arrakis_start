#!/usr/bin/env bash
# Arrakis Start - Bootstrap Script
# One-liner entry point for ComfyUI deployment on VastAI/Runpod
#
# This file is consumed as `curl -L ... | bash`, so EVERY statement lives inside a
# function. bash runs each complete command as soon as it arrives from the stream:
# with a top-level body, a connection that dies halfway through the download would
# execute the first half of the deploy (including the destructive template cleanup)
# and still exit 0. The only top-level statement is `main "$@"` on the very last
# line, which a truncated download can never reach.

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

# Abort the deploy loudly. Used only for conditions that make everything after it
# pointless (no ComfyUI, no venv, no repo) — a bootstrap that keeps going after one
# of those ends with a green "Bootstrap complete!" over a broken instance.
die() {
    log_error "$1"
    exit 1
}

# `set -e` kills this script without printing anything, so an unguarded command
# that fails leaves the operator staring at a shell prompt with no clue which of
# 1200 lines gave up — a silent exit reads exactly like "it just doesn't work".
# Name the line and the command before going down. `die` exits rather than
# failing, so intentional aborts never reach here, and neither do failures inside
# conditions, `||` chains or `if` tests, where errexit is suspended.
on_unexpected_error() {
    local exit_code="$1" line="$2" command="$3"
    log_error "Bootstrap abortado na linha ${line} (exit ${exit_code}): ${command}"
    log_error "Isso é uma falha não tratada — reporte o trecho acima."
}
trap 'on_unexpected_error "$?" "$LINENO" "$BASH_COMMAND"' ERR

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

# True when $1 is $2 itself or a descendant of it, with both paths resolved.
path_is_inside() {
    local inner outer
    inner="$(path_real "$1")"
    outer="$(path_real "$2")"
    [ "$inner" = "$outer" ] && return 0
    [ "$outer" = "/" ] && return 0
    case "$inner" in
        "$outer"/*) return 0 ;;
    esac
    return 1
}

# Emit one entry per line from a ':'- or newline-separated list, dropping empties.
# Lists of paths are NEVER expanded unquoted: `for d in $VAR` both word-splits (so
# "/workspace/my ComfyUI" becomes two paths) and glob-expands (so "/workspace/*/
# ComfyUI" matches — and deletes — everything). ':' is the separator because a
# space is legal inside a path and a colon is not, in practice.
list_entries() {
    local raw="$1"
    local entry rest
    rest="${raw//$'\n'/:}"
    while [ -n "$rest" ]; do
        entry="${rest%%:*}"
        if [ "$entry" = "$rest" ]; then
            rest=""
        else
            rest="${rest#*:}"
        fi
        if [ -n "$entry" ]; then
            printf '%s\n' "$entry"
        fi
    done
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

# Install uv if not present. uv is a Rust-based pip replacement that is 5-10x
# faster than pip for resolving and installing Python packages. Using the
# standalone installer avoids a chicken-and-egg with pip itself.
ensure_uv_installed() {
    if command -v uv >/dev/null 2>&1; then
        return 0
    fi
    log_info "Installing uv (fast Python package installer)..."

    local installer
    installer="$(mktemp "${TMPDIR:-/tmp}/uv-install.XXXXXX.sh")" || return 1
    if ! curl -LsSf --retry 5 --retry-delay 2 --connect-timeout 15 --max-time 60 \
            -o "$installer" https://astral.sh/uv/install.sh; then
        rm -f "$installer"
        log_warn "Falha ao baixar o instalador do uv — seguindo com pip padrão (mais lento)"
        return 1
    fi
    # Never hand an unverified download straight to a root shell: curl -f already
    # fails closed on a partial transfer, and this check rejects the other realistic
    # body (a captive-portal / CDN error page served with 200).
    if ! head -n 1 "$installer" | grep -q '^#!.*sh'; then
        rm -f "$installer"
        log_warn "Instalador do uv não parece um script shell — seguindo com pip padrão (mais lento)"
        return 1
    fi

    if env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh "$installer" >/dev/null 2>&1; then
        rm -f "$installer"
        if command -v uv >/dev/null 2>&1; then
            log_success "uv instalado ($(uv --version 2>/dev/null || echo unknown))"
            return 0
        fi
    fi
    rm -f "$installer"
    log_warn "Falha ao instalar uv — seguindo com pip padrão (mais lento)"
    return 1
}

# Install Python packages into a specific interpreter. Uses `uv pip install`
# when available (major speedup), otherwise falls back to `<python> -m pip install`.
# Drops pip-only flags (--progress-bar) when using uv.
pip_install_into() {
    local python_bin="$1"
    shift

    if command -v uv >/dev/null 2>&1; then
        local filtered=()
        local skip_next=0
        local arg
        for arg in "$@"; do
            if [ "$skip_next" = "1" ]; then
                skip_next=0
                continue
            fi
            case "$arg" in
                --progress-bar)
                    skip_next=1
                    ;;
                --progress-bar=*)
                    ;;
                *)
                    filtered+=("$arg")
                    ;;
            esac
        done
        uv pip install --python "$python_bin" "${filtered[@]}"
    else
        "$python_bin" -m pip install "$@"
    fi
}

# Pre-download the heavy torch wheels with aria2c (parallel streams) BEFORE
# comfy-cli touches torch. download.pytorch.org (CloudFront) throttles a SINGLE TCP
# connection hard — the 0.5-0.8 GB torch wheel crawls at ~300 kB/s on one stream
# while the nvidia-* wheels (fetched in parallel by pip) already arrive at ~40 MB/s.
# Parallel streams saturate the link. We resolve the exact wheel URLs by reading
# the PyTorch simple-index pages (~85 KB each, fast on any link), fetch them with
# aria2c, then install the local wheels (their deps come from the same index). The
# caller then passes --skip-torch-or-directml so comfy-cli does NOT re-download torch.
# Best-effort: ANY failure returns non-zero and the caller falls back to comfy-cli's
# own torch download. Driver-aware via $TORCH_INDEX_URL (cu130 on 13.x, cu128 on 12.8).
# Honors ARIA2_CONNECTIONS and DOWNLOAD_SPEED_LIMIT (same names downloader.py uses).
# Disable with PREFETCH_TORCH=0.
prefetch_torch_via_aria2c() {
    [ "${PREFETCH_TORCH:-1}" = "1" ] || return 1
    command -v aria2c >/dev/null 2>&1 || return 1
    local wheel_dir="${TORCH_WHEEL_DIR:-/workspace/.cache/torch-wheels}"
    mkdir -p "$wheel_dir" || return 1
    rm -f "$wheel_dir"/torch-*.whl "$wheel_dir"/torchvision-*.whl "$wheel_dir"/torchaudio-*.whl

    local conns="${ARIA2_CONNECTIONS:-16}"
    case "$conns" in
        ''|*[!0-9]*)
            log_warn "ARIA2_CONNECTIONS inválido ('${ARIA2_CONNECTIONS:-}'); usando 16"
            conns=16
            ;;
    esac
    local aria2_opts=(-x"$conns" -s"$conns" -k1M --console-log-level=warn
                      --auto-file-renaming=false --allow-overwrite=true)
    local speed_limit="${DOWNLOAD_SPEED_LIMIT:-0}"
    if [ -n "$speed_limit" ] && [ "$speed_limit" != "0" ]; then
        aria2_opts+=("--max-download-limit=$speed_limit")
        log_info "Limite de banda ativo no prefetch do torch: $speed_limit"
    fi

    log_info "Pré-baixando torch via aria2c (${conns} conexões) de $TORCH_INDEX_URL..."

    # 1) Resolve the exact wheel URLs by parsing the PyTorch simple-index pages
    #    (~85 KB each → fast even on a throttled CDN, unlike pip's resolver which can
    #    try to pull whole wheels for metadata and time out). Picks the highest
    #    cp312/x86_64/linux wheel inside the pinned version window of each package,
    #    so an unattended run can never jump to a future major.
    local urls
    urls="$(ARRAKIS_TORCH_BOUNDS="${TORCH_BOUNDS[*]}" \
        timeout 90 "$COMFY_PYTHON" - "$TORCH_INDEX_URL" <<'PY'
import os, re, sys, urllib.request
base = sys.argv[1].rstrip('/')
bounds = {}
for item in os.environ.get("ARRAKIS_TORCH_BOUNDS", "").split():
    pkg, low, high = item.split(":")
    bounds[pkg] = (
        tuple(int(x) for x in low.split(".")),
        tuple(int(x) for x in high.split(".")),
    )
def ver_key(fn, pkg):
    m = re.match(re.escape(pkg) + r'-([0-9]+(?:\.[0-9]+)*)', fn)
    return tuple(int(x) for x in m.group(1).split('.')) if m else ()
def best(pkg):
    low, high = bounds.get(pkg, ((), ()))
    try:
        req = urllib.request.Request(f"{base}/{pkg}/", headers={"User-Agent": "arrakis-prefetch"})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception:
        return None
    bv, burl = (), None
    for h in re.findall(r'href="([^"]+)"', html):
        u = h.split("#", 1)[0]; fn = u.rsplit("/", 1)[-1]
        if not (fn.startswith(pkg + "-") and "cp312-cp312" in fn and "x86_64" in fn
                and "linux" in fn and fn.endswith(".whl")):
            continue
        v = ver_key(fn, pkg)
        if not v or (low and not (low <= v < high)):
            continue
        full = u if u.startswith("http") else ("https://download.pytorch.org" + u if u.startswith("/") else f"{base}/{pkg}/{u}")
        if v >= bv:
            bv, burl = v, full
    return burl
urls = [best(p) for p in ("torch", "torchvision", "torchaudio")]
if any(u is None for u in urls):
    sys.exit(1)
print("\n".join(urls))
PY
)" || { log_warn "Prefetch torch: resolução de URLs falhou; usando o download padrão do comfy-cli"; return 1; }
    [ -n "$urls" ] || return 1

    # 3) Fetch each wheel with parallel streams (defeats the per-connection throttle).
    local u
    while IFS= read -r u; do
        [ -n "$u" ] || continue
        if ! aria2c "${aria2_opts[@]}" -d "$wheel_dir" "$u"; then
            log_warn "Prefetch torch: aria2c falhou em $(basename "$u"); usando o download padrão"
            return 1
        fi
    done <<< "$urls"

    ls "$wheel_dir"/torch-*.whl >/dev/null 2>&1 || return 1

    # 4) Install the local wheels; their deps (nvidia-*, numpy, ...) resolve from the
    #    same index and are already fast.
    if ! pip_install_into "$COMFY_PYTHON" \
            "$wheel_dir"/torch-*.whl "$wheel_dir"/torchvision-*.whl "$wheel_dir"/torchaudio-*.whl \
            --index-url "$TORCH_INDEX_URL"; then
        log_warn "Prefetch torch: instalação dos wheels locais falhou; usando o download padrão"
        return 1
    fi
    return 0
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

# Mirror of process_manager.py::_is_comfy_process — the same policy has to hold
# here: a port owner that is not part of the ComfyUI stack is never killed.
process_is_comfyui() {
    local pid="$1"
    local cmdline=""
    [ -r "/proc/$pid/cmdline" ] || return 1
    cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
    [ -n "$cmdline" ] || return 1
    case "$cmdline" in
        *comfyui*) return 0 ;;
        *"comfy launch"*) return 0 ;;
        *main.py*--port*) return 0 ;;
    esac
    return 1
}

# True when some process holds a LISTEN socket on this TCP port. Read straight from
# /proc so it needs no external tool.
port_has_listener() {
    local port="$1"
    local hex
    hex="$(printf '%04X' "$port")"
    { cat /proc/net/tcp /proc/net/tcp6 2>/dev/null || true; } \
        | awk -v pat=":${hex}\$" '$4 == "0A" && $2 ~ pat { found = 1 } END { exit !found }'
}

# PIDs holding a LISTEN socket on a TCP port, one per line.
listening_pids_on_port() {
    local port="$1"
    local pids=""

    if command -v ss >/dev/null 2>&1; then
        pids="$(ss -lptnH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)"
    fi
    if [ -z "$pids" ] && command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    fi
    # fuser reports every process holding a socket on the port, listening or not, and
    # has no way to filter by state — so it is only consulted when the port really has
    # a listener, otherwise its PIDs are just clients talking to that port.
    if [ -z "$pids" ] && command -v fuser >/dev/null 2>&1 && port_has_listener "$port"; then
        pids="$(fuser -n tcp "$port" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true)"
    fi

    if [ -n "$pids" ]; then
        printf '%s\n' "$pids"
    fi
    return 0
}

stop_listeners_on_port() {
    # Stop a template ComfyUI that is LISTENING on a TCP port. Used for images that
    # launch a bare `python main.py --port 8188` (e.g. RunPod comfyui-base), which the
    # path-based pkill patterns above cannot match. Only PIDs whose cmdline belongs to
    # the ComfyUI stack are signalled; anything else on the port is reported and left
    # alone (same policy as process_manager.py).
    local port="$1"
    local pids=()
    mapfile -t pids < <(listening_pids_on_port "$port")

    local pid
    local comfy_pids=()
    for pid in "${pids[@]}"; do
        if process_is_comfyui "$pid"; then
            comfy_pids+=("$pid")
        else
            log_warn "Porta $port ocupada pelo PID $pid, que não parece ser ComfyUI; não vou matá-lo."
        fi
    done

    if [ ${#comfy_pids[@]} -eq 0 ]; then
        return 0
    fi

    log_warn "Stopping template ComfyUI listening on port $port (pids: ${comfy_pids[*]})"
    for pid in "${comfy_pids[@]}"; do kill -TERM "$pid" >/dev/null 2>&1 || true; done
    sleep 2
    for pid in "${comfy_pids[@]}"; do kill -KILL "$pid" >/dev/null 2>&1 || true; done
}

template_comfy_is_still_running() {
    local template_dir="$1"
    pgrep -f "$template_dir/main.py" >/dev/null 2>&1 && return 0
    pgrep -f "comfy.*--workspace $template_dir" >/dev/null 2>&1 && return 0
    return 1
}

# True when models/ holds anything that looks like real weights. What ComfyUI's own
# tree ships under models/ is placeholder text files and small YAML configs; a single
# large file means somebody put models there, so the directory is not disposable.
template_models_hold_user_data() {
    local template_dir="$1"
    local models_dir="$template_dir/models"
    [ -d "$models_dir" ] || return 1
    local found
    found="$(find "$models_dir" -type f -size +16M -print -quit 2>/dev/null || true)"
    [ -n "$found" ]
}

# A template ComfyUI directory may only be deleted when ALL of these hold:
#   1. removing it cannot destroy this deployment or the volume: it is not a
#      filesystem root, not an ancestor of the install dir, not inside $COMFY_BASE;
#   2. something PROVES it is an image-baked install — the image's supervisor conf
#      points at it, an explicit sentinel file authorises it, or it has no .git
#      (every real install of ComfyUI is a git checkout);
#   3. models/ holds no large files, i.e. nobody has put weights in it.
# Anything else is somebody's own ComfyUI and is left untouched. This runs
# unattended and cannot be undone, so silence is never treated as consent.
template_dir_is_disposable() {
    local template_dir="$1"
    local target_dir="$2"
    local supervisor_conf="$3"

    local real
    real="$(path_real "$template_dir")"

    if [ "$real" = "/" ]; then
        log_warn "Alvo de limpeza resolve para a raiz do filesystem ($template_dir); ignorando."
        return 1
    fi
    if path_is_inside "$target_dir" "$real"; then
        log_warn "Alvo de limpeza ($template_dir) é o diretório de instalação ou o contém ($target_dir); ignorando."
        return 1
    fi
    if path_is_inside "$real" "$COMFY_BASE"; then
        log_warn "Alvo de limpeza ($template_dir) está dentro de $COMFY_BASE; ignorando."
        return 1
    fi

    local sentinel="$template_dir/$TEMPLATE_COMFY_SENTINEL"
    if [ -f "$sentinel" ]; then
        log_info "Sentinela $sentinel presente; remoção autorizada explicitamente."
    elif [ -f "$supervisor_conf" ] && grep -qF -- "$template_dir" "$supervisor_conf" 2>/dev/null; then
        log_info "Supervisor da imagem ($supervisor_conf) aponta para $template_dir; é instalação de template."
    elif [ ! -e "$template_dir/.git" ]; then
        log_info "$template_dir não é um checkout git (sem .git); tratando como instalação de template."
    else
        log_warn "$template_dir é um checkout git e nada prova que seja de template; preservando."
        log_warn "  → para autorizar a remoção, crie o arquivo $sentinel"
        return 1
    fi

    if template_models_hold_user_data "$template_dir"; then
        log_warn "$template_dir/models contém arquivos grandes (modelos de alguém); preservando o diretório."
        return 1
    fi

    return 0
}

cleanup_template_comfyui() {
    local template_dir="$1"
    local target_dir="$2"
    local template_supervisor_conf="$3"

    log_info "Checking template-managed ComfyUI conflicts..."

    # Only cleanup template when the directory actually exists.
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

    # 2) Stop leftover processes that may still hold the template port.
    stop_template_comfy_processes "$template_dir/main.py"
    stop_template_comfy_processes "python.*$template_dir/main.py"
    stop_template_comfy_processes "comfy.*--workspace $template_dir"

    # 3) Remove the folder only when it is provably a disposable template install.
    #    The supervisor conf was checked above, so re-read it from the moved-aside
    #    copy when it has already been disabled.
    local proof_conf="$template_supervisor_conf"
    if [ ! -f "$proof_conf" ] && [ -f "${template_supervisor_conf}.arrakis-disabled" ]; then
        proof_conf="${template_supervisor_conf}.arrakis-disabled"
    fi
    if template_dir_is_disposable "$template_dir" "$target_dir" "$proof_conf"; then
        log_warn "Removing template ComfyUI folder: $template_dir"
        rm -rf --one-file-system "$template_dir"
        if [ -d "$template_dir" ]; then
            log_warn "Template ComfyUI directory still exists after cleanup attempt: $template_dir"
        else
            log_success "Template ComfyUI folder removed"
        fi
    else
        log_info "Mantendo $template_dir; apenas os processos/serviços do template foram parados."
    fi

    # 4) Soft validation: warn if cleanup was partial, but keep bootstrap running.
    if template_comfy_is_still_running "$template_dir"; then
        log_warn "Template ComfyUI process still running after cleanup attempt: $template_dir"
    fi

    if [ -f "$template_supervisor_conf" ]; then
        log_warn "Template supervisor config still active after cleanup attempt: $template_supervisor_conf"
    fi

    log_success "Template ComfyUI cleanup attempt completed"
}

cleanup_template_comfyui_all() {
    if [ "$DISABLE_TEMPLATE_COMFY" != "1" ]; then
        log_warn "DISABLE_TEMPLATE_COMFY=0, skipping template ComfyUI cleanup"
        return 0
    fi

    # Stop template ComfyUI by port first (handles bare `python main.py --port 8188`
    # from RunPod comfyui-base). Our own ports are never touched.
    local tport
    for tport in "${TEMPLATE_COMFY_PORT_LIST[@]}"; do
        case "$tport" in
            ''|*[!0-9]*)
                log_warn "Porta de template inválida ignorada: '$tport'"
                continue
                ;;
        esac
        if [ "$tport" = "$COMFY_PORT" ] || [ "$tport" = "$WEB_PORT" ]; then
            log_info "Porta $tport é nossa (ComfyUI/web selector); não vou parar nada nela."
            continue
        fi
        stop_listeners_on_port "$tport"
    done

    # Clean every known template ComfyUI dir: old VastAI (/workspace/ComfyUI) and
    # new RunPod (/workspace/runpod-slim/ComfyUI). Each call no-ops if absent.
    local tdir
    for tdir in "${TEMPLATE_COMFY_DIRS[@]}"; do
        cleanup_template_comfyui "$tdir" "$COMFY_DIR" "$TEMPLATE_COMFY_SUPERVISOR_CONF"
    done
}

gpu_is_present() {
    # True when an NVIDIA GPU is actually visible on this host.
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1
}

detect_driver_max_cuda() {
    # Max CUDA runtime version the installed driver supports, as printed in the
    # nvidia-smi header ("CUDA Version: 12.8"). Empty when it can't be read
    # (no nvidia-smi, MIG, "N/A", etc.). This is the driver capability, NOT the
    # toolkit a wheel was built against.
    command -v nvidia-smi >/dev/null 2>&1 || return 0
    # "Empty" above is a legitimate answer, so this must not FAIL to produce it.
    # grep exits 1 when the banner carries no "CUDA Version:" (NVML error, MIG,
    # "N/A"), pipefail promotes that to a failed pipeline, and a caller doing a
    # plain `var="$(detect_driver_max_cuda)"` under `set -e` then dies silently
    # mid-deploy. Unreadable is not an error here — it is the empty string.
    nvidia-smi 2>/dev/null \
        | grep -oE 'CUDA Version:[[:space:]]*[0-9]+\.[0-9]+' \
        | grep -oE '[0-9]+\.[0-9]+' \
        | head -1 || true
}

torch_runtime_is_ready() {
    # Decide whether the installed torch can drive THIS host's GPU. We compare the
    # wheel's CUDA toolkit against the driver's max supported CUDA — a wheel only
    # fails to initialise when its toolkit is NEWER than the driver supports (e.g.
    # a CUDA 13 wheel on a CUDA 12.8 driver, as on RunPod comfyui-base). This
    # version comparison is deterministic and, unlike torch.cuda.is_available(),
    # does NOT produce false negatives from CUDA_VISIBLE_DEVICES, a not-yet-warm
    # GPU, MIG, etc. is_available() is only used as a fallback when the driver's
    # max CUDA can't be read.
    local gpu_present=0
    if gpu_is_present; then gpu_present=1; fi
    local driver_max
    driver_max="$(detect_driver_max_cuda)"

    ARRAKIS_GPU_PRESENT="$gpu_present" ARRAKIS_DRIVER_MAX_CUDA="$driver_max" \
        "$COMFY_PYTHON" - <<'PY' >/dev/null 2>&1
import importlib
import os

required = ("torch", "torchvision", "torchaudio")
for module_name in required:
    try:
        importlib.import_module(module_name)
    except Exception:
        raise SystemExit(1)

import torch
cuda_version = (getattr(torch.version, "cuda", None) or "").strip()
# Floor: CUDA 12.8 is the minimum for Blackwell sm_120 in stable PyTorch.
# A stricter pin can be set via TORCH_CUDA_PIN_PREFIX env var.
pin = os.environ.get("TORCH_CUDA_PIN_PREFIX", "").strip()
if pin:
    if not cuda_version.startswith(pin):
        raise SystemExit(2)
else:
    if not (cuda_version.startswith("12.8") or cuda_version.startswith("13.")):
        raise SystemExit(2)


def _ver(value):
    try:
        return tuple(int(part) for part in value.split(".")[:2])
    except Exception:
        return None


driver_max = os.environ.get("ARRAKIS_DRIVER_MAX_CUDA", "").strip()
gpu_present = os.environ.get("ARRAKIS_GPU_PRESENT") == "1"
build, drv = _ver(cuda_version), _ver(driver_max)

# Reject ONLY when the wheel's CUDA toolkit is strictly newer than the driver
# supports. When the driver's max CUDA is unknown, fall back to the runtime probe.
if build and drv:
    if build > drv:
        raise SystemExit(3)
elif gpu_present and not torch.cuda.is_available():
    raise SystemExit(3)
PY
}

# Fix conflicting APT sources from template images (e.g. VastAI templates that ship
# duplicate MEGA repo entries with different Signed-By keys), then retry once.
apt_update_with_repair() {
    if apt-get update -qq 2>/dev/null; then
        log_success "APT indices atualizados"
        return 0
    fi

    log_warn "apt-get update falhou — verificando sources conflitantes..."
    local conflicting_sources=()
    local f
    while IFS= read -r f; do
        conflicting_sources+=("$f")
    done < <(grep -rl 'mega\.nz' /etc/apt/sources.list.d/ 2>/dev/null || true)

    if [ ${#conflicting_sources[@]} -gt 0 ]; then
        log_warn "Removendo ${#conflicting_sources[@]} source(s) conflitante(s) do MEGA:"
        local src
        for src in "${conflicting_sources[@]}"; do
            log_warn "  → $src"
            rm -f "$src"
        done
    fi

    run_with_progress "Atualizando indices do APT (apos limpeza)" apt-get update -qq
}

# Cloudflared is optional — start.py keeps the tunnel off by default — so a Cloudflare
# repo/CDN hiccup must never abort the deploy. Every network call is bounded (retries
# + timeouts) and wrapped in run_with_progress, so a stalled TCP connection cannot
# hang an unattended run silently.
install_cloudflared() {
    if command -v cloudflared >/dev/null 2>&1; then
        return 0
    fi
    log_info "Installing Cloudflared..."

    local keyring="/usr/share/keyrings/cloudflare-main.gpg"
    local keyring_tmp="${keyring}.arrakis-tmp"
    local sources_list="/etc/apt/sources.list.d/cloudflared.list"

    install -d -m 0755 /usr/share/keyrings || return 1
    if ! run_with_progress "Baixando chave GPG do Cloudflare" \
            curl -fsSL --retry 5 --retry-delay 2 --connect-timeout 15 --max-time 60 \
            -o "$keyring_tmp" https://pkg.cloudflare.com/cloudflare-main.gpg; then
        rm -f "$keyring_tmp"
        return 1
    fi
    if [ ! -s "$keyring_tmp" ]; then
        rm -f "$keyring_tmp"
        log_warn "Chave GPG do Cloudflare veio vazia"
        return 1
    fi
    mv "$keyring_tmp" "$keyring"
    chmod 0644 "$keyring"
    printf 'deb [signed-by=%s] https://pkg.cloudflare.com/cloudflared any main\n' \
        "$keyring" > "$sources_list"

    if run_with_progress "Atualizando indices para instalar cloudflared" apt-get update -qq \
        && run_with_progress "Instalando cloudflared" apt-get install -y -qq cloudflared; then
        return 0
    fi

    # Leave APT usable for every later call instead of a repo that breaks `apt-get update`.
    rm -f "$sources_list"
    return 1
}

# Install what the bootstrap needs from APT, then check the tools themselves. APT is
# best-effort: base images routinely ship a broken index, and the tools are often
# already baked in — aborting at "[1/5]" over apt would leave the instance billing
# with nothing installed. Only a genuinely missing required tool is fatal.
ensure_system_packages() {
    if apt_update_with_repair; then
        run_with_progress "Instalando dependencias de sistema" \
            apt-get install -y -qq --no-install-recommends \
            python3-venv \
            python3-pip \
            aria2 \
            git \
            wget \
            curl \
            || log_warn "apt-get install falhou — validando as ferramentas já presentes na imagem"
    else
        log_warn "APT indisponível — validando as ferramentas já presentes na imagem"
    fi

    install_cloudflared \
        || log_warn "Cloudflared não instalado (opcional: o túnel vem desligado por padrão no start.py)"

    apt-get clean || true
    rm -rf /var/lib/apt/lists/* || true

    local missing_required=()
    local missing_optional=()
    python3 -m venv --help >/dev/null 2>&1 || missing_required+=("python3 -m venv (pacote python3-venv)")
    command -v git >/dev/null 2>&1 || missing_required+=("git")
    command -v curl >/dev/null 2>&1 || missing_required+=("curl")
    command -v aria2c >/dev/null 2>&1 || missing_optional+=("aria2c (downloads de modelos ficam muito mais lentos)")
    command -v wget >/dev/null 2>&1 || missing_optional+=("wget")
    python3 -m pip --version >/dev/null 2>&1 || missing_optional+=("pip do sistema (cada venv traz o seu)")

    local item
    for item in "${missing_optional[@]}"; do
        log_warn "Ferramenta opcional ausente: $item"
    done
    if [ ${#missing_required[@]} -gt 0 ]; then
        for item in "${missing_required[@]}"; do
            log_error "Ferramenta obrigatória ausente: $item"
        done
        die "Dependências de sistema obrigatórias ausentes e o APT não conseguiu instalá-las."
    fi
}

venv_python_works() {
    local python_bin="$1"
    [ -x "$python_bin" ] || return 1
    "$python_bin" -c 'import sys' >/dev/null 2>&1
}

# Ensure $1 is a usable venv. An existing bin/ directory proves nothing: on a
# persistent volume whose base Python was replaced by an image change, bin/python is
# a dangling symlink — so the interpreter is probed and the venv rebuilt with --clear
# when it cannot run. Returns 0 when the venv was (re)created, 1 when a working one
# was reused; dies when it cannot be created at all.
ensure_venv() {
    local venv_dir="$1"
    local label="$2"

    if venv_python_works "$venv_dir/bin/python"; then
        log_info "$label virtual environment already exists"
        return 1
    fi
    if [ -e "$venv_dir" ]; then
        log_warn "venv $label existe mas o interpretador não executa; recriando com --clear"
    fi
    run_with_progress "Criando venv $label" python3 -m venv --clear "$venv_dir" \
        || die "Não foi possível criar o venv $label em $venv_dir"
    venv_python_works "$venv_dir/bin/python" \
        || die "venv $label criado, mas $venv_dir/bin/python não executa"
    log_success "$label virtual environment created"
    return 0
}

# Run git with a hard timeout and, when a GitHub token is configured, with auth passed
# out-of-band: the token reaches git through the environment of the child process only,
# so it never appears in argv (readable in /proc) and is never written to .git/config,
# which lives at mode 644 on the persistent volume and is captured by volume snapshots.
#
# Credentials are supplied LAZILY, through an askpass helper git consults only when the
# server actually challenges. An `http.*.extraheader` is eager: it is attached to every
# request, so a token GitHub rejects turns a *public* clone — which needs no credentials
# at all — into a hard 401 ("could not read Username", prompts disabled). This mirrors
# how start.py authenticates custom-node clones.
ARRAKIS_GIT_ASKPASS=""

setup_git_credentials() {
    ARRAKIS_GIT_ASKPASS=""
    [ -n "${GITHUB_TOKEN:-}" ] || return 0

    local dir
    dir="$(mktemp -d)" || return 0
    cat > "$dir/askpass.sh" <<'ASKPASS'
#!/bin/sh
case "$1" in
  *[Uu]sername*) printf '%s\n' "x-access-token" ;;
  *) printf '%s\n' "$ARRAKIS_GIT_TOKEN" ;;
esac
ASKPASS
    chmod 700 "$dir/askpass.sh"
    ARRAKIS_GIT_ASKPASS="$dir/askpass.sh"
}

cleanup_git_credentials() {
    [ -n "$ARRAKIS_GIT_ASKPASS" ] || return 0
    rm -rf "$(dirname "$ARRAKIS_GIT_ASKPASS")"
    ARRAKIS_GIT_ASKPASS=""
}

git_run() {
    local timeout_s="$1"
    shift

    if [ -z "$ARRAKIS_GIT_ASKPASS" ]; then
        GIT_TERMINAL_PROMPT=0 timeout "$timeout_s" git "$@"
        return
    fi
    # Prefix assignments scope the variables to this one child, so no subshell
    # and no leakage into the caller's environment.
    GIT_TERMINAL_PROMPT=0 \
    GIT_ASKPASS="$ARRAKIS_GIT_ASKPASS" \
    ARRAKIS_GIT_TOKEN="$GITHUB_TOKEN" \
        timeout "$timeout_s" git "$@"
}

is_safe_arrakis_git_ref() {
    local ref="$1"

    # This structural validation intentionally uses Bash only: it runs before the
    # dependency step that installs Git on a fresh cloud image. When Git is already
    # present, check-ref-format below confirms the same branch grammar natively.
    case "$ref" in
        ''|@|.|..|refs/*|-*|/*|*/|.*|*/.*|*.|*..*|*//*|*.lock|*@\{*) return 1 ;;
        *[[:cntrl:]]*|*' '*|*'~'*|*'^'*|*':'*|*'?'*|*'*'*|*'['*|*'\'*) return 1 ;;
    esac
    if command -v git >/dev/null 2>&1; then
        git check-ref-format --branch "$ref" >/dev/null 2>&1
    fi
}

configure_arrakis_git_ref() {
    ARRAKIS_GIT_REF="${ARRAKIS_GIT_REF:-main}"
    if ! is_safe_arrakis_git_ref "$ARRAKIS_GIT_REF"; then
        log_error "ARRAKIS_GIT_REF inválido: '$ARRAKIS_GIT_REF'. Informe um nome de branch Git válido."
        return 1
    fi
}

# Clone the repo into a staging dir on the same filesystem and move it into place only
# after a complete clone. Retried with backoff: by the time this runs, apt, both venvs,
# ComfyUI and a multi-GB torch download have already been paid for, so a transient
# DNS/GitHub failure must not throw all of it away.
install_arrakis_repo() {
    local dest="$1"
    local url="$2"
    local ref="$3"
    local attempt stage_dir backup
    local cloned=0

    for attempt in 1 2 3; do
        stage_dir="$(mktemp -d "${dest}.stage.XXXXXX")" || return 1
        if run_with_progress "Clonando repositorio Arrakis Start (tentativa $attempt/3)" \
                git_run 60 clone --depth 1 --single-branch --branch "$ref" "$url" "$stage_dir"; then
            cloned=1
            break
        fi
        rm -rf "$stage_dir"
        stage_dir=""
        if [ "$attempt" -lt 3 ]; then
            log_warn "Clone falhou; nova tentativa em $((attempt * 5))s"
            sleep "$((attempt * 5))"
        fi
    done

    if [ "$cloned" -ne 1 ]; then
        return 1
    fi

    if [ -e "$dest" ]; then
        backup="${dest}.broken.$(date +%Y%m%d%H%M%S)"
        mv "$dest" "$backup"
        log_warn "Diretório existente sem repositório git movido para $backup"
    fi
    mv "$stage_dir" "$dest"
    return 0
}

checkout_has_local_changes() {
    local dest="$1"
    [ -z "$(git -C "$dest" status --porcelain --untracked-files=all)" ]
}

ensure_arrakis_ref_fetchspec() {
    local dest="$1"
    local ref="$2"
    local fetchspec="+refs/heads/$ref:refs/remotes/origin/$ref"

    if git -C "$dest" config --get-all remote.origin.fetch 2>/dev/null \
            | grep -Fx -- "$fetchspec" >/dev/null; then
        return 0
    fi
    git -C "$dest" config --add remote.origin.fetch "$fetchspec"
}

# Fetch the requested branch explicitly instead of relying on origin's configured
# fetchspec. A shallow clone made with --single-branch main only knows main, so the
# ordinary `git pull` cannot discover a feature branch even when it exists remotely.
update_arrakis_repo() {
    local dest="$1"
    local url="$2"
    local ref="$3"

    # Keep the stored remote token-free. Older bootstraps embedded the token in
    # this URL, so this also scrubs a leaked credential from existing volumes.
    if ! git -C "$dest" remote set-url origin "$url"; then
        log_error "Não foi possível configurar o remoto origin em $dest. Verifique o checkout existente."
        return 1
    fi

    if ! checkout_has_local_changes "$dest"; then
        log_error "Atualização bloqueada por alterações locais em $dest. Faça commit, stash ou remova as alterações antes de rodar o bootstrap novamente."
        return 1
    fi

    if ! ensure_arrakis_ref_fetchspec "$dest" "$ref"; then
        log_error "Não foi possível registrar a branch '$ref' no fetchspec do remoto origin."
        return 1
    fi

    if ! run_with_progress "Buscando branch $ref do Arrakis Start" \
            git_run 45 -C "$dest" fetch origin "+refs/heads/$ref:refs/remotes/origin/$ref"; then
        log_error "Não foi possível buscar a branch '$ref'. Verifique a rede e se a branch existe no remoto."
        return 1
    fi

    if git -C "$dest" show-ref --verify --quiet "refs/heads/$ref"; then
        if ! git -C "$dest" switch "$ref"; then
            log_error "Não foi possível mudar para a branch '$ref' sem sobrescrever dados locais. Resolva as alterações e rode o bootstrap novamente."
            return 1
        fi
    elif ! git -C "$dest" switch --track -c "$ref" "origin/$ref"; then
        log_error "Não foi possível criar a branch local '$ref' acompanhando origin/$ref. Verifique o checkout existente."
        return 1
    fi

    if ! git -C "$dest" branch --set-upstream-to="origin/$ref" "$ref"; then
        log_error "Não foi possível configurar origin/$ref como upstream da branch '$ref'."
        return 1
    fi

    if run_with_progress "Atualizando repositorio Arrakis Start (fast-forward)" \
            git_run 45 -C "$dest" merge --ff-only "origin/$ref"; then
        log_success "Arrakis Start atualizado na branch $ref"
        return 0
    fi

    log_error "Não foi possível atualizar '$ref' somente por fast-forward. O checkout local foi preservado; resolva a divergência e rode o bootstrap novamente."
    return 1
}

# Store the HF token the way huggingface_hub and hf_xet read it: $HF_HOME/token.
# Written directly instead of via `hf auth login --token <secret>`, which would put
# the secret in argv (world-readable in /proc). The old file is removed and the new
# one created under umask 077, so the token is never readable by another process —
# not even in the window a create-then-chmod leaves open.
store_hf_token() {
    local token_file="$HF_HOME/token"
    mkdir -p "$HF_HOME"
    (
        umask 077
        rm -f "$token_file"
        printf '%s' "$HF_TOKEN" > "$token_file"
    ) || return 1

    if [ ! -s "$token_file" ]; then
        log_error "Token HuggingFace não foi gravado em $token_file — modelos gated vão falhar!"
        return 1
    fi
    log_success "Token HuggingFace armazenado em $token_file (modelos gated habilitados)"
    return 0
}

main() {
    # ------------------------------------------------------------------ Configuration
    COMFY_BASE="${COMFY_BASE:-/workspace/comfy}"
    COMFY_DIR="$COMFY_BASE/ComfyUI"
    ARRAKIS_DIR="$COMFY_BASE/arrakis_start"
    COMFY_VENV_DIR="$COMFY_BASE/.venv"
    ARRAKIS_VENV_DIR="$ARRAKIS_DIR/.venv"
    COMFY_PYTHON="$COMFY_VENV_DIR/bin/python"
    COMFY_CLI="$COMFY_VENV_DIR/bin/comfy"
    ARRAKIS_PYTHON="$ARRAKIS_VENV_DIR/bin/python"
    COMFY_REQ_MARKER="$COMFY_VENV_DIR/.arrakis_comfy_requirements.sha256"
    ARRAKIS_REPO_URL="https://github.com/adbrasi/arrakis_start.git"
    configure_arrakis_git_ref \
        || die "ARRAKIS_GIT_REF precisa ser um nome de branch Git válido."

    # Template ComfyUI cleanup targets. ${VAR-default} (NOT ${VAR:-default}) so that
    # exporting a var empty really disables that part of the cleanup — with :- an
    # empty value silently re-defaults and there is no way to opt out. Lists are
    # ':'-separated and read into arrays; see list_entries().
    TEMPLATE_COMFY_DIR="${TEMPLATE_COMFY_DIR-/workspace/ComfyUI}"
    # Extra template ComfyUI install dirs to clean. Newer images keep ComfyUI
    # elsewhere — e.g. RunPod comfyui-base ships it at /workspace/runpod-slim/ComfyUI
    # and launches `python main.py --port 8188` directly (no supervisor).
    TEMPLATE_COMFY_EXTRA_DIRS="${TEMPLATE_COMFY_EXTRA_DIRS-/workspace/runpod-slim/ComfyUI}"
    # Ports a template ComfyUI may be listening on. We stop these by port (never our
    # own $COMFY_PORT / $WEB_PORT) so the template instance does not keep competing
    # for VRAM.
    TEMPLATE_COMFY_PORTS="${TEMPLATE_COMFY_PORTS-8188}"
    # Drop this file inside a directory to authorise its removal explicitly.
    TEMPLATE_COMFY_SENTINEL="${TEMPLATE_COMFY_SENTINEL:-.arrakis-template}"
    TEMPLATE_COMFY_SUPERVISOR_CONF="${TEMPLATE_COMFY_SUPERVISOR_CONF:-/etc/supervisor/conf.d/comfyui.conf}"
    DISABLE_TEMPLATE_COMFY="${DISABLE_TEMPLATE_COMFY:-1}"

    TEMPLATE_COMFY_DIRS=()
    mapfile -t TEMPLATE_COMFY_DIRS < <(list_entries "${TEMPLATE_COMFY_DIR}:${TEMPLATE_COMFY_EXTRA_DIRS}")
    _ports_raw="${TEMPLATE_COMFY_PORTS// /:}"
    _ports_raw="${_ports_raw//,/:}"
    TEMPLATE_COMFY_PORT_LIST=()
    mapfile -t TEMPLATE_COMFY_PORT_LIST < <(list_entries "$_ports_raw")

    # Our own ports (mirror start.py defaults). Used only to never stop ourselves in
    # the template-port cleanup.
    COMFY_PORT="${COMFY_PORT:-8818}"
    WEB_PORT="${WEB_PORT:-8090}"

    # comfy-cli: pinned to the 1.7 line and below 2.0. 1.7+ is what ships the Manager
    # as the comfyui_manager pip package (the step below depends on that layout), and
    # the upper bound keeps a breaking major — like the torch>=2.11 CUDA-runtime
    # regression in comfy-cli #413 — from landing on an unattended deploy.
    COMFY_CLI_SPEC="comfy-cli>=1.7,<2.0"

    # Known-good version windows for the torch triple, as "<pkg>:<min>:<max-exclusive>".
    # torch 2.7 is the first stable release with cu128/sm_120 (Blackwell) kernels; the
    # upper bounds keep a future major (torch 3.x / torchvision 1.x) from being pulled
    # in unattended. Single source of truth: both the pip specs below and the wheel
    # resolver in prefetch_torch_via_aria2c filter on these bounds.
    TORCH_BOUNDS=("torch:2.7:3.0" "torchvision:0.22:1.0" "torchaudio:2.7:3.0")
    TORCH_PIP_SPECS=()
    for _bound in "${TORCH_BOUNDS[@]}"; do
        IFS=: read -r _pkg _min _max <<< "$_bound"
        TORCH_PIP_SPECS+=("${_pkg}>=${_min},<${_max}")
    done

    # Torch wheel index for the standard (non-Sage) runtime, DERIVED FROM THE DRIVER.
    # A torch wheel only fails when its CUDA toolkit is NEWER than the driver supports,
    # so we pick the newest build the driver can actually run: cu130 on CUDA 13.x drivers
    # (unlocks Blackwell NVFP4 + FlashAttention-4), cu128 on CUDA 12.8 drivers (still
    # ships sm_120 kernels and runs on any R570+ host). comfy-cli's own install
    # auto-detects the same way; this var only governs the fallback torch repair below
    # when an incompatible wheel slips in. Override by exporting TORCH_INDEX_URL.
    if [ -z "${TORCH_INDEX_URL:-}" ]; then
        case "$(detect_driver_max_cuda)" in
            13.*|14.*) TORCH_INDEX_URL="https://download.pytorch.org/whl/cu130" ;;
            *)         TORCH_INDEX_URL="https://download.pytorch.org/whl/cu128" ;;
        esac
    fi

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

    # uv/pip resilience on RunPod's /workspace network volume. Without these, uv's cache
    # (on the container overlay) and the venv (on the network mount) live on different
    # devices, so uv falls back to slow cross-device copies (uv #10051); and uv's 30s
    # read timeout with no-retry-on-timeout (uv #17697) aborts large wheel pulls over
    # PyPI's CDN — the exact "4 retries in 130.2s" failure seen in production. Co-locate
    # the cache with the venv, force copy mode, raise the timeout, and add retries.
    export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
    export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/.cache/uv}"
    export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"
    export UV_HTTP_RETRIES="${UV_HTTP_RETRIES:-5}"
    export UV_CONCURRENT_DOWNLOADS="${UV_CONCURRENT_DOWNLOADS:-8}"
    export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-300}"
    export PIP_RETRIES="${PIP_RETRIES:-5}"
    export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/workspace/.cache/pip}"

    # GitHub auth for private custom-node repos / this repo. The helper is only
    # consulted when a server challenges, so a public clone is never affected by
    # the presence (or invalidity) of a token.
    GITHUB_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
    setup_git_credentials

    # Create directories
    mkdir -p "$COMFY_BASE" "$HF_HOME" "$TMPDIR" "$UV_CACHE_DIR" "$PIP_CACHE_DIR"

    log_info "========================================="
    log_info " Arrakis Start - ComfyUI Deployment"
    log_info "========================================="

    # --------------------------------------------------- 1. System dependencies
    log_info "[1/5] Installing system dependencies..."
    ensure_system_packages

    # Install uv — speeds up every subsequent pip-like operation by ~5-10x.
    # Safe to call even if uv is already present; it is a no-op in that case.
    ensure_uv_installed || true

    log_success "System dependencies installed"

    # ------------------------------------------- 2. ComfyUI Python environment
    log_info "[2/5] Setting up ComfyUI Python environment..."

    if ensure_venv "$COMFY_VENV_DIR" "ComfyUI"; then
        COMFY_VENV_CREATED=1
    else
        COMFY_VENV_CREATED=0
    fi

    if [ "$COMFY_VENV_CREATED" -eq 1 ]; then
        run_with_progress "Instalando tooling base do venv ComfyUI (pip/wheel/setuptools/comfy-cli)" \
            pip_install_into "$COMFY_PYTHON" --upgrade pip wheel setuptools "$COMFY_CLI_SPEC"
    elif [ ! -x "$COMFY_CLI" ]; then
        log_warn "comfy-cli não encontrado no venv; instalando..."
        run_with_progress "Instalando comfy-cli no venv ComfyUI" \
            pip_install_into "$COMFY_PYTHON" --upgrade "$COMFY_CLI_SPEC"
    else
        log_info "ComfyUI venv já pronto; pulando upgrade de tooling Python"
    fi

    # Configure hf_xet for MAXIMUM download speed.
    # HF_XET_HIGH_PERFORMANCE: saturates network/CPU; HF docs warn it allocates
    #   multi-GB buffers and should only be used with >=64GB RAM. Below that it
    #   can degrade performance. We auto-detect via total MemTotal.
    # HF_XET_NUM_CONCURRENT_RANGE_GETS: parallel chunk reads (default oficial: 16)
    HF_XET_HP_MIN_RAM_GB="${HF_XET_HP_MIN_RAM_GB:-48}"
    if [ -z "${HF_XET_HIGH_PERFORMANCE:-}" ]; then
        mem_total_kb="$(awk '/MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
        mem_total_gb=$((mem_total_kb / 1024 / 1024))
        if [ "$mem_total_gb" -ge "$HF_XET_HP_MIN_RAM_GB" ]; then
            export HF_XET_HIGH_PERFORMANCE=1
            log_info "HF_XET_HIGH_PERFORMANCE=1 ativado (RAM=${mem_total_gb}GB >= ${HF_XET_HP_MIN_RAM_GB}GB)"
        else
            log_info "HF_XET_HIGH_PERFORMANCE desativado (RAM=${mem_total_gb}GB < ${HF_XET_HP_MIN_RAM_GB}GB); usando adaptive concurrency"
        fi
    fi
    export HF_XET_NUM_CONCURRENT_RANGE_GETS="${HF_XET_NUM_CONCURRENT_RANGE_GETS:-32}"
    export HF_HUB_DOWNLOAD_TIMEOUT="${HF_HUB_DOWNLOAD_TIMEOUT:-60}"

    log_success "ComfyUI Python environment ready"

    # Make the workspace venv the active virtualenv BEFORE comfy-cli install.
    # comfy-cli resolves its target Python from VIRTUAL_ENV first (resolve_python.py),
    # so without this it installs ComfyUI's deps into the RunPod template's /venv/main
    # (pre-exported VIRTUAL_ENV) — a venv that is never used at runtime, forcing a full
    # reinstall later and downloading torch twice.
    export VIRTUAL_ENV="$COMFY_VENV_DIR"
    export PATH="$COMFY_VENV_DIR/bin:$PATH"

    # --------------------------------------------------------- 3. Install ComfyUI
    log_info "[3/5] Installing ComfyUI..."

    if [ -f "$COMFY_DIR/main.py" ]; then
        log_warn "ComfyUI already exists, skipping installation"
    else
        # Beat the single-connection CDN throttle on the torch wheel: pre-fetch it with
        # aria2c (parallel streams), then tell comfy-cli to skip its own (single-stream)
        # torch download. Best-effort — on any failure comfy-cli downloads torch itself.
        _comfy_install_extra=()
        if prefetch_torch_via_aria2c; then
            log_success "PyTorch pré-instalado via aria2c (multi-conexão); comfy-cli vai pular o torch"
            _comfy_install_extra+=(--skip-torch-or-directml)
        fi

        # comfy-cli auto-detects the CUDA build from the driver (cu130 on CUDA 13.x drivers,
        # cu128 on 12.8) — its detection is correct; we only fix the mechanics. NO
        # --fast-deps: at bootstrap there are no custom nodes yet (start.py clones them
        # later) so its cross-node dedup buys nothing, while it couples torch+everything
        # into one all-or-nothing uv resolution that a single slow wheel can abort, and it
        # has a torch>=2.11 CUDA-runtime regression (comfy-cli #413). One retry, and the
        # repo itself is verified below: the later steps can repair missing deps, but not
        # a missing ComfyUI.
        if run_with_progress "Instalando ComfyUI (comfy-cli)" \
            timeout 2400 "$COMFY_CLI" --skip-prompt --workspace "$COMFY_DIR" install --nvidia "${_comfy_install_extra[@]}"; then
            log_success "ComfyUI installed"
        elif run_with_progress "Reinstalando ComfyUI (comfy-cli, retry)" \
            timeout 2400 "$COMFY_CLI" --skip-prompt --workspace "$COMFY_DIR" install --nvidia "${_comfy_install_extra[@]}"; then
            log_success "ComfyUI installed (retry)"
        else
            log_warn "comfy-cli install falhou/expirou após retry — seguindo; as etapas seguintes reinstalam deps core/torch no venv correto."
        fi
    fi

    # A missing main.py means there is no ComfyUI at all: every step after this would
    # install dependencies (including multi-GB torch) for something that cannot start,
    # and the run would still end with a green "Bootstrap complete!".
    if [ ! -f "$COMFY_DIR/main.py" ]; then
        log_error "ComfyUI não foi instalado: $COMFY_DIR/main.py não existe."
        log_error "As duas tentativas do comfy-cli falharam (rede, PyPI ou GitHub)."
        die "Abortando antes de gastar mais tempo pago. Verifique a rede da instância e rode o bootstrap novamente."
    fi

    # Template cleanup runs ONLY after our own ComfyUI is verified present. It deletes
    # directories, so it must never happen before the first steps that can legitimately
    # fail (a broken APT index used to abort the run with the template already gone and
    # nothing installed). It is also a convenience, never a requirement: whatever it
    # cannot do, it reports — it must not take the deploy down with it.
    cleanup_template_comfyui_all \
        || log_warn "Limpeza do ComfyUI de template incompleta; seguindo com o deploy."

    # Ensure ComfyUI Python dependencies are present even if ComfyUI folder already existed.
    # This is required when /workspace/comfy/.venv is recreated from scratch.
    if [ -f "$COMFY_DIR/requirements.txt" ]; then
        if [ "$COMFY_VENV_CREATED" -eq 1 ] || ! is_requirements_synced "$COMFY_DIR/requirements.txt" "$COMFY_REQ_MARKER"; then
            log_info "Syncing ComfyUI core requirements..."
            # No --upgrade: ComfyUI pins torch/torchvision/torchaudio with no
            # version bound, so "upgrade everything already satisfied" throws away
            # the driver-selected wheel installed moments earlier and pulls the
            # newest PyPI build (a CUDA 13 one) in its place — several GB
            # re-downloaded to undo work, then repaired again further down. The
            # goal here is only that the dependencies are PRESENT; anything whose
            # constraint is unsatisfied still gets resolved without the flag.
            run_with_progress "Instalando dependencias core do ComfyUI" \
                pip_install_into "$COMFY_PYTHON" -r "$COMFY_DIR/requirements.txt"
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
                pip_install_into "$COMFY_PYTHON" -r "$COMFY_DIR/manager_requirements.txt"
            log_success "ComfyUI-Manager pip package installed"
        else
            log_info "ComfyUI-Manager pip package already present in workspace venv"
        fi
    fi

    # Ensure a PyTorch build that THIS driver can actually run is present in the
    # ComfyUI runtime. torch_runtime_is_ready only reinstalls when the wheel's CUDA
    # toolkit is strictly newer than the driver supports, so a perfectly good torch is
    # never thrown away.
    _driver_max_cuda="$(detect_driver_max_cuda)"
    log_info "Driver CUDA máx.: ${_driver_max_cuda:-desconhecido} | torch build: $("$COMFY_PYTHON" -c 'import torch;print(getattr(torch.version,"cuda","?") or "cpu")' 2>/dev/null || echo 'ausente')"
    if torch_runtime_is_ready; then
        log_info "PyTorch compatível com o driver atual já presente no runtime; pulando reinstall"
    else
        log_info "PyTorch ausente/incompatível com o driver; instalando build compatível ($TORCH_INDEX_URL)..."
        run_with_progress "Instalando PyTorch compatível com o driver ($TORCH_INDEX_URL)" \
            pip_install_into "$COMFY_PYTHON" --upgrade --force-reinstall \
            "${TORCH_PIP_SPECS[@]}" \
            --index-url "$TORCH_INDEX_URL"

        # Validation is advisory, NOT fatal: never abort the whole bootstrap over it.
        # If torch still doesn't validate (e.g. a driver capped below CUDA 12.8, or a
        # transient probe miss), warn with an actionable hint and let ComfyUI start —
        # it will surface the real error itself if there genuinely is one.
        if torch_runtime_is_ready; then
            log_success "PyTorch instalado e compatível com o driver ($TORCH_INDEX_URL)"
        else
            log_warn "PyTorch reinstalado, mas a validação ainda não confirma CUDA utilizável."
            log_warn "Seguindo mesmo assim — o ComfyUI vai iniciar e reportar o erro real se houver."
            log_warn "Se esta GPU exige outro índice CUDA (driver máx.: ${_driver_max_cuda:-desconhecido}),"
            log_warn "  defina TORCH_INDEX_URL manualmente, ex.: https://download.pytorch.org/whl/cu126"
        fi
    fi

    # ------------------------------------------------ 4. Clone/update Arrakis Start
    log_info "[4/5] Setting up Arrakis Start..."

    if [ -n "$ARRAKIS_GIT_ASKPASS" ]; then
        log_info "GitHub token disponível se o repositório exigir (nada é escrito em .git/config)"
    fi

    if [ -d "$ARRAKIS_DIR/.git" ]; then
        log_info "Updating Arrakis Start..."
        update_arrakis_repo "$ARRAKIS_DIR" "$ARRAKIS_REPO_URL" "$ARRAKIS_GIT_REF" \
            || die "Não foi possível atualizar o Arrakis Start na branch '$ARRAKIS_GIT_REF'. O checkout local foi preservado."
    else
        log_info "Cloning Arrakis Start..."
        install_arrakis_repo "$ARRAKIS_DIR" "$ARRAKIS_REPO_URL" "$ARRAKIS_GIT_REF" \
            || die "Não foi possível clonar o Arrakis Start após 3 tentativas (rede/DNS/GitHub). Verifique a rede da instância e rode o bootstrap novamente."
        log_success "Arrakis Start clonado"
    fi

    [ -f "$ARRAKIS_DIR/start.py" ] \
        || die "Checkout do Arrakis Start incompleto: $ARRAKIS_DIR/start.py não existe."

    log_success "Arrakis Start ready"

    # -------------------------------- 5. Arrakis orchestrator Python environment
    log_info "[5/5] Setting up Arrakis orchestrator environment..."

    ensure_venv "$ARRAKIS_VENV_DIR" "Arrakis" || true

    run_with_progress "Atualizando tooling base do venv Arrakis (pip/wheel/setuptools)" \
        pip_install_into "$ARRAKIS_PYTHON" --upgrade pip wheel setuptools
    # HF CLI/XET live in orchestrator venv (isolated from ComfyUI runtime deps).
    # Since huggingface_hub>=1.0 the `hf` CLI is bundled by default and the [cli]
    # extra was removed — installing it would emit a pointless warning.
    run_with_progress "Instalando huggingface_hub + hf_xet no venv Arrakis" \
        pip_install_into "$ARRAKIS_PYTHON" --upgrade "huggingface_hub>=1.3.0,<2.0" hf_xet

    # Store HF token so hf_xet backend and gated model downloads work correctly.
    # $HF_HOME/token is the cache huggingface_hub reads (it does NOT rely on the
    # HF_TOKEN env var in all code paths), so we write exactly that file.
    HF_TOKEN="${HF_TOKEN:-}"
    if [ -n "$HF_TOKEN" ]; then
        store_hf_token || log_warn "Seguindo sem token HF armazenado — downloads de modelos gated vão falhar."
    else
        log_warn "HF_TOKEN not set — gated model downloads will fail. Set HF_TOKEN in your environment."
    fi

    run_with_progress "Instalando requirements do Arrakis" \
        pip_install_into "$ARRAKIS_PYTHON" --upgrade -r "$ARRAKIS_DIR/requirements.txt"
    log_success "Arrakis orchestrator environment ready (hf_xet enabled)"

    log_info "Runtime stack (torch / sageattention) será configurada por preset na instalação."

    # All git work is done; start.py manages its own credentials for node clones.
    cleanup_git_credentials

    # Final message
    log_info "========================================="
    log_success "Bootstrap complete!"
    log_info "Starting web selector on port $WEB_PORT..."
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
}

main "$@"
