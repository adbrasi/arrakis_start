#!/usr/bin/env python3
"""Killable Hugging Face XET worker with machine-readable progress events."""

import json
import sys
import threading
import time
from typing import Optional


EVENT_PREFIX = "ARRAKIS_XET_PROGRESS "
COMPAT_PREFIX = "ARRAKIS_XET_COMPAT_FALLBACK "


class ProgressReporter:
    """Minimal tqdm-compatible reporter used by huggingface_hub/hf_xet."""

    emit_interval = 1.0

    def __init__(self, *args, **kwargs):
        self.desc = str(kwargs.get('desc') or 'download')
        self.total = int(kwargs.get('total') or 0)
        self.n = int(kwargs.get('initial') or 0)
        self._started_at = time.monotonic()
        self._last_sample_at = self._started_at
        self._last_sample_n = self.n
        self._last_emit_at = 0.0
        self._speed = 0.0
        self.format_dict = {'rate': None}
        self._lock = threading.RLock()

    def _emit(self, force: bool = False) -> None:
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_emit_at < self.emit_interval:
                return

            elapsed = now - self._last_sample_at
            delta = self.n - self._last_sample_n
            if elapsed > 0 and delta > 0:
                self._speed = delta / elapsed
                self.format_dict['rate'] = self._speed
            self._last_sample_at = now
            self._last_sample_n = self.n
            self._last_emit_at = now

            print(
                EVENT_PREFIX
                + json.dumps(
                    {
                        'phase': self.desc,
                        'current': self.n,
                        'total': self.total,
                        'speed': self._speed,
                    },
                    separators=(',', ':'),
                ),
                flush=True,
            )

    def update(self, amount: Optional[float] = 1) -> None:
        with self._lock:
            self.n += int(amount or 0)
            self._emit()

    def set_postfix_str(self, *_args, **_kwargs) -> None:
        pass

    def set_description(self, desc: str, refresh: bool = True) -> None:
        with self._lock:
            self.desc = str(desc)
            if refresh:
                self.refresh()

    def refresh(self, *_args, **_kwargs) -> None:
        self._emit()

    def close(self) -> None:
        self._emit(force=True)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def download_with_compat_fallback(download_fn, **kwargs):
    """Keep XET active if a future Hub release changes the progress API."""
    try:
        return download_fn(**kwargs, tqdm_class=ProgressReporter)
    except (TypeError, AttributeError) as exc:
        print(
            f"{COMPAT_PREFIX}{type(exc).__name__}: {exc}",
            flush=True,
        )
        return download_fn(**kwargs)


def main() -> int:
    if len(sys.argv) != 5:
        print(
            "usage: hf_xet_worker.py REPO_ID FILE_PATH REVISION LOCAL_DIR",
            file=sys.stderr,
        )
        return 2

    repo_id, file_path, revision, local_dir = sys.argv[1:]
    from huggingface_hub import hf_hub_download

    downloaded = download_with_compat_fallback(
        hf_hub_download,
        repo_id=repo_id,
        filename=file_path,
        revision=revision,
        local_dir=local_dir,
    )
    print(f"DLPATH={downloaded}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
