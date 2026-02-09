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
from pathlib import Path
from typing import List, Dict, Any
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

# Ports
WEB_PORT = int(os.environ.get('WEB_PORT', '8090'))
COMFY_PORT = int(os.environ.get('COMFY_PORT', '8818'))


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
    """Check if CUDA is available through PyTorch."""
    try:
        import torch  # type: ignore
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _normalize_pip_command(command: Any) -> List[str]:
    """Normalize preset pip command into a safe argv list."""
    if isinstance(command, str):
        tokens = shlex.split(command)
    elif isinstance(command, list):
        tokens = [str(x) for x in command if str(x).strip()]
    else:
        raise ValueError("pip command must be a string or list")

    if not tokens:
        raise ValueError("pip command is empty")

    first = tokens[0]
    python_aliases = {sys.executable, Path(sys.executable).name, 'python', 'python3'}

    if first in ('pip', 'pip3'):
        return [sys.executable, '-m', 'pip'] + tokens[1:]

    if first in python_aliases and len(tokens) >= 3 and tokens[1] == '-m' and tokens[2] == 'pip':
        return [sys.executable, '-m', 'pip'] + tokens[3:]

    return [sys.executable, '-m', 'pip'] + tokens


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

        logger.info(f"Running {description}: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            logger_msg = logger.warning if allow_failure else logger.error
            logger_msg(f"Failed {description} (exit {result.returncode})")
            if result.stdout:
                logger_msg(result.stdout.strip())
            if result.stderr:
                logger_msg(result.stderr.strip())
            if not allow_failure:
                return False
            continue

        if verify_import:
            verify = subprocess.run(
                [sys.executable, '-c', f'import {verify_import}'],
                check=False,
                capture_output=True,
                text=True
            )
            if verify.returncode != 0:
                logger_msg = logger.warning if allow_failure else logger.error
                logger_msg(f"Package installed but import failed for '{verify_import}' in {description}")
                if verify.stderr:
                    logger_msg(verify.stderr.strip())
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
        
        # Collect preset-specific pip commands
        if 'pip_commands' in preset:
            pip_commands.extend(preset['pip_commands'])
    
    # Deduplicate and save collected flags
    if collected_flags:
        unique_flags = list(dict.fromkeys(collected_flags))  # Preserve order
        state.set_comfyui_flags(unique_flags)
        logger.info(f"Saved {len(unique_flags)} preset-specific ComfyUI flags")
    
    # Execute downloads and node installs in parallel (4 workers for better throughput)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = []
        
        # 1. Download models
        if downloads:
            logger.info(f"Downloading {len(downloads)} new models...")
            _active_downloader = DownloadManager(models_dir=MODELS_DIR)
            futures.append(executor.submit(_active_downloader.download_all, downloads))
        else:
            logger.info("All models already installed, skipping downloads")
            
        # 2. Install custom nodes (concurrently)
        if nodes:
            logger.info(f"Installing {len(nodes)} custom nodes...")
            futures.append(executor.submit(install_custom_nodes, nodes))
            
        # Wait for completion
        success = True
        for future in futures:
            if not future.result():
                success = False
    
    _active_downloader = None
    
    if not success:
        logger.error("Installation failed (some items failed)")
        return False

    # 3. Run preset-specific pip commands after downloads/nodes
    if pip_commands:
        logger.info(f"Running {len(pip_commands)} preset pip command(s)...")
        if not install_pip_commands(pip_commands):
            logger.error("Installation failed during preset pip commands")
            return False
    
    # Mark presets as installed
    for preset_name in preset_names:
        state.add_preset(preset_name)
    
    logger.info("All presets installed successfully!")
    return True


def install_custom_nodes(node_urls: List[str]) -> bool:
    """Clone/update custom nodes with smart skip-existing"""
    state = get_state_manager()
    cn_dir = COMFY_DIR / 'custom_nodes'
    cn_dir.mkdir(parents=True, exist_ok=True)
    
    # Deduplicate
    node_urls = list(set(node_urls))
    
    for url in node_urls:
        node_name = url.rstrip('/').split('/')[-1]
        dest = cn_dir / node_name
        
        try:
            if (dest / '.git').exists():
                logger.info(f"✓ Already installed: {node_name} (skipping)")
                state.add_node(url)
                continue
            
            logger.info(f"Cloning: {node_name}")
            subprocess.run(
                ['git', 'clone', '--depth', '1', url, str(dest)],
                check=True,
                capture_output=True
            )
            
            # Install requirements if exists
            req_file = dest / 'requirements.txt'
            if req_file.exists():
                logger.info(f"Installing requirements for {node_name}")
                subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '-q', '-r', str(req_file)],
                    check=False,
                    capture_output=True
                )
            
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
        'comfy',
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
