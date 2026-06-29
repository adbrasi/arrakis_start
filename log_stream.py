#!/usr/bin/env python3
"""In-process log capture + feed for Server-Sent Events.

A small thread-safe ring buffer captures formatted log records from the root
logger so the web UI can stream the live install / download / custom-node logs
(the same rich output seen in the terminal) over an SSE endpoint. A Condition
wakes SSE streamers as soon as new lines arrive. Dependency-free and tiny.
"""

import logging
import threading
from collections import deque
from typing import List, Tuple

_MAX_LINES = 3000


class LogBuffer:
    """Bounded, monotonically-sequenced, thread-safe line buffer."""

    def __init__(self, maxlen: int = _MAX_LINES):
        self._buf = deque(maxlen=maxlen)
        self._seq = 0
        self._cond = threading.Condition()

    def append(self, line: str) -> None:
        with self._cond:
            self._seq += 1
            self._buf.append((self._seq, line))
            self._cond.notify_all()

    def since(self, after_id: int) -> List[Tuple[int, str]]:
        """Lines with id > after_id, oldest first (non-blocking)."""
        with self._cond:
            return [(i, l) for (i, l) in self._buf if i > after_id]

    def wait_since(self, after_id: int, timeout: float) -> List[Tuple[int, str]]:
        """Block up to `timeout`s for lines newer than after_id, then return them."""
        with self._cond:
            if self._seq <= after_id:
                self._cond.wait(timeout=timeout)
            return [(i, l) for (i, l) in self._buf if i > after_id]


_buffer = LogBuffer()


def get_log_buffer() -> LogBuffer:
    return _buffer


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _buffer.append(self.format(record))
        except Exception:
            pass


_installed = False
_install_lock = threading.Lock()


def install_log_capture(level: int = logging.INFO) -> None:
    """Attach the capture handler to the root logger. Idempotent."""
    global _installed
    with _install_lock:
        if _installed:
            return
        handler = _BufferHandler()
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level == logging.NOTSET or root.level > level:
            root.setLevel(level)
        _installed = True
