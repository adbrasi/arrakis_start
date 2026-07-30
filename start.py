#!/usr/bin/env python3
"""
Arrakis Start - ComfyUI Deployment System v2.0
Main orchestrator for preset-based installation with state management
"""

import contextlib
import os
import sys
import json
import subprocess
import logging
import queue
import re
import signal
import shlex
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse

# Import state manager
from state import get_state_manager
from process_manager import get_process_manager

# In-process progress registry surfaced through /api/status. Optional: the
# installer must keep working if the module is absent (older deployments).
try:
    import progress
except ImportError:  # pragma: no cover - progress reporting is best-effort
    progress = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Paths
SCRIPT_DIR = Path(__file__).parent.absolute()
COMFY_BASE = Path(os.environ.get('COMFY_BASE', '/workspace/comfy'))
COMFY_DIR = COMFY_BASE / 'ComfyUI'
MODELS_DIR = COMFY_DIR / 'models'
PRESETS_DIR = SCRIPT_DIR / 'presets'
VENV_DIR = COMFY_BASE / '.venv'
COMFY_PYTHON = Path(os.environ.get('COMFY_PYTHON', str(VENV_DIR / 'bin' / 'python')))
COMFY_CLI = os.environ.get('COMFY_CLI', str(VENV_DIR / 'bin' / 'comfy'))

# Ports
def _safe_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        logging.warning(f"Invalid integer for env var {name}={raw!r}, using default {default}")
        return default

WEB_PORT = _safe_int_env('WEB_PORT', 8090)
COMFY_PORT = _safe_int_env('COMFY_PORT', 8818)
# Explicit override only (e.g. user forces a specific CUDA build). When unset, the
# torch wheel index is derived per-driver by _torch_index_url() — cu130 on CUDA
# 13.x drivers, cu128 on 12.8 — so a Blackwell box is never blanket-downgraded.
TORCH_INDEX_URL_OVERRIDE = os.environ.get('TORCH_INDEX_URL', '').strip()
SAGEATTENTION_INSTALLER_URL = os.environ.get(
    'SAGEATTENTION_INSTALLER_URL',
    'https://raw.githubusercontent.com/adbrasi/sageattention220-ultimate-installer/refs/heads/main/install_sageattention220_wheel.sh'
)
SAGEATTENTION_INSTALL_ATTEMPTS = _safe_int_env('SAGEATTENTION_INSTALL_ATTEMPTS', 3)
SAGEATTENTION_RETRY_DELAY_SECONDS = _safe_int_env('SAGEATTENTION_RETRY_DELAY_SECONDS', 8)

# Parallel git clone workers for custom nodes. Clones are IO/network-bound and
# safe to run concurrently; pip requirements install stays sequential because
# pip is not concurrent-safe inside the same environment.
NODES_CLONE_WORKERS = _safe_int_env('NODES_CLONE_WORKERS', 6)
NODE_PIP_TIMEOUT_SECONDS = _safe_int_env('NODE_PIP_TIMEOUT_SECONDS', 600)

# Deadlines. Every long-running child gets one: without a deadline a silent pip
# or a stuck curl freezes the whole installer with no way out.
STREAM_COMMAND_TIMEOUT_SECONDS = _safe_int_env('STREAM_COMMAND_TIMEOUT_SECONDS', 3600)
TORCH_INSTALL_TIMEOUT_SECONDS = _safe_int_env('TORCH_INSTALL_TIMEOUT_SECONDS', 3600)
# A SageAttention source build against a fresh torch ABI legitimately takes tens
# of minutes on a single pod CPU.
SAGEATTENTION_TIMEOUT_SECONDS = _safe_int_env('SAGEATTENTION_TIMEOUT_SECONDS', 7200)
GIT_CLONE_TIMEOUT_SECONDS = _safe_int_env('GIT_CLONE_TIMEOUT_SECONDS', 900)
# Short probes (nvidia-smi, `import torch`) must never block the installer.
PROBE_TIMEOUT_SECONDS = _safe_int_env('PROBE_TIMEOUT_SECONDS', 30)
IMPORT_PROBE_TIMEOUT_SECONDS = _safe_int_env('IMPORT_PROBE_TIMEOUT_SECONDS', 180)
# How long to keep draining stdout after the direct child exited. A grandchild
# that inherited the pipe (e.g. a backgrounded build step) can hold the write
# end open forever, so the drain must be bounded instead of waiting for EOF.
STREAM_DRAIN_GRACE_SECONDS = 5.0

# GitHub token for private repositories (GITHUB_TOKEN or GH_TOKEN)
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '') or os.environ.get('GH_TOKEN', '')


def _set_progress_stage(stage: str, detail: str = "") -> None:
    """Publish the current installer phase to the /api/status registry."""
    if progress is None:
        return
    try:
        progress.set_stage(stage, detail)
    except Exception:  # pragma: no cover - progress must never break the install
        pass


def _reset_progress() -> None:
    if progress is None:
        return
    try:
        progress.reset()
    except Exception:  # pragma: no cover
        pass


def _resolve_comfy_python() -> Optional[str]:
    """Return the ComfyUI venv python if it exists, else None.

    Checks the configured COMFY_PYTHON first, then probes common cloud-template
    locations (/venv/main, etc.) in case comfy-cli targets a different venv.
    """
    if COMFY_PYTHON.exists():
        return str(COMFY_PYTHON)

    # Cloud template fallbacks (VastAI / Runpod pre-built images)
    for alt in (Path('/venv/main/bin/python'), Path('/venv/comfy/bin/python')):
        if alt.exists():
            logger.info(f"Using template runtime Python: {alt}")
            return str(alt)

    return None


def _comfy_python() -> str:
    """Resolve the ComfyUI runtime python, falling back to this interpreter.

    Only probe helpers may rely on the fallback. Anything that *installs* into
    the environment must go through require_comfy_python() first, so a missing
    venv fails loudly instead of writing packages into the host interpreter.
    """
    resolved = _resolve_comfy_python()
    if resolved:
        return resolved
    logger.warning(
        f"ComfyUI python not found at {COMFY_PYTHON}; falling back to current interpreter"
    )
    return sys.executable


def require_comfy_python() -> str:
    """Return the ComfyUI venv python or raise with the expected path.

    Installing into the wrong interpreter is unrecoverable and the resulting
    errors are misleading (``uv pip install --python /usr/bin/python3`` fails
    with "externally managed" instead of "venv missing"), so this is a hard,
    early failure.
    """
    resolved = _resolve_comfy_python()
    if resolved:
        return resolved
    raise RuntimeError(
        f"Ambiente virtual do ComfyUI não encontrado: esperado em {COMFY_PYTHON} "
        f"(COMFY_BASE={COMFY_BASE}). Rode bootstrap.sh ou defina COMFY_PYTHON/COMFY_BASE "
        "antes de instalar presets."
    )


def _venv_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment that resolves python/pip/ninja/cmake inside the ComfyUI venv.

    Source builds shell out to bare `python`, `ninja` and `cmake`; without
    VIRTUAL_ENV and the venv bin on PATH they would resolve against the ambient
    interpreter. Mirrors what ProcessManager.start() does for ComfyUI itself.
    """
    env = os.environ.copy()
    venv_bin = str(VENV_DIR / 'bin')
    env['VIRTUAL_ENV'] = str(VENV_DIR)
    env['PATH'] = f"{venv_bin}:{env.get('PATH', '')}"
    if extra:
        env.update(extra)
    return env


@contextlib.contextmanager
def _git_credential_env() -> Iterator[Dict[str, str]]:
    """Yield a git environment that authenticates without leaking the token.

    The token must never reach argv (world-readable via /proc/<pid>/cmdline) nor
    the cloned repo's .git/config (mode 644, on the persistent volume, read by
    every third-party node that runs later). A GIT_ASKPASS helper keeps it in
    this process' environment only.
    """
    env = _venv_env({'GIT_TERMINAL_PROMPT': '0'})
    if not GITHUB_TOKEN:
        yield env
        return

    helper_dir = tempfile.mkdtemp(prefix='arrakis-git-')
    helper_path = Path(helper_dir) / 'askpass.sh'
    try:
        helper_path.write_text(
            '#!/bin/sh\n'
            'case "$1" in\n'
            '  *[Uu]sername*) printf \'%s\\n\' "x-access-token" ;;\n'
            '  *) printf \'%s\\n\' "$ARRAKIS_GIT_TOKEN" ;;\n'
            'esac\n'
        )
        helper_path.chmod(0o700)
        env['GIT_ASKPASS'] = str(helper_path)
        env['ARRAKIS_GIT_TOKEN'] = GITHUB_TOKEN
        yield env
    finally:
        shutil.rmtree(helper_dir, ignore_errors=True)


def _sanitize_git_output(text: str) -> str:
    """Remove credentials from command output before logging.

    Covers GitHub, HuggingFace and Civitai tokens — any of these can leak
    through clone URLs, error messages, or tqdm progress lines.
    """
    if not text:
        return text
    for token in (
        GITHUB_TOKEN,
        os.environ.get('HF_TOKEN', ''),
        os.environ.get('HUGGING_FACE_HUB_TOKEN', ''),
        os.environ.get('CIVITAI_TOKEN', ''),
        os.environ.get('CIVITAI_API_KEY', ''),
    ):
        if token and token in text:
            text = text.replace(token, '***')
    return text


def should_ignore_preset_file(preset_file: Path) -> bool:
    """Return True when a preset file is intentionally disabled/hidden."""
    name = preset_file.name
    lower_name = name.lower()

    # Hidden preset file (e.g. .illustrious_3d_test.json)
    if name.startswith('.'):
        logger.info(f"Ignoring hidden preset file: {name}")
        return True

    # Explicitly disabled preset file (e.g. illustrious_3d_test.json.ignore)
    if lower_name.endswith('.ignore'):
        logger.debug(f"Ignoring disabled preset file (.ignore): {name}")
        return True

    # Only .json files are valid preset files
    if not lower_name.endswith('.json'):
        return True

    return False


def load_presets() -> List[Dict]:
    """Load all preset JSON files from presets/ directory"""
    presets = []

    if not PRESETS_DIR.exists():
        logger.warning(f"Presets directory not found: {PRESETS_DIR}")
        return presets

    for preset_file in sorted(PRESETS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not preset_file.is_file() or should_ignore_preset_file(preset_file):
            continue
        try:
            with open(preset_file, 'r', encoding='utf-8') as f:
                preset = json.load(f)
                preset['_filename'] = preset_file.name
                presets.append(preset)
                logger.debug(f"Loaded preset: {preset.get('name', preset_file.name)}")
        except Exception as e:
            logger.error(f"Failed to load preset {preset_file}: {e}")

    names = ", ".join(p.get('name', p['_filename']) for p in presets)
    logger.info(f"Presets carregados ({len(presets)}): {names}")
    return presets

# Global tracker for cancellation
_active_downloader = None
_install_lock = threading.Lock()
_install_cancel_event = threading.Event()
_install_status_lock = threading.Lock()
_install_status = 'idle'
_active_install_processes = set()
_active_install_processes_lock = threading.Lock()


def _set_install_status(status: str) -> None:
    global _install_status
    with _install_status_lock:
        _install_status = status


def get_install_status() -> Dict[str, Any]:
    """Return volatile installation state for the web UI/API."""
    with _install_status_lock:
        status = _install_status
    return {
        'installing': _install_lock.locked(),
        'install_status': status,
    }


def reserve_install_slot() -> bool:
    """Reserve the single installer slot before a background web job starts."""
    if not _install_lock.acquire(blocking=False):
        return False
    _install_cancel_event.clear()
    _set_install_status('running')
    return True


def _finish_install_slot(status: str) -> None:
    global _active_downloader
    _active_downloader = None
    _set_install_status(status)
    if _install_lock.locked():
        _install_lock.release()


def finish_install_reservation(status: str = 'failed') -> None:
    """Release a web-reserved slot when setup fails before install_presets()."""
    if _install_lock.locked():
        _finish_install_slot('cancelled' if _install_cancel_event.is_set() else status)


def _register_install_process(process: subprocess.Popen) -> None:
    with _active_install_processes_lock:
        _active_install_processes.add(process)


def _unregister_install_process(process: Optional[subprocess.Popen]) -> None:
    if process is None:
        return
    with _active_install_processes_lock:
        _active_install_processes.discard(process)


def _terminate_install_process(process: subprocess.Popen, grace: float = 3.0) -> None:
    """Terminate a tracked installer command and its child process group."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        try:
            process.terminate()
        except Exception:
            return

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            process.kill()
        except Exception:
            pass

