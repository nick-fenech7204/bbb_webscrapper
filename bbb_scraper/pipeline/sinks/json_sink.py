"""JSON Lines sink -- a good handoff format for "another internal application"
or an enrichment step that wants to stream/consume records independently."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.pipeline.base import Sink

logger = get_logger(__name__)


class JSONSink(Sink):
    name = "json"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, default=str) + "\n")
        logger.info("JSONSink wrote %d record(s) to %s", len(records), self.path)
        return len(records)
