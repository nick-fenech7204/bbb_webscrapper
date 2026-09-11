"""
Category directory: loads BBB's industry/category taxonomy from a local JSON
file and provides lookup/search over it.

The taxonomy itself is data, not code -- it lives at
data/reference/categories.json (see scripts/fetch_categories.py for how to
populate it from BBB). This module just knows how to load and query it.
"""
from __future__ import annotations

import json
from pathlib import Path

from bbb_scraper.config import settings
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.reference.models import Category

logger = get_logger(__name__)


class CategoryDirectory:
    def __init__(self, categories: list[Category]):
        self._categories = categories
        self._by_id = {c.id: c for c in categories}
        self._by_slug = {c.slug: c for c in categories if c.slug}

    @classmethod
    def load(cls, path: Path | str | None = None) -> CategoryDirectory:
        path = Path(path) if path else settings.categories_file
        if not path.exists():
            logger.warning(
                "Category reference file not found at %s -- run "
                "scripts/fetch_categories.py (or populate it manually) to enable "
                "category search. Falling back to an empty directory.",
                path,
            )
            return cls([])

        raw = json.loads(path.read_text(encoding="utf-8"))
        categories = [Category(**item) for item in raw]
        logger.info("Loaded %d categories from %s", len(categories), path)
        return cls(categories)

    def all(self) -> list[Category]:
        return list(self._categories)

    def get(self, id_or_slug: str) -> Category | None:
        return self._by_id.get(id_or_slug) or self._by_slug.get(id_or_slug)

    def search(self, text: str) -> list[Category]:
        """Case-insensitive lookup: exact name/slug match first, else
        substring match against the name. Returns [] for no matches, and may
        return multiple candidates the caller should disambiguate.
        """
        text_lower = text.strip().lower()
        if not text_lower:
            return []

        exact = [
            c for c in self._categories
            if c.name.lower() == text_lower or c.slug == text_lower or c.id == text
        ]
        if exact:
            return exact

        return [c for c in self._categories if text_lower in c.name.lower()]