def get_active_downloader():
    return _active_downloader

def cancel_active_install(delete_partials: bool = False):
    """Cancel the active installation and optionally delete model partials."""
    if not _install_lock.locked():
        if delete_partials:
            from downloader import cleanup_incomplete_downloads
            cleanup_incomplete_downloads(MODELS_DIR)
        return False

    logger.warning("Cancelando instalação ativa...")
    _install_cancel_event.set()
    _set_install_status('cancelling')

    downloader = _active_downloader
    if downloader is not None:
        downloader.cancel(delete_partials=delete_partials)
    elif delete_partials:
        from downloader import cleanup_incomplete_downloads
        cleanup_incomplete_downloads(MODELS_DIR)

    with _active_install_processes_lock:
        active_processes = list(_active_install_processes)
    for process in active_processes:
        _terminate_install_process(process)

    return True


def _run_probe(
    cmd: List[str],
    timeout: int,
    description: str
) -> Optional[subprocess.CompletedProcess]:
    """Run a short diagnostic command with a hard deadline.

    Returns None when the probe could not be completed (missing binary, crash,
    or timeout). A probe that hangs — a wedged `import torch`, an unresponsive
    nvidia-smi — must never stall the installer.
    """
    try:
        return subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Probe '{description}' excedeu {timeout}s e foi abortado")
        return None
    except Exception as e:
        logger.debug(f"Probe '{description}' failed: {e}")
        return None


def _cuda_available() -> bool:
    """Check CUDA availability using ComfyUI runtime python."""
    probe = _run_probe(
        [_comfy_python(), '-c', 'import torch; print(int(torch.cuda.is_available()))'],
        IMPORT_PROBE_TIMEOUT_SECONDS,
        'torch.cuda.is_available',
    )
    return probe is not None and probe.returncode == 0 and probe.stdout.strip() == '1'


def _gpu_present() -> bool:
    """True when an NVIDIA GPU is actually visible on this host (nvidia-smi)."""
    if not shutil.which('nvidia-smi'):
        return False
    probe = _run_probe(['nvidia-smi', '-L'], PROBE_TIMEOUT_SECONDS, 'nvidia-smi -L')
    return probe is not None and probe.returncode == 0 and 'GPU' in probe.stdout


