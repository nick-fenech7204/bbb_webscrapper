"""HTTP sink -- POSTs batches of records to a webhook/API. Stand-in for
"an enrichment API" or "another internal application" as a destination."""
from __future__ import annotations

from typing import Any

import requests

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.pipeline.base import Sink

logger = get_logger(__name__)


class HTTPSink(Sink):
    name = "http"

    def __init__(self, url: str, batch_size: int = 100, timeout_seconds: float = 20.0):
        if not url:
            raise ValueError("HTTPSink requires a url (see HTTP_SINK_URL in .env)")
        self.url = url
        self.batch_size = batch_size
        self.timeout_seconds = timeout_seconds

    def load(self, records: list[dict[str, Any]]) -> int:
        total = 0
        for i in range(0, len(records), self.batch_size):
            batch = records[i : i + self.batch_size]
            response = requests.post(self.url, json=batch, timeout=self.timeout_seconds)
            response.raise_for_status()
            total += len(batch)
        logger.info("HTTPSink posted %d record(s) to %s", total, self.url)
        return total
