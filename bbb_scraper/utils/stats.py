"""
Lightweight run-level counters for observability.

Not a metrics system -- just a thread-unsafe counter bag that gets logged at
the end of a scrape/ETL run so you can see, at a glance, how many requests
went out, how many failed, how many pages parsed cleanly, etc. Swap this out
for prometheus_client / statsd later if needed without touching call sites
much, since everything goes through .incr().
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class RunStats:
    counters: Counter = field(default_factory=Counter)

    def incr(self, key: str, n: int = 1) -> None:
        self.counters[key] += n

    def as_dict(self) -> dict[str, int]:
        return dict(self.counters)

    def summary_line(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in sorted(self.counters.items()))


# Common counter keys, kept here so modules agree on naming instead of
# inventing slightly different strings.
REQUESTS_SENT = "requests_sent"
REQUESTS_FAILED = "requests_failed"
REQUESTS_RETRIED = "requests_retried"
PAGES_PARSED = "pages_parsed"
PARSE_FAILURES = "parse_failures"
RECORDS_EXTRACTED = "records_extracted"
RECORDS_DEDUPED = "records_deduped"
RECORDS_LOADED = "records_loaded"
