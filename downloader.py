#!/usr/bin/env python3
"""
Download Manager - Parallel downloads with real-time progress
Supports HuggingFace (hf/hf_xet), Civitai, and direct URLs
"""

import os
import sys
import subprocess
import logging
import json
from collections import deque
from pathlib import Path
from typing import List, Dict, Optional, Callable, Tuple
from urllib.parse import urlparse, unquote, parse_qsl, urlencode, urlunparse
import shutil
import re
import requests
try:
    from websocket_server import send_download_progress, send_log_message
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False

logger = logging.getLogger(__name__)
HTTP_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ArrakisStart/2.0"

# Force unbuffered output for real-time progress
os.environ['PYTHONUNBUFFERED'] = '1'

# Get venv paths for huggingface-cli
COMFY_BASE = Path(os.environ.get('COMFY_BASE', '/workspace/comfy'))
COMFY_VENV_BIN = COMFY_BASE / '.venv' / 'bin'
ARRAKIS_DIR = Path(__file__).parent
ARRAKIS_VENV_BIN = Path(os.environ.get('ARRAKIS_VENV_BIN', str(ARRAKIS_DIR / '.venv' / 'bin')))


class DownloadManager:
    def __init__(self, models_dir: Path, progress_callback: Optional[Callable] = None):
        self.models_dir = Path(models_dir)
        self.civitai_token, self.civitai_token_source = self._load_civitai_token()
        self.hf_token = os.environ.get('HF_TOKEN', '')
        self.progress_callback = progress_callback
        self._cancelled = False
        self.failures: List[Dict[str, str]] = []
        self.attempt_logs: List[Dict[str, str]] = []
        
        # Bandwidth throttling (e.g., "50M" for 50MB/s, "0" for unlimited)
        self.speed_limit = os.environ.get('DOWNLOAD_SPEED_LIMIT', '0')
        if self.speed_limit != '0':
            logger.info(f"Download speed limit: {self.speed_limit}")
        else:
            logger.info("Download speed limit: unlimited")
        logger.info(f"HF token present: {bool(self.hf_token)}")
        logger.info(f"Civitai token present: {bool(self.civitai_token)} (source: {self.civitai_token_source})")

        # aria2 tunables (override via environment if needed)
        self.aria2_connections = os.environ.get('ARIA2_CONNECTIONS', '16')
        self.aria2_splits = os.environ.get('ARIA2_SPLITS', self.aria2_connections)
        self.aria2_min_split_size = os.environ.get('ARIA2_MIN_SPLIT_SIZE', '1M')
        logger.info(
            f"aria2 settings: connections={self.aria2_connections}, "
            f"splits={self.aria2_splits}, min_split={self.aria2_min_split_size}"
        )
        
        # Check for aria2c
        self.has_aria2c = shutil.which('aria2c') is not None
        if not self.has_aria2c:
            logger.warning("aria2c not found, falling back to wget")
        
        # Find HuggingFace CLI (check venv first, then system)
        # New CLI command is `hf`, fallback to `huggingface-cli` for compatibility
        self.hf_cli_path = self._find_hf_cli()
        self.current_process = None
        
        # Check for hf_xet (modern high-performance path for HF downloads)
        self.use_hf_xet = os.environ.get('HF_XET_HIGH_PERFORMANCE', '0') == '1'
        if self.use_hf_xet and self.hf_cli_path:
            logger.info("✓ hf_xet high-performance mode enabled for ultra-fast HuggingFace downloads")
    
    def _load_civitai_token(self) -> Tuple[str, str]:
        """Load CIVITAI token from env first, then ~/.civitai/config fallback."""
        env_token = os.environ.get('CIVITAI_TOKEN', '').strip()
        if env_token:
            return env_token, 'env:CIVITAI_TOKEN'
        
        env_alt = os.environ.get('CIVITAI_API_KEY', '').strip()
        if env_alt:
            return env_alt, 'env:CIVITAI_API_KEY'
        
        token_file = Path(os.environ.get('CIVITAI_TOKEN_FILE', str(Path.home() / '.civitai' / 'config')))
        if not token_file.exists():
            return '', 'missing'
        
        try:
            content = token_file.read_text(encoding='utf-8').strip()
            if not content:
                return '', f'file:{token_file} (empty)'
            
            # JSON style: {"token":"..."}
            if content.startswith('{') and content.endswith('}'):
                data = json.loads(content)
                for key in ('token', 'civitai_token', 'api_key'):
                    value = str(data.get(key, '')).strip()
                    if value:
                        return value, f'file:{token_file} (json:{key})'
            
            # KEY=VALUE style
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                key = k.strip().lower()
                value = v.strip().strip('"').strip("'")
                if key in ('token', 'civitai_token', 'civitai_api_key', 'api_key') and value:
                    return value, f'file:{token_file} (kv:{key})'
            
            # Raw token in file
            first = content.splitlines()[0].strip().strip('"').strip("'")
            if first and ' ' not in first and '=' not in first:
                return first, f'file:{token_file} (raw)'
        except Exception as e:
            logger.warning(f"Failed to parse Civitai token file {token_file}: {e}")
        
        return '', f'file:{token_file} (unusable)'
    
    def get_failure_report(self) -> List[Dict[str, str]]:
        """Return detailed failures for installer summary."""
        return list(self.failures)
    
    def _token_tail(self, token: str) -> str:
        if not token:
            return "missing"
        return f"...{token[-6:]}"
    
    def _record_failure(self, item: Dict, reason: str, stage: str):
        """Track a failed download item with context."""
        self.failures.append({
            'filename': item.get('filename') or self._extract_filename(item.get('url', '')),
            'dir': item.get('dir', ''),
            'url': item.get('url', ''),
            'stage': stage,
            'reason': reason
        })
    
    def _record_attempt(self, url: str, method: str, ok: bool, reason: str = ''):
        """Keep lightweight attempt logs for debugging."""
        self.attempt_logs.append({
            'url': url,
            'method': method,
            'ok': str(ok),
            'reason': reason
        })
    
    def _is_retryable_failure(self, stage: str, reason: str) -> bool:
        """Return False for deterministic/precheck errors that retries cannot fix."""
        stage = (stage or '').lower()
        reason_lower = (reason or '').lower()
        if stage in {'precheck'}:
            return False
        if 'missing (required for civitai downloads)' in reason_lower:
            return False
        if 'auth_http_401' in reason_lower or 'auth_http_403' in reason_lower:
            return False
        return True
    
    def _append_query_param(self, url: str, key: str, value: str) -> str:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query[key] = value
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    
    def _sanitize_source_url(self, url: str) -> str:
        """
        Normalize source URLs by removing noisy `download=true` query parameter
        while preserving other required parameters.
        """
        parsed = urlparse(url)
        if not parsed.query:
            return url
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [(k, v) for (k, v) in query_items if not (k == 'download' and v.lower() == 'true')]
        if filtered == query_items:
            return url
        return urlunparse(parsed._replace(query=urlencode(filtered, doseq=True)))
    
    def _build_civitai_auth_url(self, url: str) -> str:
        if 'civitai.com' not in url or not self.civitai_token:
            return url
        return self._append_query_param(url, 'token', self.civitai_token)
    
    def _extract_filename_from_content_disposition(self, content_disposition: str) -> str:
        """Extract filename from Content-Disposition header/query value."""
        if not content_disposition:
            return ''
        # RFC 5987: filename*=UTF-8''...
        m = re.search(r"filename\\*=(?:UTF-8''|)([^;]+)", content_disposition, flags=re.IGNORECASE)
        if m:
            return unquote(m.group(1).strip().strip('"'))
        # Legacy: filename="..."
        m = re.search(r'filename="([^"]+)"', content_disposition, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Legacy: filename=...
        m = re.search(r'filename=([^;]+)', content_disposition, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip().strip('"')
        return ''
    
    def _extract_civitai_filename_from_url(self, url: str) -> str:
        """Extract actual filename from civitai redirect URL query parameters."""
        parsed = urlparse(url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        cd = params.get('response-content-disposition') or params.get('content-disposition') or ''
        filename = self._extract_filename_from_content_disposition(cd)
        return filename.strip()
    
    def _resolve_civitai_download_url(self, url: str) -> Tuple[Optional[str], str]:
        """
        Resolve Civitai API URL into direct CDN URL using authenticated no-redirect request.
        This avoids auth issues with some download clients.
        """
        if not self.civitai_token:
            return None, "CIVITAI_TOKEN missing"
        
        auth_url = self._build_civitai_auth_url(url)
        headers = {
            'Authorization': f'Bearer {self.civitai_token}',
            'User-Agent': HTTP_USER_AGENT,
            'Accept': 'application/octet-stream,*/*',
            'Referer': 'https://civitai.com/'
        }
        
        try:
            response = requests.get(
                auth_url,
                headers=headers,
                allow_redirects=False,
                timeout=30
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get('Location')
                if location:
                    return location, ""
                return None, f"civitai_redirect_without_location_{response.status_code}"
            if response.status_code == 200:
                return auth_url, ""
            if response.status_code in (401, 403):
                return None, (
                    f"civitai_auth_http_{response.status_code} "
                    f"(token tail: {self._token_tail(self.civitai_token)})"
                )
            return None, f"civitai_resolve_http_{response.status_code}"
        except Exception as e:
            return None, f"civitai_resolve_exception: {e}"
    
    def _find_hf_cli(self) -> Optional[str]:
        """Find HuggingFace CLI executable (new `hf` or legacy `huggingface-cli`)"""
        # Try new `hf` command first (recommended)
        for cmd_name in ['hf', 'huggingface-cli']:
            # Prefer Arrakis venv (orchestrator), fallback to ComfyUI venv.
            for venv_bin in (ARRAKIS_VENV_BIN, COMFY_VENV_BIN):
                venv_hf = venv_bin / cmd_name
                if venv_hf.exists():
                    logger.info(f"Found HF CLI in venv: {cmd_name} ({venv_bin})")
                    return str(venv_hf)
            
            # Check system PATH
            system_hf = shutil.which(cmd_name)
            if system_hf:
                logger.info(f"Found HF CLI in system: {cmd_name}")
                return system_hf
        
        logger.warning("HuggingFace CLI (hf/huggingface-cli) not found, will use aria2c for HF downloads")
        return None
    
    def cancel(self):
        """Cancel ongoing downloads immediately"""
        self._cancelled = True
        if self.current_process:
            logger.warning("Killing active download process...")
            try:
                self.current_process.kill()
            except Exception as e:
                logger.error(f"Failed to kill process: {e}")
        logger.info("Download cancelled by user")
    
    def _report_progress(self, message: str, current: int = 0, total: int = 0):
        """Report progress via callback"""
        if self.progress_callback:
            self.progress_callback({
                'message': message,
                'current': current,
                'total': total
            })
        logger.info(message)
        # Force flush for real-time output
        sys.stdout.flush()
        sys.stderr.flush()
    
    def download_all(self, downloads: List[Dict]) -> bool:
        """Download all files in the list"""
        total = len(downloads)
        self._cancelled = False
        self.failures = []
        self.attempt_logs = []
        self._report_progress(f"Starting download of {total} files", 0, total)
        
        success_count = 0
        for i, item in enumerate(downloads, 1):
            # Check for cancellation
            if self._cancelled:
                self._report_progress("Download cancelled", i, total)
                return False
            
            url = item.get('url', '')
            target_dir = item.get('dir', '')
            filename = item.get('filename', '')
            
            if not url:
                logger.warning(f"[{i}/{total}] Skipping item with no URL")
                continue
            
            if self._cancelled:
                self._report_progress("Download cancelled", i, total)
                return False
            
            self._report_progress(f"[{i}/{total}] {filename or 'file'}", i, total)
            
            # Retry logic: up to 3 attempts
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                if self._cancelled:
                    return False
                    
                ok, reason, stage = self._download_file(url, target_dir, filename)
                if ok:
                    success_count += 1
                    break
                else:
                    retryable = self._is_retryable_failure(stage, reason)
                    if attempt < max_retries:
                        if retryable:
                            logger.warning(
                                f"Download failed ({filename or url}) [{stage}: {reason}], "
                                f"retrying ({attempt}/{max_retries})..."
                            )
                        else:
                            logger.error(
                                f"Non-retryable failure ({filename or url}) [{stage}: {reason}]"
                            )
                            self._record_failure(
                                {'url': url, 'dir': target_dir, 'filename': filename},
                                reason=reason,
                                stage=stage
                            )
                            break
                    else:
                        logger.error(f"Failed to download after {max_retries} attempts: {filename or url}")
                        self._record_failure(
                            {'url': url, 'dir': target_dir, 'filename': filename},
                            reason=reason,
                            stage=stage
                        )
        
        self._report_progress(f"Downloaded {success_count}/{total} files successfully", total, total)
        if self.failures:
            logger.error("Download failure summary:")
            for idx, failure in enumerate(self.failures, 1):
                logger.error(
                    f"[{idx}] file={failure['filename']} dir={failure['dir']} "
                    f"stage={failure['stage']} reason={failure['reason']} url={failure['url']}"
                )
        return success_count == total
    
    def _download_file(self, url: str, target_dir: str, filename: str = '') -> Tuple[bool, str, str]:
        """Download a single file"""
        url = self._sanitize_source_url(url)
        is_civitai_source = 'civitai.com' in url

        # Create target directory
        dest_dir = self.models_dir / target_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine initial filename
        provided_filename = filename.strip() if isinstance(filename, str) else ''
        if provided_filename:
            filename = provided_filename
        elif is_civitai_source:
            # For Civitai API URLs, avoid placeholder filename before redirect resolution.
            filename = ''
        else:
            filename = self._extract_filename(url)
        
        dest_path = (dest_dir / filename) if filename else None
        
        # Skip if exists
        if dest_path is not None and dest_path.exists():
            logger.info(f"✓ Already exists: {filename}")
            return True, 'already_exists', 'skip'

        # Validate Civitai token early for clearer errors
        if is_civitai_source and not self.civitai_token:
            reason = "CIVITAI_TOKEN is missing (required for Civitai downloads)"
            logger.error(reason)
            return False, reason, 'precheck'
        
        # HuggingFace priority: always try HF CLI first for HF URLs (token optional).
        if 'huggingface.co' in url and self.hf_cli_path:
            result, reason = self._download_hf_direct(url, dest_dir, filename)
            self._record_attempt(url, 'hf-cli', result, reason)
            if result:
                return True, 'ok', 'hf-cli'
            # Fallback to huggingface_hub API before generic downloaders
            hub_ok, hub_reason = self._download_hf_via_python(url, dest_dir, filename)
            self._record_attempt(url, 'hf-hub-python', hub_ok, hub_reason)
            if hub_ok:
                return True, 'ok', 'hf-hub-python'
        
        # Resolve Civitai URL with authenticated redirect handling
        if is_civitai_source:
            resolved_url, resolve_reason = self._resolve_civitai_download_url(url)
            if not resolved_url:
                logger.error(f"Civitai URL resolution failed: {resolve_reason}")
                return False, resolve_reason, 'civitai-resolve'
            download_url = resolved_url
            civitai_filename = self._extract_civitai_filename_from_url(resolved_url)
            if civitai_filename:
                filename = civitai_filename
            elif not filename:
                # Fallback deterministic name if redirect lacks content-disposition.
                filename = self._extract_filename(url)
            dest_path = dest_dir / filename
            if dest_path.exists():
                logger.info(f"✓ Already exists: {filename}")
                return True, 'already_exists', 'skip'
            if resolved_url != url:
                logger.info("Resolved Civitai download URL via authenticated redirect")
        else:
            # Add CivitAI token for generic path if needed
            download_url = self._add_civitai_token(url)
        
        # Download with aria2c or wget
        if self.has_aria2c:
            ok, reason = self._download_aria2c(
                download_url,
                dest_dir,
                filename,
                prefer_content_disposition=False
            )
            self._record_attempt(url, 'aria2c', ok, reason)
            if ok:
                return True, 'ok', 'aria2c'
            fallback_ok, fallback_reason = self._download_wget(
                download_url,
                dest_dir / filename,
                prefer_content_disposition=False
            )
            self._record_attempt(url, 'wget-fallback', fallback_ok, fallback_reason)
            if fallback_ok:
                return True, 'ok', 'wget-fallback'
            return False, fallback_reason or reason, 'aria2c->wget'
        else:
            ok, reason = self._download_wget(
                download_url,
                dest_path,
                prefer_content_disposition=False
            )
            self._record_attempt(url, 'wget', ok, reason)
            if ok:
                return True, 'ok', 'wget'
            return False, reason, 'wget'
    
    def _add_civitai_token(self, url: str) -> str:
        """Add CivitAI API token to URL if needed"""
        authenticated_url = self._build_civitai_auth_url(url)
        if authenticated_url != url:
            logger.debug("Added CivitAI token to URL")
        return authenticated_url
    
    def _download_hf_direct(self, url: str, dest_dir: Path, filename: str) -> Tuple[bool, str]:
        """Download from HuggingFace using `hf download` with hf_xet for max speed"""
        # Parse HF URL: https://huggingface.co/repo/resolve/main/file.safetensors
        clean_url = url.split('?', 1)[0]
        match = re.search(r'huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)', clean_url)
        if not match:
            logger.warning(f"Could not parse HF URL, falling back to aria2c: {url}")
            return False, "invalid_hf_url_format"
        
        repo_id, branch, file_path = match.groups()
        # URL decode the file path
        file_path = unquote(file_path)
        
        logger.info(f"Downloading from HuggingFace: {repo_id}/{file_path}")
        
        # Configure environment for hf_xet maximum speed
        env = os.environ.copy()
        env['HF_XET_HIGH_PERFORMANCE'] = '1'
        env['HF_XET_NUM_CONCURRENT_RANGE_GETS'] = os.environ.get('HF_XET_NUM_CONCURRENT_RANGE_GETS', '32')
        env['HF_HUB_DOWNLOAD_TIMEOUT'] = os.environ.get('HF_HUB_DOWNLOAD_TIMEOUT', '60')
        
        # Build command - works for both `hf` and `huggingface-cli`
        cmd = [
            self.hf_cli_path,
            'download',
            repo_id,
            file_path,
            '--revision', branch,
            '--local-dir', str(dest_dir)
        ]
        if self.hf_token:
            cmd.extend(['--token', self.hf_token])
        
        try:
            # Run with visible output for progress
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1  # Line buffered
            )
            self.current_process = process
            
            tail = deque(maxlen=12)
            # Stream output in real-time
            for line in process.stdout:
                print(line, end='', flush=True)
                if line.strip():
                    tail.append(line.strip())
                
                # Parse progress for WebSocket
                if HAS_WEBSOCKET:
                    # hf_transfer output or tqdm
                    # Example: 45%|████▌     | 1.23G/2.75G [00:12<00:15, 102MB/s]
                    match = re.search(r'(\d+)%\|.*\[.*, ([\d\.]+\w+/s)\]', line)
                    if match:
                        percent = float(match.group(1))
                        speed = match.group(2)
                        send_download_progress(filename, percent, speed, "")
            
            process.wait()
            self.current_process = None
            
            if process.returncode == 0:
                logger.info(f"✓ Downloaded from HF: {filename}")
                return True, ""
            else:
                logger.error(f"HF download failed with code {process.returncode}")
                reason = f"hf_cli_exit_{process.returncode}"
                if tail:
                    reason = f"{reason} | tail: {' || '.join(tail)}"
                return False, reason
                
        except Exception as e:
            self.current_process = None
            logger.error(f"HF download failed: {e}")
            return False, str(e)
    
    def _download_hf_via_python(self, url: str, dest_dir: Path, filename: str) -> Tuple[bool, str]:
        """Fallback HuggingFace download via huggingface_hub API."""
        clean_url = url.split('?', 1)[0]
        match = re.search(r'huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)', clean_url)
        if not match:
            return False, "invalid_hf_url_for_python_fallback"
        repo_id, branch, file_path = match.groups()
        
        try:
            from huggingface_hub import hf_hub_download
            downloaded_path = hf_hub_download(
                repo_id=repo_id,
                filename=file_path,
                revision=branch,
                local_dir=str(dest_dir),
                token=self.hf_token or None
            )
            downloaded = Path(downloaded_path)
            target = dest_dir / filename
            if downloaded.resolve() != target.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(downloaded), str(target))
            logger.info(f"✓ Downloaded from huggingface_hub: {filename}")
            return True, ""
        except Exception as e:
            return False, f"hf_hub_python_exception: {e}"
    
    def _download_aria2c(
        self,
        url: str,
        dest_dir: Path,
        filename: str,
        prefer_content_disposition: bool = False
    ) -> Tuple[bool, str]:
        """Download using aria2c (parallel, resumable) with visible progress"""
        # For Civitai-origin downloads, always honor content-disposition.
        use_content_disposition = prefer_content_disposition or ('civitai.com' in url)
        
        cmd = [
            'aria2c',
            '-c',  # Continue download
            '-x', self.aria2_connections,  # connections per server
            '-s', self.aria2_splits,  # split parts
            f'--max-connection-per-server={self.aria2_connections}',
            f'--min-split-size={self.aria2_min_split_size}',
            '--file-allocation=none',
            '--console-log-level=notice',
            '--summary-interval=1',
            '--dir', str(dest_dir),
        ]
        
        # Add HF token header if needed
        if 'huggingface.co' in url and self.hf_token:
            cmd.extend(['--header', f'Authorization: Bearer {self.hf_token}'])
        if 'huggingface.co' in url:
            cmd.extend(['--header', f'User-Agent: {HTTP_USER_AGENT}'])
        
        # Add Civitai headers to reduce auth/redirect issues
        if 'civitai.com' in url:
            if self.civitai_token:
                cmd.extend(['--header', f'Authorization: Bearer {self.civitai_token}'])
            cmd.extend(['--header', f'User-Agent: {HTTP_USER_AGENT}'])
            cmd.extend(['--header', 'Accept: application/octet-stream,*/*'])
            cmd.extend(['--header', 'Referer: https://civitai.com/'])
        
        # Add speed limit if configured (I7: bandwidth throttling)
        if self.speed_limit != '0':
            cmd.extend(['--max-download-limit', self.speed_limit])
        
        if use_content_disposition:
            cmd.append('--content-disposition=true')
            cmd.append('--auto-file-renaming=false')
        else:
            cmd.extend(['--out', filename])
        
        cmd.append(url)
        
        try:
            # Run with real-time output
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self.current_process = process
            
            tail = deque(maxlen=12)
            for line in process.stdout:
                print(line, end='', flush=True)
                if line.strip():
                    tail.append(line.strip())
                
                # Parse progress for WebSocket
                if HAS_WEBSOCKET:
                    # aria2c output: [#2089b0 27MiB/91MiB(29%) CN:8 DL:110MiB ETA:1s]
                    match = re.search(r'\(([\d\.]+)%\).*DL:([\d\.]+\w+)(?:.*?ETA:([\d\w]+))?', line)
                    if match:
                        percent = float(match.group(1))
                        speed = match.group(2) + "/s"
                        eta = match.group(3) or ""
                        send_download_progress(filename, percent, speed, eta)
            
            process.wait()
            self.current_process = None
            
            if process.returncode == 0:
                logger.info(f"✓ Downloaded: {filename}")
                return True, ""
            else:
                logger.error(f"aria2c failed with code {process.returncode}")
                reason = f"aria2c_exit_{process.returncode}"
                if tail:
                    reason = f"{reason} | tail: {' || '.join(tail)}"
                return False, reason
                
        except Exception as e:
            logger.error(f"aria2c error: {e}")
            return False, str(e)
    
    def _download_wget(
        self,
        url: str,
        dest_path: Path,
        prefer_content_disposition: bool = False
    ) -> Tuple[bool, str]:
        """Download using wget (fallback) with content-disposition support"""
        use_content_disposition = prefer_content_disposition or ('civitai.com' in url)
        cmd = [
            'wget',
            '--progress=bar:force',
            '-c',  # Continue
            '--content-disposition',
        ]
        
        # Add HF token header if needed
        if 'huggingface.co' in url and self.hf_token:
            cmd.extend(['--header', f'Authorization: Bearer {self.hf_token}'])
        if 'huggingface.co' in url:
            cmd.extend(['--user-agent', HTTP_USER_AGENT])
        
        if 'civitai.com' in url:
            if self.civitai_token:
                cmd.extend(['--header', f'Authorization: Bearer {self.civitai_token}'])
            cmd.extend(['--user-agent', HTTP_USER_AGENT])
            cmd.extend(['--header', 'Referer: https://civitai.com/'])
        
        if not use_content_disposition:
            cmd.extend(['-O', str(dest_path)])
        else:
            cmd.extend(['-P', str(dest_path.parent)])
        
        cmd.append(url)
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self.current_process = process
            
            tail = deque(maxlen=12)
            for line in process.stdout:
                print(line, end='', flush=True)
                if line.strip():
                    tail.append(line.strip())
                
                # Parse progress for WebSocket
                if HAS_WEBSOCKET:
                    # wget output:  52% [============>           ] 14,833,969  21.3MB/s  eta 1s
                    match = re.search(r'(\d+)%.*?([\d\.]+[KMG]B/s).*?eta\s+([\w\d]+)', line)
                    if match:
                        percent = float(match.group(1))
                        speed = match.group(2)
                        eta = match.group(3)
                        send_download_progress(dest_path.name, percent, speed, eta)
            
            process.wait()
            self.current_process = None
            
            if process.returncode == 0:
                logger.info(f"✓ Downloaded: {dest_path.name}")
                return True, ""
            else:
                logger.error(f"wget failed with code {process.returncode}")
                reason = f"wget_exit_{process.returncode}"
                if tail:
                    reason = f"{reason} | tail: {' || '.join(tail)}"
                return False, reason
                
        except Exception as e:
            logger.error(f"wget error: {e}")
            return False, str(e)
    
    def _add_auth_token(self, url: str) -> str:
        """Add authentication token if needed"""
        # Civitai
        if 'civitai.com' in url and self.civitai_token:
            if 'token=' not in url:
                separator = '&' if '?' in url else '?'
                url = f"{url}{separator}token={self.civitai_token}"
        
        return url
    
    def _extract_filename(self, url: str) -> str:
        """Extract filename from URL"""
        parsed = urlparse(url)
        
        # Try to get from path
        path = unquote(parsed.path)
        if path:
            filename = path.split('/')[-1]
            if filename and '.' in filename:
                return filename
        
        # Try to get from query params (Civitai)
        if 'civitai.com' in url:
            if '/models/' in url:
                model_id = url.split('/models/')[1].split('?')[0]
                return f"civitai_{model_id}.safetensors"
        
        return 'downloaded_file'
