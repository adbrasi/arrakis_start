#!/usr/bin/env python3
"""
Web Server - Preset selector interface
Serves web UI and handles installation requests
"""

import json
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Callable, List, Dict
import urllib.parse
import threading

logger = logging.getLogger(__name__)

# Global callback for getting presets
_presets_callback = None


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
    
    def _handle_get_presets(self):
        """Return available presets as JSON"""
        try:
            presets = _presets_callback() if _presets_callback else []
            
            # Clean up presets for frontend (remove internal fields)
            clean_presets = []
            for p in presets:
                clean = {
                    'name': p.get('name', p.get('_filename', 'Unknown')),
                    'description': p.get('description', ''),
                    'models_count': len(p.get('models', [])),
                    'nodes_count': len(p.get('nodes', []))
                }
                clean_presets.append(clean)
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(clean_presets).encode())
        
        except Exception as e:
            logger.error(f"Failed to get presets: {e}")
            self.send_error(500, str(e))
    
    def _handle_install(self):
        """Handle preset installation request"""
        try:
            # Read request body
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length)
            data = json.loads(body)
            
            preset_names = data.get('presets', [])
            
            if not preset_names:
                self.send_error(400, "No presets specified")
                return
            
            logger.info(f"Installation request: {preset_names}")
            
            # Start installation in background thread
            from start import install_presets, start_comfyui, start_cloudflared
            
            def install_and_start():
                success = install_presets(preset_names)
                if success:
                    logger.info("Starting ComfyUI and Cloudflared...")
                    start_comfyui()
                    start_cloudflared()
            
            thread = threading.Thread(target=install_and_start, daemon=True)
            thread.start()
            
            # Send immediate response
            self.send_response(202)  # Accepted
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = {
                'status': 'started',
                'message': 'Installation started in background'
            }
            self.wfile.write(json.dumps(response).encode())
        
        except Exception as e:
            logger.error(f"Installation failed: {e}")
            self.send_error(500, str(e))
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"{self.address_string()} - {format % args}")


def run_server(port: int = 8090, presets_callback: Callable = None):
    """Start the web server"""
    global _presets_callback
    _presets_callback = presets_callback
    
    server = HTTPServer(('0.0.0.0', port), PresetHandler)
    
    logger.info(f"Web server running on http://0.0.0.0:{port}")
    logger.info("Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        server.shutdown()
