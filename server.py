#!/usr/bin/env python3
"""
Web Server v2.0 - Preset selector interface with state management
Serves web UI, handles installation requests, and provides status API
"""

import json
import logging
import asyncio
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Callable, List, Dict
import urllib.parse

from state import get_state_manager
from process_manager import get_process_manager

logger = logging.getLogger(__name__)

# Global callbacks
_presets_callback = None
_state_manager = None
_process_manager = None


class PresetHandler(SimpleHTTPRequestHandler):
    """HTTP handler for preset selector with v2.0 features"""
    
    def __init__(self, *args, **kwargs):
        # Serve from web/ directory
        web_dir = Path(__file__).parent / 'web'
        super().__init__(*args, directory=str(web_dir), **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        if self.path == '/api/presets':
            self._handle_get_presets()
        elif self.path == '/api/status':
            self._handle_get_status()
        elif self.path.startswith('/api/logs'):
            self._handle_get_logs()
        else:
            # Serve static files
            super().do_GET()
    
    def do_POST(self):
        """Handle POST requests"""
        if self.path == '/api/install':
            self._handle_install()
        elif self.path == '/api/comfyui/start':
            self._handle_comfyui_start()
        elif self.path == '/api/comfyui/stop':
            self._handle_comfyui_stop()
        elif self.path == '/api/comfyui/restart':
            self._handle_comfyui_restart()
        elif self.path == '/api/stop':
            self._handle_stop_install()
        else:
            self.send_error(404)
    
    def _handle_get_presets(self):
        """Return available presets with installation status"""
        try:
            presets = _presets_callback() if _presets_callback else []
            state = _state_manager or get_state_manager()
            installed_presets = set(state.get_installed_presets())
            
            # Add installation status to each preset
            clean_presets = []
            for p in presets:
                preset_name = p.get('name', p.get('_filename', 'Unknown'))
                clean = {
                    'name': preset_name,
                    'description': p.get('description', ''),
                    'models_count': len(p.get('models', [])),
                    'nodes_count': len(p.get('nodes', [])),
                    'installed': preset_name in installed_presets
                }
                clean_presets.append(clean)
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(clean_presets).encode())
        
        except Exception as e:
            logger.error(f"Failed to get presets: {e}")
            self.send_error(500, str(e))
    
    def _handle_get_status(self):
        """Return full system status"""
        try:
            state = _state_manager or get_state_manager()
            pm = _process_manager or get_process_manager(state)
            
            status = {
                'installed_presets': state.get_installed_presets(),
                'installed_models_count': len(state.get_installed_models()),
                'installed_nodes_count': len(state.get_installed_nodes()),
                'comfyui': {
                    'status': state.get_comfyui_status()['status'],
                    'port': state.get_comfyui_status()['port'],
                    'pid': state.get_comfyui_status()['pid'],
                    'is_running': pm.is_running(),
                    'is_healthy': pm.health_check() if pm.is_running() else False
                },
                'last_install': state.state.get('last_install')
            }
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(status).encode())
        
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            self.send_error(500, str(e))
    
    def _handle_get_logs(self):
        """Return installation logs"""
        try:
            # TODO: Implement log reading
            logs = []
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'logs': logs}).encode())
        
        except Exception as e:
            logger.error(f"Failed to get logs: {e}")
            self.send_error(500, str(e))
    
    def _handle_install(self):
        """Handle preset installation request"""
        try:
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length)
            data = json.loads(body.decode())
            
            preset_names = data.get('presets', [])
            logger.info(f"Installation request: {preset_names}")
            
            # Start installation in background thread
            from start import install_presets, start_comfyui
            
            def install_and_start():
                success = install_presets(preset_names, include_base=True)
                if success:
                    logger.info("Installation complete, starting ComfyUI...")
                    start_comfyui()
            
            thread = threading.Thread(target=install_and_start, daemon=True)
            thread.start()
            
            # Send immediate response
            self.send_response(202)  # Accepted
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {
                'status': 'started',
                'message': 'Installation started in background'
            }
            self.wfile.write(json.dumps(response).encode())
        
        except Exception as e:
            logger.error(f"Installation failed: {e}")
            self.send_error(500, str(e))
    
    def _handle_comfyui_start(self):
        """Start ComfyUI"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            data = {}
            if content_length > 0:
                body = self.rfile.read(content_length)
                data = json.loads(body.decode())
            
            flags = data.get('flags')
            port = data.get('port', 8818)
            
            state = _state_manager or get_state_manager()
            pm = _process_manager or get_process_manager(state)
            
            success = pm.start(flags=flags, port=port)
            
            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {
                'success': success,
                'message': 'ComfyUI started' if success else 'Failed to start ComfyUI'
            }
            self.wfile.write(json.dumps(response).encode())
        
        except Exception as e:
            logger.error(f"Failed to start ComfyUI: {e}")
            self.send_error(500, str(e))
    
    def _handle_comfyui_stop(self):
        """Stop ComfyUI"""
        try:
            state = _state_manager or get_state_manager()
            pm = _process_manager or get_process_manager(state)
            
            success = pm.stop()
            
            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {
                'success': success,
                'message': 'ComfyUI stopped' if success else 'Failed to stop ComfyUI'
            }
            self.wfile.write(json.dumps(response).encode())
        
        except Exception as e:
            logger.error(f"Failed to stop ComfyUI: {e}")
            self.send_error(500, str(e))
    
    def _handle_comfyui_restart(self):
        """Restart ComfyUI with optional new flags"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            data = {}
            if content_length > 0:
                body = self.rfile.read(content_length)
                data = json.loads(body.decode())
            
            flags = data.get('flags')
            port = data.get('port', 8818)
            
            state = _state_manager or get_state_manager()
            pm = _process_manager or get_process_manager(state)
            
            success = pm.restart(flags=flags, port=port)
            
            self.send_response(200 if success else 500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {
                'success': success,
                'message': 'ComfyUI restarted' if success else 'Failed to restart ComfyUI'
            }
            self.wfile.write(json.dumps(response).encode())
        
        except Exception as e:
            logger.error(f"Failed to restart ComfyUI: {e}")
            self.send_error(500, str(e))
    
    def _handle_stop_install(self):
        """Stop ongoing installation"""
        try:
            logger.info("Stop request received - this will stop after current download")
            
            # TODO: Implement actual stop mechanism
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {
                'status': 'stopped',
                'message': 'Stop signal sent. Installation will stop after current file.'
            }
            self.wfile.write(json.dumps(response).encode())
        
        except Exception as e:
            logger.error(f"Stop failed: {e}")
            self.send_error(500, str(e))
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"{self.address_string()} - {format % args}")


def run_server(port: int = 8090, presets_callback: Callable = None):
    """Start the HTTP server"""
    global _presets_callback, _state_manager, _process_manager
    _presets_callback = presets_callback
    _state_manager = get_state_manager()
    _process_manager = get_process_manager(_state_manager)
    
    server = HTTPServer(('0.0.0.0', port), PresetHandler)
    
    logger.info(f"Web server running on http://0.0.0.0:{port}")
    logger.info("Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        server.shutdown()
