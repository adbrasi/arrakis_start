#!/usr/bin/env python3
"""
Process Manager - ComfyUI lifecycle management
Start, stop, restart ComfyUI with configurable flags
"""

import os
import sys
import subprocess
import logging
import signal
import time
import requests
from pathlib import Path
from typing import List, Optional, Dict
import psutil

logger = logging.getLogger(__name__)

# Paths
COMFY_BASE = Path(os.environ.get('COMFY_BASE', '/workspace/comfy'))
COMFY_DIR = COMFY_BASE / 'ComfyUI'
VENV_DIR = COMFY_BASE / '.venv'


class ProcessManager:
    """Manages ComfyUI process lifecycle"""
    
    def __init__(self, state_manager):
        self.state_manager = state_manager
        self.process = None
    
    def is_running(self) -> bool:
        """Check if ComfyUI is running"""
        status = self.state_manager.get_comfyui_status()
        pid = status.get('pid')
        
        if pid is None:
            return False
        
        # Check if process exists
        try:
            process = psutil.Process(pid)
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    
    def health_check(self, port: int = 8818, timeout: int = 5) -> bool:
        """Check if ComfyUI is responding"""
        try:
            response = requests.get(
                f"http://localhost:{port}/system_stats",
                timeout=timeout
            )
            return response.status_code == 200
        except:
            return False
    
    def start(self, flags: Optional[List[str]] = None, port: int = 8818) -> bool:
        """Start ComfyUI with optional flags + preset-specific flags"""
        if self.is_running():
            logger.warning("ComfyUI is already running")
            return False
        
        # Default flags
        default_flags = [
            '--listen', '0.0.0.0',
            '--port', str(port),
            '--preview-method', 'latent2rgb',
            '--front-end-version', 'Comfy-Org/ComfyUI_frontend@latest'
        ]
        
        # Get preset-specific flags from state
        preset_flags = self.state_manager.get_comfyui_flags()
        if preset_flags:
            logger.info(f"Adding preset-specific flags: {preset_flags}")
        
        # Merge: defaults + preset flags + explicit flags (last wins)
        all_flags = default_flags.copy()
        all_flags.extend(preset_flags)
        if flags:
            all_flags.extend(flags)
        
        # Deduplicate while preserving order (later values override)
        flags = list(dict.fromkeys(all_flags))
        
        logger.info(f"Starting ComfyUI on port {port} with flags: {flags}")
        
        try:
            # Build command
            cmd = [
                'comfy',
                '--workspace', str(COMFY_DIR),
                'launch',
                '--'
            ] + flags
            
            # Start process WITHOUT capturing output - logs go directly to terminal
            # This allows real-time log viewing and prevents Python buffering issues
            self.process = subprocess.Popen(
                cmd,
                cwd=str(COMFY_DIR)
                # No stdout/stderr capture - ComfyUI logs appear in real-time
            )
            
            # Update state
            self.state_manager.set_comfyui_status(
                status="starting",
                pid=self.process.pid,
                flags=flags,
                port=port
            )
            
            # Wait for startup (check health)
            logger.info("Waiting for ComfyUI to start...")
            for i in range(30):  # 30 second timeout
                time.sleep(1)
                if self.health_check(port):
                    self.state_manager.set_comfyui_status(
                        status="running",
                        pid=self.process.pid,
                        flags=flags,
                        port=port
                    )
                    logger.info(f"✓ ComfyUI started successfully on port {port}")
                    return True
            
            # Timeout
            logger.error("ComfyUI failed to start within 30 seconds")
            self.state_manager.set_comfyui_status(status="error")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start ComfyUI: {e}")
            self.state_manager.set_comfyui_status(status="error")
            return False
    
    def stop(self, timeout: int = 10) -> bool:
        """Stop ComfyUI gracefully"""
        if not self.is_running():
            logger.warning("ComfyUI is not running")
            self.state_manager.set_comfyui_status(status="stopped", pid=None)
            return True
        
        status = self.state_manager.get_comfyui_status()
        pid = status.get('pid')
        
        logger.info(f"Stopping ComfyUI (PID: {pid})...")
        
        try:
            process = psutil.Process(pid)
            
            # Try graceful shutdown first
            process.terminate()
            
            # Wait for process to exit
            try:
                process.wait(timeout=timeout)
                logger.info("✓ ComfyUI stopped gracefully")
            except psutil.TimeoutExpired:
                # Force kill if timeout
                logger.warning("Graceful shutdown timeout, forcing kill...")
                process.kill()
                process.wait(timeout=5)
                logger.info("✓ ComfyUI force killed")
            
            self.state_manager.set_comfyui_status(status="stopped", pid=None)
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop ComfyUI: {e}")
            return False
    
    def restart(self, flags: Optional[List[str]] = None, port: int = 8818) -> bool:
        """Restart ComfyUI with optional new flags"""
        logger.info("Restarting ComfyUI...")
        
        # Stop if running
        if self.is_running():
            if not self.stop():
                logger.error("Failed to stop ComfyUI for restart")
                return False
        
        # Wait a bit
        time.sleep(2)
        
        # Start with new flags
        return self.start(flags=flags, port=port)
    
    def get_logs(self, lines: int = 100) -> List[str]:
        """Get recent ComfyUI logs"""
        # TODO: Implement log reading from stdout capture
        return []


# Global instance
_process_manager = None

def get_process_manager(state_manager):
    """Get global process manager instance"""
    global _process_manager
    if _process_manager is None:
        _process_manager = ProcessManager(state_manager)
    return _process_manager
