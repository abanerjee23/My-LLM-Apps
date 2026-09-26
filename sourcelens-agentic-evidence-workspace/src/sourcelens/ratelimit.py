from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    """Sliding one-hour window per authenticated user and action.

    In-process state is enough while Cloud Run runs a single instance (--max-instances=1);
    a shared store is needed before scaling out.
    """

    def __init__(self, window_seconds: int = 3600):
        self.window = window_seconds
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, user_id: str, action: str, limit: int) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[(user_id, action)]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True
