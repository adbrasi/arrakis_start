#!/usr/bin/env python3
"""
Download Manager - Parallel downloads with aria2c
Supports HuggingFace, Civitai, and direct URLs
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse, parse_qs
import shutil

logger = logging.getLogger(__name__)


class DownloadManager:
    def __init__(self, models_dir: Path):
        self.models_dir = Path(models_dir)
        self.civitai_token = os.environ.get('CIVITAI_TOKEN', '')
        self.hf_token = os.environ.get('HF_TOKEN', '')
        
        # Check for aria2c
        self.has_aria2c = shutil.which('aria2c') is not None
        if not self.has_aria2c:
            logger.warning("aria2c not found, falling back to wget")
    
    def download_all(self, downloads: List[Dict]) -> bool:
        """Download all files in the list"""
        total = len(downloads)
        logger.info(f"Starting download of {total} files")
        
        success_count = 0
        for i, item in enumerate(downloads, 1):
            url = item.get('url', '')
            target_dir = item.get('dir', '')
            filename = item.get('filename', '')
            
            if not url:
                logger.warning(f"[{i}/{total}] Skipping item with no URL")
                continue
            
            logger.info(f"[{i}/{total}] {filename or 'file'}")
            
            if self._download_file(url, target_dir, filename):
                success_count += 1
            else:
                logger.error(f"Failed to download: {filename or url}")
        
        logger.info(f"Downloaded {success_count}/{total} files successfully")
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
        
        # Add authentication tokens
        download_url = self._add_auth_token(url)
        
        # Download
        if self.has_aria2c:
            return self._download_aria2c(download_url, dest_dir, filename)
        else:
            return self._download_wget(download_url, dest_path)
    
    def _download_aria2c(self, url: str, dest_dir: Path, filename: str) -> bool:
        """Download using aria2c (parallel, resumable)"""
        cmd = [
            'aria2c',
            '-c',  # Continue download
            '-x', '2',  # Max connections per server
            '-s', '2',  # Split download
            '--disk-cache=0',
            '--file-allocation=none',
            '--console-log-level=warn',
            '--dir', str(dest_dir),
            '--out', filename,
            url
        ]
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"aria2c failed: {e.stderr}")
            # Fallback to wget
            return self._download_wget(url, dest_dir / filename)
    
    def _download_wget(self, url: str, dest_path: Path) -> bool:
        """Download using wget (fallback)"""
        cmd = [
            'wget',
            '-q',
            '--show-progress',
            '-c',  # Continue
            '-O', str(dest_path),
            url
        ]
        
        try:
            subprocess.run(cmd, check=True)
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
        
        # HuggingFace - handled via HF_TOKEN env var by huggingface-cli
        # For direct downloads, we'd need to add headers (not supported in aria2c easily)
        
        return url
    
    def _extract_filename(self, url: str) -> str:
        """Extract filename from URL"""
        parsed = urlparse(url)
        
        # Try to get from path
        path = parsed.path
        if path:
            filename = path.split('/')[-1]
            if filename:
                return filename
        
        # Try to get from query params (Civitai)
        if 'civitai.com' in url:
            # Use model ID as filename
            if '/models/' in url:
                model_id = url.split('/models/')[1].split('?')[0]
                return f"civitai_{model_id}.safetensors"
        
        # Fallback
        return 'downloaded_file'
    
    def download_hf_file(self, repo_id: str, filename: str, dest_dir: Path) -> bool:
        """Download from HuggingFace using huggingface-cli"""
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        cmd = [
            'huggingface-cli',
            'download',
            repo_id,
            filename,
            '--local-dir', str(dest_dir),
            '--local-dir-use-symlinks', 'False'
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info(f"✓ Downloaded from HF: {filename}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"HF download failed: {e.stderr}")
            return False