def _version_pair(value: Optional[str]) -> Optional[Tuple[int, int]]:
    """Parse 'X.Y[.Z]' into a (major, minor) tuple, or None if unparseable."""
    if not value:
        return None
    try:
        parts = value.strip().split('.')
        return (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return None


def _driver_max_cuda() -> Optional[str]:
    """Max CUDA version the installed driver supports (nvidia-smi header)."""
    if not shutil.which('nvidia-smi'):
        return None
    out = _run_probe(['nvidia-smi'], PROBE_TIMEOUT_SECONDS, 'nvidia-smi')
    if out is None or out.returncode != 0:
        return None
    match = re.search(r'CUDA Version:\s*([0-9]+\.[0-9]+)', out.stdout)
    return match.group(1) if match else None


def _torch_index_url() -> str:
    """PyTorch wheel index, driver-aware. Honors an explicit TORCH_INDEX_URL env
    override; otherwise picks the newest build the driver can actually run: cu130
    on CUDA 13.x drivers (unlocks Blackwell NVFP4 + FlashAttention-4), cu128 on
    CUDA 12.8 (still ships sm_120 kernels, runs on any R570+ host). Mirrors
    bootstrap.sh — never blanket-downgrades a Blackwell box from cu130 to cu128.
    """
    if TORCH_INDEX_URL_OVERRIDE:
        return TORCH_INDEX_URL_OVERRIDE
    driver = _version_pair(_driver_max_cuda())
    if driver and driver >= (13, 0):
        return 'https://download.pytorch.org/whl/cu130'
    return 'https://download.pytorch.org/whl/cu128'


def _torch_build_cuda() -> Optional[str]:
    """CUDA toolkit the installed torch wheel was built against (e.g. '12.8')."""
    probe = _run_probe(
        [_comfy_python(), '-c', 'import torch; print(getattr(torch.version, "cuda", "") or "")'],
        IMPORT_PROBE_TIMEOUT_SECONDS,
        'torch.version.cuda',
    )
    if probe is None:
        return None
    value = probe.stdout.strip()
    return value or None


def _cuda_tag(version: Optional[str]) -> Optional[str]:
    """Map a CUDA version ('12.8') to its PyTorch wheel tag ('cu128')."""
    pair = _version_pair(version)
    if not pair:
        return None
    return f"cu{pair[0]}{pair[1]}"


def _index_cuda_tag(index_url: str) -> Optional[str]:
    """Extract the CUDA tag ('cu128') from a PyTorch wheel index URL."""
    match = re.search(r'(cu\d{3,4})', index_url or '')
    return match.group(1) if match else None


def _torch_incompatible_with_driver() -> bool:
    """True when the installed torch wheel cannot drive this host's GPU.

    Only a MAJOR mismatch breaks initialisation: CUDA minor-version compatibility
    means a cu128 wheel runs fine on any 12.x driver (a 12.4 driver included), so
    comparing full versions produced a false positive that triggered a pointless
    multi-GB reinstall on every fresh process. A cu130 wheel on a 12.x driver, on
    the other hand, dies at startup with "NVIDIA driver ... is too old".

    Falls back to the runtime probe only when the versions can't be read (the
    probe alone has false negatives: CUDA_VISIBLE_DEVICES, cold GPU, MIG, ...).
    """
    build = _version_pair(_torch_build_cuda())
    driver = _version_pair(_driver_max_cuda())
    if build and driver:
        return build[0] > driver[0]
    # Couldn't compare versions — fall back to the runtime probe.
    return not _cuda_available()


_torch_compat_checked = False


def _ensure_torch_driver_compatible() -> None:
    """Repair a torch build that cannot run on the current GPU driver.

    A torch wheel only fails to initialise when its CUDA toolkit is strictly NEWER
    than the driver supports (e.g. a cu130 wheel on a CUDA 12.8 driver — ComfyUI
    then dies at startup with "RuntimeError: The NVIDIA driver on your system is
    too old"). We detect that and reinstall a driver-appropriate build chosen by
    _torch_index_url() — cu130 on CUDA 13.x drivers, cu128 on 12.8 — so a good
    cu130 on a Blackwell box is NEVER downgraded. Advisory only (never raises) and
    runs at most once per process. No-op when torch already matches the driver or
    no GPU is present.
    """
    global _torch_compat_checked
    if _torch_compat_checked:
        return
    _torch_compat_checked = True

    if not _gpu_present():
        return
    if not _torch_incompatible_with_driver():
        return

    driver_max = _driver_max_cuda() or 'desconhecido'
    build_cuda = _torch_build_cuda()
    index_url = _torch_index_url()

    # A reinstall can only help when it would actually pull a different build.
    # _torch_index_url() never goes below cu128, so on a driver that can't run
    # cu128 the "repair" would download several GB of the very wheel we just
    # diagnosed as incompatible.
    if _index_cuda_tag(index_url) == _cuda_tag(build_cuda):
        logger.warning(
            f"torch (CUDA {build_cuda or '?'}) não bate com o driver (CUDA máx.: {driver_max}), "
            f"mas o índice disponível ({index_url}) instalaria exatamente o mesmo build — "
            "reinstalar não resolveria. Atualize o driver da GPU ou defina TORCH_INDEX_URL "
            "para um índice compatível."
        )
        return

    logger.warning(
        f"torch build (CUDA {build_cuda or '?'}) é mais novo que o driver "
        f"suporta (CUDA máx.: {driver_max}). Reinstalando build compatível de {index_url}"
    )
    cmd = [
        _comfy_python(), '-m', 'pip', 'install', '--upgrade', '--force-reinstall',
        'torch', 'torchvision', 'torchaudio',
        '--index-url', index_url,
    ]
    code, _ = _run_streaming_command(
        cmd,
        'PyTorch driver-compat repair',
        log_prefix='torch',
        timeout_sec=TORCH_INSTALL_TIMEOUT_SECONDS,
        progress_stage='runtime',
    )
    if code != 0:
        logger.warning("PyTorch driver-compat reinstall returned non-zero; continuing anyway")
        return
    if _torch_incompatible_with_driver():
        logger.warning(
            f"torch ainda não bate com o driver após reinstalar de {index_url} — seguindo assim. "
            f"Driver máx.: {driver_max}. Atualize o driver da GPU (Blackwell exige R570+ para "
            "cu128 e R580+ para cu130) ou defina TORCH_INDEX_URL manualmente."
        )
    else:
        logger.info(f"✓ torch compatível com o driver após reinstalar de {index_url}")


def _normalize_pip_command(command: Any) -> List[str]:
    """Normalize preset pip command into a safe argv list."""
    target_python = _comfy_python()
    if isinstance(command, str):
        tokens = shlex.split(command)
    elif isinstance(command, list):
        tokens = [str(x) for x in command if str(x).strip()]
    else:
        raise ValueError("pip command must be a string or list")

    if not tokens:
        raise ValueError("pip command is empty")

    first = tokens[0]
    python_aliases = {
        sys.executable,
        Path(sys.executable).name,
        target_python,
        Path(target_python).name,
        'python',
        'python3'
    }

    if first in ('pip', 'pip3'):
        return [target_python, '-m', 'pip'] + tokens[1:]

    if first in python_aliases and len(tokens) >= 3 and tokens[1] == '-m' and tokens[2] == 'pip':
        return [target_python, '-m', 'pip'] + tokens[3:]

    return [target_python, '-m', 'pip'] + tokens


def _pip_install_argv(args: List[str], target_python: Optional[str] = None) -> List[str]:
    """Build a 'pip install' argv, preferring uv (far faster: no per-call Python
    interpreter startup and a much faster resolver) with a pip fallback. `args`
    are the install arguments after 'install' (e.g. ['-r', req_file]). uv installs
    into the venv given by --python; it also inherits the UV_* resilience env that
    bootstrap.sh exports (cache on /workspace, copy link-mode, longer timeouts).
    Falls back to '<py> -m pip install' when uv is not on PATH.
    """
    py = target_python or _comfy_python()
    uv = shutil.which('uv')
    if uv:
        return [uv, 'pip', 'install', '--color', 'never', '--python', py, *args]
    return [py, '-m', 'pip', 'install', *args]


_BYTE_UNITS = {
    'b': 1,
    'kb': 1000, 'mb': 1000 ** 2, 'gb': 1000 ** 3, 'tb': 1000 ** 4,
    'kib': 1024, 'mib': 1024 ** 2, 'gib': 1024 ** 3, 'tib': 1024 ** 4,
}

# uv/pip announce every wheel they fetch, e.g. "Downloading opencv-python (70.4MiB)"
# or "Downloading opencv_python-4.10-...whl (62.5 MB)".
_PKG_DOWNLOAD_RE = re.compile(
    r'\b(?:Downloading|Downloaded)\s+(\S+)\s*\(\s*([\d.]+)\s*([KMGTP]?i?B)\s*\)',
    re.IGNORECASE,
)
# Phase lines, e.g. "Resolved 3 packages in 19.48s" / "Prepared 5 packages in 1m 2s".
_PKG_PHASE_RE = re.compile(
    r'\b(Resolved|Prepared|Built|Installed|Audited|Uninstalled)\s+(\d+)\s+packages?',
    re.IGNORECASE,
)

_PKG_PHASE_LABELS = {
    'resolved': 'resolvidos',
    'prepared': 'preparados',
    'built': 'compilados',
    'installed': 'instalados',
    'audited': 'auditados',
    'uninstalled': 'removidos',
}


def _parse_size(value: str, unit: str) -> Optional[int]:
    multiplier = _BYTE_UNITS.get(unit.lower())
    if multiplier is None:
        return None
    try:
        return int(float(value) * multiplier)
    except ValueError:
        return None


class _PackageProgress:
    """Turn uv/pip stdout into a human-readable progress line.

    Raw CPU/IO deltas tell a human nothing while a 70 MiB wheel trickles in for
    twelve minutes. uv does announce the package and its size, so we track the
    in-flight set and measure transferred bytes from the process tree's I/O
    counters (approximate, hence the '~').
    """

    def __init__(self) -> None:
        self.active: Dict[str, int] = {}
        self.phase = ''
        self.detail = ''
        self.awaiting_baseline = False
        self._baseline_bytes: Optional[float] = None
        self._baseline_at: float = 0.0

    def observe_line(self, line: str) -> bool:
        """Consume one output line. Returns True when the detail changed."""
        download = _PKG_DOWNLOAD_RE.search(line)
        if download:
            size = _parse_size(download.group(2), download.group(3))
            name = download.group(1).strip()
            if size is not None and name:
                if not self.active:
                    self.awaiting_baseline = True
                self.active[name] = size
                self.detail = self._describe(None, 0.0)
                return True

        phase = _PKG_PHASE_RE.search(line)
        if phase:
            verb = phase.group(1).lower()
            label = _PKG_PHASE_LABELS.get(verb, verb)
            self.phase = f"{phase.group(2)} pacotes {label}"
            if verb in ('prepared', 'built', 'installed', 'audited'):
                # Downloads for this batch are over; stop reporting stale bytes.
                self.active.clear()
                self._baseline_bytes = None
                self.awaiting_baseline = False
            self.detail = self._describe(None, 0.0)
            return True

        return False

    def set_baseline(self, activity: Optional[Dict[str, float]]) -> None:
        self.awaiting_baseline = False
        if activity is None:
            return
        self._baseline_bytes = activity['io_bytes']
        self._baseline_at = time.monotonic()

    def _describe(self, activity: Optional[Dict[str, float]], now: float) -> str:
        if not self.active:
            return self.phase
        names = sorted(self.active, key=lambda n: self.active[n], reverse=True)
        total = sum(self.active.values())
        if len(names) == 1:
            label = f"baixando {names[0]}"
        else:
            label = f"baixando {len(names)} pacotes ({names[0]}, ...)"

        if activity is None or self._baseline_bytes is None:
            return f"{label} ({_format_activity_bytes(total)})"

        transferred = min(max(0.0, activity['io_bytes'] - self._baseline_bytes), float(total))
        elapsed = max(0.001, now - self._baseline_at)
        percent = (transferred / total * 100.0) if total else 0.0
        return (
            f"{label}: ~{_format_activity_bytes(transferred)} de "
            f"{_format_activity_bytes(total)} ({percent:.0f}%), "
            f"{_format_activity_bytes(transferred / elapsed)}/s"
        )

    def heartbeat_detail(self, activity: Optional[Dict[str, float]]) -> str:
        self.detail = self._describe(activity, time.monotonic())
        return self.detail


def _stream_command(
    cmd: List[str],
    description: str,
    log_prefix: str = 'cmd',
    env: Optional[Dict[str, str]] = None,
    timeout_sec: float = 0,
    heartbeat_interval: float = 20.0,
    progress_stage: Optional[str] = None,
    collect_lines: bool = True,
) -> Tuple[int, List[str], str]:
    """Run a cancellable child, streaming its output with progress heartbeats.

    Returns (returncode, collected_lines, last_line). returncode is -2 when the
    install was cancelled and -1 when the deadline expired or the child could not
    be reaped.

    The loop never waits for stdout EOF to decide it is done: a grandchild that
    inherited the pipe (backgrounded build step, orphaned aria2c) keeps the write
    end open forever. Once the direct child exits we drain for at most
    STREAM_DRAIN_GRACE_SECONDS and move on. Cancellation and the deadline are
    evaluated unconditionally, so they also fire after the child is gone.
    """
    logger.info(f"Running {description}: {_sanitize_git_output(' '.join(cmd))}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env if env is not None else _venv_env(),
        start_new_session=True,
    )
    _register_install_process(proc)
    assert proc.stdout is not None

    output_lines: List[str] = []
    output_queue: "queue.Queue[str]" = queue.Queue()
    reader_done = threading.Event()
    # Set when the main loop stops consuming: the reader keeps draining so an
    # orphaned writer never blocks on a full pipe, but discards what it reads so
    # the queue cannot grow without bound.
    reader_discard = threading.Event()

    def _read_output():
        # The reader owns proc.stdout and closes it itself. Closing a pipe from
        # another thread while this one is blocked inside read() deadlocks on the
        # buffered reader's internal lock — that is what made the abandoned-drain
        # path hang even after the loop learned to give up.
        try:
            for output_line in proc.stdout:
                if reader_discard.is_set():
                    continue
                output_queue.put(output_line)
        except Exception:
            pass
        finally:
            reader_done.set()
            try:
                proc.stdout.close()
            except Exception:
                pass

    threading.Thread(target=_read_output, daemon=True).start()

    tracker = _PackageProgress()
    started_at = time.monotonic()
    last_log_at = started_at
    last_activity = _process_activity_snapshot(proc.pid)
    last_line = ''
    cancelled = False
    timed_out = False
    abandoned = False
    drain_deadline: Optional[float] = None
    poll_interval = min(max(float(heartbeat_interval), 0.1), 1.0)

    try:
        while True:
            now = time.monotonic()

            if _install_cancel_event.is_set():
                cancelled = True
                logger.warning(
                    f"[{log_prefix}] cancelamento solicitado — encerrando {description}"
                )
                _terminate_install_process(proc)
                break

            if timeout_sec > 0 and now - started_at >= timeout_sec:
                timed_out = True
                logger.error(
                    f"[{log_prefix}] timeout after {timeout_sec:.0f}s — "
                    f"terminating process group ({description})"
                )
                _terminate_install_process(proc)
                break

            try:
                line = output_queue.get(timeout=poll_interval)
            except queue.Empty:
                line = None

            if line is not None:
                line = line.rstrip()
                if line:
                    last_line = line
                    if collect_lines:
                        output_lines.append(line)
                    logger.info(f"[{log_prefix}] {_sanitize_git_output(line)}")
                    last_log_at = time.monotonic()
                    if tracker.observe_line(line) and progress_stage:
                        _set_progress_stage(progress_stage, f"{description}: {tracker.detail}")
                    if tracker.awaiting_baseline:
                        tracker.set_baseline(_process_activity_snapshot(proc.pid))

            now = time.monotonic()
            exited = proc.poll() is not None

            if not exited and now - last_log_at >= heartbeat_interval:
                activity = _process_activity_snapshot(proc.pid)
                detail = tracker.heartbeat_detail(activity)
                elapsed = now - started_at
                if detail:
                    logger.info(f"[{log_prefix}] {detail} ({elapsed:.0f}s)")
                    if progress_stage:
                        _set_progress_stage(progress_stage, f"{description}: {detail}")
                elif activity is not None and last_activity is not None:
                    cpu_delta = max(0.0, activity['cpu_seconds'] - last_activity['cpu_seconds'])
                    io_delta = max(0.0, activity['io_bytes'] - last_activity['io_bytes'])
                    if cpu_delta >= 0.01 or io_delta >= 1024:
                        logger.info(
                            f"[{log_prefix}] ativo: CPU +{cpu_delta:.1f}s, "
                            f"I/O +{_format_activity_bytes(io_delta)}, "
                            f"RAM {_format_activity_bytes(activity['rss_bytes'])}, "
                            f"processos={int(activity['processes'])} "
                            f"({elapsed:.0f}s)"
                        )
                    else:
                        logger.info(
                            f"[{log_prefix}] ativo, mas sem nova saída/CPU/I/O "
                            f"detectável nos últimos {heartbeat_interval:.0f}s "
                            f"({elapsed:.0f}s)"
                        )
                else:
                    logger.info(
                        f"[{log_prefix}] ativo sem telemetria disponível ({elapsed:.0f}s)"
                    )
                last_activity = activity
                last_log_at = now

            if exited:
                if reader_done.is_set() and output_queue.empty():
                    break
                # The child is gone but the pipe is still open: something it
                # spawned inherited stdout. Drain briefly, then give up instead
                # of blocking the installer forever.
                if drain_deadline is None:
                    drain_deadline = now + STREAM_DRAIN_GRACE_SECONDS
                elif now >= drain_deadline:
                    abandoned = True
                    logger.warning(
                        f"[{log_prefix}] processo terminou mas a saída segue aberta por um "
                        f"processo filho; abandonando a leitura ({description})"
                    )
                    break
            else:
                drain_deadline = None
    except Exception as e:
        logger.warning(f"[{log_prefix}] stream read error: {e}")
    finally:
        # Nothing else consumes the queue from here on.
        reader_discard.set()
        if proc.poll() is None:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning(f"[{log_prefix}] processo não encerrou; enviando SIGKILL")
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
        _unregister_install_process(proc)

    # A cancel that arrived while the child was already dying still means the run
    # was cancelled, not that the command legitimately failed.
    if cancelled or _install_cancel_event.is_set():
        returncode = -2
    elif timed_out:
        returncode = -1
    elif proc.returncode is None:
        returncode = -1
    else:
        returncode = proc.returncode
    if abandoned and returncode == 0:
        logger.info(f"[{log_prefix}] {description} terminou com sucesso (saída abandonada)")
    return returncode, output_lines, last_line


def _run_streaming_command(
    cmd: List[str],
    description: str,
    log_prefix: str = 'cmd',
    env: Optional[Dict[str, str]] = None,
    timeout_sec: float = STREAM_COMMAND_TIMEOUT_SECONDS,
    heartbeat_interval: float = 20.0,
    progress_stage: Optional[str] = None,
) -> Tuple[int, List[str]]:
    """Run a cancellable command with streamed logs, deadline and heartbeat."""
    returncode, output_lines, _ = _stream_command(
        cmd,
        description,
        log_prefix=log_prefix,
        env=env,
        timeout_sec=timeout_sec,
        heartbeat_interval=heartbeat_interval,
        progress_stage=progress_stage,
    )
    return returncode, output_lines


def _verify_python_import(package_name: str, python_bin: Optional[str] = None) -> bool:
    """Verify package import in selected Python environment."""
    target_python = python_bin or _comfy_python()
    verify = _run_probe(
        [target_python, '-c', f'import {package_name}'],
        IMPORT_PROBE_TIMEOUT_SECONDS,
        f'import {package_name}',
    )
    if verify is None:
        logger.error(f"Import check for '{package_name}' não concluiu (timeout/erro)")
        return False
    if verify.returncode != 0:
        logger.error(f"Import check failed for '{package_name}'")
        if verify.stderr:
            logger.error(_sanitize_git_output(verify.stderr.strip()))
        return False
    return True


def _can_import(package_name: str, python_bin: Optional[str] = None) -> bool:
    """Fast import probe without noisy logs."""
    target_python = python_bin or _comfy_python()
    probe = _run_probe(
        [target_python, '-c', f'import {package_name}'],
        IMPORT_PROBE_TIMEOUT_SECONDS,
        f'import {package_name}',
    )
    return probe is not None and probe.returncode == 0


def _detect_runtime_stack() -> str:
    """Detect runtime stack from installed packages in ComfyUI venv."""
    comfy_python = _comfy_python()
    if _can_import('sageattention', python_bin=comfy_python):
        return 'sageattention'
    if _can_import('torch', python_bin=comfy_python):
        return 'standard'
    return 'unknown'


def _run_sageattention_installer(
    comfy_activate: Path,
    action: str = 'auto',
    env: Optional[Dict[str, str]] = None
) -> Tuple[bool, List[str]]:
    """
    Run SageAttention installer with retry/backoff.
    Uses pipefail so download failures are not masked by the shell pipe.
    """
    if action not in {'auto', 'build'}:
        raise ValueError(f"Unsupported SageAttention installer action: {action}")

    attempts = max(SAGEATTENTION_INSTALL_ATTEMPTS, 1)
    retry_delay = max(SAGEATTENTION_RETRY_DELAY_SECONDS, 1)
    last_output: List[str] = []
    logger.info(
        f"Starting SageAttention installer "
        f"(action={action}, url={SAGEATTENTION_INSTALLER_URL}, attempts={attempts})"
    )

    curl_shell = (
        f"set -o pipefail && source {shlex.quote(str(comfy_activate))} && "
        f"curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors "
        f"--connect-timeout 30 --max-time 300 {shlex.quote(SAGEATTENTION_INSTALLER_URL)} "
        f"| bash -s -- {shlex.quote(action)}"
    )
    wget_shell = (
        f"set -o pipefail && source {shlex.quote(str(comfy_activate))} && "
        f"wget -qO- --timeout=30 --tries=5 {shlex.quote(SAGEATTENTION_INSTALLER_URL)} "
        f"| bash -s -- {shlex.quote(action)}"
    )

    for attempt in range(1, attempts + 1):
        if _install_cancel_event.is_set():
            return False, last_output
        curl_cmd = ['bash', '-lc', curl_shell]
        result_code, output_lines = _run_streaming_command(
            curl_cmd,
            f"SageAttention unified installer (curl attempt {attempt}/{attempts})",
            log_prefix='sage',
            env=env,
            timeout_sec=SAGEATTENTION_TIMEOUT_SECONDS,
            progress_stage='runtime',
        )
        last_output = output_lines
        if result_code == 0:
            return True, output_lines
        if _install_cancel_event.is_set():
            return False, output_lines

        logger.warning(f"SageAttention installer via curl failed (exit {result_code})")

        wget_cmd = ['bash', '-lc', wget_shell]
        result_code, output_lines = _run_streaming_command(
            wget_cmd,
            f"SageAttention unified installer (wget fallback {attempt}/{attempts})",
            log_prefix='sage',
            env=env,
            timeout_sec=SAGEATTENTION_TIMEOUT_SECONDS,
            progress_stage='runtime',
        )
        last_output = output_lines
        if result_code == 0:
            return True, output_lines
        if _install_cancel_event.is_set():
            return False, output_lines

        logger.warning(f"SageAttention installer via wget failed (exit {result_code})")
        if attempt < attempts:
            logger.info(f"Retrying SageAttention installer in {retry_delay}s...")
            if _install_cancel_event.wait(retry_delay):
                return False, last_output

    return False, last_output


def _rebuild_sageattention_for_current_torch(
    comfy_activate: Path
) -> Tuple[bool, List[str]]:
    """Build SageAttention against the active torch ABI and publish when authorized."""
    # The build shells out to python/ninja/cmake: they must come from the venv.
    build_env = _venv_env({'SKIP_TORCH_INSTALL': '1'})
    logger.warning(
        "Prebuilt SageAttention wheel is not importable with the active torch ABI. "
        "Rebuilding from source against the current torch runtime."
    )
    return _run_sageattention_installer(
        comfy_activate,
        action='build',
        env=build_env
    )


def configure_runtime_stack(use_sage_attention: bool) -> bool:
    """Configure runtime stack only when SageAttention is explicitly requested."""
    state = get_state_manager()
    current_stack = state.get_runtime_stack()
    detected_stack = _detect_runtime_stack()

    if detected_stack != 'unknown' and detected_stack != current_stack:
        logger.info(
            f"Runtime stack autodetected as '{detected_stack}' (state had '{current_stack}'). "
            "Updating state marker."
        )
        state.set_runtime_stack(detected_stack)
        current_stack = detected_stack

    if use_sage_attention:
        if current_stack == 'sageattention':
            logger.info("SageAttention runtime already active, skipping reconfiguration")
            return True

        comfy_activate = VENV_DIR / 'bin' / 'activate'
        logger.info(
            "Preset requests SageAttention: keeping normal ComfyUI install and "
            "running unified SageAttention installer"
        )
        ok, output_lines = _run_sageattention_installer(comfy_activate)
        if not ok:
            logger.error("SageAttention installer failed after retries")
            if output_lines:
                logger.error(f"Last installer lines: {' | '.join(output_lines[-10:])}")
            return False

        comfy_python = _comfy_python()
        for package_name in ('torch', 'triton'):
            if not _verify_python_import(package_name, python_bin=comfy_python):
                return False

        if not _can_import('sageattention', python_bin=comfy_python):
            ok, output_lines = _rebuild_sageattention_for_current_torch(comfy_activate)
            if not ok:
                logger.error("SageAttention source rebuild failed")
                if output_lines:
                    logger.error(f"Last build lines: {' | '.join(output_lines[-10:])}")
                return False

        if not _verify_python_import('sageattention', python_bin=comfy_python):
            return False

        state.set_runtime_stack('sageattention')
        logger.info("✓ SageAttention runtime stack configured")
        return True

    # Important: keep normal ComfyUI runtime untouched for non-Sage presets.
    if current_stack == 'sageattention':
        logger.info(
            "Preset without SageAttention selected. Keeping current runtime stack unchanged."
        )
    else:
        if current_stack == 'unknown':
            state.set_runtime_stack('standard')
        logger.info("Preset without SageAttention selected. No runtime stack changes applied.")
    return True


# Conditions a preset may attach to a pip command. An unknown key is a preset
# bug, not a licence to run the command: a typo like "cuda_unavailable" used to
# evaluate as True and install a CUDA-only wheel on a CPU box.
PIP_CONDITIONS: Dict[str, Callable[[], bool]] = {
    'cuda_available': _cuda_available,
}


class _ConditionProbes:
    """Cache condition probes, invalidated whenever the environment changes.

    A pip command can install torch, so a value probed before the loop goes
    stale; re-probing after every executed command keeps later conditions honest
    without paying for a subprocess per command.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, bool] = {}

    def value(self, name: str) -> bool:
        if name not in self._cache:
            self._cache[name] = PIP_CONDITIONS[name]()
            logger.info(f"Condição '{name}' avaliada como {self._cache[name]}")
        return self._cache[name]

    def invalidate(self) -> None:
        self._cache.clear()


def install_pip_commands(pip_commands: List[Any]) -> bool:
    """Install preset-defined pip dependencies."""
    if not pip_commands:
        return True

    probes = _ConditionProbes()

    for index, item in enumerate(pip_commands, start=1):
        if _install_cancel_event.is_set():
            logger.warning("Instalação cancelada antes dos comandos pip restantes")
            return False
        if isinstance(item, str):
            command = item
            condition = None
            allow_failure = False
            verify_import = None
            description = f"pip command #{index}"
        elif isinstance(item, dict):
            command = item.get('command') or item.get('cmd')
            condition = item.get('condition')
            if item.get('when_cuda_available') is True:
                condition = 'cuda_available'
            allow_failure = bool(item.get('allow_failure', False))
            verify_import = item.get('verify_import')
            description = item.get('description', f"pip command #{index}")
        else:
            logger.error(f"Invalid pip command format at position {index}: {type(item)}")
            return False

        if not command:
            logger.error(f"Missing command in pip command #{index}")
            return False

        if condition is not None:
            if not isinstance(condition, str) or condition not in PIP_CONDITIONS:
                logger.error(
                    f"Condição inválida em {description}: {condition!r}. "
                    f"Condições suportadas: {', '.join(sorted(PIP_CONDITIONS))}"
                )
                return False
            if not probes.value(condition):
                logger.warning(f"Skipping {description}: condição '{condition}' é falsa")
                continue

        try:
            cmd = _normalize_pip_command(command)
        except Exception as e:
            logger.error(f"Failed to normalize {description}: {e}")
            return False

        _set_progress_stage('pip', description)
        result_code, output_lines = _run_streaming_command(
            cmd,
            description,
            log_prefix='pip',
            progress_stage='pip',
        )
        # The command may have replaced torch (or anything else a condition
        # probes), so cached probe results are no longer trustworthy.
        probes.invalidate()

        if result_code != 0:
            logger_msg = logger.warning if allow_failure else logger.error
            logger_msg(f"Failed {description} (exit {result_code})")
            if output_lines:
                logger_msg(f"Last pip lines: {' | '.join(output_lines[-10:])}")
            if not allow_failure:
                return False
            continue

        if verify_import and not _verify_python_import(verify_import):
            log_fn = logger.warning if allow_failure else logger.error
            log_fn(f"Package installed but import failed for '{verify_import}' in {description}")
            if not allow_failure:
                return False
            # allow_failure only downgrades the severity — it does not turn a
            # broken install into a completed one.
            logger.warning(f"⚠ Concluído com falha tolerada: {description}")
            continue

        logger.info(f"✓ Completed: {description}")

    return True


def install_presets(
    preset_names: List[str],
    include_base: bool = True,
    _slot_reserved: bool = False,
    _keep_slot: bool = False,
) -> bool:
    """Run one installation at a time and publish its terminal status."""
    if not _slot_reserved and not reserve_install_slot():
        logger.error("Já existe uma instalação em andamento")
        return False
    if _slot_reserved and not _install_lock.locked():
        logger.error("Installer slot was not reserved")
        return False

    result = False
    try:
        if _install_cancel_event.is_set():
            logger.warning("Instalação cancelada antes de iniciar")
            return False
        result = _install_presets_impl(preset_names, include_base=include_base)
        return result
    finally:
        if _install_cancel_event.is_set():
            terminal_status = 'cancelled'
        else:
            terminal_status = 'completed' if result else 'failed'
        if _keep_slot:
            _set_install_status(terminal_status)
        else:
            _finish_install_slot(terminal_status)


def _install_presets_impl(preset_names: List[str], include_base: bool = True) -> bool:
    """Install selected presets with smart skip-existing and parallelism.

    Returns True only when everything requested actually landed. A failed
    download batch, a failed custom node, or a SageAttention runtime that no
    longer imports is a failed install — the caller (web UI / CLI) must never be
    told a half-installed pod is ready.
    """
    from downloader import DownloadManager
    state = get_state_manager()
    global _active_downloader

    _reset_progress()
    _set_progress_stage('preparando', 'lendo presets')

    # Installing into the wrong interpreter is unrecoverable, so this is checked
    # before any work happens instead of surfacing later as an opaque
    # "externally managed environment" error from uv.
    try:
        require_comfy_python()
    except RuntimeError as e:
        logger.error(str(e))
        _set_progress_stage('erro', 'venv do ComfyUI ausente')
        return False

    # Auto-include base preset unless explicitly disabled
    if include_base and 'Base' not in preset_names:
        preset_names = ['Base'] + preset_names
        logger.info("Auto-including 'Base' preset")

    logger.info(f"Installing presets: {', '.join(preset_names)}")

    # Load all presets
    all_presets = load_presets()
    preset_map = {p.get('name', p['_filename']): p for p in all_presets}

    # An unknown name means the request no longer matches what is on disk (preset
    # renamed, or disabled via *.json.ignore while a browser held a stale list).
    # Silently skipping it and reporting success installed nothing and lied.
    unknown_presets = [name for name in preset_names if name not in preset_map]
    if unknown_presets:
        logger.error(
            f"Presets inexistentes ou desativados: {', '.join(unknown_presets)}. "
            f"Disponíveis: {', '.join(sorted(preset_map)) or 'nenhum'}. "
            "Atualize a página e selecione novamente."
        )
        _set_progress_stage('erro', f"presets inexistentes: {', '.join(unknown_presets)}")
        return False

    # Collect all downloads, nodes, flags, and preset pip commands
    downloads = []
    nodes = []
    pip_commands = []
    use_sage_attention = False
    processed_presets = []
    known_models: List[Dict[str, Any]] = []

    for preset_name in preset_names:
        if _install_cancel_event.is_set():
            logger.warning("Instalação cancelada ao preparar presets")
            return False
        if preset_name in processed_presets:
            continue
        processed_presets.append(preset_name)

        preset = preset_map[preset_name]

        # Filter out already-installed models
        if 'models' in preset:
            for model in preset['models']:
                filename = model.get('filename', '')
                model_dir = model.get('dir', '')
                filename = filename.strip() if isinstance(filename, str) else ''

                # When filename is empty (e.g., Civitai content-disposition), we cannot
                # pre-check existence reliably here. Let downloader resolve and decide.
                if not filename:
                    downloads.append(model)
                    continue

                known_models.append(model)
                dest_path = MODELS_DIR / model_dir / filename
                legacy_partial = dest_path.with_name(f"{dest_path.name}.aria2")
                if dest_path.exists() and not legacy_partial.exists():
                    logger.info(f"✓ Already exists: {filename}")
                else:
                    downloads.append(model)

        # Add custom nodes
        if 'nodes' in preset:
            nodes.extend(preset['nodes'])

        # Runtime stack selector
        if bool(preset.get('use_sage_attention', False)):
            use_sage_attention = True
            logger.info(f"Preset '{preset_name}' enables SageAttention runtime stack")

        # Collect preset-specific pip commands
        if 'pip_commands' in preset:
            pip_commands.extend(preset['pip_commands'])

    # 1. Configure runtime stack before preset-specific pip commands.
    if _install_cancel_event.is_set():
        return False
    _set_progress_stage('runtime', 'configurando runtime stack')
    if not configure_runtime_stack(use_sage_attention=use_sage_attention):
        logger.error("Installation failed during runtime stack configuration")
        _set_progress_stage('erro', 'falha ao configurar runtime stack')
        return False

    # 2. Run preset-specific pip commands
    if pip_commands:
        logger.info(f"Running {len(pip_commands)} preset pip command(s) before downloads...")
        _set_progress_stage('pip', f"{len(pip_commands)} comando(s) pip do preset")
        if not install_pip_commands(pip_commands):
            logger.error("Installation failed during preset pip commands")
            _set_progress_stage('erro', 'falha nos comandos pip do preset')
            return False

    # 3. Execute downloads and node installs in parallel
    if _install_cancel_event.is_set():
        return False
    # Stage name matches what downloader.py publishes while it streams files.
    _set_progress_stage(
        'models',
        f"{len(downloads)} modelo(s) e {len(nodes)} custom node(s)"
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        download_future = None
        nodes_future = None

        # 3.1 Download models
        if downloads:
            logger.info(f"Downloading {len(downloads)} new models...")
            _active_downloader = DownloadManager(models_dir=MODELS_DIR)
            download_future = executor.submit(_active_downloader.download_all, downloads)
        else:
            logger.info("All models already installed, skipping downloads")

        # 3.2 Install custom nodes (concurrently)
        if nodes:
            logger.info(f"Installing {len(nodes)} custom nodes...")
            nodes_future = executor.submit(install_custom_nodes, nodes)

        download_success = True
        nodes_result = {"success": True, "failed": []}
        try:
            if download_future is not None:
                download_success = bool(download_future.result())
        except Exception as e:
            logger.error(f"Download task raised an exception: {e}")
            download_success = False
        try:
            if nodes_future is not None:
                nodes_result = nodes_future.result()
                if not isinstance(nodes_result, dict):
                    nodes_result = {"success": bool(nodes_result), "failed": []}
        except Exception as e:
            logger.error(f"Nodes install task raised an exception: {e}")
            nodes_result = {"success": False, "failed": []}

    downloader_failures = []
    try:
        if _active_downloader and hasattr(_active_downloader, 'get_failure_report'):
            downloader_failures = _active_downloader.get_failure_report()
    finally:
        _active_downloader = None

    if _install_cancel_event.is_set() or nodes_result.get('cancelled'):
        logger.warning(
            "Instalação cancelada. Arquivos concluídos foram preservados e "
            "downloads parciais serão retomados na próxima execução."
        )
        return False

    # A failed batch is a failed install, with or without a populated failure
    # list: download_all() also returns False for queue-level errors (e.g. two
    # models mapped to the same destination) that record nothing, and those used
    # to be reported as a clean install with zero models on disk.
    if not download_success:
        if downloader_failures:
            logger.error("Detailed download failures:")
            for idx, failure in enumerate(downloader_failures, 1):
                logger.error(
                    f"[{idx}] file={failure.get('filename')} dir={failure.get('dir')} "
                    f"stage={failure.get('stage')} reason={failure.get('reason')} "
                    f"url={failure.get('url')}"
                )
        else:
            logger.error(
                "O lote de downloads falhou sem registrar arquivos individuais "
                "(erro de fila/configuração — veja o log do downloader acima)."
            )

    if not nodes_result["success"]:
        failed = nodes_result.get("failed", [])
        logger.error(
            f"Custom nodes que falharam ({len(failed)}): {', '.join(failed)}"
        )

    # 4. Ensure the installed torch can actually drive this GPU. Custom-node
    # requirements may have pulled a CUDA wheel newer than the driver supports;
    # repair to a driver-appropriate build before ComfyUI is (re)started.
    if _install_cancel_event.is_set():
        return False
    _set_progress_stage('finalizando', 'verificando runtime')
    _ensure_torch_driver_compatible()

    # 5. Preset pip commands and node requirements run after SageAttention was
    # built and verified, and any of them can replace torch — which silently
    # breaks the freshly built wheel. Re-check now and downgrade the marker so
    # the next run rebuilds instead of short-circuiting on a stale marker.
    runtime_ok = _revalidate_sageattention_runtime(state)

    # 6. Mark only complete presets as installed: the UI must not claim that a
    # partial preset is finished, and leaving it pending makes the next run
    # resume only the missing artifacts.
    if _install_cancel_event.is_set():
        return False
    failed_node_names = set(nodes_result.get('failed', []))
    for preset_name in processed_presets:
        issues = _preset_install_issues(
            preset_map[preset_name],
            downloader_failures,
            failed_node_names,
        )
        if issues:
            state.remove_preset(preset_name)
            logger.warning(
                f"Preset '{preset_name}' permanece pendente: {'; '.join(issues)}. "
                "Execute novamente para retomar somente o que falta."
            )
        else:
            state.add_preset(preset_name)

    # 7. Record every model that is actually on disk. Nothing used to be recorded
    # after a successful transfer, so installed_models stayed empty and uninstall
    # could never remove anything.
    _record_installed_models(state, known_models)

    # 8. Persist ComfyUI flags for the union of installed presets (a single-preset
    # install must not drop the flags of presets that stay installed), plus the
    # runtime-stack flag ComfyUI needs to actually use SageAttention.
    _persist_comfyui_flags(state, preset_map)

    all_ok = download_success and nodes_result["success"] and runtime_ok
    if all_ok:
        logger.info("All presets installed successfully!")
        _set_progress_stage('concluído', 'instalação finalizada')
    else:
        logger.error(
            "Instalação terminou com falhas (veja os erros acima). ComfyUI não será "
            "iniciado automaticamente; corrija os erros e execute novamente — apenas "
            "o que falta será retomado."
        )
        _set_progress_stage('erro', 'instalação incompleta')
    return all_ok


def _revalidate_sageattention_runtime(state) -> bool:
    """Confirm the SageAttention wheel still imports after all pip work.

    Returns False (and downgrades the state marker) when it does not, so the
    next run rebuilds instead of trusting a stale 'sageattention' marker.
    """
    if state.get_runtime_stack() != 'sageattention':
        return True
    if _can_import('sageattention', python_bin=_comfy_python()):
        return True
    logger.error(
        "sageattention deixou de importar após os comandos pip/requirements "
        "(provavelmente o torch foi substituído). Marcando runtime como 'standard' "
        "para reconstruir na próxima execução."
    )
    state.set_runtime_stack('standard')
    return False


def _record_installed_models(state, models: List[Dict[str, Any]]) -> None:
    """Persist every model that is present on disk, in a single state write.

    The old code called add_model() from the already-exists branch only, which
    both missed every freshly downloaded file (so installed_models stayed empty
    and uninstall could never remove anything) and rewrote the whole state file
    once per pre-existing file.
    """
    already_recorded = state.get_installed_models()
    pending: List[Dict[str, Any]] = []
    seen: set = set()
    for model in models:
        filename = str(model.get('filename') or '').strip()
        if not filename:
            continue
        model_dir = str(model.get('dir') or '')
        if (model_dir, filename) in seen:
            continue
        seen.add((model_dir, filename))
        dest = MODELS_DIR / model_dir / filename
        partial = dest.with_name(f"{dest.name}.aria2")
        if not dest.exists() or partial.exists():
            continue
        try:
            size = dest.stat().st_size
        except OSError:
            size = 0
        existing = already_recorded.get(filename)
        if existing and existing.get('size') == size and existing.get('dir') == model_dir:
            continue
        pending.append({
            'filename': filename,
            'dir': model_dir,
            'url': model.get('url', ''),
            'size': size,
        })

    if pending:
        state.add_models(pending)
        logger.info(f"Registrados {len(pending)} modelo(s) no estado")


def _persist_comfyui_flags(state, preset_map: Dict[str, Dict[str, Any]]) -> None:
    """Store the ComfyUI flags for every installed preset plus the runtime flag.

    Flags must come from the union of installed presets: rebuilding the list from
    a single install request dropped the flags of presets that are still
    installed. The SageAttention flag is derived from the runtime stack because
    that reflects what is really in the venv — installing the wheel is useless
    unless ComfyUI is launched with --use-sage-attention.
    """
    flags: List[str] = []
    if state.get_runtime_stack() == 'sageattention':
        flags.append('--use-sage-attention')

    for preset_name in state.get_installed_presets():
        preset = preset_map.get(preset_name)
        if not preset:
            continue
        preset_flags = preset.get('comfyui_flags') or []
        if preset_flags:
            logger.info(f"Preset '{preset_name}' contributes flags: {preset_flags}")
        flags.extend(str(flag) for flag in preset_flags)

    unique_flags = list(dict.fromkeys(flags))
    state.set_comfyui_flags(unique_flags)
    logger.info(f"Saved ComfyUI flags ({len(unique_flags)}): {' '.join(unique_flags) or 'nenhuma'}")


def _preset_install_issues(
    preset: Dict[str, Any],
    downloader_failures: List[Dict[str, str]],
    failed_node_names: set,
    models_dir: Optional[Path] = None,
) -> List[str]:
    """Return concrete reasons why a preset is not fully installed."""
    root = models_dir or MODELS_DIR
    failed_urls = {
        str(failure.get('url', '')).strip()
        for failure in downloader_failures
        if failure.get('url')
    }
    failed_files = {
        str(failure.get('filename', '')).strip()
        for failure in downloader_failures
        if failure.get('filename')
    }
    issues: List[str] = []

    for model in preset.get('models', []):
        filename = str(model.get('filename') or '').strip()
        model_url = str(model.get('url') or '').strip()
        model_dir = str(model.get('dir') or '').strip()
        if filename:
            target = root / model_dir / filename
            legacy_control = target.with_name(f"{target.name}.aria2")
            if not target.exists() or legacy_control.exists():
                issues.append(f"modelo ausente: {filename}")
            elif filename in failed_files or model_url in failed_urls:
                issues.append(f"download falhou: {filename}")
        elif model_url in failed_urls:
            issues.append("download sem filename falhou")

    for node_url in preset.get('nodes', []):
        node_name = str(node_url).rstrip('/').split('/')[-1]
        if node_name in failed_node_names:
            issues.append(f"custom node falhou: {node_name}")

    return list(dict.fromkeys(issues))


def uninstall_preset(preset_name: str) -> Dict[str, Any]:
    """Remove model files belonging exclusively to a preset.

    Models shared with other still-installed presets are kept. Custom nodes
    are NOT touched (they are typically small and frequently shared).

    Returns a dict with: success, preset, deleted (list), shared_kept,
    civitai_skipped, missing, errors, bytes_freed.
    """
    state = get_state_manager()

    if preset_name.lower() == 'base':
        return {"success": False, "error": "Preset 'Base' não pode ser removido"}

    if not state.is_preset_installed(preset_name):
        return {"success": False, "error": f"Preset '{preset_name}' não está instalado"}

    all_presets = load_presets()
    preset_map = {p.get('name', p['_filename']): p for p in all_presets}

    target = preset_map.get(preset_name)
    if not target:
        # Preset file disappeared — still allow cleaning state, but no files to remove
        state.remove_preset(preset_name)
        return {
            "success": True, "preset": preset_name,
            "deleted": [], "shared_kept": 0, "civitai_skipped": 0,
            "missing": 0, "errors": [], "bytes_freed": 0,
            "warning": "Arquivo do preset não encontrado; apenas estado foi limpo"
        }

    # (dir, filename) pairs that must be kept because other installed presets use them
    other_installed = [
        n for n in state.get_installed_presets()
        if n != preset_name and n in preset_map
    ]
    keep_files = set()
    for other_name in other_installed:
        for m in preset_map[other_name].get('models', []):
            d = m.get('dir', '')
            f = (m.get('filename') or '').strip()
            if f:
                keep_files.add((d, f))

    models_root = MODELS_DIR.resolve()
    deleted: List[Dict[str, Any]] = []
    shared_kept = 0
    civitai_skipped = 0
    missing = 0
    errors: List[Dict[str, str]] = []
    bytes_freed = 0

    for m in target.get('models', []):
        d = m.get('dir', '') or ''
        f = (m.get('filename') or '').strip()
        url = (m.get('url') or '').strip()

        if not f:
            # Civitai/empty filename: filename was resolved at runtime, can't
            # identify reliably here. Skip with a notice. Sanitize the URL
            # because Civitai download links may carry tokens as query params.
            civitai_skipped += 1
            logger.warning(
                f"Pulando arquivo sem nome definido (Civitai?): {_sanitize_git_output(url)}"
            )
            continue

        if (d, f) in keep_files:
            shared_kept += 1
            logger.info(f"Mantendo (compartilhado com outro preset): {d}/{f}")
            continue

        try:
            target_path = (MODELS_DIR / d / f).resolve()
        except Exception as e:
            errors.append({"file": f, "error": f"resolve_failed: {e}"})
            continue

        # Path traversal safety
        try:
            target_path.relative_to(models_root)
        except ValueError:
            errors.append({"file": f, "error": "path_outside_models_dir"})
            logger.error(f"Recusado: caminho fora de {models_root}: {target_path}")
            continue

        if not target_path.exists():
            missing += 1
            state.remove_model(f)
            continue

        try:
            size = target_path.stat().st_size
            target_path.unlink()
            bytes_freed += size
            deleted.append({"dir": d, "filename": f, "size": size})
            state.remove_model(f)
            logger.info(f"Removido: {d}/{f} ({size} bytes)")
        except Exception as e:
            errors.append({"file": f, "error": str(e)})
            logger.error(f"Falha ao remover {target_path}: {e}")

    state.remove_preset(preset_name)

    logger.info(
        f"Uninstall '{preset_name}': {len(deleted)} removidos, "
        f"{shared_kept} mantidos (compartilhados), {missing} ausentes, "
        f"{civitai_skipped} sem filename, {len(errors)} erros, "
        f"{bytes_freed} bytes liberados"
    )

    return {
        "success": True,
        "preset": preset_name,
        "deleted": deleted,
        "shared_kept": shared_kept,
        "civitai_skipped": civitai_skipped,
        "missing": missing,
        "errors": errors,
        "bytes_freed": bytes_freed,
    }


def _process_activity_snapshot(pid: int) -> Optional[Dict[str, float]]:
    """Aggregate CPU, I/O, RAM, and child count for one process tree."""
    try:
        import psutil
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except Exception:
        return None

    cpu_seconds = 0.0
    io_bytes = 0.0
    rss_bytes = 0.0
    live_processes = 0
    for process in processes:
        try:
            cpu = process.cpu_times()
            cpu_seconds += cpu.user + cpu.system
            io = process.io_counters()
            storage_io = io.read_bytes + io.write_bytes
            character_io = io.read_chars + io.write_chars
            io_bytes += max(storage_io, character_io)
            rss_bytes += process.memory_info().rss
            live_processes += 1
        except Exception:
            continue

    return {
        'cpu_seconds': cpu_seconds,
        'io_bytes': io_bytes,
        'rss_bytes': rss_bytes,
        'processes': float(live_processes),
    }


def _format_activity_bytes(size: float) -> str:
    if size >= 1_073_741_824:
        return f"{size / 1_073_741_824:.1f}GB"
    if size >= 1_048_576:
        return f"{size / 1_048_576:.0f}MB"
    if size >= 1024:
        return f"{size / 1024:.0f}KB"
    return f"{size:.0f}B"


def _run_pip_install_streaming(
    cmd: List[str],
    node_name: str,
    heartbeat_interval: float = 20,
    timeout_sec: float = NODE_PIP_TIMEOUT_SECONDS,
    env: Optional[Dict[str, str]] = None,
    progress_stage: Optional[str] = None,
):
    """Run pip install with streamed output, package progress and hard timeout.

    Thin wrapper over _stream_command(): the loop that drains stdout, reports
    which wheel is being fetched, honours cancellation and enforces the deadline
    lives there so pip, torch and the SageAttention build all share it.
    Returns (returncode, last_line); -1 signals a timeout, -2 a cancellation.
    """
    returncode, _, last_line = _stream_command(
        cmd,
        f"pip install ({node_name})",
        log_prefix=f"{node_name} pip",
        env=env,
        timeout_sec=timeout_sec,
        heartbeat_interval=heartbeat_interval,
        progress_stage=progress_stage,
        collect_lines=False,
    )
    return returncode, last_line


def _configure_manager_security():
    """Configure ComfyUI-Manager security for cloud deployment.

    Sets security_level=weak and network_mode=personal_cloud so that
    the Manager UI is fully functional on VastAI/Runpod instances
    (listening on 0.0.0.0).

    Uses RawConfigParser to avoid Python's DEFAULT-section magic that
    would write ``[DEFAULT]`` instead of the ``[default]`` header the
    Manager actually expects.
    """
    import configparser

    config_dir = COMFY_DIR / 'user' / '__manager'
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / 'config.ini'

    config = configparser.RawConfigParser(default_section='__unused__')
    if config_path.exists():
        config.read(str(config_path))

    if not config.has_section('default'):
        config.add_section('default')

    config.set('default', 'security_level', 'weak')
    config.set('default', 'network_mode', 'personal_cloud')

    with open(config_path, 'w') as f:
        config.write(f)

    logger.info("✓ Manager security configured (security_level=weak, network_mode=personal_cloud)")


def _is_manager_pip_installed() -> bool:
    """Check if ComfyUI-Manager v4+ is installed as a pip package."""
    try:
        result = subprocess.run(
            [_comfy_python(), '-c', 'import comfyui_manager; print("ok")'],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0 and result.stdout.strip() == 'ok'
    except Exception:
        return False


def _run_capture_cancellable(
    cmd: List[str],
    timeout_sec: float = GIT_CLONE_TIMEOUT_SECONDS,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run a short installer command with captured output, deadline and cancel.

    Without a deadline a git operation that waits for input or hangs on a dead
    connection blocks the installer forever, so callers always get one.
    """
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env if env is not None else _venv_env({'GIT_TERMINAL_PROMPT': '0'}),
        start_new_session=True,
    )
    _register_install_process(process)
    deadline = time.monotonic() + timeout_sec if timeout_sec > 0 else None
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if _install_cancel_event.is_set():
                    _terminate_install_process(process)
                    stdout, stderr = process.communicate()
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    logger.error(
                        f"Comando excedeu {timeout_sec:.0f}s e foi encerrado: "
                        f"{_sanitize_git_output(' '.join(cmd))}"
                    )
                    _terminate_install_process(process)
                    stdout, stderr = process.communicate()
                    return subprocess.CompletedProcess(
                        cmd, -1, stdout, (stderr or '') + f"\ntimeout after {timeout_sec:.0f}s"
                    )
        return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    finally:
        _unregister_install_process(process)


