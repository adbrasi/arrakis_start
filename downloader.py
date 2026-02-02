#!/usr/bin/env python3
"""
Download Manager - Parallel downloads with real-time progress
Supports HuggingFace (with hf_transfer), Civitai, and direct URLs
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable
from urllib.parse import urlparse, parse_qs
import shutil
import re

logger = logging.getLogger(__name__)


class DownloadManager:
    def __init__(self, models_dir: Path, progress_callback: Optional[Callable] = None):
        self.models_dir = Path(models_dir)
        self.civitai_token = os.environ.get('CIVITAI_TOKEN', '')
        self.hf_token = os.environ.get('HF_TOKEN', '')
        self.progress_callback = progress_callback
        
        # Check for aria2c
        self.has_aria2c = shutil.which('aria2c') is not None
        if not self.has_aria2c:
            logger.warning("aria2c not found, falling back to wget")
        
        # Check for hf_transfer (100x faster HF downloads)
        self.use_hf_transfer = os.environ.get('HF_HUB_ENABLE_HF_TRANSFER', '0') == '1'
        if self.use_hf_transfer:
            logger.info("✓ hf_transfer enabled for ultra-fast HuggingFace downloads")
    
    def _report_progress(self, message: str, current: int = 0, total: int = 0):
        """Report progress via callback"""
        if self.progress_callback:
            self.progress_callback({
                'message': message,
                'current': current,
                'total': total
            })
        logger.info(message)
    
    def download_all(self, downloads: List[Dict]) -> bool:
        """Download all files in the list"""
        total = len(downloads)
        self._report_progress(f"Starting download of {total} files", 0, total)
        
        success_count = 0
        for i, item in enumerate(downloads, 1):
            url = item.get('url', '')
            target_dir = item.get('dir', '')
            filename = item.get('filename', '')
            
            if not url:
                logger.warning(f"[{i}/{total}] Skipping item with no URL")
                continue
            
            self._report_progress(f"[{i}/{total}] {filename or 'file'}", i, total)
            
            if self._download_file(url, target_dir, filename):
                success_count += 1
            else:
                logger.error(f"Failed to download: {filename or url}")
        
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
        if 'huggingface.co' in url and self.hf_token:
            return self._download_hf_direct(url, dest_dir, filename)
        
        # Add authentication tokens
        download_url = self._add_auth_token(url)
        
        # Download
        if self.has_aria2c:
            return self._download_aria2c(download_url, dest_dir, filename)
        else:
            return self._download_wget(download_url, dest_path)
    
    def _download_hf_direct(self, url: str, dest_dir: Path, filename: str) -> bool:
        """Download from HuggingFace using huggingface-cli with hf_transfer"""
        # Parse HF URL: https://huggingface.co/repo/resolve/main/file.safetensors
        match = re.search(r'huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)', url)
        if not match:
            logger.warning(f"Could not parse HF URL, falling back to direct download: {url}")
            return self._download_aria2c(url, dest_dir, filename) if self.has_aria2c else self._download_wget(url, dest_dir / filename)
        
        repo_id, branch, file_path = match.groups()
        
        logger.info(f"Downloading from HuggingFace: {repo_id}/{file_path}")
        
        env = os.environ.copy()
        if self.use_hf_transfer:
            env['HF_HUB_ENABLE_HF_TRANSFER'] = '1'
        
        cmd = [
            'huggingface-cli',
            'download',
            repo_id,
            file_path,
            '--revision', branch,
            '--local-dir', str(dest_dir),
            '--local-dir-use-symlinks', 'False'
        ]
        
        try:
            result = subprocess.run(
                cmd,
                env=env,
                check=True,
                capture_output=False,  # Show progress in terminal
                text=True
            )
            logger.info(f"✓ Downloaded from HF: {filename}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"HF download failed: {e}")
            return False
    
    def _download_aria2c(self, url: str, dest_dir: Path, filename: str) -> bool:
        """Download using aria2c (parallel, resumable) with visible progress"""
        # For Civitai, use --content-disposition to get correct filename
        use_content_disposition = 'civitai.com' in url
        
        cmd = [
            'aria2c',
            '-c',  # Continue download
            '-x', '4',  # 4 connections per server (increased from 2)
            '-s', '4',  # Split into 4 parts
            '--max-connection-per-server=4',
            '--min-split-size=1M',
            '--file-allocation=none',
            '--console-log-level=notice',  # Show progress
            '--summary-interval=1',  # Update every second
            '--dir', str(dest_dir),
        ]
        
        if use_content_disposition:
            cmd.append('--content-disposition=true')
            cmd.append('--auto-file-renaming=false')
        else:
            cmd.extend(['--out', filename])
        
        cmd.append(url)
        
        try:
            # Run with visible output for progress
            result = subprocess.run(cmd, check=True, text=True)
            logger.info(f"✓ Downloaded: {filename}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"aria2c failed, falling back to wget")
            # Fallback to wget
            return self._download_wget(url, dest_dir / filename)
    
    def _download_wget(self, url: str, dest_path: Path) -> bool:
        """Download using wget (fallback) with content-disposition support"""
        cmd = [
            'wget',
            '--show-progress',
            '-c',  # Continue
            '--content-disposition',  # Use server-provided filename for Civitai
        ]
        
        # Only use -O if not using content-disposition or not Civitai
        if 'civitai.com' not in url:
            cmd.extend(['-O', str(dest_path)])
        else:
            cmd.extend(['-P', str(dest_path.parent)])
        
        cmd.append(url)
        
        try:
            subprocess.run(cmd, check=True)
            logger.info(f"✓ Downloaded: {dest_path.name}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"wget failed: {e}")
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
        path = parsed.path
        if path:
            filename = path.split('/')[-1]
            if filename and '.' in filename:
                return filename
        
        # Try to get from query params (Civitai)
        if 'civitai.com' in url:
            # Use model ID as filename
            if '/models/' in url:
                model_id = url.split('/models/')[1].split('?')[0]
                return f"civitai_{model_id}.safetensors"
        
        # Fallback
        return 'downloaded_file'

