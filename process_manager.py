#!/usr/bin/env python3
"""
Process Manager - ComfyUI lifecycle management
Start, stop, restart ComfyUI with configurable flags
"""

import os
import sys
import subprocess
import logging
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

    def _pid_is_alive(self, pid: Optional[int]) -> bool:
        """Check if PID exists and is not a zombie."""
        if not pid:
            return False
        try:
            process = psutil.Process(pid)
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _find_port_owner_pid(self, port: int) -> Optional[int]:
        """Return PID that owns a listening socket on the target port."""
        try:
            for conn in psutil.net_connections(kind='inet'):
                if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                    return conn.pid
        except Exception as e:
            logger.warning(f"Could not inspect port owner for {port}: {e}")
        return None

    def _is_comfy_process(self, pid: Optional[int]) -> bool:
        """Best-effort check that a PID belongs to ComfyUI stack."""
        if not pid:
            return False
        try:
            process = psutil.Process(pid)
            cmdline = " ".join(process.cmdline()).lower()
            return (
                'comfyui' in cmdline or
                'comfy launch' in cmdline or
                str(COMFY_DIR).lower() in cmdline
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _terminate_pid(self, pid: int, timeout: int = 10) -> bool:
        """Terminate PID gracefully, fallback to kill."""
        try:
            process = psutil.Process(pid)
            logger.info(f"Stopping process PID {pid}: {' '.join(process.cmdline())}")
            process.terminate()
            try:
                process.wait(timeout=timeout)
                logger.info(f"✓ PID {pid} stopped gracefully")
                return True
            except psutil.TimeoutExpired:
                logger.warning(f"PID {pid} did not stop in {timeout}s, forcing kill...")
                process.kill()
                process.wait(timeout=5)
                logger.info(f"✓ PID {pid} force killed")
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            logger.info(f"PID {pid} is no longer running")
            return True
        except Exception as e:
            logger.error(f"Failed to terminate PID {pid}: {e}")
            return False
    
    def is_running(self) -> bool:
        """Check if ComfyUI is running"""
        status = self.state_manager.get_comfyui_status()
        pid = status.get('pid')
        port = status.get('port', 8818)
        
        if self._pid_is_alive(pid):
            return True

        # Fallback for stale/missing PID: if health endpoint responds, ComfyUI is alive.
        if self.health_check(port=port, timeout=2):
            owner_pid = self._find_port_owner_pid(port)
            logger.warning(
                f"ComfyUI responds on port {port} but tracked PID is stale ({pid}); "
                f"updating state to PID {owner_pid}"
            )
            self.state_manager.set_comfyui_status(
                status="running",
                pid=owner_pid,
                port=port
            )
            return True

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

        if self._is_port_in_use(port):
            owner_pid = self._find_port_owner_pid(port)
            logger.error(
                f"Cannot start ComfyUI: port {port} is already in use "
                f"(owner PID: {owner_pid})"
            )
            self.state_manager.set_comfyui_status(status="error", port=port)
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

            # Ensure runtime env for Blackwell + SageAttention stability.
            env = os.environ.copy()
            env.setdefault('NVCC_APPEND_FLAGS', '--threads 8')
            env.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')
            env.setdefault('MAX_JOBS', '32')
            logger.info(
                "ComfyUI env: "
                f"NVCC_APPEND_FLAGS={env.get('NVCC_APPEND_FLAGS')} "
                f"PYTORCH_CUDA_ALLOC_CONF={env.get('PYTORCH_CUDA_ALLOC_CONF')} "
                f"MAX_JOBS={env.get('MAX_JOBS')}"
            )
            
            # Start process WITHOUT capturing output - logs go directly to terminal
            # This allows real-time log viewing and prevents Python buffering issues
            self.process = subprocess.Popen(
                cmd,
                cwd=str(COMFY_DIR),
                env=env
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
    
    def ensure_stopped(self, port: int = 8818, timeout: int = 10) -> bool:
        """
        Ensure ComfyUI is stopped by tracked PID and by port ownership.
        This handles stale state where PID no longer matches the process on port.
        """
        status = self.state_manager.get_comfyui_status()
        tracked_pid = status.get('pid')
        stopped_any = False
        ok = True

        if self._pid_is_alive(tracked_pid):
            logger.info(f"Stopping tracked ComfyUI PID: {tracked_pid}")
            ok = self._terminate_pid(tracked_pid, timeout=timeout) and ok
            stopped_any = True

        owner_pid = self._find_port_owner_pid(port)
        if owner_pid and owner_pid != tracked_pid:
            if self._is_comfy_process(owner_pid):
                logger.warning(
                    f"Found ComfyUI-like process on port {port} with PID {owner_pid} "
                    "not tracked in state; stopping it."
                )
                ok = self._terminate_pid(owner_pid, timeout=timeout) and ok
                stopped_any = True
            else:
                logger.error(
                    f"Port {port} is owned by non-Comfy process PID {owner_pid}; "
                    "refusing to kill automatically."
                )
                ok = False

        logger.info(f"Waiting for port {port} to be released...")
        for _ in range(timeout):
            if not self._is_port_in_use(port):
                logger.info(f"✓ Port {port} released")
                break
            time.sleep(1)
        else:
            logger.error(f"Port {port} is still in use after stop attempts")
            ok = False

        if stopped_any:
            print("\n" + "="*60)
            print("\033[1;31m⏹ COMFYUI DESLIGADO! ⏹\033[0m")
            print("="*60 + "\n")
        else:
            logger.info("No running ComfyUI process detected during stop check")

        self.state_manager.set_comfyui_status(
            status="stopped",
            pid=None,
            port=port,
            clear_pid=True
        )
        return ok

    def stop(self, timeout: int = 10) -> bool:
        """Stop ComfyUI and ensure port release."""
        status = self.state_manager.get_comfyui_status()
        port = status.get('port', 8818)
        return self.ensure_stopped(port=port, timeout=timeout)
    
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
