"""Simple randomized-delay rate limiter (politeness delay between requests).

Not a token bucket / QPS limiter -- for scraping, a randomized sleep between
min/max is usually what you want: it's simple and avoids a robotic, fixed
cadence. Swap for something fancier if you need true concurrency control.
"""
from __future__ import annotations

import random
import time


class RateLimiter:
    def __init__(self, min_delay_seconds: float, max_delay_seconds: float):
        if min_delay_seconds < 0 or max_delay_seconds < min_delay_seconds:
            raise ValueError("require 0 <= min_delay_seconds <= max_delay_seconds")
        self.min_delay = min_delay_seconds
        self.max_delay = max_delay_seconds

    def wait(self) -> float:
        delay = random.uniform(self.min_delay, self.max_delay)
        if delay > 0:
            time.sleep(delay)
        return delay
