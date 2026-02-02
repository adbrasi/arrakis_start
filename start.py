#!/usr/bin/env python3
"""
Arrakis Start - ComfyUI Deployment System
Main orchestrator for preset-based installation
"""

import os
import sys
import json
import subprocess
import logging
from pathlib import Path
from typing import List, Dict
import argparse

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


def install_presets(preset_names: List[str]) -> bool:
    """Install selected presets using downloader"""
    from downloader import DownloadManager
    
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
        
        # Add models to download queue
        if 'models' in preset:
            downloads.extend(preset['models'])
        
        # Add custom nodes
        if 'nodes' in preset:
            nodes.extend(preset['nodes'])
    
    # Download models
    if downloads:
        dm = DownloadManager(models_dir=MODELS_DIR)
        success = dm.download_all(downloads)
        if not success:
            logger.error("Some downloads failed")
            return False
    
    # Install custom nodes
    if nodes:
        success = install_custom_nodes(nodes)
        if not success:
            logger.error("Some custom nodes failed to install")
            return False
    
    logger.info("All presets installed successfully!")
    return True


def install_custom_nodes(node_urls: List[str]) -> bool:
    """Clone/update custom nodes"""
    cn_dir = COMFY_DIR / 'custom_nodes'
    cn_dir.mkdir(parents=True, exist_ok=True)
    
    # Deduplicate
    node_urls = list(set(node_urls))
    
    for url in node_urls:
        node_name = url.rstrip('/').split('/')[-1]
        dest = cn_dir / node_name
        
        try:
            if (dest / '.git').exists():
                logger.info(f"Updating: {node_name}")
                subprocess.run(
                    ['git', '-C', str(dest), 'pull', '--ff-only'],
                    check=False,
                    capture_output=True
                )
            else:
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
    """Start Cloudflared tunnel"""
    logger.info("Starting Cloudflared tunnel...")
    
    cmd = [
        'cloudflared',
        'tunnel',
        '--url', f'http://localhost:{COMFY_PORT}'
    ]
    
    subprocess.Popen(cmd)
    logger.info("Cloudflared tunnel started")


def main():
    parser = argparse.ArgumentParser(description='Arrakis Start - ComfyUI Deployment')
    parser.add_argument(
        '--presets',
        nargs='+',
        help='Presets to install (e.g., base qwen-image)'
    )
    parser.add_argument(
        '--web-only',
        action='store_true',
        help='Only start web selector (no auto-install)'
    )
    parser.add_argument(
        '--start-comfy',
        action='store_true',
        help='Start ComfyUI and Cloudflared after installation'
    )
    
    args = parser.parse_args()
    
    # Install presets if specified
    if args.presets:
        success = install_presets(args.presets)
        if not success:
            logger.error("Installation failed")
            sys.exit(1)
        
        if args.start_comfy:
            start_comfyui()
            start_cloudflared()
    
    # Start web server
    elif args.web_only or not args.presets:
        start_web_server()


if __name__ == '__main__':
    main()
