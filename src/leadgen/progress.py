"""Thread-safe progress counters shared between a running job and the web interface."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict


class Cancelled(Exception):
    pass


class Progress:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cancel_requested = False
        self.data: Dict[str, Any] = {
            "status": "starting", "stage": "", "message": "", "institutions_total": 0,
            "institutions_done": 0, "current": "", "pages": 0, "lookups": 0, "contacts": 0,
            "emails_published": 0, "started_at": time.time(), "finished_at": None,
            "file": None, "error": None,
        }

    def update(self, **kw: Any) -> None:
        with self._lock:
            self.data.update(kw)

    def incr(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.data[key] = self.data.get(key, 0) + n

    def check(self) -> None:
        if self.cancel_requested:
            raise Cancelled()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            d = dict(self.data)
        end = d["finished_at"] or time.time()
        d["elapsed"] = int(end - d["started_at"])
        return d


class NullProgress(Progress):
    """Used by the command line: counts nothing visible, never cancels."""
