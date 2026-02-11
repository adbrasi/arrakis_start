#!/usr/bin/env python3
"""
State Manager - Persistent state tracking
Tracks installed presets, models, nodes, and ComfyUI status
"""

import json
import logging
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
        self.state = self._load_state()
    
    def _load_state(self) -> Dict:
        """Load state from disk"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state: {e}")
        
        # Default state
        return {
            "installed_presets": [],
            "installed_models": {},  # {filename: {dir, url, size, installed_at}}
            "installed_nodes": [],
            "comfyui_status": "stopped",
            "comfyui_pid": None,
            "comfyui_flags": [],
            "comfyui_port": 8818,
            "last_install": None,
            "version": "2.0"
        }
    
    def _save_state(self):
        """Save state to disk atomically (write to temp, then rename)"""
        import tempfile
        try:
            # Write to temporary file first
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.json',
                prefix='state_',
                dir=self.state_file.parent
            )
            try:
                with os.fdopen(temp_fd, 'w') as f:
                    json.dump(self.state, f, indent=2)
                # Atomic rename (works on same filesystem)
                import shutil
                shutil.move(temp_path, self.state_file)
            except Exception:
                # Clean up temp file on error
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    # Preset tracking
    def add_preset(self, preset_name: str):
        """Mark preset as installed"""
        if preset_name not in self.state["installed_presets"]:
            self.state["installed_presets"].append(preset_name)
            self.state["last_install"] = datetime.now().isoformat()
            self._save_state()
            logger.info(f"Marked preset as installed: {preset_name}")
    
    def is_preset_installed(self, preset_name: str) -> bool:
        """Check if preset is installed"""
        return preset_name in self.state["installed_presets"]
    
    def get_installed_presets(self) -> List[str]:
        """Get list of installed presets"""
        return self.state["installed_presets"]
    
    # Model tracking
    def add_model(self, filename: str, model_dir: str, url: str, size: int = 0):
        """Track installed model"""
        self.state["installed_models"][filename] = {
            "dir": model_dir,
            "url": url,
            "size": size,
            "installed_at": datetime.now().isoformat()
        }
        self._save_state()
    
    def is_model_installed(self, filename: str) -> bool:
        """Check if model is installed"""
        return filename in self.state["installed_models"]
    
    def get_installed_models(self) -> Dict:
        """Get all installed models"""
        return self.state["installed_models"]
    
    # Node tracking
    def add_node(self, node_url: str):
        """Mark custom node as installed"""
        if node_url not in self.state["installed_nodes"]:
            self.state["installed_nodes"].append(node_url)
            self._save_state()
    
    def is_node_installed(self, node_url: str) -> bool:
        """Check if node is installed"""
        return node_url in self.state["installed_nodes"]
    
    def get_installed_nodes(self) -> List[str]:
        """Get list of installed nodes"""
        return self.state["installed_nodes"]
    
    # ComfyUI flags (preset-specific)
    def set_comfyui_flags(self, flags: List[str]):
        """Set ComfyUI flags from installed presets"""
        self.state["comfyui_flags"] = flags
        self._save_state()
        
    def get_comfyui_flags(self) -> List[str]:
        """Get preset-specific ComfyUI flags"""
        return self.state.get("comfyui_flags", [])
    
    # ComfyUI status
    def set_comfyui_status(self, status: str, pid: Optional[int] = None,
                          flags: Optional[List[str]] = None, port: int = 8818,
                          clear_pid: bool = False):
        """Update ComfyUI status"""
        self.state["comfyui_status"] = status
        if clear_pid:
            self.state["comfyui_pid"] = None
        elif pid is not None:
            self.state["comfyui_pid"] = pid
        if flags is not None:
            self.state["comfyui_flags"] = flags
        self.state["comfyui_port"] = port
        self._save_state()
    
    def get_comfyui_status(self) -> Dict:
        """Get ComfyUI status"""
        return {
            "status": self.state["comfyui_status"],
            "pid": self.state["comfyui_pid"],
            "flags": self.state["comfyui_flags"],
            "port": self.state["comfyui_port"]
        }
    
    # Full state
    def get_full_state(self) -> Dict:
        """Get complete state"""
        return self.state.copy()
    
    def reset_state(self):
        """Reset state to defaults"""
        self.state = self._load_state()
        self.state = {
            "installed_presets": [],
            "installed_models": {},
            "installed_nodes": [],
            "comfyui_status": "stopped",
            "comfyui_pid": None,
            "comfyui_flags": [],
            "comfyui_port": 8818,
            "last_install": None,
            "version": "2.0"
        }
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