def _git_checkout_is_complete(dest: Path, env: Optional[Dict[str, str]] = None) -> bool:
    """True when dest holds a git repo with a resolvable HEAD.

    The presence of a `.git` entry proves nothing: a clone killed mid-flight (the
    cancel path SIGKILLs after a 3s grace) leaves `.git` behind with no HEAD, and
    the node would then be marked installed with its code absent. --git-dir is
    explicit so git cannot walk up and answer for a parent repository.
    """
    git_dir = dest / '.git'
    if not git_dir.exists():
        return False
    probe = _run_capture_cancellable(
        ['git', f'--git-dir={git_dir}', 'rev-parse', '--verify', 'HEAD'],
        timeout_sec=60,
        env=env,
    )
    return probe.returncode == 0


def _clone_node(
    url: str,
    cn_dir: Path,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[str, str, Path, bool, Optional[str]]:
    """Clone a single custom node with retry. Returns (url, node_name, dest,
    clone_ok, skip_reason). skip_reason is set when no clone was needed.

    Clones land in a temporary sibling directory and are moved into place only
    once git succeeded, so an interrupted clone can never leave a half-populated
    node behind for the next run to trust.
    """
    node_name = url.rstrip('/').split('/')[-1]
    dest = cn_dir / node_name

    if _git_checkout_is_complete(dest, env=env):
        return (url, node_name, dest, True, 'already_installed')

    if dest.exists():
        backup = cn_dir / f"{node_name}.backup-{int(time.time())}"
        logger.warning(
            f"Node directory is not a complete git checkout: {dest}. "
            f"Renaming to {backup.name} and cloning again."
        )
        try:
            dest.rename(backup)
        except Exception as e:
            logger.error(f"Could not rename stale node dir {dest}: {e}")
            return (url, node_name, dest, False, None)

    logger.info(f"Cloning: {node_name}")

    for attempt in range(1, 3):
        if _install_cancel_event.is_set():
            return (url, node_name, dest, False, 'cancelled')

        staging = cn_dir / f".{node_name}.cloning-{os.getpid()}-{attempt}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            # The URL carries no credentials: authentication comes from the
            # GIT_ASKPASS helper in `env`, so the token stays out of argv and out
            # of the cloned repo's .git/config.
            clone = _run_capture_cancellable(
                ['git', 'clone', '--depth', '1', url, str(staging)],
                env=env,
            )
            if clone.returncode == 0:
                try:
                    staging.rename(dest)
                    return (url, node_name, dest, True, None)
                except Exception as e:
                    logger.error(f"Could not move cloned node into place ({dest}): {e}")
                    return (url, node_name, dest, False, None)

            if _install_cancel_event.is_set():
                return (url, node_name, dest, False, 'cancelled')

            logger.warning(
                f"Clone failed for {node_name} (attempt {attempt}/2, exit {clone.returncode})"
            )
            if clone.stderr:
                logger.warning(f"[{node_name} stderr] {_sanitize_git_output(clone.stderr.strip())}")
            if clone.stdout:
                logger.warning(f"[{node_name} stdout] {_sanitize_git_output(clone.stdout.strip())}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        if attempt == 1:
            time.sleep(2)

    return (url, node_name, dest, False, None)


def install_custom_nodes(node_urls: List[str]) -> Dict[str, Any]:
    """Clone/update custom nodes with resilience - continues on failure.

    Clones run in parallel (IO/network-bound, safe). Requirements install
    stays sequential because pip can corrupt an env under concurrent writes.

    Returns dict with 'success' (bool), 'failed' (list of failed node names).
    """
    state = get_state_manager()
    cn_dir = COMFY_DIR / 'custom_nodes'
    cn_dir.mkdir(parents=True, exist_ok=True)

    # Deduplicate while preserving order
    node_urls = list(dict.fromkeys(node_urls))
    failed_nodes: List[str] = []

    # Always configure Manager security (covers both pip and git installs)
    if any('ComfyUI-Manager' in u for u in node_urls):
        _configure_manager_security()

    with _git_credential_env() as git_env:
        # Partition into: manager-via-pip, already-installed, and needs-clone.
        to_clone: List[str] = []
        for url in node_urls:
            if _install_cancel_event.is_set():
                return {"success": False, "failed": failed_nodes, "cancelled": True}
            node_name = url.rstrip('/').split('/')[-1]
            if node_name == 'ComfyUI-Manager' and _is_manager_pip_installed():
                logger.info("✓ ComfyUI-Manager v4+ detected as pip package (skipping git clone)")
                state.add_node(url)
                continue
            node_dest = cn_dir / node_name
            if state.is_node_installed(url) and _git_checkout_is_complete(node_dest, env=git_env):
                logger.info(f"✓ Fully installed node: {node_name} (skipping)")
                continue
            to_clone.append(url)

        if not to_clone:
            return {"success": len(failed_nodes) == 0, "failed": failed_nodes}

        workers = max(1, min(NODES_CLONE_WORKERS, len(to_clone)))
        logger.info(
            f"Cloning {len(to_clone)} custom nodes (parallel workers={workers}); "
            "requirements install as clones complete"
        )
        _set_progress_stage('nodes', f"clonando {len(to_clone)} custom node(s)")

        # Pipeline: submit clones to a pool and consume completions as they arrive
        # in the main thread so `pip install -r requirements.txt` can start for a
        # finished node while other nodes are still cloning. Pip itself stays
        # sequential (single-threaded here) because concurrent writes into the
        # same venv can corrupt dist-info.
        done_nodes = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_clone_node, url, cn_dir, git_env) for url in to_clone]
            for fut in as_completed(futures):
                try:
                    url, node_name, dest, clone_ok, skip_reason = fut.result()
                except Exception as e:
                    # Losing the result silently used to report a clean install.
                    logger.error(f"Clone task raised an exception: {e}")
                    failed_nodes.append('<tarefa de clone com exceção>')
                    continue

                if skip_reason == 'cancelled' or _install_cancel_event.is_set():
                    logger.warning("Instalação de custom nodes cancelada")
                    for pending in futures:
                        pending.cancel()
                    return {"success": False, "failed": failed_nodes, "cancelled": True}

                if skip_reason == 'already_installed':
                    logger.info(
                        f"Existing clone without completed state: {node_name}; "
                        "resuming requirements installation"
                    )

                done_nodes += 1
                if not clone_ok:
                    logger.error(f"Failed to clone node {node_name} after retries: {url}")
                    failed_nodes.append(node_name)
                    continue

                req_file = dest / 'requirements.txt'
                if req_file.exists():
                    logger.info(f"Installing requirements for {node_name}...")
                    _set_progress_stage(
                        'nodes',
                        f"[{done_nodes}/{len(to_clone)}] requirements de {node_name}"
                    )
                    retcode, last_line = _run_pip_install_streaming(
                        _pip_install_argv(['-r', str(req_file)]),
                        node_name,
                        progress_stage='nodes',
                    )
                    if retcode != 0:
                        if _install_cancel_event.is_set():
                            return {"success": False, "failed": failed_nodes, "cancelled": True}
                        logger.warning(
                            f"Requirements install failed for {node_name} (exit {retcode}), continuing"
                        )
                        if last_line:
                            logger.warning(f"[{node_name} pip last] {last_line}")
                        failed_nodes.append(node_name)
                        continue

                state.add_node(url)

    if failed_nodes:
        logger.warning(f"Nodes that failed to install: {', '.join(failed_nodes)}")

    return {"success": len(failed_nodes) == 0, "failed": failed_nodes}


