"""
Metro directory: loads the curated metro-coverage dropdown list from a local
JSON file and provides lookup over it.

Deliberately small and hand-picked (data/reference/metros.json) -- not every
place in us_cities.csv, just the major metros worth offering as a "sweep
this whole metro" option. This module just knows how to load and query it,
same pattern as categories.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from bbb_scraper.config import settings
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.reference.models import Metro

logger = get_logger(__name__)


class MetroDirectory:
    def __init__(self, metros: list[Metro]):
        self._metros = metros
        self._by_id = {m.id: m for m in metros}

    @classmethod
    def load(cls, path: Path | str | None = None) -> MetroDirectory:
        path = Path(path) if path else settings.metros_file
        if not path.exists():
            logger.warning(
                "Metro reference file not found at %s -- metro-coverage search "
                "unavailable. Falling back to an empty directory.",
                path,
            )
            return cls([])

        raw = json.loads(path.read_text(encoding="utf-8"))
        metros = [Metro(**item) for item in raw]
        logger.info("Loaded %d metros from %s", len(metros), path)
        return cls(metros)

    def all(self) -> list[Metro]:
        return list(self._metros)

    def get(self, id: str) -> Metro | None:
        return self._by_id.get(id)
