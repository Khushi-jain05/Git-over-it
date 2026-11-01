"""Token bucket on requests/min and tokens/min (docs/07 §2)."""

from __future__ import annotations

import threading
import time


def estimate_tokens(text: str) -> int:
    return len(text) // 4


class RateLimiter:
    def __init__(self, rpm: int, tpm: int, clock=time.monotonic, sleep=time.sleep):
        self.rpm, self.tpm, self.clock, self.sleep = rpm, tpm, clock, sleep
        self.req, self.tok = float(rpm), float(tpm)
        self.last = clock()
        self.lock = threading.Lock()

    def _refill(self) -> None:
        now = self.clock()
        dt, self.last = now - self.last, now
        self.req = min(self.rpm, self.req + dt * self.rpm / 60)
        self.tok = min(self.tpm, self.tok + dt * self.tpm / 60)

    def acquire(self, tokens: int) -> None:
        tokens = min(tokens, self.tpm)  # a single oversized request waits for a full bucket, not forever
        with self.lock:
            while True:
                self._refill()
                if self.req >= 1 and self.tok >= tokens:
                    self.req -= 1
                    self.tok -= tokens
                    return
                need = max((1 - self.req) * 60 / self.rpm, (tokens - self.tok) * 60 / self.tpm, 0.05)
                self.sleep(need)
