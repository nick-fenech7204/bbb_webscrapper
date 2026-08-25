"""CSV sink -- simplest possible durable output, and also what a Power BI
"import from CSV" data source would point at directly."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.pipeline.base import Sink

logger = get_logger(__name__)


class CSVSink(Sink):
    name = "csv"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0

        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = self.path.exists() and self.path.stat().st_size > 0

        fieldnames = sorted({key for record in records for key in record.keys()})
        with self.path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerows(records)

        logger.info("CSVSink wrote %d record(s) to %s", len(records), self.path)
        return len(records)