def start_web_server():
    """Start the preset selector web server"""
    from server import run_server

    logger.info(f"Starting web selector on port {WEB_PORT}")
    logger.info(f"Access via VastAI/Runpod port forwarding")

    run_server(port=WEB_PORT, presets_callback=load_presets)


def start_comfyui() -> bool:
    """Start ComfyUI through the process manager.

    Delegating instead of duplicating the launch keeps a single code path that
    applies the persisted preset flags (including --use-sage-attention, without
    which the whole SageAttention install is dead weight), checks the port, waits
    for the healthcheck and records the PID in state.
    """
    logger.info(f"Starting ComfyUI on port {COMFY_PORT}")
    state = get_state_manager()
    flags = state.get_comfyui_flags()
    if flags:
        logger.info(f"Flags persistidas dos presets: {' '.join(flags)}")

    process_manager = get_process_manager(state)
    started = process_manager.start(port=COMFY_PORT)
    if started:
        logger.info(f"ComfyUI started at http://0.0.0.0:{COMFY_PORT}")
    else:
        logger.error("ComfyUI não subiu (veja o log acima para porta ocupada ou timeout)")
    return started


def start_cloudflared():
    """Start Cloudflared tunnel (disabled by default - user configures VastAI ports)"""
    # Cloudflared is disabled by default
    # Users should configure port forwarding in VastAI/Runpod instead
    logger.info("Cloudflared auto-start is disabled (configure VastAI/Runpod port forwarding)")
    return

    # Uncomment below to enable Cloudflared
    # logger.info("Starting Cloudflared tunnel...")
    # cmd = [
    #     'cloudflared',
    #     'tunnel',
    #     '--url', f'http://localhost:{COMFY_PORT}'
    # ]
    # subprocess.Popen(cmd)
    # logger.info("Cloudflared tunnel started")


