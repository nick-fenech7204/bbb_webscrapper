"""No-op sink: logs record count and discards. Useful for dry runs and tests."""
from __future__ import annotations

from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.pipeline.base import Sink

logger = get_logger(__name__)


class NullSink(Sink):
    name = "null"

    def load(self, records: list[dict[str, Any]]) -> int:
        logger.info("NullSink received %d record(s) (dry run, nothing persisted)", len(records))
        return len(records)
