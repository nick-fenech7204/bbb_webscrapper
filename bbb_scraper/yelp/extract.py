"""
Yelp "extract" stage: turn a (term, location) into a deduped list of
YelpBusiness records, paging the Fusion businesses/search endpoint.

Quota reality (confirmed 2026-09-10 from the RateLimit-Remaining header):
the free Starter tier is **300 calls / 24h**, reset midnight UTC. Every
response is disk-cached (bbb_scraper/yelp/client.py), so a given query is
paid for once.

`search_area` is one `location=` query + pagination: ~5 calls, up to
Fusion's hard cap of 240 results per query (offset+limit <= 240). That cap
means dense metros are only partially covered -- Miami has ~2,300 "car
dealers", we see the top 240 by Yelp's relevance ranking. Widening that
(a lat/lon grid of tight-radius sub-searches roughly doubled BBB<->Yelp
match coverage in testing) costs ~10x the calls, so it's kept out of the
shipped path on purpose -- the matcher's default stays cheap.
"""
from __future__ import annotations

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.yelp.client import (
    YELP_SEARCH_MAX_LIMIT,
    YELP_SEARCH_MAX_OFFSET,
    YelpClient,
)
from bbb_scraper.yelp.models import YelpBusiness
from bbb_scraper.yelp.parser import parse_yelp_search_response

logger = get_logger(__name__)


class YelpExtractor:
    def __init__(self, client: YelpClient | None = None):
        self.client = client or YelpClient()

    def search_area(
        self,
        term: str,
        location: str,
        *,
        max_results: int = YELP_SEARCH_MAX_OFFSET,
        sort_by: str | None = None,
    ) -> list[YelpBusiness]:
        """Page businesses/search for one term+location, dedupe by yelp_id,
        return in Yelp's result order.

        `max_results` is clamped to Fusion's 240 hard ceiling. `sort_by` is
        Yelp's own (best_match | rating | review_count | distance); left as
        Yelp's default (best_match) when None.
        """
        target = min(max_results, YELP_SEARCH_MAX_OFFSET)
        seen: set[str] = set()
        out: list[YelpBusiness] = []
        total: int | None = None

        for offset in range(0, target, YELP_SEARCH_MAX_LIMIT):
            limit = min(YELP_SEARCH_MAX_LIMIT, target - offset)
            data = self.client.search(
                term=term,
                location=location,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
            )
            if total is None:
                total = data.get("total")
                logger.info(
                    "yelp search_area  %r @ %r -> total=%s (retrievable <=%d)",
                    term, location, total, target,
                )
            page = parse_yelp_search_response(
                data,
                search_term=term,
                search_location=location,
                source_page=offset // YELP_SEARCH_MAX_LIMIT,
            )
            new = [b for b in page if b.yelp_id not in seen]
            seen.update(b.yelp_id for b in new)
            out.extend(new)

            if len(page) < limit or (total is not None and offset + limit >= total):
                break  # ran out of real results before the ceiling

        logger.info("yelp search_area  %r @ %r -> %d unique", term, location, len(out))
        return out
