#!/usr/bin/env python3
"""
Download Manager - Parallel downloads with real-time progress
Supports HuggingFace (hf/hf_xet), Civitai, and direct URLs
"""

import os
import sys
import signal
import subprocess
import logging
import json
import time
from collections import deque
from pathlib import Path
from typing import List, Dict, Optional, Callable, Tuple
from urllib.parse import urlparse, unquote, parse_qsl, urlencode, urlunparse, urljoin
import shutil
import re
import threading
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

# Minimum RAM (GB) to safely enable HF_XET_HIGH_PERFORMANCE.
# HF docs: HP mode allocates multi-GB buffers (up to 64GB) and is intended
# for machines with at least 64GB RAM. Below that, it can degrade performance.
HF_XET_HP_MIN_RAM_GB = int(os.environ.get('HF_XET_HP_MIN_RAM_GB', '48'))


def _should_enable_hf_xet_hp() -> bool:
    """Return True when HF_XET_HIGH_PERFORMANCE can be safely enabled.

    Honors an explicit override via HF_XET_HIGH_PERFORMANCE env var: '0' disables,
    any other truthy value forces-enable. Without an override, we probe RAM via
    psutil and only enable HP mode when total memory >= HF_XET_HP_MIN_RAM_GB.
    """
    override = os.environ.get('HF_XET_HIGH_PERFORMANCE')
    if override is not None:
        return override.strip() not in ('0', '', 'false', 'False')

    try:
        import psutil
        total_gb = psutil.virtual_memory().total / (1024 ** 3)
        return total_gb >= HF_XET_HP_MIN_RAM_GB
    except Exception:
        # If psutil isn't available, stay on the safe side.
        return False

# Get venv paths for huggingface-cli
COMFY_BASE = Path(os.environ.get('COMFY_BASE', '/workspace/comfy'))
COMFY_VENV_BIN = COMFY_BASE / '.venv' / 'bin'
ARRAKIS_DIR = Path(__file__).parent
ARRAKIS_VENV_BIN = Path(os.environ.get('ARRAKIS_VENV_BIN', str(ARRAKIS_DIR / '.venv' / 'bin')))


