"""
Extract: the only layer that combines scraping (network) with parsing.

Keeping this separate from transform/load means transform/load can be fully
unit tested without ever touching the network (they just consume
BusinessSummary / BusinessDetail objects or plain dicts), and parser
development (parsing/*) can run entirely against saved fixtures without this
module involved either. This is where the two meet.
"""
from __future__ import annotations

from collections.abc import Callable

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.parsing.business_parser import parse_business_page
from bbb_scraper.parsing.models import BusinessDetail, BusinessSummary
from bbb_scraper.parsing.search_parser import parse_response_center, parse_search_results
from bbb_scraper.reference.cities import CityDirectory
from bbb_scraper.reference.geo import generate_coverage_points
from bbb_scraper.reference.models import Category, Location, Metro, parse_location
from bbb_scraper.scraping.business import BBBBusinessClient
from bbb_scraper.scraping.capture import RawCapture
from bbb_scraper.scraping.client import HttpClient
from bbb_scraper.scraping.search import BBBSearchClient
from bbb_scraper.utils.stats import RunStats

logger = get_logger(__name__)


class Extractor:
    def __init__(
        self,
        *,
        stats: RunStats | None = None,
        session_id: str | None = None,
        cfg: Settings | None = None,
    ):
        self.cfg = cfg or default_settings
        self.stats = stats or RunStats()
        self.http = HttpClient(self.cfg, stats=self.stats, session_id=session_id)
        self.capture = RawCapture()
        self.search_client = BBBSearchClient(self.http, capture=self.capture, cfg=self.cfg)
        self.business_client = BBBBusinessClient(self.http, capture=self.capture)

    def extract_search(
        self,
        category: Category,
        location: Location,
        max_pages: int | None = None,
        *,
        sort: str | None = None,
    ) -> list[BusinessSummary]:
        """Page through search results for one (category, location).

        Every response carries its own `page`/`pageSize`/`totalPages`/
        `totalResults` (confirmed 2026-08-31) -- used here to stop as soon
        as BBB says there's nothing more, and to detect truncation
        precisely: BBB caps `totalPages` at `cfg.bbb_max_search_pages` (15)
        regardless of `pageSize`, so a query with `totalResults` above
        `pageSize * totalPages` has more matches than pagination can ever
        reach (728 results behind a 15 x 15 = 225 reachable window, in the
        response this was modeled on). Getting the rest means running
        multiple narrower searches and deduping the results together, which
        isn't handled here (see etl/dedupe.py -- dedupe works on
        already-extracted records; this is about the extraction strategy
        feeding it).
        """
        cap = self.cfg.bbb_max_search_pages
        max_pages = min(max_pages, cap) if max_pages is not None else cap

        all_summaries: list[BusinessSummary] = []
        total_results = page_size = reported_pages = None

        for page in range(1, max_pages + 1):
            result = self.search_client.search(category, location, page=page, sort=sort)
            total_results = result.data.get("totalResults")
            page_size = result.data.get("pageSize")
            reported_pages = result.data.get("totalPages")

            summaries = parse_search_results(
                result.data, category=category, location=location, page=page, stats=self.stats
            )
            logger.info(
                "Parsed %d listing record(s) from page %d for category=%r location=%r",
                len(summaries), page, category.name, location.display,
            )
            all_summaries.extend(summaries)

            if not summaries:
                break
            if reported_pages is not None and page >= reported_pages:
                break  # fetched every page BBB says exists for this query

        if total_results is not None and page_size is not None and reported_pages is not None:
            reachable = page_size * reported_pages
            if total_results > reachable:
                logger.warning(
                    "category=%r location=%r reports %d total results but only %d "
                    "are reachable through pagination (pageSize=%d x totalPages=%d) "
                    "-- narrow this search and dedupe against other searches for the rest.",
                    category.name, location.display, total_results, reachable,
                    page_size, reported_pages,
                )

        return all_summaries

    def extract_search_coverage(
        self,
        category: Category,
        location: Location,
        *,
        radius_miles: float = 25.0,
        num_points: int = 16,
        max_pages_per_point: int = 2,
        sort_by_distance: bool = True,
    ) -> list[BusinessSummary]:
        """Sweep multiple search anchors around `location` instead of one
        search -- works around BBB's `find_loc`/`find_latlng` not actually
        scoping results to a local radius (confirmed 2026-09-02: a Seattle/
        Tacoma/Bellevue/Federal Way pilot all returned nearly the same
        statewide pool of ~230 businesses spanning 91 WA cities, some 140+
        miles apart). `location` should be name-based (city/state or zip,
        not already carrying lat/lon) -- the first search against it is
        what gives us BBB's own resolved center point (`location.latLng` in
        the raw response, see `parsing.search_parser.parse_response_center`
        -- only populated for name-based searches), so no separate
        geocoding step or library is needed.

        `max_pages_per_point` defaults low (2, not the usual up-to-15) on
        purpose: with `sort_by_distance` on, each anchor's first page or
        two should already surface the businesses actually near *that*
        anchor. Fetching every one of 16+ nearby anchors to its full
        15-page depth would mean heavily overlapping result sets across
        anchors, burning requests without meaningfully improving coverage.
        Raise it if you find anchors aren't reaching far enough into their
        own local results.

        Returns every summary found across every anchor, **undeduped** --
        the same business appearing under multiple nearby anchors is
        expected (see `BusinessSummary.bbb_id`'s docstring on per-listing,
        not per-company, identity) and not itself a problem; dedupe with
        `etl/dedupe.py` same as any other batch, same as the CLI/UI already
        do for a single search's results.
        """
        sort = "Distance" if sort_by_distance else None
        all_summaries: list[BusinessSummary] = []
        center: tuple[float, float] | None = None

        # Center point fetched by hand (not via extract_search) so we can
        # also pull BBB's resolved center coordinates out of its raw
        # response -- extract_search only returns parsed summaries.
        for page in range(1, max_pages_per_point + 1):
            result = self.search_client.search(category, location, page=page, sort=sort)
            if page == 1:
                center = parse_response_center(result.data)
            summaries = parse_search_results(
                result.data, category=category, location=location, page=page, stats=self.stats
            )
            all_summaries.extend(summaries)
            if not summaries:
                break

        if center is None:
            logger.warning(
                "Could not resolve a center point for %r -- BBB's response had no "
                "location.latLng (only populated for name-based find_loc searches, "
                "not one already carrying lat/lon). Returning just the single search "
                "already run; no coverage sweep possible without a center.",
                location.display,
            )
            return all_summaries

        center_lat, center_lon = center
        # generate_coverage_points' index 0 is the center itself -- already
        # searched above, skip it here to avoid a redundant duplicate search.
        ring_points = generate_coverage_points(
            center_lat, center_lon, radius_miles, count=num_points
        )[1:]
        logger.info(
            "Resolved %r to (%.5f, %.5f) -- sweeping %d additional point(s) within %g mile(s)",
            location.display, center_lat, center_lon, len(ring_points), radius_miles,
        )

        for lat, lon in ring_points:
            ring_location = Location(raw=f"{lat:.5f},{lon:.5f}", lat=lat, lon=lon)
            all_summaries.extend(
                self.extract_search(category, ring_location, max_pages=max_pages_per_point, sort=sort)
            )

        return all_summaries

    def extract_search_area_coverage(
        self,
        category: Category,
        location: Location,
        *,
        radius_miles: float = 40.0,
        min_population: int = 25_000,
        max_pages_per_place: int = 15,
        sort_by_distance: bool = True,
        city_directory: CityDirectory | None = None,
        on_place_done: Callable[[str, int, int, int], None] | None = None,
    ) -> list[BusinessSummary]:
        """Sweep every real, substantial city/CDP within `radius_miles` of
        `location` -- not lat/lon ring points like `extract_search_coverage`
        -- because BBB's "local" result pool is tied to the specific NAMED
        place searched (`find_loc`), not just proximity to a point:
        confirmed 2026-09-02, a lat/lon point ~13 miles from Miami's own
        resolved center (`find_latlng`) found a completely different,
        non-overlapping set of real local car dealerships than a plain
        "Kendall, FL" search did, even though Kendall is itself about that
        far from downtown Miami. Sweeping real named places, not
        coordinates, is what actually reaches full local coverage -- see
        `reference/cities.py`'s module docstring.

        Works for any resolvable place, not just a curated metro -- a small
        town (e.g. "Palm Coast, FL") that isn't in data/reference/metros.json
        still gets this treatment: it's searched itself either way, and
        picks up whatever real substantial places (if any) are genuinely
        within range of it too. A place with nothing nearby meeting
        `min_population` just degrades gracefully to "search that one town"
        -- there's no special-casing needed for "this isn't a big metro."
        (`extract_search_metro_coverage` below is a thin wrapper over this
        for the curated-metro-list use case -- CLI `--metro`, the batch
        scraper -- where a fixed, known-good list of named metros matters;
        this is the general version, used directly by the main Streamlit
        search UI so any typed-in place gets the same smart behavior.)

        `min_population` filters `reference/cities.py`'s full US place list
        (every incorporated place + CDP in the country, from
        data/reference/us_cities.csv) down to substantial cities only --
        without it a 40-mile radius around a big metro can include 100+
        tiny places, multiplying request count far past what's useful (see
        scripts/build_us_cities.py's module docstring).

        `max_pages_per_place` defaults to full depth (15, not a shallow
        couple of pages like `extract_search_coverage`'s anchors use) since
        each place here is a real named search in its own right, not an
        arbitrary nearby point -- there's no "heavily overlapping anchors"
        concern the way there is with lat/lon ring points.

        `on_place_done`, if given, is called after every place finishes
        (the seed place included) as `on_place_done(place_display_name,
        done_count, total_count, running_summary_count)` -- lets a caller
        (e.g. a UI) show real per-place progress instead of one big blocking
        call with no feedback until it's entirely done.
        """
        sort = "Distance" if sort_by_distance else None
        directory = city_directory or CityDirectory.load()

        all_summaries: list[BusinessSummary] = []
        center: tuple[float, float] | None = None

        # Seed place fetched by hand (not via extract_search) so we can also
        # pull BBB's resolved center coordinates out of its raw response.
        for page in range(1, max_pages_per_place + 1):
            result = self.search_client.search(category, location, page=page, sort=sort)
            if page == 1:
                center = parse_response_center(result.data)
            summaries = parse_search_results(
                result.data, category=category, location=location, page=page, stats=self.stats
            )
            all_summaries.extend(summaries)
            if not summaries:
                break

        if center is None:
            logger.warning(
                "Could not resolve a center point for %r -- BBB's response had no "
                "location.latLng. Returning just the seed place's own search; no "
                "area sweep possible without a center.",
                location.display,
            )
            if on_place_done:
                on_place_done(location.display, 1, 1, len(all_summaries))
            return all_summaries

        center_lat, center_lon = center
        nearby_cities = directory.within_radius(
            center_lat, center_lon, radius_miles, min_population=min_population
        )
        # Drop the seed place itself if the directory also contains it --
        # otherwise it gets searched twice (once above by name, once again
        # here) for no benefit.
        seed_key = ((location.city or "").lower(), (location.state or "").upper())
        nearby_cities = [c for c in nearby_cities if (c.name.lower(), c.state.upper()) != seed_key]

        logger.info(
            "%r resolved to (%.5f, %.5f) -- sweeping %d nearby place(s) "
            "(population >= %d) within %g mile(s)",
            location.display, center_lat, center_lon, len(nearby_cities), min_population, radius_miles,
        )

        total_places = 1 + len(nearby_cities)
        if on_place_done:
            on_place_done(location.display, 1, total_places, len(all_summaries))

        for i, city in enumerate(nearby_cities, start=2):
            all_summaries.extend(
                self.extract_search(
                    category, city.to_location(), max_pages=max_pages_per_place, sort=sort
                )
            )
            if on_place_done:
                on_place_done(city.display, i, total_places, len(all_summaries))

        return all_summaries

    def extract_search_metro_coverage(
        self,
        category: Category,
        metro: Metro,
        *,
        radius_miles: float = 40.0,
        min_population: int = 25_000,
        max_pages_per_place: int = 15,
        sort_by_distance: bool = True,
        city_directory: CityDirectory | None = None,
        on_place_done: Callable[[str, int, int, int], None] | None = None,
    ) -> list[BusinessSummary]:
        """Curated-metro-list wrapper over `extract_search_area_coverage` --
        for the CLI's `--metro` flag and the batch scraper, where sweeping a
        fixed, known-good list of named metros (data/reference/metros.json)
        is exactly the point. See that method for the real logic/docs.
        """
        return self.extract_search_area_coverage(
            category, parse_location(metro.seed_location),
            radius_miles=radius_miles, min_population=min_population,
            max_pages_per_place=max_pages_per_place, sort_by_distance=sort_by_distance,
            city_directory=city_directory, on_place_done=on_place_done,
        )

    def extract_business(self, profile_url: str, *, referer: str | None = None) -> BusinessDetail:
        result = self.business_client.fetch(profile_url, referer=referer)
        return parse_business_page(result.html, profile_url=profile_url, stats=self.stats)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> Extractor:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
