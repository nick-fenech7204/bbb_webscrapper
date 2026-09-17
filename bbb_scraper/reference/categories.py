"""
Category directory: loads an industry/category taxonomy from a local JSON
file and provides lookup/search over it. Generic over which taxonomy --
BBB's own (data/reference/categories.json, see scripts/fetch_categories.py)
and Angi's companylist taxonomy (data/reference/angi_categories.json, see
scripts/fetch_angi_categories.py) are both just a `CategoryDirectory.load()`
call away, same id/name/slug shape either way (see data/reference/README.md
for each file's schema and how it was built). This module just knows how to
load and query *a* taxonomy; it isn't BBB-specific.
"""
from __future__ import annotations

import json
import re
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
        substring match against the name, in EITHER direction. Returns []
        for no matches, and may return multiple candidates the caller
        should disambiguate.

        Real bug, caught 2026-09-17 live-testing a sample batch: the
        substring check used to only test `text in name` (the query being a
        substring of the category), never the reverse. A plain regular
        plural like "Electricians" is never a substring of this directory's
        own singular "Electrician" -- so a batch run for that exact,
        completely ordinary industry name silently dropped Angi for the
        whole run (a "no confident category match" best-effort skip, not a
        crash, which is exactly why nothing caught it before now). Checking
        both directions fixes any simple query-is-the-plural-of-the-name
        case for free. It does NOT fix a genuinely different word form
        (Angi's own "Roofing"/"House Painting"/"Handyman" vs. the common
        trade names "Roofers"/"Painters"/"Handymen") -- neither is a
        substring of the other no matter which direction you check, and
        that needs a real synonym mapping, not a smarter substring check.
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

        return [
            c for c in self._categories
            if text_lower in c.name.lower() or c.name.lower() in text_lower
        ]

    def resolve_one(self, text: str) -> Category | None:
        """Like search(), but for a best-effort/non-interactive caller that
        just wants "the one match, or nothing" -- no match and an ambiguous
        multi-match both collapse to None (a batch run can't ask a human to
        disambiguate mid-flight the way a CLI script can). Used by
        scripts/batch_scrape_metros.py's Angi integration: an unresolvable
        category just means Angi enrichment is skipped for the run, same
        never-fatal contract as a missing Yelp key."""
        matches = self.search(text)
        return matches[0] if len(matches) == 1 else None

    def resolve_by_slug_token(self, text: str) -> Category | None:
        """Like resolve_one, but matches on `slug` as a whole word inside
        `text` rather than `name` as a substring -- for when `text` is a
        DIFFERENT taxonomy's label that happens to embed this directory's
        own short slug as one of its words (e.g. Angi's "HVAC Companies" ->
        this (BBB) directory's slug "hvac"). resolve_one/search wouldn't
        catch this: "hvac companies" isn't a substring of this entry's own
        name ("Heating and Air Conditioning"), and never will be no matter
        how good the name is, since the two taxonomies just use different
        words for the same real-world category.

        Added 2026-09-16, a real incident: scripts/batch_scrape_metros.py
        sends its one shared --industry string to BOTH Angi (resolved
        against Angi's own 167-category directory first) and BBB (used as
        literal free-text search, per data/reference/README.md's own "BBB's
        search takes any phrase" assumption). Picking "HVAC Companies" from
        the Angi-seeded Streamlit dropdown sent that exact phrase to BBB's
        search too -- confirmed live, 2026-09-16: BBB's own full-text search
        for "HVAC Companies" returns fire/water-damage restoration companies
        as its top results (146 total, SERVPRO first), not HVAC contractors,
        while "Heating and Air Conditioning" (this directory's own confirmed
        entry, BBB's real category id 10182-000) returns real ones (4882
        total, genuine HVAC businesses). A whole 5-metro batch's worth of
        BBB data came back the wrong industry before this was caught -- see
        git history for the incident writeup. Whole-word only (not a bare
        substring) to stay conservative: "hvac" must appear as its own token,
        not buried inside an unrelated longer word.
        """
        tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
        matches = [c for c in self._categories if c.slug and c.slug.lower() in tokens]
        return matches[0] if len(matches) == 1 else None
