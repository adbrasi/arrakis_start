#!/usr/bin/env python3
"""In-process progress registry surfaced through ``/api/status``.

This replaces ``websocket_server.py``, which was never started: nothing called
``start_websocket_server()``, the frontend had no WebSocket client, and every
``send_download_progress`` call pushed a dict onto a queue that had no consumer.

Polling ``/api/status`` is the correct channel here, not a second socket:

* the web UI already polls it every 5 seconds;
* cloud hosts (VastAI/RunPod) expose only the single web port, so a WebSocket
  listener on another port could never reach the browser through their proxy.

Writers (downloader, installer) call the ``set_*`` helpers from any thread.
The HTTP handler calls :func:`snapshot` to render the current state.
"""

import threading
import time
from typing import Any, Dict, Optional

# Finished entries kept for display after completion.
_MAX_RECENT = 12

_lock = threading.RLock()

_downloads: Dict[str, Dict[str, Any]] = {}
_recent: list = []
_stage: Dict[str, Any] = {'stage': 'idle', 'detail': ''}
_counts: Dict[str, int] = {'done': 0, 'total': 0}


def reset() -> None:
    """Clear all progress state. Called when a new install run starts."""
    with _lock:
        _downloads.clear()
        _recent.clear()
        _stage.update({'stage': 'idle', 'detail': ''})
        _counts.update({'done': 0, 'total': 0})


def set_stage(stage: str, detail: str = "") -> None:
    """Record the coarse install phase (models, nodes, pip, runtime...)."""
    with _lock:
        _stage['stage'] = str(stage)
        _stage['detail'] = str(detail)


def set_counts(done: int, total: int) -> None:
    """Record how many queue items have concluded."""
    with _lock:
        _counts['done'] = max(0, int(done))
        _counts['total'] = max(0, int(total))


def set_download(filename: str, current: int, total: Optional[int],
                 speed_bps: float = 0.0, backend: str = "") -> None:
    """Record byte-level progress for one file.

    ``total`` may be ``None`` when the backend cannot report a size; consumers
    must render bytes-so-far instead of inventing a percentage.
    """
    if not filename:
        return
    with _lock:
        entry = _downloads.setdefault(filename, {'filename': filename})
        entry['current'] = max(0, int(current or 0))
        entry['total'] = int(total) if total and total > 0 else None
        entry['speed_bps'] = max(0.0, float(speed_bps or 0.0))
        entry['backend'] = str(backend or entry.get('backend') or '')
        entry['updated_at'] = time.time()


def finish_download(filename: str, ok: bool, reason: str = "") -> None:
    """Move a file out of the active set into the recent-history list."""
    if not filename:
        return
    with _lock:
        entry = _downloads.pop(filename, {'filename': filename})
        entry['ok'] = bool(ok)
        entry['reason'] = str(reason or '')
        entry['finished_at'] = time.time()
        _recent.append(entry)
        del _recent[:-_MAX_RECENT]


def snapshot() -> Dict[str, Any]:
    """Return a deep-enough copy for JSON serialization."""
    with _lock:
        return {
            'stage': _stage['stage'],
            'detail': _stage['detail'],
            'done': _counts['done'],
            'total': _counts['total'],
            'active': [dict(e) for e in _downloads.values()],
            'recent': [dict(e) for e in _recent],
        }
