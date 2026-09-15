"""Orchestration: paginate one (category, metro) companylist search, then
fetch + parse every business's own profile page it links to.

Politeness/scope note (see bbb-scraper's ethical-scraping-boundary
practice): this walks *every* page of a category+metro search up to
`max_businesses`, not an unbounded "get everything" sweep by default --
some categories run well into the thousands of results for a single big
city (confirmed by checking real result counts before ever writing this
module: Chicago Plumbers alone is 1091), and a full run isn't needed to
prove the pipeline or produce a genuinely useful dataset.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterator

from bbb_scraper.angi.client import AngiClient
from bbb_scraper.angi.flight_data import reassemble
from bbb_scraper.angi.models import BusinessDetail
from bbb_scraper.angi.parsing import parse_business_detail, parse_listing_page
from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.exceptions import ScrapeError
from bbb_scraper.logging_setup import get_logger

logger = get_logger(__name__)

OnProgress = Callable[[str, int, int | None], None]  # (stage, done, total)

# Column order for a flattened BusinessDetail -- shared by
# scripts/scrape_angi_category.py's CSV writer and
# scripts/batch_scrape_metros.py's own Angi checkpoint, so the two never
# drift apart (see business_detail_to_row's docstring).
ANGI_CSV_FIELDS = [
    "name", "phone", "website", "address", "street", "city", "state", "zip_code",
    "overall_rating", "review_count",
    "rating_5_star_pct", "rating_4_star_pct", "rating_3_star_pct", "rating_2_star_pct", "rating_1_star_pct",
    "is_paid_pro", "is_corporate_account", "is_super_service_award_winner",
    "bonded", "insured", "licenses",
    "categories", "num_categories", "about_us", "highlights",
    "searched_category", "searched_metro", "profile_url",
]


def _round_or_none(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def business_detail_to_row(d: BusinessDetail) -> dict:
    """Flatten one BusinessDetail to a plain dict matching ANGI_CSV_FIELDS --
    list fields "; "-joined (see bbb_scraper.match.merge's ANGI_FIELDS
    docstring for why "; " and not ",": several category/license names
    already contain a literal comma of their own). The single source of
    truth for this shape -- both scripts/scrape_angi_category.py's CSV
    writer and scripts/batch_scrape_metros.py's in-batch Angi scrape use
    this, so there's only one place to update if BusinessDetail ever grows
    a field (previously this logic was duplicated, exactly the "two call
    sites, one gets updated" bug class this project has been bitten by
    before -- see bbb-scraper-status memory, the has_intel incident).
    """
    breakdown_by_star = {b.star: b.percentage for b in d.rating_breakdown}
    return {
        "name": d.name, "phone": d.phone, "website": d.website,
        "address": d.address, "street": d.street, "city": d.city,
        "state": d.state, "zip_code": d.zip_code,
        # Angi's own overallRating is a raw float division (e.g.
        # 4.933734939759036) -- real precision, not a display value, so
        # round it for a CSV a person actually reads.
        "overall_rating": _round_or_none(d.overall_rating, 2),
        "review_count": d.review_count,
        "rating_5_star_pct": _round_or_none(breakdown_by_star.get(5), 1),
        "rating_4_star_pct": _round_or_none(breakdown_by_star.get(4), 1),
        "rating_3_star_pct": _round_or_none(breakdown_by_star.get(3), 1),
        "rating_2_star_pct": _round_or_none(breakdown_by_star.get(2), 1),
        "rating_1_star_pct": _round_or_none(breakdown_by_star.get(1), 1),
        "is_paid_pro": d.is_paid_pro, "is_corporate_account": d.is_corporate_account,
        "is_super_service_award_winner": d.is_super_service_award_winner,
        "bonded": d.bonded, "insured": d.insured,
        "licenses": "; ".join(d.licenses),
        "categories": "; ".join(d.categories), "num_categories": len(d.categories),
        "about_us": d.about_us, "highlights": "; ".join(d.highlights),
        "searched_category": d.searched_category, "searched_metro": d.searched_metro,
        "profile_url": d.profile_url,
    }


def _listing_url(state: str, city: str, category_slug: str, page: int) -> str:
    base = f"/companylist/us/{state}/{city}/{category_slug}.htm"
    return base if page <= 1 else f"{base}?page={page}"


def scrape_category(
    state: str,
    city: str,
    category_slug: str,
    *,
    category_label: str | None = None,
    metro_label: str | None = None,
    max_businesses: int | None = None,
    cfg: Settings | None = None,
    client: AngiClient | None = None,
    use_proxy: bool = True,
    on_progress: OnProgress | None = None,
) -> Iterator[BusinessDetail]:
    """Yields one BusinessDetail per business found, fetched from its own
    profile page (not just the summary shown on the listing page -- the
    listing card alone doesn't carry address/website/about-us/licensing).

    `state`/`city` are Angi's own URL slugs (e.g. "il"/"chicago" -- see
    data/reference for the equivalent BBB-side lookups; Angi doesn't share
    BBB's exact slug spelling for every place, confirm before assuming).
    `category_slug` is one of data/reference/angi_categories.json's `slug`
    values -- not a guessable slugification of the category name (see that
    file's README section).
    """
    cfg = cfg or default_settings
    owns_client = client is None
    client = client or AngiClient(cfg, use_proxy=use_proxy)
    category_label = category_label or category_slug
    metro_label = metro_label or f"{city}, {state}".upper()

    try:
        seen_urls: set[str] = set()
        page = 1
        total_pages: int | None = None
        emitted = 0

        while True:
            if max_businesses is not None and emitted >= max_businesses:
                break
            if total_pages is not None and page > total_pages:
                break

            try:
                listing_html = client.get(_listing_url(state, city, category_slug, page))
            except ScrapeError as exc:
                # Unlike a single business (skip and keep going), a listing
                # page that never loads means no more URLs to work through
                # at all from here on -- stop cleanly with whatever was
                # already collected rather than crash the whole run.
                logger.warning("angi: giving up on page %d of %s/%s/%s -- %s",
                                page, state, city, category_slug, exc)
                break
            listing = parse_listing_page(reassemble(listing_html))

            if listing.result_count is not None and listing.page_size:
                total_pages = math.ceil(listing.result_count / listing.page_size)
                if max_businesses is not None:
                    total_pages = min(total_pages, math.ceil(max_businesses / listing.page_size))

            if not listing.profile_urls:
                # No businesses at all on a page that should have some --
                # stop rather than loop forever; a genuinely exhausted
                # category (fewer results than resultCount implied) looks
                # the same as a transient empty response, and either way
                # there's nothing more to fetch from this page onward.
                logger.warning(
                    "angi: page %d of %s/%s/%s returned no listings -- stopping pagination",
                    page, state, city, category_slug,
                )
                break

            if on_progress:
                on_progress(
                    "listing",
                    page,
                    total_pages,
                )

            for relative_url in listing.profile_urls:
                if max_businesses is not None and emitted >= max_businesses:
                    break
                if relative_url in seen_urls:
                    continue  # a business can legitimately reappear across pages if results shift
                seen_urls.add(relative_url)

                detail_url = cfg.angi_base_url + relative_url
                try:
                    detail = _fetch_detail_with_soft_retry(client, detail_url)
                except ScrapeError as exc:
                    # One business failing outright (e.g. a sustained 429
                    # that outlasted client.get's own retries+cooldown)
                    # shouldn't cost everything already collected -- same
                    # best-effort-per-item philosophy as Yelp/webcheck
                    # enrichment elsewhere in this project. Skipped, not
                    # yielded as an all-empty row -- a request that never
                    # succeeded isn't the same as a page that loaded but
                    # had nothing to parse.
                    logger.warning("angi: skipping %s -- %s", detail_url, exc)
                    continue
                detail.searched_category = category_label
                detail.searched_metro = metro_label
                emitted += 1
                if on_progress:
                    on_progress("detail", emitted, max_businesses)
                yield detail

            page += 1
    finally:
        if owns_client:
            client.close()


_MAX_DETAIL_ATTEMPTS = 3


def _fetch_detail_with_soft_retry(client: AngiClient, url: str) -> BusinessDetail:
    """A detail page can come back HTTP 200 with the expected title but
    missing its own business-profile data component entirely -- confirmed
    by testing, not assumed, and root-caused (see client.py's module
    docstring): a server-side experiment cookie can lock a session onto the
    empty page variant. client.py now clears cookies before every request
    specifically so each attempt here is an independent roll rather than a
    guaranteed repeat of the same failure -- measured around a 60-65%
    per-attempt success rate on the full variant, so
    1 - 0.375**_MAX_DETAIL_ATTEMPTS is roughly how often this function
    still comes back empty after exhausting its attempts (a genuinely
    sparse/removed listing, not a coincidence).
    """
    detail = None
    for attempt in range(1, _MAX_DETAIL_ATTEMPTS + 1):
        html = client.get(url)
        detail = parse_business_detail(reassemble(html), url)
        if detail.name is not None:
            return detail
        if attempt < _MAX_DETAIL_ATTEMPTS:
            logger.info("angi: empty parse for %s (attempt %d/%d), retrying",
                        url, attempt, _MAX_DETAIL_ATTEMPTS)
    logger.warning("angi: %s never returned full profile data after %d attempts",
                    url, _MAX_DETAIL_ATTEMPTS)
    return detail
