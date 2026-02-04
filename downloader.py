#!/usr/bin/env python3
"""
Download Manager - Parallel downloads with real-time progress
Supports HuggingFace (with hf_transfer), Civitai, and direct URLs
"""

import os
import sys
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable
from urllib.parse import urlparse, parse_qs, unquote
import shutil
import re
try:
    from websocket_server import send_download_progress, send_log_message
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False

logger = logging.getLogger(__name__)

# Force unbuffered output for real-time progress
os.environ['PYTHONUNBUFFERED'] = '1'

# Get venv path for huggingface-cli
COMFY_BASE = Path(os.environ.get('COMFY_BASE', '/workspace/comfy'))
VENV_BIN = COMFY_BASE / '.venv' / 'bin'


class DownloadManager:
    def __init__(self, models_dir: Path, progress_callback: Optional[Callable] = None):
        self.models_dir = Path(models_dir)
        self.civitai_token = os.environ.get('CIVITAI_TOKEN', '')
        self.hf_token = os.environ.get('HF_TOKEN', '')
        self.progress_callback = progress_callback
        self._cancelled = False
        
        # Bandwidth throttling (e.g., "50M" for 50MB/s, "0" for unlimited)
        self.speed_limit = os.environ.get('DOWNLOAD_SPEED_LIMIT', '0')
        if self.speed_limit != '0':
            logger.info(f"Download speed limit: {self.speed_limit}")
        
        # Check for aria2c
        self.has_aria2c = shutil.which('aria2c') is not None
        if not self.has_aria2c:
            logger.warning("aria2c not found, falling back to wget")
        
        # Find HuggingFace CLI (check venv first, then system)
        # New CLI command is `hf`, fallback to `huggingface-cli` for compatibility
        self.hf_cli_path = self._find_hf_cli()
        self.current_process = None
        
        # Check for hf_xet (100x faster HF downloads, replaces deprecated hf_transfer)
        self.use_hf_xet = os.environ.get('HF_XET_HIGH_PERFORMANCE', '0') == '1'
        if self.use_hf_xet and self.hf_cli_path:
            logger.info("✓ hf_xet high-performance mode enabled for ultra-fast HuggingFace downloads")
    
    def _find_hf_cli(self) -> Optional[str]:
        """Find HuggingFace CLI executable (new `hf` or legacy `huggingface-cli`)"""
        # Try new `hf` command first (recommended)
        for cmd_name in ['hf', 'huggingface-cli']:
            # Check venv first
            venv_hf = VENV_BIN / cmd_name
            if venv_hf.exists():
                logger.info(f"Found HF CLI in venv: {cmd_name}")
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
                    
                if self._download_file(url, target_dir, filename):
                    success_count += 1
                    break
                else:
                    if attempt < max_retries:
                        logger.warning(f"Download failed, retrying ({attempt}/{max_retries})...")
                    else:
                        logger.error(f"Failed to download after {max_retries} attempts: {filename or url}")
        
        self._report_progress(f"Downloaded {success_count}/{total} files successfully", total, total)
        return success_count == total
    
    def _download_file(self, url: str, target_dir: str, filename: str = '') -> bool:
        """Download a single file"""
        # Create target directory
        dest_dir = self.models_dir / target_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine filename
        if not filename:
            filename = self._extract_filename(url)
        
        dest_path = dest_dir / filename
        
        # Skip if exists
        if dest_path.exists():
            logger.info(f"✓ Already exists: {filename}")
            return True
        
        # Use HuggingFace CLI for HF downloads (with hf_transfer support)
        if 'huggingface.co' in url and self.hf_cli_path and self.hf_token:
            result = self._download_hf_direct(url, dest_dir, filename)
            if result:
                return True
            # Fall through to aria2c if HF CLI fails
        
        # Add CivitAI authentication token if needed
        download_url = self._add_civitai_token(url)
        
        # Download with aria2c or wget
        if self.has_aria2c:
            return self._download_aria2c(download_url, dest_dir, filename)
        else:
            return self._download_wget(download_url, dest_path)
    
    def _add_civitai_token(self, url: str) -> str:
        """Add CivitAI API token to URL if needed"""
        if 'civitai.com' not in url or not self.civitai_token:
            return url
        
        # Check if URL already has query parameters
        separator = '&' if '?' in url else '?'
        
        # Append token as per CivitAI docs: ?token=apikey or &token=apikey
        authenticated_url = f"{url}{separator}token={self.civitai_token}"
        logger.debug(f"Added CivitAI token to URL")
        return authenticated_url
    
    def _download_hf_direct(self, url: str, dest_dir: Path, filename: str) -> bool:
        """Download from HuggingFace using `hf download` with hf_xet for max speed"""
        # Parse HF URL: https://huggingface.co/repo/resolve/main/file.safetensors
        match = re.search(r'huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)', url)
        if not match:
            logger.warning(f"Could not parse HF URL, falling back to aria2c: {url}")
            return False
        
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
            '--local-dir', str(dest_dir),
            '--local-dir-use-symlinks', 'False'
        ]
        
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
            
            # Stream output in real-time
            for line in process.stdout:
                print(line, end='', flush=True)
                
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
                return True
            else:
                logger.error(f"HF download failed with code {process.returncode}")
                return False
                
        except Exception as e:
            self.current_process = None
            logger.error(f"HF download failed: {e}")
            return False
    
    def _download_aria2c(self, url: str, dest_dir: Path, filename: str) -> bool:
        """Download using aria2c (parallel, resumable) with visible progress"""
        # For Civitai, use --content-disposition to get correct filename
        use_content_disposition = 'civitai.com' in url
        
        cmd = [
            'aria2c',
            '-c',  # Continue download
            '-x', '4',  # 4 connections per server
            '-s', '4',  # Split into 4 parts
            '--max-connection-per-server=4',
            '--min-split-size=1M',
            '--file-allocation=none',
            '--console-log-level=notice',
            '--summary-interval=1',
            '--dir', str(dest_dir),
        ]
        
        # Add HF token header if needed
        if 'huggingface.co' in url and self.hf_token:
            cmd.extend(['--header', f'Authorization: Bearer {self.hf_token}'])
        
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
            
            for line in process.stdout:
                print(line, end='', flush=True)
                
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
                return True
            else:
                logger.error(f"aria2c failed, falling back to wget")
                return self._download_wget(url, dest_dir / filename)
                
        except Exception as e:
            logger.error(f"aria2c error: {e}")
            return self._download_wget(url, dest_dir / filename)
    
    def _download_wget(self, url: str, dest_path: Path) -> bool:
        """Download using wget (fallback) with content-disposition support"""
        cmd = [
            'wget',
            '--progress=bar:force',
            '-c',  # Continue
            '--content-disposition',
        ]
        
        # Add HF token header if needed
        if 'huggingface.co' in url and self.hf_token:
            cmd.extend(['--header', f'Authorization: Bearer {self.hf_token}'])
        
        if 'civitai.com' not in url:
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
            
            for line in process.stdout:
                print(line, end='', flush=True)
                
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
                return True
            else:
                logger.error(f"wget failed with code {process.returncode}")
                return False
                
        except Exception as e:
            logger.error(f"wget error: {e}")
            return False
    
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
