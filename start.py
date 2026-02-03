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
from pathlib import Path
from typing import List, Dict
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


def install_presets(preset_names: List[str], include_base: bool = True) -> bool:
    """Install selected presets with smart skip-existing"""
    from downloader import DownloadManager
    state = get_state_manager()
    
    # Auto-include base preset unless explicitly disabled
    if include_base and 'Base' not in preset_names:
        preset_names = ['Base'] + preset_names
        logger.info("Auto-including 'Base' preset")
    
    logger.info(f"Installing presets: {', '.join(preset_names)}")
    
    # Load all presets
    all_presets = load_presets()
    preset_map = {p.get('name', p['_filename']): p for p in all_presets}
    
    # Collect all downloads
    downloads = []
    nodes = []
    
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
    
    # Download models
    if downloads:
        logger.info(f"Downloading {len(downloads)} new models...")
        dm = DownloadManager(models_dir=MODELS_DIR)
        success = dm.download_all(downloads)
        if not success:
            logger.error("Some downloads failed")
            return False
        
        # Track installed models
        for model in downloads:
            state.add_model(
                model.get('filename', ''),
                model.get('dir', ''),
                model.get('url', ''),
                0
            )
    else:
        logger.info("All models already installed, skipping downloads")
    
    # Install custom nodes
    if nodes:
        success = install_custom_nodes(nodes)
        if not success:
            logger.error("Some custom nodes failed to install")
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