class DownloadManager:
    def __init__(self, models_dir: Path, progress_callback: Optional[Callable] = None):
        self.models_dir = Path(models_dir)
        self.civitai_token, self.civitai_token_source = self._load_civitai_token()
        self.hf_token = self._load_hf_token()
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
        logger.info(f"HF token: {self._token_tail(self.hf_token)}")
        if not self.hf_token:
            logger.warning(
                "HF_TOKEN not set — gated model downloads will fail. "
                "Set the HF_TOKEN environment variable with your HuggingFace token."
            )
        # Auto-store token on disk so hf_xet backend and hf CLI read it for gated models.
        # Always overwrite: the env var is the source of truth (user may rotate tokens).
        if self.hf_token:
            self._ensure_hf_token_stored()
        logger.info(f"Civitai token present: {bool(self.civitai_token)} (source: {self.civitai_token_source})")

        # aria2 tunables (override via environment if needed).
        # HuggingFace goes through an LFS bridge with aggressive rate-limiting;
        # 8 connections is a safer default than the 16 used for direct CDNs
        # (Civitai and arbitrary URLs). Both are configurable via env.
        self.aria2_connections = os.environ.get('ARIA2_CONNECTIONS', '16')
        self.aria2_hf_connections = os.environ.get('ARIA2_HF_CONNECTIONS', '8')
        self.aria2_splits = os.environ.get('ARIA2_SPLITS', self.aria2_connections)
        self.aria2_min_split_size = os.environ.get('ARIA2_MIN_SPLIT_SIZE', '1M')
        self.aria2_stall_timeout_seconds = int(os.environ.get('ARIA2_STALL_TIMEOUT_SECONDS', '120'))
        logger.info(
            f"aria2 settings: connections={self.aria2_connections} "
            f"(HF={self.aria2_hf_connections}), "
            f"splits={self.aria2_splits}, min_split={self.aria2_min_split_size}"
        )
        logger.info(f"aria2 stall timeout: {self.aria2_stall_timeout_seconds}s")

        # Check for aria2c
        self.has_aria2c = shutil.which('aria2c') is not None
        if not self.has_aria2c:
            logger.warning("aria2c not found, falling back to wget")

        # Find HuggingFace CLI (check venv first, then system)
        # New CLI command is `hf`, fallback to `huggingface-cli` for compatibility
        self.hf_cli_path, self._hf_cli_pip = self._find_hf_cli()
        # Track all live download subprocesses so cancel() can kill them all
        # and download_all() can safely run multiple downloads in parallel.
        self._active_procs: "set[subprocess.Popen]" = set()
        self._process_lock = threading.Lock()
        self._failures_lock = threading.Lock()

        # Parallel downloads: defaults to 3 concurrent files. Each aria2c call
        # still uses its own 16-connection pool, so total connections can reach
        # ~48 per CDN. Tune via DOWNLOAD_PARALLELISM env var.
        self.parallel_downloads = max(
            1, int(os.environ.get('DOWNLOAD_PARALLELISM', '3'))
        )
        logger.info(f"Parallel downloads: {self.parallel_downloads}")

        # Ensure hf_xet is installed in the same env as the HF CLI
        self._ensure_hf_xet()
    
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
    
    def _load_hf_token(self) -> str:
        """Load HuggingFace token from env vars.

        HF_TOKEN is the official env var (used by huggingface_hub, hf_xet,
        and the hf CLI).  HUGGING_FACE_HUB_TOKEN is the deprecated alias
        still recognised by the library.  We check both but HF_TOKEN wins.
        """
        for var in ('HF_TOKEN', 'HUGGING_FACE_HUB_TOKEN'):
            value = os.environ.get(var, '').strip()
            if value:
                if var != 'HF_TOKEN':
                    logger.info(f"HF token loaded from deprecated {var} — consider using HF_TOKEN instead")
                return value
        return ''

    def get_failure_report(self) -> List[Dict[str, str]]:
        """Return detailed failures for installer summary."""
        with self._failures_lock:
            return list(self.failures)
    
    def _token_tail(self, token: str) -> str:
        if not token:
            return "missing"
        return f"...{token[-6:]}" if len(token) > 6 else "(short/invalid)"

    def _ensure_hf_token_stored(self):
        """Store HF_TOKEN to disk so hf CLI and hf_xet can read it for gated models.

        huggingface_hub reads the token from $HF_HOME/token (controlled by
        HF_TOKEN_PATH env var).  The env var HF_TOKEN takes precedence at runtime,
        but hf_xet and some CLI code paths read the file directly.
        """
        hf_home = Path(os.environ.get('HF_HOME', Path.home() / '.cache' / 'huggingface'))
        token_file = hf_home / 'token'

        needs_write = True
        if token_file.exists():
            try:
                stored = token_file.read_text().strip()
                needs_write = stored != self.hf_token
                if needs_write:
                    logger.info(
                        f"Stored HF token ({self._token_tail(stored)}) differs from "
                        f"env HF_TOKEN ({self._token_tail(self.hf_token)}), updating"
                    )
            except Exception:
                needs_write = True

        if needs_write:
            try:
                hf_home.mkdir(parents=True, exist_ok=True)
                token_file.write_text(self.hf_token)
                token_file.chmod(0o600)
                logger.info(f"HF token stored at {token_file}")
            except Exception as e:
                logger.warning(
                    f"Could not store HF token at {token_file}: {e}. "
                    f"Gated models may fail with hf_xet."
                )
        else:
            logger.info(f"HF token on disk matches env ({token_file})")
    
    def _record_failure(self, item: Dict, reason: str, stage: str):
        """Track a failed download item with context (thread-safe)."""
        entry = {
            'filename': item.get('filename') or self._extract_filename(item.get('url', '')),
            'dir': item.get('dir', ''),
            'url': item.get('url', ''),
            'stage': stage,
            'reason': reason
        }
        with self._failures_lock:
            self.failures.append(entry)

    def _record_attempt(self, url: str, method: str, ok: bool, reason: str = ''):
        """Keep lightweight attempt logs for debugging (thread-safe)."""
        entry = {
            'url': url,
            'method': method,
            'ok': str(ok),
            'reason': reason
        }
        with self._failures_lock:
            self.attempt_logs.append(entry)
    
    def _is_retryable_failure(self, stage: str, reason: str) -> bool:
        """Return False for deterministic/precheck errors that retries cannot fix."""
        stage = (stage or '').lower()
        reason_lower = (reason or '').lower()
        if stage in {'precheck', 'auth'}:
            return False
        if 'missing (required for civitai downloads)' in reason_lower:
            return False
        if 'auth_redirect_login' in reason_lower:
            return False
        if 'auth_http_401' in reason_lower or 'auth_http_403' in reason_lower:
            return False
        if 'auth_gated_model_not_accepted' in reason_lower:
            return False
        if '401 unauthorized' in reason_lower or '403 forbidden' in reason_lower:
            return False
        if 'requires you to be logged in' in reason_lower:
            return False
        if 'username/password authentication failed' in reason_lower:
            return False
        return True

    def _classify_hf_auth_error(self, reason: str) -> str:
        """Classify deterministic HuggingFace auth failures.

        Distinguishes between invalid token (401), gated model not accepted (403
        with 'access to model' or 'gated' hints), and generic forbidden (403).
        """
        reason_lower = (reason or '').lower()
        if (
            'auth_http_401' in reason_lower or
            '401 unauthorized' in reason_lower or
            '401 client error' in reason_lower
        ):
            return 'auth_http_401'
        if 'username/password authentication failed' in reason_lower:
            return 'auth_http_401'

        is_403 = (
            'auth_http_403' in reason_lower or
            '403 forbidden' in reason_lower or
            '403 client error' in reason_lower
        )
        if is_403:
            # Detect gated model errors (user hasn't accepted the license on HF)
            gated_hints = (
                'access to model', 'gated repo', 'gated model',
                'must agree', 'accept the', 'restricted',
                'you need to agree', 'access request',
            )
            if any(hint in reason_lower for hint in gated_hints):
                logger.error(
                    "GATED MODEL: You have a valid token but haven't accepted "
                    "this model's license on huggingface.co. Visit the model "
                    "page and click 'Agree and access' to enable downloads."
                )
                return 'auth_gated_model_not_accepted'
            return 'auth_http_403'
        return ''

    def _is_invalid_existing_file(self, dest_path: Path, source_url: str) -> bool:
        """
        Detect invalid leftovers from failed downloads (e.g., HTML 401 pages).
        Prevents false "Already exists" successes on retry.
        """
        try:
            if not dest_path.exists():
                return False

            size = dest_path.stat().st_size
            if size == 0:
                return True

            if 'huggingface.co' not in source_url:
                return False

            # Small HF files that contain auth HTML should not be treated as valid artifacts.
            if size <= 256 * 1024:
                with open(dest_path, 'rb') as f:
                    snippet = f.read(4096).decode('utf-8', errors='ignore').lower()
                if (
                    '<html' in snippet or
                    '<!doctype html' in snippet or
                    'unauthorized' in snippet or
                    'authentication failed' in snippet
                ):
                    return True
        except Exception:
            return False

        return False
    
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
        m = re.search(r"filename\*=(?:UTF-8''|)([^;]+)", content_disposition, flags=re.IGNORECASE)
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
            # stream=True + context manager: we only inspect status/headers (and a
            # small JSON error body), never the file body. Without it, a rare 200
            # carrying the file inline would buffer the entire model into RAM.
            with requests.get(
                auth_url,
                headers=headers,
                allow_redirects=False,
                timeout=30,
                stream=True,
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = (response.headers.get('Location') or '').strip()
                    if location:
                        location_lower = location.lower()
                        if location_lower.startswith('/login') or 'reason=download-auth' in location_lower:
                            return None, (
                                f"civitai_auth_http_401_auth_redirect_login "
                                f"(token tail: {self._token_tail(self.civitai_token)})"
                            )
                        return urljoin(auth_url, location), ""
                    return None, f"civitai_redirect_without_location_{response.status_code}"
                if response.status_code == 200:
                    return auth_url, ""
                if response.status_code in (401, 403):
                    detail = ""
                    try:
                        body = response.json()
                        msg = str(body.get('message') or body.get('error') or '').strip()
                        if msg:
                            detail = f": {msg}"
                    except Exception:
                        detail = ""
                    return None, (
                        f"civitai_auth_http_{response.status_code} "
                        f"{detail} "
                        f"(token tail: {self._token_tail(self.civitai_token)})"
                    )
                return None, f"civitai_resolve_http_{response.status_code}"
        except Exception as e:
            return None, f"civitai_resolve_exception: {e}"
    
    def _read_lines_cr_aware(self, stream):
        """Read lines from a binary stream, splitting on both \\r and \\n.

        tqdm and hf_xet progress bars use \\r to overwrite lines in-place.
        Python's default line iteration only splits on \\n, so progress
        updates are buffered until a newline arrives (often only at the end).
        This method yields each \\r-delimited update as a separate line.
        """
        buf = bytearray()
        while True:
            chunk = stream.read(1)
            if not chunk:
                if buf:
                    yield buf.decode('utf-8', errors='replace')
                break
            if chunk in (b'\r', b'\n'):
                if buf:
                    yield buf.decode('utf-8', errors='replace')
                    buf = bytearray()
            else:
                buf += chunk

    # ------------------------------------------------------------------ #
    # Robust progress/stall primitives (bytes-on-disk, not stdout scraping)
    # ------------------------------------------------------------------ #

    def _hf_python(self) -> str:
        """Return the python executable that owns the HF CLI (same venv as hf_xet)."""
        if self.hf_cli_path:
            cli_bin = Path(self.hf_cli_path).resolve().parent
            for name in ('python', 'python3'):
                cand = cli_bin / name
                if cand.exists():
                    return str(cand)
        return sys.executable

    @staticmethod
    def _hf_staging_dir(dest_dir: Path) -> Path:
        """Dir where huggingface_hub writes the in-progress *.incomplete file for a
        `--local-dir` download. Stable across hf_hub versions (only the temp file's
        name changes); both the hf_xet and classic HTTP backends write here."""
        return Path(dest_dir) / ".cache" / "huggingface" / "download"

    @staticmethod
    def _tree_bytes(*paths) -> Tuple[int, int]:
        """Sum apparent size (st_size) and allocated size (st_blocks*512) over the
        given files/dirs. Allocated size guards against any sparse/preallocated temp
        file, so growth is detected even if st_size were set up-front by a backend."""
        total_size = 0
        total_alloc = 0
        for p in paths:
            try:
                if p is None:
                    continue
                if p.is_dir():
                    for root, _dirs, files in os.walk(p):
                        for f in files:
                            try:
                                st = os.stat(os.path.join(root, f))
                                total_size += st.st_size
                                total_alloc += getattr(st, 'st_blocks', 0) * 512
                            except OSError:
                                pass
                elif p.exists():
                    st = p.stat()
                    total_size += st.st_size
                    total_alloc += getattr(st, 'st_blocks', 0) * 512
            except OSError:
                pass
        return total_size, total_alloc

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        if bps >= 1_073_741_824:
            return f"{bps / 1_073_741_824:.1f}GB/s"
        if bps >= 1_048_576:
            return f"{bps / 1_048_576:.0f}MB/s"
        if bps >= 1024:
            return f"{bps / 1024:.0f}KB/s"
        return f"{bps:.0f}B/s"

    @staticmethod
    def _fmt_eta(seconds: float) -> str:
        seconds = int(max(0, seconds))
        if seconds >= 3600:
            return f"{seconds // 3600}h{(seconds % 3600) // 60}m"
        if seconds >= 60:
            return f"{seconds // 60}m{seconds % 60}s"
        return f"{seconds}s"

    @staticmethod
    def _speed_suffix(target: Path, elapsed: float) -> str:
        try:
            if elapsed > 0.5 and target.exists():
                sb = target.stat().st_size
                if sb > 0:
                    bps = sb / elapsed
                    if bps >= 1_073_741_824:
                        return f" ({bps / 1_073_741_824:.1f} GB/s, {elapsed:.0f}s)"
                    if bps >= 1_048_576:
                        return f" ({bps / 1_048_576:.0f} MB/s, {elapsed:.0f}s)"
                    return f" ({bps / 1024:.0f} KB/s, {elapsed:.0f}s)"
        except Exception:
            pass
        return ""

    def _terminate_process(self, process: subprocess.Popen, grace: float = 8.0) -> None:
        """Stop a download subprocess cleanly: SIGINT first (lets the hf_xet Rust
        backend run abort_xet_session and avoid orphaned threads / corrupt temp
        files — verified in xet-core), then SIGKILL if it doesn't exit in `grace`s."""
        try:
            process.send_signal(signal.SIGINT)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.3)
        try:
            process.kill()
        except Exception:
            pass

    def _hf_remote_size(self, repo_id: str, branch: str, file_path: str) -> Optional[int]:
        """Best-effort total file size via a HEAD on the resolve URL (for % display).
        Non-fatal: returns None on any problem (we then report bytes/speed only)."""
        try:
            from urllib.parse import quote
            url = f"https://huggingface.co/{repo_id}/resolve/{branch}/{quote(file_path)}"
            headers = {'User-Agent': HTTP_USER_AGENT, 'Accept-Encoding': 'identity'}
            if self.hf_token:
                headers['Authorization'] = f'Bearer {self.hf_token}'
            resp = requests.head(url, headers=headers, allow_redirects=True, timeout=15)
            size = resp.headers.get('x-linked-size') or resp.headers.get('Content-Length')
            return int(size) if size and str(size).isdigit() else None
        except Exception:
            return None

    def _run_disk_watchdog(self, process, staging_dir, final_path, filename,
                           expected_size, stall_state):
        """Authoritative progress + stall signal = BYTES WRITTEN TO DISK.

        This replaces scraping the CLI's stdout (the hf_xet/tqdm bar goes to stderr
        and is throttled/absent on a non-TTY pipe, which left us blind). Disk bytes
        are backend-agnostic (XET and HTTP stream the same *.incomplete file) and
        version-independent. Resets the stall clock on any growth (size OR
        allocation), emits throttled progress, and stops the process (SIGINT→SIGKILL)
        when no new bytes land for the stall timeout."""
        to = self.aria2_stall_timeout_seconds
        warn_after = max(30, to // 4) if to > 0 else 0
        poll = 3.0
        prev_size, prev_alloc = self._tree_bytes(staging_dir, final_path)
        stall_state['last_bytes'] = prev_size
        now0 = time.monotonic()
        last_emit_ts = now0
        last_emit_bytes = prev_size
        warned = False
        while True:
            time.sleep(poll)
            if process.poll() is not None or self._cancelled:
                return
            size, alloc = self._tree_bytes(staging_dir, final_path)
            now = time.monotonic()
            if size > prev_size or alloc > prev_alloc:
                stall_state['last_progress'] = now
                stall_state['last_bytes'] = size
                warned = False
                prev_size, prev_alloc = size, alloc
                if now - last_emit_ts >= 5:
                    dt = now - last_emit_ts
                    speed_bps = max(0, size - last_emit_bytes) / dt if dt > 0 else 0
                    speed = self._fmt_speed(speed_bps)
                    pct = (size / expected_size * 100.0) if expected_size else 0.0
                    eta = self._fmt_eta((expected_size - size) / speed_bps) \
                        if (expected_size and speed_bps > 0) else ""
                    if HAS_WEBSOCKET:
                        send_download_progress(filename, pct, speed, eta)
                    if expected_size:
                        logger.info(
                            f"  ↓ {filename}: {pct:.0f}% "
                            f"({size / 1_048_576:.0f}/{expected_size / 1_048_576:.0f} MB) @ {speed}"
                            + (f" ETA {eta}" if eta else "")
                        )
                    else:
                        logger.info(f"  ↓ {filename}: {size / 1_048_576:.0f} MB @ {speed}")
                    last_emit_ts = now
                    last_emit_bytes = size
            else:
                stalled_for = now - stall_state['last_progress']
                if warn_after and not warned and stalled_for > warn_after:
                    logger.warning(
                        f"  ⚠ {filename}: sem bytes novos em disco há {stalled_for:.0f}s "
                        f"(timeout em {to}s)"
                    )
                    warned = True
                if to > 0 and stalled_for > to:
                    stall_state['killed'] = True
                    logger.error(
                        f"Stall em disco para {filename} (sem bytes novos por {to}s), "
                        f"encerrando (SIGINT) e caindo para fallback"
                    )
                    self._terminate_process(process)
                    return

    def _finalize_hf_file(self, dest_dir: Path, file_path: str, filename: str,
                          target: Path, downloaded: Path,
                          expected_size: Optional[int]) -> Tuple[bool, str]:
        """Move the HF-downloaded file (placed at <dest_dir>/<repo_path>) to the
        requested <dest_dir>/<filename>, prune empty repo subdirs, and sanity-check
        the size. Returns (ok, reason)."""
        try:
            if downloaded.exists():
                if downloaded.resolve() != target.resolve():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(downloaded), str(target))
                    parent = downloaded.parent
                    while parent != dest_dir and parent.exists():
                        try:
                            parent.rmdir()
                        except OSError:
                            break
                        parent = parent.parent
            elif not target.exists():
                flat = dest_dir / Path(file_path).name
                if flat.exists() and flat.resolve() != target.resolve():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(flat), str(target))
                else:
                    return False, f"hf_download_missing_output: expected '{downloaded}' or '{target}'"
            if not target.exists():
                return False, "hf_download_missing_output"
            if expected_size and target.stat().st_size < expected_size:
                return False, f"hf_download_incomplete_{target.stat().st_size}_of_{expected_size}"
            return True, ""
        except Exception as e:
            return False, f"hf_finalize_error: {e}"

    def _verify_download_landed(self, dest_dir: Path, filename: str,
                                content_disposition: bool) -> Tuple[bool, str]:
        """Verify a generic (aria2c/wget) download actually produced a file, so a
        returncode==0 with a misnamed/empty output isn't reported as success."""
        try:
            if filename:
                p = dest_dir / filename
                if p.exists() and p.stat().st_size > 0:
                    return True, ""
                if not content_disposition:
                    return False, f"verify_failed: '{filename}' missing/empty in {dest_dir}"
            if content_disposition:
                # Server may have saved under its content-disposition name; accept the
                # newest non-trivial file (the control file is excluded).
                newest, newest_mt = None, -1.0
                for f in dest_dir.iterdir():
                    if f.is_file() and not f.name.endswith('.aria2'):
                        try:
                            mt = f.stat().st_mtime
                        except OSError:
                            continue
                        if mt > newest_mt:
                            newest, newest_mt = f, mt
                if newest and newest.stat().st_size > 0:
                    return True, ""
            return False, f"verify_failed: nothing landed in {dest_dir}"
        except Exception as e:
            return False, f"verify_error: {e}"

    def _find_hf_cli(self) -> Tuple[Optional[str], Optional[str]]:
        """Find HuggingFace CLI executable and its corresponding pip.

        Returns (hf_cli_path, pip_path) so we can install packages in the
        correct venv — the same one where `hf` CLI lives.
        """
        for cmd_name in ['hf', 'huggingface-cli']:
            # Prefer Arrakis venv (orchestrator), fallback to ComfyUI venv.
            for venv_bin in (ARRAKIS_VENV_BIN, COMFY_VENV_BIN):
                venv_hf = venv_bin / cmd_name
                if venv_hf.exists():
                    pip_path = str(venv_bin / 'pip')
                    logger.info(f"Found HF CLI in venv: {cmd_name} ({venv_bin})")
                    return str(venv_hf), pip_path

            # Check system PATH
            system_hf = shutil.which(cmd_name)
            if system_hf:
                logger.info(f"Found HF CLI in system: {cmd_name}")
                # For system installs, use the python that owns the CLI
                cli_dir = Path(system_hf).resolve().parent
                pip_candidate = cli_dir / 'pip'
                pip_path = str(pip_candidate) if pip_candidate.exists() else 'pip'
                return system_hf, pip_path

        logger.warning("HuggingFace CLI (hf/huggingface-cli) not found, will use aria2c for HF downloads")
        return None, None

    def _ensure_hf_xet(self):
        """Ensure hf_xet is installed in the HF CLI's venv and log diagnostics.

        hf_xet is the modern XET transfer backend that provides ultra-fast
        downloads (GBs/s). Without it, huggingface_hub falls back to slow
        default HTTP transfers.
        """
        self.has_hf_xet = False

        if not self.hf_cli_path:
            return

        # Determine the python executable in the same venv as the HF CLI
        cli_bin_dir = Path(self.hf_cli_path).resolve().parent
        hf_python = cli_bin_dir / 'python'
        if not hf_python.exists():
            hf_python = cli_bin_dir / 'python3'
        if not hf_python.exists():
            # Fallback: just use current python
            hf_python = Path(sys.executable)

        # Check huggingface_hub version and hf_xet availability in that env
        check_script = "\n".join([
            "import json",
            "d = {}",
            "try:",
            "    import huggingface_hub; d['hf_hub_version'] = huggingface_hub.__version__",
            "except Exception:",
            "    d['hf_hub_version'] = 'not_found'",
            "try:",
            "    import hf_xet; d['hf_xet'] = True",
            "except Exception:",
            "    d['hf_xet'] = False",
            "try:",
            "    import hf_transfer; d['hf_transfer'] = True",
            "except Exception:",
            "    d['hf_transfer'] = False",
            "print(json.dumps(d))",
        ])

        try:
            result = subprocess.run(
                [str(hf_python), '-c', check_script],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                info = json.loads(result.stdout.strip())
            else:
                info = {'hf_hub_version': 'unknown', 'hf_xet': False, 'hf_transfer': False}
                logger.warning(f"Failed to check HF env: {result.stderr.strip()}")
        except Exception as e:
            info = {'hf_hub_version': 'unknown', 'hf_xet': False, 'hf_transfer': False}
            logger.warning(f"Could not inspect HF CLI environment: {e}")

        hf_hub_ver = info.get('hf_hub_version', 'unknown')
        has_xet = info.get('hf_xet', False)
        has_transfer = info.get('hf_transfer', False)

        logger.info(
            f"HF CLI env: huggingface_hub={hf_hub_ver}, "
            f"hf_xet={'✓' if has_xet else '✗'}, "
            f"hf_transfer={'✓' if has_transfer else '✗'}, "
            f"python={hf_python}"
        )

        if has_xet:
            self.has_hf_xet = True
            logger.info("✓ hf_xet available — XET transfer backend active (ultra-fast downloads)")
            return

        # hf_xet not installed — try to auto-install it
        logger.warning("hf_xet NOT installed in HF CLI env — downloads will be SLOW without it")
        logger.info("Auto-installing hf_xet...")

        pip_path = self._hf_cli_pip or 'pip'
        try:
            install_result = subprocess.run(
                [pip_path, 'install', '-q', '--upgrade', 'hf_xet'],
                capture_output=True, text=True, timeout=120
            )
            if install_result.returncode == 0:
                self.has_hf_xet = True
                logger.info("✓ hf_xet auto-installed successfully — XET backend now active")
            else:
                # hf_transfer is deprecated by HuggingFace in favor of hf_xet;
                # no useful fallback is available anymore. Surface a clear error
                # so the user can fix the env (likely network/mirror issue).
                logger.error(
                    f"Failed to auto-install hf_xet (exit {install_result.returncode}): "
                    f"{install_result.stderr.strip()}"
                )
                logger.error(
                    "HuggingFace downloads will use slow default HTTP backend. "
                    "Install manually with: pip install --upgrade hf_xet"
                )
        except Exception as e:
            logger.error(f"Auto-install failed: {e}")
    
    def _register_process(self, process: subprocess.Popen) -> None:
        """Track a live subprocess so cancel() can kill it later."""
        with self._process_lock:
            self._active_procs.add(process)

    def _unregister_process(self, process: Optional[subprocess.Popen]) -> None:
        """Stop tracking a subprocess (called after wait/kill)."""
        if process is None:
            return
        with self._process_lock:
            self._active_procs.discard(process)

    def cancel(self):
        """Cancel ongoing downloads immediately (kills every live subprocess)."""
        self._cancelled = True
        with self._process_lock:
            active = list(self._active_procs)
            self._active_procs.clear()
        for proc in active:
            logger.warning("Killing active download process...")
            try:
                proc.kill()
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
    
    def _download_one_with_retry(self, item: Dict, label: str) -> bool:
        """Download a single item with retry logic. Returns True on success.

        Designed to run inside a ThreadPoolExecutor worker. All shared state
        access (failures list, active processes) is protected by locks.
        """
        url = item.get('url', '')
        target_dir = item.get('dir', '')
        filename = item.get('filename', '')

        domain = urlparse(url).netloc or url[:40]
        self._report_progress(
            f"{label} Baixando: {filename or 'arquivo'} ({domain})"
        )

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            if self._cancelled:
                return False

            ok, reason, stage = self._download_file(url, target_dir, filename)
            if ok:
                return True

            retryable = self._is_retryable_failure(stage, reason)
            if attempt < max_retries and retryable:
                logger.warning(
                    f"Download failed ({filename or url}) [{stage}: {reason}], "
                    f"retrying ({attempt}/{max_retries})..."
                )
                continue

            if not retryable:
                logger.error(
                    f"Non-retryable failure ({filename or url}) [{stage}: {reason}]"
                )
            else:
                logger.error(
                    f"Failed to download after {max_retries} attempts: {filename or url}"
                )
            self._record_failure(
                {'url': url, 'dir': target_dir, 'filename': filename},
                reason=reason,
                stage=stage
            )
            return False

        return False

    def download_all(self, downloads: List[Dict]) -> bool:
        """Download all files in the list, running up to parallel_downloads at once."""
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

        # Filter invalid items early
        valid_downloads = [d for d in downloads if d.get('url')]
        skipped = len(downloads) - len(valid_downloads)
        if skipped:
            logger.warning(f"Skipped {skipped} item(s) with no URL")

        total = len(valid_downloads)
        self._cancelled = False
        self.failures = []
        self.attempt_logs = []

        if total == 0:
            self._report_progress("No downloads requested", 0, 0)
            return True

        workers = max(1, min(self.parallel_downloads, total))
        self._report_progress(
            f"Starting download of {total} files (parallel={workers})", 0, total
        )

        success_count = 0
        completed = 0
        success_lock = threading.Lock()

        # Hard backstop against an infinite hang: if NO download completes for this
        # many seconds, every still-pending download is treated as stuck (e.g. a
        # silent hf_xet stream on a huge file that dodges the per-path stall check
        # because the read loop is blocked) — we abandon them, warn, and move on so
        # a single file can never freeze the whole install. Generous by default so
        # legitimately large/slow downloads still finish; tune via env.
        overall_stall = max(120, int(os.environ.get('DOWNLOAD_OVERALL_STALL_SECONDS', '1800') or '1800'))

        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='dl')
        future_to_idx = {}
        for idx, item in enumerate(valid_downloads, 1):
            label = f"[{idx}/{total}]"
            future = executor.submit(self._download_one_with_retry, item, label)
            future_to_idx[future] = idx

        pending = set(future_to_idx)
        aborted = False
        try:
            while pending:
                done_set, pending = wait(pending, timeout=overall_stall, return_when=FIRST_COMPLETED)
                if not done_set:
                    # Nothing finished within the window → the rest are stuck.
                    stuck = sorted(future_to_idx[f] for f in pending)
                    logger.error(
                        f"{len(pending)} download(s) sem NENHUMA conclusão há >{overall_stall}s — "
                        f"abandonando travado(s) e seguindo: {', '.join(str(i) for i in stuck)}"
                    )
                    for f in pending:
                        idx = future_to_idx[f]
                        item = valid_downloads[idx - 1]
                        self._record_failure(
                            {'url': item.get('url', ''), 'dir': item.get('dir', ''),
                             'filename': item.get('filename', '')},
                            reason=f'overall_stall_timeout_{overall_stall}s',
                            stage='stall'
                        )
                        with success_lock:
                            completed += 1
                            done = completed
                        self._report_progress(f"[{idx}/{total}] download travado — pulado", done, total)
                    self.cancel()  # kill active subprocesses so worker threads can exit
                    aborted = True
                    break
                for future in done_set:
                    idx = future_to_idx[future]
                    try:
                        ok = future.result()
                    except Exception as e:
                        logger.error(f"[{idx}/{total}] Download task raised: {e}")
                        ok = False
                    with success_lock:
                        if ok:
                            success_count += 1
                        completed += 1
                        done = completed
                    self._report_progress(
                        f"Progress: {done}/{total} concluded (ok={success_count})",
                        done,
                        total
                    )
        finally:
            # On abort, don't block on the stuck worker threads (cancel() killed
            # their subprocesses, so they'll unwind shortly on their own).
            executor.shutdown(wait=not aborted, cancel_futures=True)

        self._report_progress(
            f"Downloaded {success_count}/{total} files successfully", total, total
        )
        if self.failures:
            logger.error("Download failure summary:")
            with self._failures_lock:
                failures_snapshot = list(self.failures)
            for idx, failure in enumerate(failures_snapshot, 1):
                logger.error(
                    f"[{idx}] file={failure['filename']} dir={failure['dir']} "
                    f"stage={failure['stage']} reason={failure['reason']} url={failure['url']}"
                )
        return success_count == total
    
    def _download_file(self, url: str, target_dir: str, filename: str = '') -> Tuple[bool, str, str]:
        """Download a single file"""
        url = self._sanitize_source_url(url)
        is_civitai_source = 'civitai.com' in url
        hf_auth_failure = ''

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
            if self._is_invalid_existing_file(dest_path, url):
                logger.warning(
                    f"Found invalid existing file for {filename}, removing and retrying download"
                )
                try:
                    dest_path.unlink()
                except Exception as e:
                    logger.warning(f"Could not remove invalid file {dest_path}: {e}")
                    return False, f"invalid_existing_file_cleanup_failed: {e}", 'precheck'
            else:
                logger.info(f"✓ Already exists: {filename}")
                return True, 'already_exists', 'skip'

        # Validate Civitai token early for clearer errors
        if is_civitai_source and not self.civitai_token:
            reason = "CIVITAI_TOKEN is missing (required for Civitai downloads)"
            logger.error(reason)
            return False, reason, 'precheck'

        # HuggingFace priority: try HF CLI first (supports hf_xet for max speed).
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

            # Classify auth errors but DO NOT fail-fast yet — aria2c with
            # Authorization header can succeed where hf CLI / hf_hub fail
            # (e.g. token format issues, hf_xet JWT negotiation bugs).
            hf_auth_failure = self._classify_hf_auth_error(f"{reason} || {hub_reason}")
            if hf_auth_failure:
                if 'gated_model_not_accepted' in hf_auth_failure:
                    # Gated model not accepted: aria2c won't help either,
                    # the user needs to accept the license on huggingface.co.
                    return False, hf_auth_failure, 'auth'
                logger.warning(
                    f"HF CLI + Python API falharam com auth error ({hf_auth_failure}), "
                    f"tentando aria2c/wget como fallback..."
                )

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
                if self._is_invalid_existing_file(dest_path, url):
                    logger.warning(
                        f"Found invalid existing file for {filename}, removing and retrying download"
                    )
                    try:
                        dest_path.unlink()
                    except Exception as e:
                        logger.warning(f"Could not remove invalid file {dest_path}: {e}")
                        return False, f"invalid_existing_file_cleanup_failed: {e}", 'precheck'
                else:
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
            # If HF CLI had an auth error and aria2c/wget also failed,
            # report as auth (non-retryable) to avoid pointless retries.
            if hf_auth_failure:
                return False, hf_auth_failure, 'auth'
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
            if hf_auth_failure:
                return False, hf_auth_failure, 'auth'
            return False, reason, 'wget'
    
    def _add_civitai_token(self, url: str) -> str:
        """Add CivitAI API token to URL if needed"""
        authenticated_url = self._build_civitai_auth_url(url)
        if authenticated_url != url:
            logger.debug("Added CivitAI token to URL")
        return authenticated_url
    
    def _download_hf_direct(self, url: str, dest_dir: Path, filename: str) -> Tuple[bool, str]:
        """Download from HuggingFace via `hf download` (hf_xet backend).

        Progress and stall detection are driven by BYTES WRITTEN TO DISK (the
        *.incomplete file under <dest_dir>/.cache/huggingface/download/), NOT by
        scraping the CLI's stdout: the hf_xet/tqdm bar goes to stderr and is
        throttled/absent on a non-TTY pipe, which used to leave us blind. The disk
        signal is backend-agnostic (XET and HTTP write the same file) and version
        independent. The process is stopped with SIGINT so hf_xet aborts cleanly.
        """
        clean_url = url.split('?', 1)[0]
        match = re.search(r'huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)', clean_url)
        if not match:
            logger.warning(f"Could not parse HF URL, falling back to aria2c: {url}")
            return False, "invalid_hf_url_format"

        repo_id, branch, file_path = match.groups()
        file_path = unquote(file_path)
        if not filename:
            filename = Path(file_path).name

        xet_label = " [XET]" if self.has_hf_xet else ""
        logger.info(f"━━ HuggingFace{xet_label}: {repo_id}/{file_path} → {filename}")

        # Token goes via env (HF_TOKEN), never argv — avoids leaking it in `ps`.
        env = os.environ.copy()
        if self.hf_token:
            env['HF_TOKEN'] = self.hf_token
        if _should_enable_hf_xet_hp():
            env['HF_XET_HIGH_PERFORMANCE'] = '1'
        env['HF_XET_NUM_CONCURRENT_RANGE_GETS'] = os.environ.get('HF_XET_NUM_CONCURRENT_RANGE_GETS', '32')
        env['HF_HUB_DOWNLOAD_TIMEOUT'] = os.environ.get('HF_HUB_DOWNLOAD_TIMEOUT', '30')
        env.pop('HF_HUB_DISABLE_PROGRESS_BARS', None)

        cmd = [
            self.hf_cli_path, 'download', repo_id, file_path,
            '--revision', branch, '--local-dir', str(dest_dir),
        ]

        staging_dir = self._hf_staging_dir(dest_dir)
        final_path = dest_dir / file_path
        target = dest_dir / filename
        expected_size = self._hf_remote_size(repo_id, branch, file_path)

        process: Optional[subprocess.Popen] = None
        watchdog: Optional[threading.Thread] = None
        try:
            process = subprocess.Popen(
                cmd, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=0,
            )
            self._register_process(process)
            download_start_ts = time.monotonic()
            tail = deque(maxlen=12)
            stall_state = {'last_progress': time.monotonic(), 'killed': False, 'last_bytes': 0}
            watchdog = threading.Thread(
                target=self._run_disk_watchdog,
                args=(process, staging_dir, final_path, filename, expected_size, stall_state),
                daemon=True,
            )
            watchdog.start()

            # Drain stdout so the pipe never blocks; surface only error/auth lines.
            # Progress and stalls are handled by the disk watchdog (above).
            for line in self._read_lines_cr_aware(process.stdout):
                stripped = line.strip()
                if not stripped or stripped.startswith('|') or '%|' in stripped:
                    continue
                tail.append(stripped)
                low = stripped.lower()
                if any(kw in low for kw in (
                    'error', 'denied', 'forbidden', 'unauthorized', 'gated',
                    'requires', 'not found', 'failed', 'traceback', 'restricted',
                )):
                    logger.info(f"  [hf] {stripped}")
                else:
                    logger.debug(f"  [hf] {stripped}")

            process.wait()
            if watchdog is not None:
                watchdog.join(timeout=2)
            self._unregister_process(process)
            try:
                if process.stdout:
                    process.stdout.close()
            except Exception:
                pass

            killed = stall_state['killed']
            # Salvage: if the file actually landed (success, or killed right as it
            # finished), finalize it instead of forcing a needless fallback.
            if process.returncode == 0 or final_path.exists() or target.exists():
                ok, reason = self._finalize_hf_file(
                    dest_dir, file_path, filename, target, final_path, expected_size
                )
                if ok:
                    elapsed = time.monotonic() - download_start_ts
                    logger.info(
                        f"✓ Downloaded from HF{xet_label}: {filename}"
                        f"{self._speed_suffix(target, elapsed)}"
                    )
                    return True, ""
                if process.returncode == 0 and not killed:
                    return False, reason

            if killed:
                r = f"hf_cli_stall_timeout_{self.aria2_stall_timeout_seconds}s"
                return False, (f"{r} | tail: {' || '.join(tail)}" if tail else r)
            logger.error(f"HF download failed with code {process.returncode}")
            r = f"hf_cli_exit_{process.returncode}"
            return False, (f"{r} | tail: {' || '.join(tail)}" if tail else r)

        except Exception as e:
            if process is not None:
                try:
                    self._terminate_process(process, grace=2)
                except Exception:
                    pass
            self._unregister_process(process)
            logger.error(f"HF download failed: {e}")
            return False, str(e)

    def _download_hf_via_python(self, url: str, dest_dir: Path, filename: str) -> Tuple[bool, str]:
        """Fallback: huggingface_hub in a SEPARATE, KILLABLE subprocess with the XET
        backend DISABLED (HF_HUB_DISABLE_XET=1) — the documented workaround for
        hf_xet transfer stalls (xet-core#789). Running it as a subprocess (not an
        in-process thread) is what lets cancel()/the stall watchdog actually stop it;
        the old in-process thread was unkillable and kept downloading after "timeout".
        Progress/stall use the same on-disk byte signal as the primary path."""
        clean_url = url.split('?', 1)[0]
        match = re.search(r'huggingface\.co/([^/]+/[^/]+)/resolve/([^/]+)/(.+)', clean_url)
        if not match:
            return False, "invalid_hf_url_for_python_fallback"
        repo_id, branch, file_path = match.groups()
        file_path = unquote(file_path)
        logger.info(f"Fallback: huggingface_hub sem XET (HTTP) → {repo_id}/{file_path}")

        env = os.environ.copy()
        env['HF_HUB_DISABLE_XET'] = '1'
        env.pop('HF_XET_HIGH_PERFORMANCE', None)
        env.pop('HF_HUB_DISABLE_PROGRESS_BARS', None)
        if self.hf_token:
            env['HF_TOKEN'] = self.hf_token
        env['HF_HUB_DOWNLOAD_TIMEOUT'] = os.environ.get('HF_HUB_DOWNLOAD_TIMEOUT', '30')

        script = (
            "import sys\n"
            "from huggingface_hub import hf_hub_download\n"
            "p = hf_hub_download(repo_id=sys.argv[1], filename=sys.argv[2], "
            "revision=sys.argv[3], local_dir=sys.argv[4])\n"
            "print('DLPATH=' + p)\n"
        )
        cmd = [self._hf_python(), '-c', script, repo_id, file_path, branch, str(dest_dir)]

        staging_dir = self._hf_staging_dir(dest_dir)
        final_path = dest_dir / file_path
        target = dest_dir / filename
        expected_size = self._hf_remote_size(repo_id, branch, file_path)

        process: Optional[subprocess.Popen] = None
        watchdog: Optional[threading.Thread] = None
        try:
            process = subprocess.Popen(
                cmd, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, bufsize=0,
            )
            self._register_process(process)
            start = time.monotonic()
            tail = deque(maxlen=20)
            stall_state = {'last_progress': time.monotonic(), 'killed': False, 'last_bytes': 0}
            watchdog = threading.Thread(
                target=self._run_disk_watchdog,
                args=(process, staging_dir, final_path, filename, expected_size, stall_state),
                daemon=True,
            )
            watchdog.start()

            for line in self._read_lines_cr_aware(process.stdout):
                s = line.strip()
                if s:
                    tail.append(s)

            process.wait()
            if watchdog is not None:
                watchdog.join(timeout=2)
            self._unregister_process(process)
            try:
                if process.stdout:
                    process.stdout.close()
            except Exception:
                pass

            if process.returncode == 0 or final_path.exists() or target.exists():
                ok, reason = self._finalize_hf_file(
                    dest_dir, file_path, filename, target, final_path, expected_size
                )
                if ok:
                    logger.info(
                        f"✓ Downloaded (huggingface_hub/HTTP): {filename}"
                        f"{self._speed_suffix(target, time.monotonic() - start)}"
                    )
                    return True, ""
                if process.returncode == 0 and not stall_state['killed']:
                    return False, reason

            if stall_state['killed']:
                return False, f"hf_python_stall_timeout_{self.aria2_stall_timeout_seconds}s"
            r = f"hf_python_exit_{process.returncode}"
            # keep the tail (GatedRepoError / 403 traceback) so auth classification works
            return False, (f"{r} | tail: {' || '.join(list(tail)[-6:])}" if tail else r)

        except Exception as e:
            if process is not None:
                try:
                    self._terminate_process(process, grace=2)
                except Exception:
                    pass
            self._unregister_process(process)
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

        # HF uses an LFS bridge with stricter rate limits; use fewer connections
        # per file to avoid 429 responses. Civitai/direct CDNs tolerate 16.
        is_hf = 'huggingface.co' in url
        connections = self.aria2_hf_connections if is_hf else self.aria2_connections
        splits = connections if is_hf else self.aria2_splits

        # Bound retries/timeouts so a flaky link can't loop internally for minutes.
        # The in-loop stall check (kept below) is the primary stall guard; these
        # caps stop aria2c from silently retrying forever on a dead connection.
        connect_to = max(30, min(60, self.aria2_stall_timeout_seconds))
        read_to = max(30, self.aria2_stall_timeout_seconds)
        cmd = [
            'aria2c',
            '-c',  # Continue download
            '-x', connections,  # connections per server
            '-s', splits,  # split parts
            f'--max-connection-per-server={connections}',
            f'--min-split-size={self.aria2_min_split_size}',
            '--file-allocation=none',
            '--console-log-level=notice',
            '--summary-interval=1',
            '--max-tries=2',
            '--retry-wait=3',
            f'--connect-timeout={connect_to}',
            f'--timeout={read_to}',
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

        process: Optional[subprocess.Popen] = None
        try:
            # Use binary mode + unbuffered for CR-aware progress reading
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0
            )
            self._register_process(process)

            tail = deque(maxlen=12)
            last_progress_ts = time.monotonic()
            last_percent = 0.0
            last_logged_pct = -10
            last_log_ts = time.monotonic()
            stall_warn_seconds = max(30, self.aria2_stall_timeout_seconds // 4)
            stall_warned = False
            last_speed_zero_warned = False

            for line in self._read_lines_cr_aware(process.stdout):
                stripped = line.strip()
                if not stripped:
                    continue
                tail.append(stripped)

                # aria2c output: [#2089b0 27MiB/91MiB(29%) CN:8 DL:110MiB ETA:1s]
                match = re.search(r'\(([\d\.]+)%\).*DL:([\d\.]+\w+)(?:.*?ETA:([\d\w]+))?', stripped)
                if match:
                    percent = float(match.group(1))
                    speed = match.group(2) + "/s"
                    eta = match.group(3) or ""
                    now = time.monotonic()
                    speed_is_zero = match.group(2).startswith("0B")

                    if HAS_WEBSOCKET:
                        send_download_progress(filename, percent, speed, eta)

                    # Warn when speed drops to zero
                    if speed_is_zero and not last_speed_zero_warned:
                        logger.warning(f"  ⚠ {filename}: velocidade caiu para 0 — aguardando conexão...")
                        last_speed_zero_warned = True
                    elif not speed_is_zero:
                        last_speed_zero_warned = False
                        stall_warned = False

                    # Log progress periodically (every ~10% or every 30s)
                    if percent - last_logged_pct >= 10 or (now - last_log_ts) > 30:
                        eta_str = f" ETA {eta}" if eta else ""
                        logger.info(f"  ↓ {filename}: {percent:.0f}% @ {speed}{eta_str}")
                        last_logged_pct = percent
                        last_log_ts = now

                    # Track forward progress
                    if percent > (last_percent + 0.01):
                        last_percent = percent
                        last_progress_ts = now
                    elif not speed_is_zero:
                        last_progress_ts = now
                else:
                    # Non-progress lines: promote important ones to info
                    if not stripped.startswith('[#') and not stripped.startswith('***'):
                        line_lower = stripped.lower()
                        if any(kw in line_lower for kw in ('error', 'download', 'redirect', 'connect', 'warning', 'exception')):
                            logger.info(f"  [aria2c] {stripped}")
                        else:
                            logger.debug(f"  [aria2c] {stripped}")

                # Guard against stalled aria2c sessions (e.g., DL:0B forever)
                now = time.monotonic()
                stall_elapsed = now - last_progress_ts
                if self.aria2_stall_timeout_seconds > 0 and process.poll() is None:
                    if not stall_warned and stall_elapsed > stall_warn_seconds:
                        logger.warning(
                            f"  ⚠ {filename}: sem progresso há {stall_elapsed:.0f}s "
                            f"(timeout em {self.aria2_stall_timeout_seconds}s) — "
                            f"internet lenta ou travado?"
                        )
                        stall_warned = True
                    if stall_elapsed > self.aria2_stall_timeout_seconds:
                        logger.error(
                            f"aria2c stall timeout para {filename or url} "
                            f"(sem progresso por {self.aria2_stall_timeout_seconds}s), encerrando"
                        )
                        process.kill()
                        process.wait()
                        self._unregister_process(process)
                        reason = f"aria2c_stall_timeout_{self.aria2_stall_timeout_seconds}s"
                        if tail:
                            reason = f"{reason} | tail: {' || '.join(tail)}"
                        return False, reason

            process.wait()
            self._unregister_process(process)

            if process.returncode == 0:
                landed, vreason = self._verify_download_landed(
                    dest_dir, filename, use_content_disposition
                )
                if landed:
                    logger.info(f"✓ Downloaded: {filename}")
                    return True, ""
                logger.error(f"aria2c exit 0 mas arquivo não confirmado: {vreason}")
                return False, f"aria2c_{vreason}"
            else:
                logger.error(f"aria2c failed with code {process.returncode}")
                reason = f"aria2c_exit_{process.returncode}"
                if tail:
                    reason = f"{reason} | tail: {' || '.join(tail)}"
                return False, reason

        except Exception as e:
            if process is not None:
                try:
                    process.kill()
                    process.wait()
                except Exception:
                    pass
            self._unregister_process(process)
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
        # --timeout sets dns/connect/read timeouts: wget aborts a SILENTLY stalled
        # read after this many seconds (its progress output stops on a dead TCP
        # connection, so our in-loop stall check alone can't see it). --tries caps
        # wget's own internal retries (default 20) so a flaky link can't loop for
        # many minutes past the per-path budget.
        wget_to = max(30, self.aria2_stall_timeout_seconds)
        cmd = [
            'wget',
            '--progress=bar:force',
            '-c',  # Continue
            '--content-disposition',
            f'--timeout={wget_to}',
            '--tries=2',
            '--waitretry=3',
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

        process: Optional[subprocess.Popen] = None
        try:
            # Use binary mode + unbuffered for CR-aware progress reading
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0
            )
            self._register_process(process)

            tail = deque(maxlen=12)
            last_logged_pct = -10
            last_log_ts = time.monotonic()
            last_progress_ts = time.monotonic()
            last_percent = 0.0
            stall_warned = False

            for line in self._read_lines_cr_aware(process.stdout):
                stripped = line.strip()
                if not stripped:
                    continue
                tail.append(stripped)

                # wget output:  52% [============>           ] 14,833,969  21.3MB/s  eta 1s
                match = re.search(r'(\d+)%.*?([\d\.]+[KMG]B/s).*?eta\s+([\w\d]+)', stripped)
                if match:
                    percent = float(match.group(1))
                    speed = match.group(2)
                    eta = match.group(3)
                    now = time.monotonic()

                    if HAS_WEBSOCKET:
                        send_download_progress(dest_path.name, percent, speed, eta)

                    # Log every ~10% or every 30s
                    if percent - last_logged_pct >= 10 or (now - last_log_ts) > 30:
                        logger.info(f"  ↓ {dest_path.name}: {percent:.0f}% @ {speed} ETA {eta}")
                        last_logged_pct = percent
                        last_log_ts = now

                    # Track forward progress
                    if percent > (last_percent + 0.01):
                        last_percent = percent
                        last_progress_ts = now
                        stall_warned = False
                else:
                    # Promote important wget lines to info
                    line_lower = stripped.lower()
                    if any(kw in line_lower for kw in ('error', 'failed', 'redirect', 'location', 'saving')):
                        logger.info(f"  [wget] {stripped}")
                    elif stripped and not stripped.startswith('%'):
                        logger.debug(f"  [wget] {stripped}")

                # Stall timeout for wget
                now = time.monotonic()
                stall_elapsed = now - last_progress_ts
                if self.aria2_stall_timeout_seconds > 0 and process.poll() is None:
                    stall_warn_seconds = max(30, self.aria2_stall_timeout_seconds // 4)
                    if not stall_warned and stall_elapsed > stall_warn_seconds:
                        logger.warning(
                            f"  ⚠ {dest_path.name}: sem progresso há {stall_elapsed:.0f}s "
                            f"(timeout em {self.aria2_stall_timeout_seconds}s)"
                        )
                        stall_warned = True
                    if stall_elapsed > self.aria2_stall_timeout_seconds:
                        logger.error(
                            f"wget stall timeout para {dest_path.name} "
                            f"(sem progresso por {self.aria2_stall_timeout_seconds}s), encerrando"
                        )
                        process.kill()
                        process.wait()
                        self._unregister_process(process)
                        reason = f"wget_stall_timeout_{self.aria2_stall_timeout_seconds}s"
                        if tail:
                            reason = f"{reason} | tail: {' || '.join(tail)}"
                        return False, reason

            process.wait()
            self._unregister_process(process)

            if process.returncode == 0:
                landed, vreason = self._verify_download_landed(
                    dest_path.parent, dest_path.name, use_content_disposition
                )
                if landed:
                    logger.info(f"✓ Downloaded: {dest_path.name}")
                    return True, ""
                logger.error(f"wget exit 0 mas arquivo não confirmado: {vreason}")
                return False, f"wget_{vreason}"
            else:
                logger.error(f"wget failed with code {process.returncode}")
                reason = f"wget_exit_{process.returncode}"
                if tail:
                    reason = f"{reason} | tail: {' || '.join(tail)}"
                return False, reason

        except Exception as e:
            if process is not None:
                try:
                    process.kill()
                    process.wait()
                except Exception:
                    pass
            self._unregister_process(process)
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
