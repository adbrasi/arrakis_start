#!/usr/bin/env python3
"""
State Manager - Persistent state tracking
Tracks installed presets, models, nodes, and ComfyUI status
"""

import json
import logging
import threading
from pathlib import Path
from typing import List, Dict, Optional, Set
from datetime import datetime
import os

logger = logging.getLogger(__name__)

# Paths
COMFY_BASE = Path(os.environ.get('COMFY_BASE', '/workspace/comfy'))
STATE_FILE = COMFY_BASE / 'arrakis_start' / 'data' / 'state.json'


class StateManager:
    """Manages persistent state for Arrakis Start"""

    def __init__(self):
        self.state_file = STATE_FILE
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load state from disk.

        A corrupt file is preserved as ``state.json.corrupt`` rather than being
        silently replaced by defaults: adopting defaults makes every installed
        preset and model disappear, and the first subsequent write makes that
        loss permanent — triggering a full re-download of every model.
        """
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError(f"state root is {type(loaded).__name__}, not object")
                # Merge with defaults so new keys are always present
                defaults = self._default_state()
                defaults.update(loaded)
                return defaults
            except Exception as e:
                backup = self.state_file.with_suffix('.json.corrupt')
                try:
                    os.replace(self.state_file, backup)
                    preserved = f"; arquivo preservado em {backup}"
                except Exception as move_err:
                    preserved = f"; não foi possível preservar o arquivo ({move_err})"
                logger.error(
                    f"state.json ilegível ({e}){preserved}. Partindo do estado "
                    "padrão: presets e modelos instalados podem precisar ser "
                    "redetectados."
                )

        return self._default_state()

    @staticmethod
    def _default_state() -> Dict:
        """Return a fresh default state dict."""
        return {
            "installed_presets": [],
            "installed_models": {},  # {filename: {dir, url, size, installed_at}}
            "installed_nodes": [],
            "comfyui_status": "stopped",
            "comfyui_pid": None,
            # Process start time of comfyui_pid. A PID alone is not an identity:
            # it survives container restarts in this file and gets recycled, so
            # killing "the tracked PID" can hit an unrelated process.
            "comfyui_pid_create_time": None,
            "comfyui_flags": [],
            # None means "never recorded", which is distinguishable from a real
            # recorded 8818 — the caller can then fall back to COMFY_PORT.
            "comfyui_port": None,
            "runtime_stack": "unknown",
            "last_install": None,
            "version": "2.0"
        }

    def _save_state(self) -> bool:
        """Save state to disk atomically (write to temp, fsync, then os.replace).

        The fsync calls are not optional. Cloud instances are terminated
        abruptly, and on ext4 ``data=ordered`` the rename metadata can commit
        while the data blocks are still unwritten — leaving a zero-length
        state.json that the next boot cannot read.

        Returns True on success. Callers that must not silently diverge from
        disk should check it.
        """
        import tempfile
        try:
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.json',
                prefix='state_',
                dir=self.state_file.parent
            )
            try:
                with os.fdopen(temp_fd, 'w') as f:
                    json.dump(self.state, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.state_file)
                # Durably record the rename itself.
                try:
                    dir_fd = os.open(str(self.state_file.parent), os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
                return True
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        except Exception as e:
            logger.error(
                f"Falha ao gravar state.json ({e}); o estado em memória divergiu "
                "do disco"
            )
            return False

    # Preset tracking
    def add_preset(self, preset_name: str):
        """Mark preset as installed"""
        with self._lock:
            if preset_name not in self.state["installed_presets"]:
                self.state["installed_presets"].append(preset_name)
                self.state["last_install"] = datetime.now().isoformat()
                self._save_state()
                logger.info(f"Marked preset as installed: {preset_name}")

    def is_preset_installed(self, preset_name: str) -> bool:
        """Check if preset is installed"""
        with self._lock:
            return preset_name in self.state["installed_presets"]

    def get_installed_presets(self) -> List[str]:
        """Get list of installed presets"""
        with self._lock:
            return list(self.state["installed_presets"])

    def remove_preset(self, preset_name: str) -> bool:
        """Remove preset from installed list. Returns True if it was present."""
        with self._lock:
            if preset_name in self.state["installed_presets"]:
                self.state["installed_presets"].remove(preset_name)
                self._save_state()
                logger.info(f"Removed preset from state: {preset_name}")
                return True
            return False

    # Model tracking
    def add_model(self, filename: str, model_dir: str, url: str, size: int = 0):
        """Track installed model"""
        with self._lock:
            self.state["installed_models"][filename] = {
                "dir": model_dir,
                "url": url,
                "size": size,
                "installed_at": datetime.now().isoformat()
            }
            self._save_state()

    def add_models(self, models: List[Dict]) -> None:
        """Track many installed models with a single disk write.

        Recording them one at a time rewrites the whole state file per model —
        32 full rewrites when re-installing a 32-model preset.

        Each item: ``{'filename': str, 'dir': str, 'url': str, 'size': int}``.
        """
        if not models:
            return
        with self._lock:
            now = datetime.now().isoformat()
            for model in models:
                filename = model.get('filename')
                if not filename:
                    continue
                self.state["installed_models"][filename] = {
                    "dir": model.get('dir', ''),
                    "url": model.get('url', ''),
                    "size": int(model.get('size') or 0),
                    "installed_at": now,
                }
            self._save_state()

    def is_model_installed(self, filename: str) -> bool:
        """Check if model is installed"""
        with self._lock:
            return filename in self.state["installed_models"]

    def get_installed_models(self) -> Dict:
        """Get all installed models"""
        with self._lock:
            return dict(self.state["installed_models"])

    def remove_model(self, filename: str) -> bool:
        """Remove model entry from installed_models. Returns True if it was present."""
        with self._lock:
            if filename in self.state["installed_models"]:
                del self.state["installed_models"][filename]
                self._save_state()
                return True
            return False

    # Node tracking
    def add_node(self, node_url: str):
        """Mark custom node as installed"""
        with self._lock:
            if node_url not in self.state["installed_nodes"]:
                self.state["installed_nodes"].append(node_url)
                self._save_state()

    def is_node_installed(self, node_url: str) -> bool:
        """Check if node is installed"""
        with self._lock:
            return node_url in self.state["installed_nodes"]

    def get_installed_nodes(self) -> List[str]:
        """Get list of installed nodes"""
        with self._lock:
            return list(self.state["installed_nodes"])

    # ComfyUI flags (preset-specific)
    def set_comfyui_flags(self, flags: List[str]):
        """Set ComfyUI flags from installed presets"""
        with self._lock:
            self.state["comfyui_flags"] = flags
            self._save_state()

    def get_comfyui_flags(self) -> List[str]:
        """Get preset-specific ComfyUI flags"""
        with self._lock:
            return list(self.state.get("comfyui_flags", []))

    # Runtime stack tracking
    def set_runtime_stack(self, stack: str):
        """Persist current runtime stack marker."""
        with self._lock:
            self.state["runtime_stack"] = stack
            self._save_state()

    def get_runtime_stack(self) -> str:
        """Get current runtime stack marker."""
        with self._lock:
            return self.state.get("runtime_stack", "unknown")

    # ComfyUI status
    def set_comfyui_status(self, status: str, pid: Optional[int] = None,
                          flags: Optional[List[str]] = None,
                          port: Optional[int] = None,
                          clear_pid: bool = False,
                          pid_create_time: Optional[float] = None):
        """Update ComfyUI status.

        ``pid_create_time`` is stored alongside the PID so a later stop can
        prove the PID still refers to the same process instead of signalling
        whatever recycled it. It is cleared together with the PID.

        ``port`` is only overwritten when given, so a status update that does
        not know the port cannot clobber a recorded one.
        """
        with self._lock:
            self.state["comfyui_status"] = status
            if clear_pid:
                self.state["comfyui_pid"] = None
                self.state["comfyui_pid_create_time"] = None
            elif pid is not None:
                self.state["comfyui_pid"] = pid
                self.state["comfyui_pid_create_time"] = pid_create_time
            elif pid_create_time is not None:
                self.state["comfyui_pid_create_time"] = pid_create_time
            if flags is not None:
                self.state["comfyui_flags"] = flags
            if port is not None:
                self.state["comfyui_port"] = port
            self._save_state()

    def get_comfyui_status(self) -> Dict:
        """Get ComfyUI status"""
        with self._lock:
            return {
                "status": self.state["comfyui_status"],
                "pid": self.state["comfyui_pid"],
                "pid_create_time": self.state.get("comfyui_pid_create_time"),
                "flags": list(self.state["comfyui_flags"]),
                "port": self.state["comfyui_port"]
            }

    # Full state
    def get_full_state(self) -> Dict:
        """Get complete state"""
        with self._lock:
            import copy
            return copy.deepcopy(self.state)

    def reset_state(self):
        """Reset state to defaults"""
        with self._lock:
            self.state = self._default_state()
            self._save_state()
            logger.info("State reset to defaults")


# Global instance
_state_manager = None

def get_state_manager() -> StateManager:
    """Get global state manager instance"""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager
