#!/usr/bin/env python3
"""
Arrakis Start - ComfyUI Deployment System v2.0
Main orchestrator for preset-based installation with state management
"""

import os
import sys
import json
import subprocess
import logging
import shlex
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
import argparse

# Import state manager
from state import get_state_manager
from process_manager import get_process_manager

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
WEB_PORT = int(os.environ.get('WEB_PORT', '8090'))
COMFY_PORT = int(os.environ.get('COMFY_PORT', '8818'))
DEFAULT_TORCH_INDEX_URL = os.environ.get('TORCH_INDEX_URL', 'https://download.pytorch.org/whl/cu128')
SAGEATTENTION_INSTALLER_URL = os.environ.get(
    'SAGEATTENTION_INSTALLER_URL',
    'https://raw.githubusercontent.com/adbrasi/sageattention220-ultimate-installer/refs/heads/main/install_sageattention220_wheel.sh'
)


def _comfy_python() -> str:
    """Return ComfyUI runtime python executable."""
    if COMFY_PYTHON.exists():
        return str(COMFY_PYTHON)
    logger.warning(
        f"ComfyUI python not found at {COMFY_PYTHON}; falling back to current interpreter"
    )
    return sys.executable


def load_presets() -> List[Dict]:
    """Load all preset JSON files from presets/ directory"""
    presets = []
    
    if not PRESETS_DIR.exists():
        logger.warning(f"Presets directory not found: {PRESETS_DIR}")
        return presets
    
    for preset_file in PRESETS_DIR.glob('*.json'):
        try:
            with open(preset_file, 'r', encoding='utf-8') as f:
                preset = json.load(f)
                preset['_filename'] = preset_file.name
                presets.append(preset)
                logger.info(f"Loaded preset: {preset.get('name', preset_file.name)}")
        except Exception as e:
            logger.error(f"Failed to load preset {preset_file}: {e}")
    
    return presets

# Global tracker for cancellation
_active_downloader = None

def get_active_downloader():
    return _active_downloader

def cancel_active_install():
    """Cancel the currently active installation"""
    global _active_downloader
    if _active_downloader:
        logger.warning("Cancelling active installation...")
        _active_downloader.cancel()
        _active_downloader = None
        return True
    return False


def _cuda_available() -> bool:
    """Check CUDA availability using ComfyUI runtime python."""
    py = _comfy_python()
    try:
        probe = subprocess.run(
            [py, '-c', 'import torch; print(int(torch.cuda.is_available()))'],
            check=False,
            capture_output=True,
            text=True
        )
        return probe.returncode == 0 and probe.stdout.strip() == '1'
    except Exception:
        return False


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