def main():
    parser = argparse.ArgumentParser(description='Arrakis Start - ComfyUI Deployment')
    parser.add_argument(
        '--presets',
        nargs='+',
        help='Presets to install (e.g., qwen-image sdxl-anime). Base is auto-included.'
    )
    parser.add_argument(
        '--base-only',
        action='store_true',
        help='Install only the base preset'
    )
    parser.add_argument(
        '--no-base',
        action='store_true',
        help='Do not auto-include base preset'
    )
    parser.add_argument(
        '--web-only',
        action='store_true',
        help='Only start web selector (no auto-install)'
    )
    parser.add_argument(
        '--start-comfy',
        action='store_true',
        help='Start ComfyUI after installation (Cloudflared disabled by default)'
    )
    parser.add_argument(
        '--enable-cloudflared',
        action='store_true',
        help='Enable Cloudflared tunnel (disabled by default)'
    )

    args = parser.parse_args()

    def _launch_comfy() -> None:
        if not start_comfyui():
            sys.exit(1)
        if args.enable_cloudflared:
            start_cloudflared()

    # Install base-only if specified
    if args.base_only:
        if not install_presets(['Base'], include_base=False):
            logger.error("Installation failed")
            sys.exit(1)
        if args.start_comfy:
            _launch_comfy()

    # Install presets if specified
    elif args.presets:
        if not install_presets(args.presets, include_base=not args.no_base):
            logger.error("Installation failed")
            sys.exit(1)
        if args.start_comfy:
            _launch_comfy()

    # Start ComfyUI with what is already installed (documented usage:
    # `python start.py --start-comfy`). This used to fall through to the web
    # selector and never start ComfyUI at all.
    elif args.start_comfy and not args.web_only:
        _launch_comfy()

    # Start web server
    else:
        start_web_server()


if __name__ == '__main__':
    main()
