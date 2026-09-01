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
        batch_fieldnames = sorted({key for record in records for key in record.keys()})

        existing_fieldnames = self._read_header()

        if existing_fieldnames is None:
            # No file yet (or it's empty) -- write fresh.
            with self.path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=batch_fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(records)

        elif set(batch_fieldnames) <= set(existing_fieldnames):
            # This batch's columns are already covered by the file's header
            # (order doesn't need to match -- DictWriter writes by fieldname,
            # not position, and fills any fieldname missing from a given row
            # with '' via its default restval). Safe to append as-is.
            with self.path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=existing_fieldnames, extrasaction="ignore")
                writer.writerows(records)

        else:
            # This batch has columns the file doesn't -- appending with a
            # mismatched fieldname list would silently shift values into the
            # wrong columns (DictWriter writes column N of *this* fieldnames
            # list, not column N of whatever's on disk). Rewrite the whole
            # file with the union header instead; existing rows keep their
            # values, new columns backfill '' for them via restval.
            union_fieldnames = sorted(set(existing_fieldnames) | set(batch_fieldnames))
            with self.path.open("r", newline="", encoding="utf-8") as f:
                existing_rows = list(csv.DictReader(f))
            logger.info(
                "CSVSink: new fields %s not in existing header -- rewriting %s "
                "with a %d-column union header (was %d)",
                sorted(set(batch_fieldnames) - set(existing_fieldnames)),
                self.path, len(union_fieldnames), len(existing_fieldnames),
            )
            with self.path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=union_fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(existing_rows)
                writer.writerows(records)

        logger.info("CSVSink wrote %d record(s) to %s", len(records), self.path)
        return len(records)

    def _read_header(self) -> list[str] | None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        with self.path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
        return header
