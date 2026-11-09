"""Rate limiter sederhana berbasis sliding window per IP."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Membatasi jumlah request per jendela waktu, dihitung per client IP."""

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Catat satu hit untuk key, kembalikan True jika masih dalam kuota."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            # Buang hit yang sudah keluar dari jendela.
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str) -> float:
        """Detik yang harus ditunggu sebelum request berikutnya diizinkan."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0.0
            return max(0.0, self.window - (now - q[0]))
