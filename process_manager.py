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
COMFY_STARTUP_TIMEOUT = int(os.environ.get('COMFY_STARTUP_TIMEOUT', '120'))


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
            
            # Wait for startup (check health and early process crash)
            logger.info(f"Waiting for ComfyUI to start (timeout: {COMFY_STARTUP_TIMEOUT}s)...")
            for i in range(COMFY_STARTUP_TIMEOUT):
                time.sleep(1)
                exit_code = self.process.poll()
                if exit_code is not None:
                    logger.error(f"ComfyUI process exited before startup (exit code: {exit_code})")
                    self.state_manager.set_comfyui_status(status="error")
                    return False
                if self.health_check(port):
                    self.state_manager.set_comfyui_status(
                        status="running",
                        pid=self.process.pid,
                        flags=flags,
                        port=port
                    )
                    # Colorful success banner
                    print("\n" + "="*60)
                    print("\033[1;32m🚀 COMFYUI LIGADO! PRONTO PARA USO! 🚀\033[0m")
                    print("\033[1;36m   Entre no portal do VastAI e selecione ComfyUI!\033[0m")
                    print("="*60 + "\n")
                    logger.info(f"✓ ComfyUI started successfully on port {port}")
                    return True
            
            # Timeout
            logger.error(f"ComfyUI failed to start within {COMFY_STARTUP_TIMEOUT} seconds")
            self.state_manager.set_comfyui_status(status="error")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start ComfyUI: {e}")
            self.state_manager.set_comfyui_status(status="error")
            return False
    
    def stop(self, timeout: int = 10) -> bool:
        """Stop ComfyUI gracefully and wait for port release"""
        if not self.is_running():
            logger.warning("ComfyUI is not running")
            self.state_manager.set_comfyui_status(status="stopped", pid=None)
            return True
        
        status = self.state_manager.get_comfyui_status()
        pid = status.get('pid')
        port = status.get('port', 8818)
        
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
            
            # CRITICAL: Wait for port to be released
            logger.info(f"Waiting for port {port} to be released...")
            for i in range(10):  # Wait up to 10 seconds
                time.sleep(1)
                if not self._is_port_in_use(port):
                    logger.info(f"✓ Port {port} released")
                    break
            else:
                logger.warning(f"Port {port} may still be in use, but continuing...")
            
            # Colorful stop banner
            print("\n" + "="*60)
            print("\033[1;31m⏹ COMFYUI DESLIGADO! ⏹\033[0m")
            print("="*60 + "\n")
            
            self.state_manager.set_comfyui_status(status="stopped", pid=None)
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop ComfyUI: {e}")
            return False
    
    def _is_port_in_use(self, port: int) -> bool:
        """Check if a port is in use"""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return False  # Port is free
            except OSError:
                return True  # Port is in use
    
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