def _run_streaming_command(
    cmd: List[str],
    description: str,
    log_prefix: str = 'cmd',
    env: Optional[Dict[str, str]] = None
) -> Tuple[int, List[str]]:
    """Run command with streamed logs and collect output lines for diagnostics."""
    logger.info(f"Running {description}: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env
    )
    output_lines: List[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        if line:
            output_lines.append(line)
            logger.info(f"[{log_prefix}] {line}")
    process.wait()
    return process.returncode, output_lines


def _verify_python_import(package_name: str, python_bin: Optional[str] = None) -> bool:
    """Verify package import in selected Python environment."""
    target_python = python_bin or _comfy_python()
    verify = subprocess.run(
        [target_python, '-c', f'import {package_name}'],
        check=False,
        capture_output=True,
        text=True
    )
    if verify.returncode != 0:
        logger.error(f"Import check failed for '{package_name}'")
        if verify.stderr:
            logger.error(verify.stderr.strip())
        return False
    return True


def configure_runtime_stack(use_sage_attention: bool) -> bool:
    """Configure runtime stack only when SageAttention is explicitly requested."""
    state = get_state_manager()
    current_stack = state.get_runtime_stack()

    if use_sage_attention:
        if current_stack == 'sageattention':
            logger.info("SageAttention runtime already active, skipping reconfiguration")
            return True

        comfy_activate = VENV_DIR / 'bin' / 'activate'
        logger.info(
            "Preset requests SageAttention: keeping normal ComfyUI install and "
            "running unified SageAttention installer"
        )
        cmd = [
            'bash',
            '-lc',
            (
                f"source {shlex.quote(str(comfy_activate))} && "
                f"curl -fsSL {shlex.quote(SAGEATTENTION_INSTALLER_URL)} | bash -s -- auto"
            )
        ]
        result_code, output_lines = _run_streaming_command(
            cmd,
            "SageAttention unified installer",
            log_prefix='sage'
        )
        if result_code != 0:
            logger.error(f"SageAttention installer failed (exit {result_code})")
            if output_lines:
                logger.error(f"Last installer lines: {' | '.join(output_lines[-10:])}")
            return False

        comfy_python = _comfy_python()
        for package_name in ('torch', 'triton', 'sageattention'):
            if not _verify_python_import(package_name, python_bin=comfy_python):
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


def install_pip_commands(pip_commands: List[Any]) -> bool:
    """Install preset-defined pip dependencies."""
    if not pip_commands:
        return True

    cuda_available = _cuda_available()
    logger.info(f"CUDA available for pip conditions: {cuda_available}")

    for index, item in enumerate(pip_commands, start=1):
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

        if condition == 'cuda_available' and not cuda_available:
            logger.warning(f"Skipping {description}: CUDA unavailable")
            continue

        try:
            cmd = _normalize_pip_command(command)
        except Exception as e:
            logger.error(f"Failed to normalize {description}: {e}")
            return False

        result_code, output_lines = _run_streaming_command(cmd, description, log_prefix='pip')

        if result_code != 0:
            logger_msg = logger.warning if allow_failure else logger.error
            logger_msg(f"Failed {description} (exit {result_code})")
            if output_lines:
                logger_msg(f"Last pip lines: {' | '.join(output_lines[-10:])}")
            if not allow_failure:
                return False
            continue

        if verify_import:
            if not _verify_python_import(verify_import):
                logger_msg = logger.warning if allow_failure else logger.error
                logger_msg(f"Package installed but import failed for '{verify_import}' in {description}")
                if not allow_failure:
                    return False

        logger.info(f"✓ Completed: {description}")

    return True


def install_presets(preset_names: List[str], include_base: bool = True) -> bool:
    """Install selected presets with smart skip-existing and parallelism"""
    from downloader import DownloadManager
    state = get_state_manager()
    global _active_downloader
    
    # Auto-include base preset unless explicitly disabled
    if include_base and 'Base' not in preset_names:
        preset_names = ['Base'] + preset_names
        logger.info("Auto-including 'Base' preset")
    
    logger.info(f"Installing presets: {', '.join(preset_names)}")
    
    # Load all presets
    all_presets = load_presets()
    preset_map = {p.get('name', p['_filename']): p for p in all_presets}
    
    # Collect all downloads, nodes, flags, and preset pip commands
    downloads = []
    nodes = []
    collected_flags = []  # Preset-specific ComfyUI flags
    pip_commands = []
    use_sage_attention = False
    
    for preset_name in preset_names:
        if preset_name not in preset_map:
            logger.error(f"Preset not found: {preset_name}")
            continue
        
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

                dest_path = MODELS_DIR / model_dir / filename
                if dest_path.exists():
                    logger.info(f"✓ Already exists: {filename}")
                    state.add_model(filename, model_dir, model.get('url', ''), 0)
                else:
                    downloads.append(model)
        
        # Add custom nodes
        if 'nodes' in preset:
            nodes.extend(preset['nodes'])
        
        # Collect preset-specific ComfyUI flags
        if 'comfyui_flags' in preset:
            collected_flags.extend(preset['comfyui_flags'])
            logger.info(f"Preset '{preset_name}' adds flags: {preset['comfyui_flags']}")

        # Runtime stack selector
        if bool(preset.get('use_sage_attention', False)):
            use_sage_attention = True
            logger.info(f"Preset '{preset_name}' enables SageAttention runtime stack")
        
        # Collect preset-specific pip commands
        if 'pip_commands' in preset:
            pip_commands.extend(preset['pip_commands'])

    # 1. Configure runtime stack before preset-specific pip commands.
    if not configure_runtime_stack(use_sage_attention=use_sage_attention):
        logger.error("Installation failed during runtime stack configuration")
        return False

    # 2. Run preset-specific pip commands
    if pip_commands:
        logger.info(f"Running {len(pip_commands)} preset pip command(s) before downloads...")
        if not install_pip_commands(pip_commands):
            logger.error("Installation failed during preset pip commands")
            return False

    # Deduplicate and save collected flags (can be empty to clear stale state)
    unique_flags = list(dict.fromkeys(collected_flags))
    state.set_comfyui_flags(unique_flags)
    logger.info(f"Saved {len(unique_flags)} preset-specific ComfyUI flags")
    
    # 3. Execute downloads and node installs in parallel
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
        nodes_success = True
        if download_future is not None:
            download_success = bool(download_future.result())
        if nodes_future is not None:
            nodes_success = bool(nodes_future.result())
    
    downloader_failures = []
    if _active_downloader and hasattr(_active_downloader, 'get_failure_report'):
        downloader_failures = _active_downloader.get_failure_report()
    _active_downloader = None
    
    # Download errors are non-blocking (continue install/start); node errors remain blocking.
    if not download_success:
        if downloader_failures:
            logger.warning("Detailed download failures:")
            for idx, failure in enumerate(downloader_failures, 1):
                logger.warning(
                    f"[{idx}] file={failure.get('filename')} dir={failure.get('dir')} "
                    f"stage={failure.get('stage')} reason={failure.get('reason')} "
                    f"url={failure.get('url')}"
                )
            
            # Configuration/credential errors are fatal (don't pretend install succeeded).
            fatal_download_error = any(
                str(f.get('stage', '')).lower() == 'precheck' or
                'missing (required for civitai downloads)' in str(f.get('reason', '')).lower()
                for f in downloader_failures
            )
            if fatal_download_error:
                logger.error("Installation failed due to missing/invalid download configuration (precheck error)")
                return False
        
        logger.warning("Some downloads failed, continuing installation as requested.")
    
    if not nodes_success:
        logger.error("Installation failed: one or more custom nodes failed to install")
        return False
    
    # 4. Mark presets as installed
    for preset_name in preset_names:
        state.add_preset(preset_name)
    
    logger.info("All presets installed successfully!")
    return True


def install_custom_nodes(node_urls: List[str]) -> bool:
    """Clone/update custom nodes with better diagnostics and retry."""
    state = get_state_manager()
    cn_dir = COMFY_DIR / 'custom_nodes'
    cn_dir.mkdir(parents=True, exist_ok=True)
    
    # Deduplicate while preserving order
    node_urls = list(dict.fromkeys(node_urls))
    
    for url in node_urls:
        node_name = url.rstrip('/').split('/')[-1]
        dest = cn_dir / node_name
        
        try:
            if (dest / '.git').exists():
                logger.info(f"✓ Already installed: {node_name} (skipping)")
                state.add_node(url)
                continue

            if dest.exists():
                backup = cn_dir / f"{node_name}.backup-{int(time.time())}"
                logger.warning(
                    f"Node directory exists without git metadata: {dest}. "
                    f"Renaming to {backup.name} and retrying clone."
                )
                dest.rename(backup)
            
            logger.info(f"Cloning: {node_name}")
            clone_ok = False
            for attempt in range(1, 3):
                clone = subprocess.run(
                    ['git', 'clone', '--depth', '1', url, str(dest)],
                    check=False,
                    capture_output=True,
                    text=True
                )
                if clone.returncode == 0:
                    clone_ok = True
                    break

                logger.warning(
                    f"Clone failed for {node_name} (attempt {attempt}/2, exit {clone.returncode})"
                )
                if clone.stderr:
                    logger.warning(f"[{node_name} stderr] {clone.stderr.strip()}")
                if clone.stdout:
                    logger.warning(f"[{node_name} stdout] {clone.stdout.strip()}")
                if attempt == 1:
                    time.sleep(2)

            if not clone_ok:
                logger.error(f"Failed to clone node {node_name} after retries: {url}")
                return False
            
            # Install requirements if exists
            req_file = dest / 'requirements.txt'
            if req_file.exists():
                logger.info(f"Installing requirements for {node_name}")
                req = subprocess.run(
                    [_comfy_python(), '-m', 'pip', 'install', '-q', '-r', str(req_file)],
                    check=False,
                    capture_output=True,
                    text=True
                )
                if req.returncode != 0:
                    logger.warning(
                        f"Requirements install failed for {node_name} (exit {req.returncode}), continuing"
                    )
                    if req.stderr:
                        logger.warning(f"[{node_name} req stderr] {req.stderr.strip()}")
                    if req.stdout:
                        logger.warning(f"[{node_name} req stdout] {req.stdout.strip()}")
            
            # Track as installed
            state.add_node(url)
        
        except Exception as e:
            logger.error(f"Failed to install {node_name}: {e}")
            return False
    
    return True


def start_web_server():
    """Start the preset selector web server"""
    from server import run_server
    
    logger.info(f"Starting web selector on port {WEB_PORT}")
    logger.info(f"Access via VastAI/Runpod port forwarding")
    
    run_server(port=WEB_PORT, presets_callback=load_presets)


def start_comfyui():
    """Start ComfyUI server"""
    logger.info(f"Starting ComfyUI on port {COMFY_PORT}")
    
    # Activate venv and run comfy
    activate_script = VENV_DIR / 'bin' / 'activate'
    
    cmd = [
        COMFY_CLI,
        '--workspace', str(COMFY_DIR),
        'launch',
        '--',
        '--listen', '0.0.0.0',
        '--port', str(COMFY_PORT),
        '--preview-method', 'latent2rgb',
        '--front-end-version', 'Comfy-Org/ComfyUI_frontend@latest'
    ]
    
    # Run in subprocess
    subprocess.Popen(cmd, cwd=str(COMFY_DIR))
    logger.info(f"ComfyUI started at http://0.0.0.0:{COMFY_PORT}")


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
    
    # Install base-only if specified
    if args.base_only:
        success = install_presets(['Base'], include_base=False)
        if not success:
            logger.error("Installation failed")
            sys.exit(1)
        
        if args.start_comfy:
            start_comfyui()
            if args.enable_cloudflared:
                start_cloudflared()
    
    # Install presets if specified
    elif args.presets:
        success = install_presets(args.presets, include_base=not args.no_base)
        if not success:
            logger.error("Installation failed")
            sys.exit(1)
        
        if args.start_comfy:
            start_comfyui()
            if args.enable_cloudflared:
                start_cloudflared()
    
    # Start web server
    elif args.web_only or not args.presets:
        start_web_server()


if __name__ == '__main__':
    main()
