#!/usr/bin/env python3
"""
Web Server - Minimal preset selector interface
Serves web UI and handles installation requests
"""

import json
import logging
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Callable, List

from state import get_state_manager

logger = logging.getLogger(__name__)

# Module-level state
_presets_callback = None
_state_manager = None


class PresetHandler(SimpleHTTPRequestHandler):
    """HTTP handler for preset selector"""
    
    def __init__(self, *args, **kwargs):
        # Serve from web/ directory
        web_dir = Path(__file__).parent / 'web'
        super().__init__(*args, directory=str(web_dir), **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        if self.path == '/api/presets':
            self._handle_get_presets()
        else:
            # Serve static files
            super().do_GET()
    
    def do_POST(self):
        """Handle POST requests"""
        if self.path == '/api/install':
            self._handle_install()
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
            self.wfile.write(json.dumps({'presets': clean_presets}).encode())
        
        except Exception as e:
            logger.error(f"Failed to get presets: {e}")
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
            from process_manager import ProcessManager
            
            def install_and_restart():
                state = _state_manager or get_state_manager()
                pm = ProcessManager(state)
                
                # STEP 1: Stop ComfyUI if running (to avoid port conflict)
                if pm.is_running():
                    logger.info("Stopping ComfyUI before installation...")
                    pm.stop()
                    import time
                    time.sleep(2)  # Wait for port to be released
                
                # STEP 2: Install presets (this also saves preset flags to state)
                logger.info(f"Installing presets: {preset_names}")
                success = install_presets(preset_names, include_base=True)
                
                # STEP 3: Restart ComfyUI with new preset flags
                if success:
                    logger.info("Installation complete, starting ComfyUI with preset flags...")
                    pm.start()  # start() will automatically merge preset flags from state
                    logger.info("✓ ComfyUI started successfully")
                else:
                    logger.error("Installation failed")
            
            thread = threading.Thread(target=install_and_restart, daemon=True)
            thread.start()
            
            # Send immediate response
            self.send_response(202)  # Accepted
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'message': 'Installation started'
            }).encode())
        
        except Exception as e:
            logger.error(f"Installation error: {e}")
            self.send_error(500, str(e))


def run_server(port: int = 8090, presets_callback: Callable = None):
    """Run the HTTP server"""
    global _presets_callback, _state_manager
    _presets_callback = presets_callback
    _state_manager = get_state_manager()
    
    server = HTTPServer(('0.0.0.0', port), PresetHandler)
    logger.info(f"Web server running on http://0.0.0.0:{port}")
    logger.info("Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("\nShutting down server...")
        server.shutdown()
