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

# Module-level state
_presets_callback = None
_state_manager = None

# Install progress tracking (for HTTP polling fallback when WebSocket fails)
_install_progress = {
    'status': 'idle',  # idle, installing, complete, error
    'message': '',
    'percent': 0,
    'filename': '',
    'speed': '',
    'eta': ''
}

def update_install_progress(status='installing', message='', percent=0, filename='', speed='', eta=''):
    """Update install progress for HTTP polling"""
    global _install_progress
    _install_progress = {
        'status': status,
        'message': message,
        'percent': percent,
        'filename': filename,
        'speed': speed,
        'eta': eta
    }
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
        elif self.path == '/api/progress':
            self._handle_get_progress()
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
    
    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
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
    
    def _handle_get_progress(self):
        """Return current installation progress (HTTP polling fallback for WebSocket)"""
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(_install_progress).encode())
        
        except Exception as e:
            logger.error(f"Failed to get progress: {e}")
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
            from start import install_presets
            from process_manager import get_process_manager
            
            def install_and_restart():
                pm = get_process_manager()
                
                # STEP 1: Stop ComfyUI if running (to avoid port conflict)
                update_install_progress(status='installing', message='Stopping ComfyUI...', percent=5)
                if pm.is_running():
                    logger.info("Stopping ComfyUI before installation...")
                    pm.stop()
                    import time
                    time.sleep(2)  # Wait for port to be released
                
                # STEP 2: Install presets (this also saves preset flags to state)
                update_install_progress(status='installing', message='Installing presets...', percent=10)
                success = install_presets(preset_names, include_base=True)
                
                # Send WebSocket notification (may fail through Cloudflare)
                try:
                    from websocket_server import send_install_complete
                    send_install_complete(success, preset_names)
                except Exception as e:
                    logger.warning(f"Failed to send install_complete: {e}")
                
                # STEP 3: Restart ComfyUI with new preset flags
                if success:
                    update_install_progress(status='installing', message='Starting ComfyUI with preset flags...', percent=95)
                    logger.info("Installation complete, starting ComfyUI with preset flags...")
                    pm.start()  # start() will automatically merge preset flags from state
                    update_install_progress(status='complete', message='Installation complete!', percent=100)
                else:
                    update_install_progress(status='error', message='Installation failed', percent=0)
            
            thread = threading.Thread(target=install_and_restart, daemon=True)
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
            logger.info("Stop request received, cancelling active installation...")
            
            from start import cancel_active_install
            success = cancel_active_install()
            
            message = 'Installation stopped immediately.' if success else 'No active installation found to stop.'
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            response = {
                'status': 'stopped',
                'message': message
            }
            self.wfile.write(json.dumps(response).encode())
        
        except Exception as e:
            logger.error(f"Stop failed: {e}")
            self.send_error(500, str(e))
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"{self.address_string()} - {format % args}")


def run_server(port: int = 8090, presets_callback: Callable = None):
    """Start the HTTP server and WebSocket server"""
    global _presets_callback, _state_manager, _process_manager
    _presets_callback = presets_callback
    _state_manager = get_state_manager()
    _process_manager = get_process_manager(_state_manager)
    
    # Start WebSocket server in background thread
    from websocket_server import start_websocket_server
    
    def run_ws():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start_websocket_server(port=port+1))
    
    ws_thread = threading.Thread(target=run_ws, daemon=True)
    ws_thread.start()
    
    server = HTTPServer(('0.0.0.0', port), PresetHandler)
    
    logger.info(f"Web server running on http://0.0.0.0:{port}")
    logger.info(f"WebSocket server running on ws://0.0.0.0:{port+1}")
    logger.info("Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        server.shutdown()
