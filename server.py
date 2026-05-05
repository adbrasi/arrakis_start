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
    
    def log_message(self, format, *args):
        """Silence ALL HTTP request logs - user doesn't want to see them"""
        pass  # Don't log any HTTP requests
    
    
    def do_GET(self):
        """Handle GET requests"""
        if self.path == '/api/presets':
            self._handle_get_presets()
        elif self.path == '/api/status':
            self._handle_get_status()
        elif self.path.startswith('/api/workflows/'):
            filename = self.path[len('/api/workflows/'):]
            self._handle_get_workflow(filename)
        else:
            # Serve static files
            super().do_GET()
    
    def do_POST(self):
        """Handle POST requests"""
        if self.path == '/api/install':
            self._handle_install()
        elif self.path == '/api/uninstall':
            self._handle_uninstall()
        elif self.path == '/api/restart':
            self._handle_restart()
        elif self.path == '/api/shutdown':
            self._handle_shutdown()
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
            
            # Add installation status to each preset (skip "Base" - it's auto-installed)
            clean_presets = []
            for p in presets:
                preset_name = p.get('name', p.get('_filename', 'Unknown'))
                
                # Skip Base preset - it's automatically included
                if preset_name.lower() == 'base':
                    continue
                
                # Resolve workflow: local file takes priority over external URL
                workflow_file = p.get('workflow', '')
                workflow_url = p.get('workflow_url', '')
                workflow_local = False
                if workflow_file:
                    workflow_url = f'/api/workflows/{workflow_file}'
                    workflow_local = True

                clean = {
                    'name': preset_name,
                    'description': p.get('description', ''),
                    'models_count': len(p.get('models', [])),
                    'nodes_count': len(p.get('nodes', [])),
                    'installed': preset_name in installed_presets,
                    'workflow_url': workflow_url,
                    'workflow_local': workflow_local,
                    'workflow_file': workflow_file,
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
    
    def _handle_get_workflow(self, filename):
        """Serve a workflow file from the workflows/ directory"""
        try:
            # Prevent path traversal
            if '/' in filename or '..' in filename or not filename.endswith('.json'):
                self.send_error(400, 'Invalid filename')
                return

            workflows_dir = Path(__file__).parent / 'workflows'
            workflow_path = workflows_dir / filename

            if not workflow_path.exists():
                self.send_error(404, 'Workflow not found')
                return

            content = workflow_path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(content)

        except Exception as e:
            logger.error(f"Failed to serve workflow {filename}: {e}")
            self.send_error(500, str(e))

    def _handle_get_status(self):
        """Return ComfyUI status"""
        try:
            state = _state_manager or get_state_manager()
            from process_manager import ProcessManager
            pm = ProcessManager(state)
            is_running = pm.is_running()
            status_data = state.get_comfyui_status()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'running': is_running,
                'status': status_data.get('status', 'unknown'),
                'port': status_data.get('port', 8818),
                'installed_presets': state.get_installed_presets()
            }).encode())
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            self.send_error(500, str(e))

    def _handle_restart(self):
        """Handle ComfyUI restart request (kill + start with last preset flags)"""
        try:
            from process_manager import ProcessManager
            state = _state_manager or get_state_manager()

            def do_restart():
                try:
                    pm = ProcessManager(state)
                    logger.info("Restart requested via web UI")

                    # Stop ComfyUI
                    if not pm.ensure_stopped(timeout=20):
                        logger.error("Failed to stop ComfyUI for restart")
                        return

                    import time
                    time.sleep(2)

                    # Start with existing preset flags from state
                    started = pm.start()
                    if started:
                        logger.info("ComfyUI restarted successfully via web UI")
                    else:
                        logger.error("ComfyUI failed to start after restart")
                except Exception as e:
                    logger.error(f"Restart thread error: {e}")

            thread = threading.Thread(target=do_restart, daemon=True)
            thread.start()

            self.send_response(202)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'message': 'Restart initiated'
            }).encode())
        except Exception as e:
            logger.error(f"Restart error: {e}")
            self.send_error(500, str(e))

    def _handle_install(self):
        """Handle preset installation request"""
        try:
            cl = self.headers.get('Content-Length')
            if not cl:
                self.send_error(411, 'Content-Length required')
                return
            content_length = int(cl)
            max_body_size = 1 * 1024 * 1024  # 1 MB
            if content_length > max_body_size:
                self.send_error(413, 'Request body too large')
                return
            body = self.rfile.read(content_length)
            data = json.loads(body.decode())
            
            preset_names = data.get('presets', [])
            extra_flags = data.get('extra_flags', [])
            logger.info(f"Installation request: {preset_names} (extra flags: {extra_flags})")

            # Start installation in background thread
            from start import install_presets
            from process_manager import ProcessManager

            def install_and_restart():
                try:
                    state = _state_manager or get_state_manager()
                    pm = ProcessManager(state)

                    # STEP 1: Always ensure ComfyUI is stopped (including stale PID state)
                    logger.info("Ensuring ComfyUI is stopped before installation...")
                    if not pm.ensure_stopped(timeout=20):
                        print("\n" + "="*60)
                        print("\033[1;31m❌ ERRO AO PARAR COMFYUI ❌\033[0m")
                        print("="*60 + "\n")
                        logger.error("Failed to stop existing ComfyUI process/port before installation")
                        return

                    # STEP 2: Install presets (this also saves preset flags to state)
                    logger.info(f"Installing presets: {preset_names}")
                    success = install_presets(preset_names, include_base=True)

                    # STEP 3: Restart ComfyUI with new preset flags (+ optional extra flags from UI).
                    # Use restart() instead of start() so that any stray ComfyUI instance
                    # (e.g. one that came back up during the long install window) is
                    # replaced with a fresh launch carrying the freshly-saved preset flags.
                    if success:
                        print("\n" + "="*60)
                        print("\033[1;33m📦 INSTALAÇÃO COMPLETA! 📦\033[0m")
                        print("\033[1;37m   Iniciando ComfyUI com novos presets...\033[0m")
                        print("="*60 + "\n")
                        logger.info("Installation complete, (re)starting ComfyUI with preset flags...")
                        started = pm.restart(flags=extra_flags if extra_flags else None)
                        if started:
                            logger.info("✓ ComfyUI started successfully")
                        else:
                            logger.error(
                                "ComfyUI failed to start after installation — "
                                "check logs above for startup timeout or port conflict"
                            )
                    else:
                        print("\n" + "="*60)
                        print("\033[1;31m❌ ERRO NA INSTALAÇÃO ❌\033[0m")
                        print("="*60 + "\n")
                        logger.error("Installation failed")
                except Exception as e:
                    logger.error(f"Install thread error: {e}")
            
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

    def _handle_uninstall(self):
        """Handle preset uninstall request — deletes models specific to a preset"""
        try:
            cl = self.headers.get('Content-Length')
            if not cl:
                self.send_error(411, 'Content-Length required')
                return
            content_length = int(cl)
            if content_length > 1024 * 1024:
                self.send_error(413, 'Request body too large')
                return
            body = self.rfile.read(content_length)
            data = json.loads(body.decode())

            preset_name = (data.get('preset') or '').strip()
            if not preset_name:
                self.send_error(400, 'Missing "preset" field')
                return

            logger.info(f"Uninstall request: {preset_name}")

            from start import uninstall_preset, get_active_downloader

            # Block while an installation is in progress: a parallel uninstall
            # could delete a file the downloader just wrote (or is writing).
            if get_active_downloader() is not None:
                self.send_response(409)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'success': False,
                    'error': 'Instalação em andamento — aguarde a conclusão antes de remover.'
                }).encode())
                return

            result = uninstall_preset(preset_name)

            status_code = 200 if result.get('success') else 400
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as e:
            logger.error(f"Uninstall error: {e}")
            self.send_error(500, str(e))

    def _handle_shutdown(self):
        """Handle Arrakis Start shutdown request"""
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'success': True,
                'message': 'Shutdown initiated'
            }).encode())

            def do_shutdown():
                try:
                    import time
                    time.sleep(0.5)
                    logger.info("Shutdown requested via web UI")
                    # Stop ComfyUI first
                    from process_manager import ProcessManager
                    state = _state_manager or get_state_manager()
                    pm = ProcessManager(state)
                    if pm.is_running():
                        logger.info("Stopping ComfyUI before shutdown...")
                        pm.ensure_stopped(timeout=15)
                    logger.info("Arrakis Start shutting down...")
                    import os, signal
                    os.kill(os.getpid(), signal.SIGTERM)
                except Exception as e:
                    logger.error(f"Shutdown thread error: {e}")
                    os._exit(1)

            thread = threading.Thread(target=do_shutdown, daemon=True)
            thread.start()

        except Exception as e:
            logger.error(f"Shutdown error: {e}")
            self.send_error(500, str(e))


def run_server(port: int = 8090, presets_callback: Callable = None):
    """Run the HTTP server"""
    global _presets_callback, _state_manager
    _presets_callback = presets_callback
    _state_manager = get_state_manager()
    
    server = HTTPServer(('0.0.0.0', port), PresetHandler)
    
    # Colorful startup banner
    print("\n" + "="*60)
    print("\033[1;35m🌐 ARRAKIS START WEBUI INICIADA! 🌐\033[0m")
    print("\033[1;36m   Entre no portal do VastAI e selecione 'Arrakis Start'!\033[0m")
    print("="*60 + "\n")
    
    logger.info(f"Web server running on http://0.0.0.0:{port}")
    logger.info("Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("\nShutting down server...")
        server.shutdown()
