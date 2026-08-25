"""Excel sink. Requires the `excel` extra (`pip install -e ".[excel]"`) --
imported lazily so the core package doesn't require pandas/openpyxl."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.pipeline.base import Sink

logger = get_logger(__name__)


class ExcelSink(Sink):
    name = "excel"

    def __init__(self, path: Path | str, sheet_name: str = "businesses"):
        self.path = Path(path)
        self.sheet_name = sheet_name

    def load(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "ExcelSink requires the 'excel' extra: pip install -e \".[excel]\""
            ) from exc

        self.path.parent.mkdir(parents=True, exist_ok=True)

        new_df = pd.DataFrame(records)
        if self.path.exists():
            existing_df = pd.read_excel(self.path, sheet_name=self.sheet_name)
            combined = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined = new_df

        combined.to_excel(self.path, sheet_name=self.sheet_name, index=False)
        logger.info("ExcelSink wrote %d record(s) to %s", len(records), self.path)
        return len(records)
