#!/usr/bin/env python
"""
Batch metro scraper: run one industry across many metros in one sitting,
checkpointing after every metro (safe to interrupt and resume -- a
re-run skips metros already done) and publishing each one straight to the
static site as soon as it finishes, so `site/` grows incrementally instead
of needing one big all-or-nothing run.

Usage:
    python scripts/batch_scrape_metros.py --industry "Car Dealers" \\
        --metros miami-fl,tampa-fl,orlando-fl

    python scripts/batch_scrape_metros.py --industry "Car Dealers" --all-metros

    python scripts/run_search.py --list-metros   # see available metro ids

Meant to run a long time (many metros x many requests each) -- built to be
launched as a background process (the Streamlit page does this for you;
from a plain terminal, background it yourself).

Per metro:
  1. BBB and Angi scrape *concurrently* (2026-09-15 -- previously Angi only
     existed as a separate script, scripts/run_batch_with_angi.py, now
     retired in favor of this being the one real entry point):
       - extract_search_metro_coverage (same as a single metro sweep) ->
         BBB records, transformed + deduped (+ optionally --details).
       - unless --no-angi: scrape_category over the resolved Angi category
         for this metro's (state, city) -- see _scrape_metro_angi.
     These two hit completely unrelated sites (bbb.org vs angi.com) and
     neither reads anything the other produces, so there is no reason to
     make one wait on the other -- they run on their own threads via
     ThreadPoolExecutor and this step is done once BOTH finish. Each is
     best-effort with respect to the other: an Angi failure never touches
     the BBB result for this metro or vice versa (see _scrape_metro_angi).
  2. unless --no-check-websites: bbb_scraper.webcheck checks every unique
     website URL on the BBB records for dead/404/parked (see
     _check_metro_websites) -- unproxied on purpose (a normal one-off
     visit to each business's own site, nothing to evade, and it's a
     different host per business so there's no single site to go easy
     on). Best-effort like Yelp below: a failure here is logged and this
     metro's records just come out unchecked, never fatal to the metro.
  3. unless --no-yelp: one Yelp Fusion `search_area` for the metro (~5 API
     calls), matched to the BBB records. Best-effort -- if the key is
     missing, the daily quota is nearly spent, or a call fails, Yelp is
     dropped for the rest of the batch and metros just come out BBB-only.
     **Steps 2 and 3 stay sequential, deliberately, not a second thread
     pool**: build_master_table (used by step 3) reorders rows (matched
     pairs first, then bbb_only) rather than preserving input order, so
     merging a concurrently-computed webcheck pass back into it would need
     a merge-by-key step, not a simple zip -- real complexity for close to
     no benefit, since Yelp's ~5 calls take seconds while webcheck (already
     internally parallel, ThreadPoolExecutor, see bbb_scraper/webcheck) is
     the slow side of that pair regardless of ordering. Website-check runs
     first specifically because build_master_table's BBB_FIELDS pulls
     website_dead/website_status/website_checked_at onto the master row --
     they have to already be on `records` before step 3 builds it.
  4. unless --no-angi (and the Angi scrape above produced anything): merge
     Angi into the wide table by exact phone match (bbb_scraper/angi/
     enrich.py) -- also recomputes every derived-intelligence column so
     lead_priority_score reflects the Angi match immediately.
  5. unless --no-mapquest: for every row in the wide table (needs
     bbb_name/bbb_phone/bbb_city/bbb_state, which only exist once step 3
     has built it), resolve an approximate coordinate for the business's
     own BBB city (bbb_scraper.reference.cities.CityDirectory -- city-
     level precision is enough, confirmed live) and search MapQuest's own
     unauthenticated GraphQL API (bbb_scraper/mapquest) for real Yelp-
     sourced reviews under that business -- see that module's own
     docstring for how this endpoint was found and confirmed real. Every
     row gets it (not a --top N curated subset like the standalone
     scripts/fetch_mapquest_reviews.py -- Nick's explicit ask, 2026-09-15,
     to integrate this "fully" so every batch run gets it automatically).
     Writes mapquest_url/mapquest_review_count/mapquest_reviews (JSON-in-
     cell, same convention as BBB's/Angi's own review columns) directly
     onto each row -- a post-hoc column addition like webcheck/Yelp above,
     not baked into build_master_table. One shared MapQuestClient for the
     whole batch, proxied by default -- bare/rotating, a fresh proxy
     connection per request, never sticky (--no-mapquest-use-proxy to go
     direct instead, same shape as --angi-use-proxy; see _open_mapquest's
     own docstring for the real incident, 2026-09-15, that took two live-
     tested iterations to land here). Best-effort like everything else
     here: a client-setup failure
     disables MapQuest for the whole batch, a single business's
     search/match failure just leaves that row's columns empty, neither
     is ever fatal. Checkpoint/dataset only
     for now -- these columns are deliberately absent from merge.py's
     scoring and publish_site_data.py's public site fields ("just in the
     dataset, nothing yet different for the website").
  6. write data/processed/batch/<industry-slug>--<metro-id>.csv -- the wide
     BBB|Yelp(|Angi)(|MapQuest) master table (bbb_* / yelp_* / angi_* /
     mapquest_* columns + derived-intelligence columns; yelp_*/angi_*
     blank when there was no match). This file's existence is the resume
     marker: a metro already checkpointed for this industry is skipped on
     a re-run (--force to redo). The raw Angi scrape also gets its own
     checkpoint, same as the standalone script used to write:
     data/processed/angi/<angi-category-slug>--<metro-id>.csv.
  7. append the BBB records (not the wide table) into the shared
     data/processed/businesses.csv sink, same as every other run.
  8. unless --no-publish: publish that metro to site/data/ locally (BBB
     fields + the matched yelp_name/rating/review_count/url + Angi's
     rating/specialties/etc. + our derived intelligence columns -- raw
     Yelp/Angi/MapQuest beyond those stays out).
  9. unless --no-deploy: immediately push site/ live for this metro (S3
     sync + CloudFront invalidation, see scripts/deploy_site.py) -- right
     away, not batched up for the end, so a metro is live within seconds
     of finishing rather than sitting local-only for however long the
     rest of the batch takes.
  Steps 6-9 are wrapped: a failure anywhere in there (including step 5,
  also inside the same try) is logged and this metro is skipped, the rest
  of the batch keeps going rather than the whole run dying (this used to
  be able to kill hours of already-finished, already-correct work over a
  bug in a print statement -- see git history). A deploy failure
  specifically is caught on its own and never undoes the fact that the
  scrape + checkpoint + local publish for that metro already succeeded.

In --details mode, step 1's BBB per-business detail loop can itself run
well over an hour for a big metro (confirmed: 65-78 minutes, real
2026-09-11 runs) -- a *hard* kill in there (closed terminal, sleeping
laptop, killed process) previously lost the entire metro with zero trace,
since nothing reached disk until the loop finished. It now writes a
recoverable partial snapshot to data/processed/batch/_partial/ every 25
businesses (see _write_partial_checkpoint) -- insurance against a total
loss, not resume logic (a re-run still re-scrapes the metro from scratch
today). Angi's own scrape has no equivalent partial-checkpoint yet -- a
hard kill mid-Angi loses that metro's Angi progress same as before this
concurrency change; worth adding the same treatment if Angi runs grow as
long as BBB's --details runs regularly do.

After the whole batch: rebuilds data/processed/<industry-slug>_all_metros.csv
-- every metro run for this industry so far, concatenated, as one file.

--progress-file <path> (optional) writes structured per-metro JSON progress
(status/counts, not log text) as the batch runs -- Streamlit's batch page
polls this to render a clean per-metro checklist instead of tailing the
raw log (see _write_progress). Purely additive: a plain CLI run without
this flag behaves exactly as before.
"""
from __future__ import annotations

import argparse
import csv
import json
import queue
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for publish_site_data below

import deploy_site
from publish_site_data import publish_master_rows, slugify

from bbb_scraper.angi.enrich import enrich_with_angi
from bbb_scraper.angi.scraper import ANGI_CSV_FIELDS, business_detail_to_row, scrape_category
from bbb_scraper.config import settings
from bbb_scraper.etl.dedupe import dedupe_records
from bbb_scraper.etl.extract import Extractor
from bbb_scraper.etl.transform import transform_detail, transform_summary
from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.mapquest.client import MapQuestClient
from bbb_scraper.mapquest.matcher import find_business
from bbb_scraper.match.dedupe import dedupe_by_phone
from bbb_scraper.match.enrich import enrich_bbb_with_yelp, open_yelp_enrichment
from bbb_scraper.match.merge import recompute_intel
from bbb_scraper.pipeline.registry import build_sinks_from_settings
from bbb_scraper.pipeline.sinks.csv_sink import CSVSink
from bbb_scraper.reference.categories import CategoryDirectory
from bbb_scraper.reference.cities import CityDirectory
from bbb_scraper.reference.metros import MetroDirectory
from bbb_scraper.reference.models import Category, Metro, parse_location
from bbb_scraper.scraping.search import build_referer
from bbb_scraper.sentiment.analyze import analyze_business_reviews
from bbb_scraper.sentiment.client import OllamaClient, is_available
from bbb_scraper.utils.flatten import flatten_record
from bbb_scraper.utils.stats import RunStats
from bbb_scraper.webcheck.enrich import check_websites

configure_logging()
logger = get_logger(__name__)

BATCH_DIR = REPO_ROOT / "data" / "processed" / "batch"
ANGI_DIR = REPO_ROOT / "data" / "processed" / "angi"

# Ceiling on how long scrape_one_metro_bbb_and_angi will wait for the Angi
# side before giving up on it as hung, see that function's docstring for the
# real incident this exists for. Generous on purpose: a real, healthy Angi
# run (the default --angi-max-businesses of 150, each needing its own
# profile fetch, some needing the soft-retry for the dual-page-variant bug)
# can legitimately take 20-30+ minutes on its own -- this needs to be well
# above that so a merely-slow-but-working run is never mistaken for a hang.
ANGI_MAX_WAIT_SECONDS = 45 * 60


def _write_progress(path: Path | None, state: dict) -> None:
    """Best-effort structured progress for a UI to poll (2026-09-13, replacing
    the old raw-log-tail view in Streamlit's batch page -- Nick's ask, after
    the log-bloat incident, for "a clean loading UI... or even just counts",
    not a wall of scrolling request-by-request text). No-op when `path` is
    None -- opt-in via --progress-file, so a plain CLI/manual run behaves
    exactly as before. Atomic write (temp file + replace), same pattern as
    _write_partial_checkpoint, so a poller never sees a half-written file.

    **Real incident, 2026-09-16: this used to let a transient Windows file
    lock kill the entire batch.** `tmp_path.replace(path)` raised
    PermissionError mid-batch (something else -- almost certainly
    Streamlit's own file-watcher, polling this exact path for the UI this
    function exists to feed -- briefly held the file open right as this
    tried to replace it). That's a real but genuinely transient OS-level
    race, not a sign anything is actually wrong, and this function is
    explicitly a UI nicety, never a source of truth (the real state is the
    checkpoint CSV / site JSON already on disk) -- but main()'s only
    protection was the per-metro try/except around checkpoint/publish work,
    which this call also sits inside. The first PermissionError got
    miscaught as "this metro's checkpoint/publish step failed" even though
    the metro (Orlando, that day) had already fully succeeded -- then the
    except block's OWN recovery call to this same function threw the exact
    same PermissionError a second time, uncaught, which crashed the whole
    process and silently dropped every metro still queued behind it (2 of
    5, that day). Swallowing the failure here, where it's actually safe to
    ignore, is the fix -- not a broader try/except somewhere upstream that
    would also risk swallowing a real scrape/checkpoint/publish failure by
    mistake.
    """
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(state), encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        logger.warning("Progress file write failed (UI-only, not fatal) -- continuing", exc_info=True)


def _write_partial_checkpoint(path: Path, records: list[dict]) -> None:
    """Overwrite `path` with the current in-progress snapshot of a metro's
    detail-fetch loop -- pure defense-in-depth against a hard kill mid-metro
    (closed terminal, sleeping laptop, a frozen-looking Streamlit tab
    restarted by hand, ...). Added after a real incident (2026-09-11): two
    back-to-back `--details` batches (Roof Contractors/Atlanta, then
    Electricians/Dallas) each ran 65-78 minutes -- thousands of successful
    requests -- then vanished with zero checkpoint and no exception, because
    nothing reached disk until `scrape_one_metro` returned at the very end of
    the whole metro. This writes a recoverable snapshot every
    `partial_every` businesses instead, so a future crash loses at most the
    last few minutes, not the whole metro. Not resume logic (a re-run still
    re-scrapes from scratch) -- just making sure a crash is never a total loss.

    Written atomically (temp file + `replace()`) so a kill mid-write can
    never leave a half-written, corrupt partial file behind.
    """
    if not records:
        return
    deduped = dedupe_by_phone(dedupe_records(records))
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [flatten_record(r) for r in deduped]
    fieldnames = sorted({key for row in rows for key in row})
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(path)


def scrape_one_metro(
    category: Category, metro: Metro, *,
    radius_miles: float, min_population: int, max_pages_per_place: int,
    fetch_details: bool, stats: RunStats,
    partial_checkpoint_path: Path | None = None, partial_every: int = 25,
) -> list[dict]:
    """One metro's worth of BBB records -- details-first if fetch_details,
    same merge pattern already proven on the real Miami car-dealers run (a
    business's detail record wins over its own summary on id collision,
    since it's fetched first and dedupe keeps first-seen).

    Two dedup passes, deliberately different and both kept:
      1. dedupe_records (etl/dedupe.py) -- per-*listing* dedup by id. Two
         requests landing the same BBB address twice (overlapping sweep
         points) collapse; two different branch addresses of the same
         company do NOT -- that's correct, real BBB data.
      2. dedupe_by_phone (match/dedupe.py) -- per-*lead* dedup on top of
         that. A company with several branch listings sharing one phone
         number (common, and BBB-correct) still reads as one row here,
         because from here on these records feed the lead list, checkpoint,
         and businesses.csv -- not a BBB browsing view.

    `partial_checkpoint_path`, when given, gets a recoverable snapshot of
    `detail_records + summary_records` every `partial_every` businesses --
    see `_write_partial_checkpoint`. Only matters in `--details` mode; a
    no-details sweep is one pagination loop, not thousands of individual
    requests, so it was never the failure mode this exists for.
    """
    seed_location = parse_location(metro.seed_location)
    with Extractor(stats=stats) as extractor:
        summaries = extractor.extract_search_metro_coverage(
            category, metro, radius_miles=radius_miles,
            min_population=min_population, max_pages_per_place=max_pages_per_place,
        )
        summary_records = [transform_summary(s) for s in summaries]

        if not fetch_details:
            return dedupe_by_phone(dedupe_records(summary_records, stats=stats))

        detail_records = []
        for i, summary in enumerate(summaries, 1):
            if not summary.profile_url:
                continue
            try:
                referer = build_referer(category, seed_location, page=summary.source_page or 1)
                detail = extractor.extract_business(summary.profile_url, referer=referer)
                detail_records.append(transform_detail(detail))
            except Exception:
                logger.exception("Detail fetch failed for %s", summary.profile_url)

            if partial_checkpoint_path and i % partial_every == 0:
                _write_partial_checkpoint(partial_checkpoint_path, detail_records + summary_records)

        if partial_checkpoint_path:
            _write_partial_checkpoint(partial_checkpoint_path, detail_records + summary_records)

    return dedupe_by_phone(dedupe_records(detail_records + summary_records, stats=stats))


def _resolve_angi_category(name: str) -> tuple[str, str] | None:
    """Industry display name -> (slug, canonical Angi display name), or None
    on no/ambiguous match. Best-effort/non-interactive on purpose (unlike
    the old scripts/run_batch_with_angi.py, which sys.exit'd on a bad
    match) -- this runs inside a long unattended batch, so an unresolvable
    Angi category should disable Angi for the run the same way a missing
    Yelp key disables Yelp, never kill the batch. See data/reference/
    README.md -- Angi slugs aren't a guessable slugification of the label.
    """
    directory = CategoryDirectory.load(settings.angi_categories_file)
    match = directory.resolve_one(name)
    return (match.slug, match.name) if match else None


def _resolve_bbb_category_name(industry: str) -> str:
    """The actual BBB search phrase for --industry -- data/reference/
    categories.json's confirmed name when its slug appears as a whole word
    in `industry`, else `industry` verbatim (unchanged from before this
    existed). See CategoryDirectory.resolve_by_slug_token's own docstring
    for the real incident this fixes: --industry is one shared string sent
    to both Angi (resolved against its own directory above) and, until now,
    BBB as literal free text -- true for most industries, since BBB's
    search genuinely does take any reasonable phrase, but a real batch
    proved that assumption unsafe for at least one case ("HVAC Companies",
    Angi's own label, returned fire/water-damage restoration companies from
    BBB instead of HVAC contractors). This only ever narrows to a *more*
    BBB-confirmed phrase than the literal input, never changes behavior for
    an industry with no entry in this still-small (11-category) file."""
    directory = CategoryDirectory.load(settings.categories_file)
    match = directory.resolve_by_slug_token(industry)
    if match is None:
        return industry
    print(f"BBB category: resolved {industry!r} -> confirmed phrase {match.name!r} "
          f"(data/reference/categories.json id {match.id})")
    return match.name


def _angi_state_city(metro_id: str) -> tuple[str, str]:
    """"phoenix-az" -> ("az", "phoenix"); "san-antonio-tx" -> ("tx", "san-antonio").
    A metro id's last hyphen-separated segment is always the 2-letter state
    (this project's own data/reference/metros.json convention) -- Angi's
    own city slug is usually the same spelling, but isn't guaranteed to be
    (confirm on a real 404 before assuming; see bbb_scraper/angi/scraper.py's
    docstring)."""
    city, _, state = metro_id.rpartition("-")
    return state, city


def _scrape_metro_angi(
    metro: Metro, category_slug: str, category_label: str, *,
    max_businesses: int | None, use_proxy: bool = True,
) -> list[dict]:
    """Best-effort Angi scrape for one metro, run on its own thread
    concurrently with scrape_one_metro (see main()) -- Angi reads nothing
    BBB produces, so there's no reason to wait on it or vice versa. Same
    never-fatal contract as _check_metro_websites/Yelp: any failure here
    (including one partway through, after some businesses were already
    gathered) is logged and this metro just comes out with no/partial Angi
    data, never a crashed batch and never taking scrape_one_metro's own
    result down with it -- the two futures in main() are awaited
    independently.

    `use_proxy` defaults to True, matching AngiClient's own default --
    briefly False here (2026-09-15) after a live smoke test caught
    AngiClient's then-current sticky-session-based proxy rotation 407ing
    100% of requests (see bbb_scraper/angi/client.py's own module
    docstring). Fixed the same day by rebuilding proxy rotation around a
    fresh, bare (never sticky) connection per request instead -- confirmed
    live against real Angi listing pages, zero failures -- so this is back
    to matching the default everywhere else. --no-angi-use-proxy remains
    for local debugging without a proxy configured.
    """
    rows: list[dict] = []
    try:
        state, city = _angi_state_city(metro.id)
        for detail in scrape_category(
            state, city, category_slug,
            category_label=category_label, metro_label=metro.name,
            max_businesses=max_businesses, use_proxy=use_proxy,
        ):
            if detail.name is not None:
                rows.append(business_detail_to_row(detail))
    except Exception:
        logger.exception("Angi scrape failed partway for %s -- keeping %d business(es) already gathered",
                          metro.name, len(rows))
        print(f"    Angi scrape FAILED partway (see log) -- keeping {len(rows)} already gathered")
        return rows
    print(f"    Angi: {len(rows)} businesses ({category_label}, {metro.name})")
    return rows


def _write_angi_checkpoint(path: Path, rows: list[dict]) -> None:
    """Raw Angi scrape output for one metro, same file shape/location
    scripts/scrape_angi_category.py always wrote (data/processed/angi/) --
    kept even though the batch also folds these rows into the wide master
    table, so the raw Angi data is independently inspectable/reusable
    (e.g. re-running just enrich_with_angi later) without re-scraping."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ANGI_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def scrape_one_metro_bbb_and_angi(
    category: Category, metro: Metro, *,
    radius_miles: float, min_population: int, max_pages_per_place: int,
    fetch_details: bool, stats: RunStats, partial_checkpoint_path: Path | None,
    angi_enabled: bool, angi_category_slug: str | None, angi_category_label: str | None,
    angi_max_businesses: int | None, angi_use_proxy: bool = False,
) -> tuple[list[dict], list[dict]]:
    """BBB and Angi for one metro, concurrently when angi_enabled -- see the
    module docstring's step 1 for why (unrelated sites, neither reads the
    other's output). Split out from main() specifically so the concurrency
    itself is directly testable without also standing up main()'s full
    CLI-parsing/state-tracking machinery.

    Returns (bbb_records, angi_rows) -- angi_rows is [] when angi_enabled is
    False, or when Angi blew its wait ceiling (see below). A genuine BBB
    failure still raises out of here exactly as it did pre-concurrency (the
    caller's try/except is unchanged) -- BBB is never given a timeout here;
    a real metro can legitimately take hours in --details mode and that's
    not a hang.

    **Real incident, 2026-09-15: Angi's own thread can hang forever, not
    just raise.** Confirmed live: an overnight batch survived the machine
    being put to sleep on the BBB side (curl's own timeout fired on wake,
    tenacity retried, scraping continued) but Angi's in-flight request never
    recovered -- 9+ hours with zero Angi log activity while BBB kept
    working fine, no exception ever raised (so _scrape_metro_angi's own
    try/except never even saw it -- a hang isn't an exception). The first
    fix attempt used ThreadPoolExecutor with `angi_future.result(timeout=
    ...)` + `pool.shutdown(wait=False)` -- looked right, but testing it for
    real (a throwaway script: submit a task that blocks on a never-set
    Event, time out waiting on it, shut the pool down non-blocking, then
    exit) proved the WHOLE PROCESS still hangs at interpreter exit anyway:
    concurrent.futures' own atexit hook joins every worker thread it has
    ever created, for every executor, regardless of that executor's own
    shutdown(wait=...) -- `wait=False` only stops THIS function from
    blocking, not the process from blocking later when it tries to exit.

    The actual fix: plain `threading.Thread(daemon=True)` + a `queue.Queue`
    for each side, not ThreadPoolExecutor. Confirmed with the same kind of
    throwaway script that a daemon thread blocked forever does NOT stop the
    process from exiting immediately once nothing else is waiting on it --
    daemon threads are excluded from that atexit join by design. `.get()`
    with no timeout on BBB's queue is equivalent to the old
    `bbb_future.result()`; `ANGI_MAX_WAIT_SECONDS` bounds the wait on
    Angi's queue (measured from this call's own start, not from when BBB
    happens to finish, so a long BBB run doesn't also grant Angi extra
    unearned time) -- `queue.Empty` there is treated as "no Angi data for
    this metro," same shape as any other best-effort Angi failure. This
    still doesn't (and can't) kill a truly hung thread -- Python has no API
    to forcibly stop one blocked on a system call -- it's abandoned, not
    terminated, and lingers harmlessly (blocked on I/O, not spinning CPU)
    until the whole process eventually exits, at which point daemon status
    is exactly what lets that exit actually happen.
    """
    if not angi_enabled:
        records = scrape_one_metro(
            category, metro, radius_miles=radius_miles, min_population=min_population,
            max_pages_per_place=max_pages_per_place, fetch_details=fetch_details,
            stats=stats, partial_checkpoint_path=partial_checkpoint_path,
        )
        return records, []

    started = time.monotonic()
    bbb_outcome: queue.Queue = queue.Queue(maxsize=1)
    angi_outcome: queue.Queue = queue.Queue(maxsize=1)

    def _run_bbb() -> None:
        try:
            result = scrape_one_metro(
                category, metro, radius_miles=radius_miles, min_population=min_population,
                max_pages_per_place=max_pages_per_place, fetch_details=fetch_details,
                stats=stats, partial_checkpoint_path=partial_checkpoint_path,
            )
        except Exception as exc:  # noqa: BLE001 -- re-raised on the caller's thread below, not swallowed
            bbb_outcome.put(("error", exc))
        else:
            bbb_outcome.put(("ok", result))

    def _run_angi() -> None:
        # _scrape_metro_angi never raises on its own (see its docstring) --
        # this thread exists so a HANG there (not an exception) can be
        # abandoned via the timeout below instead of blocking forever.
        result = _scrape_metro_angi(
            metro, angi_category_slug, angi_category_label,
            max_businesses=angi_max_businesses, use_proxy=angi_use_proxy,
        )
        angi_outcome.put(("ok", result))

    threading.Thread(target=_run_bbb, daemon=True).start()
    threading.Thread(target=_run_angi, daemon=True).start()

    status, payload = bbb_outcome.get()  # no timeout -- a real multi-hour metro isn't a hang
    if status == "error":
        raise payload
    records = payload

    remaining = max(0.0, ANGI_MAX_WAIT_SECONDS - (time.monotonic() - started))
    try:
        _, angi_rows = angi_outcome.get(timeout=remaining)
    except queue.Empty:
        logger.warning(
            "Angi scrape for %s exceeded its %.0f-minute ceiling and appears hung (e.g. a "
            "request that survived a sleep/suspend in a bad state) -- continuing without "
            "Angi for this metro. Its thread is abandoned (daemon), not killed -- see this "
            "function's docstring.", metro.name, ANGI_MAX_WAIT_SECONDS / 60,
        )
        print(f"    Angi scrape for {metro.name} exceeded its {ANGI_MAX_WAIT_SECONDS / 60:.0f}"
              f"min ceiling (likely hung) -- continuing without Angi for this metro")
        angi_rows = []
    return records, angi_rows


def _check_metro_websites(records: list[dict]) -> list[dict]:
    """Best-effort website-liveness check for one metro's BBB records (see
    bbb_scraper.webcheck) -- unproxied (a normal one-off visit to each
    business's own site; a different host per business, nothing to evade
    or go easy on), deliberately conservative about what it calls dead
    (see the module's own docstring). Wrapped the same way Yelp enrichment
    is: any failure here is logged and this metro's records come back
    unchecked (website_dead_flag reads 0, same as "never checked" always
    has) rather than taking the metro down.
    """
    try:
        checked = check_websites(records)
    except Exception:
        logger.exception("Website check failed -- continuing without it for this metro")
        print("    website check FAILED (see log) -- continuing without it for this metro")
        return records
    dead = sum(1 for r in checked if r.get("website_dead"))
    print(f"    website check: {dead}/{len(checked)} dead/parked/unreachable")
    return checked


def _open_mapquest(
    enabled: bool, *, use_proxy: bool = True,
) -> tuple[MapQuestClient | None, CityDirectory | None, str | None]:
    """One shared MapQuestClient + CityDirectory for the whole batch
    (constructed once, not per metro/business) -- same "resolve once,
    best-effort" shape as _resolve_angi_category. Returns (None, None,
    reason) when disabled or when setup itself fails (e.g. reference data
    missing) -- a setup failure disables MapQuest for the entire batch the
    same way a missing Yelp key disables Yelp, never kills the run.
    MapQuestClient's own __init__ only warns (never raises) on missing
    proxy credentials -- see its _new_session -- so this mostly guards
    against something like a missing us_cities.csv.

    `use_proxy` defaults to True, matching MapQuestClient's own default --
    briefly False here (2026-09-15) after the very first real batch run
    through this code path failed 100% of MapQuest searches with Decodo's
    sticky-session 407 (see bbb_scraper/mapquest/client.py's module
    docstring) -- the identical failure Angi's own batch integration hit
    (see _scrape_metro_angi above). Fixed the same day, same way, for
    both: a fresh, bare (never sticky) proxy connection per request
    instead of periodic sticky-session rotation -- confirmed live against
    real MapQuest searches, zero failures -- so this is back to matching
    the default everywhere else. --no-mapquest-use-proxy remains for local
    debugging without a proxy configured.
    """
    if not enabled:
        return None, None, None
    try:
        client = MapQuestClient(use_proxy=use_proxy)
        city_directory = CityDirectory.load()
    except Exception:
        logger.exception("MapQuest setup failed -- disabling MapQuest for this batch")
        return None, None, "setup failed (see log)"
    return client, city_directory, None


def _enrich_metro_with_mapquest(
    master_rows: list[dict], client: MapQuestClient, city_directory: CityDirectory,
) -> tuple[int, int]:
    """Best-effort MapQuest review enrichment for one metro's already-built
    master rows -- writes mapquest_url/mapquest_review_count/mapquest_reviews/
    mapquest_rating_provider/mapquest_rating_value directly onto every row
    (post-hoc column addition, same pattern as _check_metro_websites/webcheck
    above), not baked into build_master_table. Same matching logic as the
    standalone scripts/fetch_mapquest_reviews.py, just run for every row here
    instead of a --top N curated subset (this is the "fully integrated"
    batch step; that script is still there for enriching an existing
    checkpoint after the fact).

    mapquest_rating_provider/mapquest_rating_value carry MapQuestMatch's own
    aggregate rating (e.g. "YELP", 4.5) -- these fields existed on the model
    since the first MapQuest commit but were never actually written to a
    column anywhere (caught 2026-09-15, real gap: the model captured it,
    nothing wrote it out).

    Returns (matched, total_reviews) for the caller's own progress line.
    Never raises: a single business's search/match failure is logged and
    just leaves that one row's columns empty (mapquest_reviews="[]" when a
    search happened but nothing confidently matched, vs "" when the row
    couldn't even be searched -- no name, no city in reference data, or the
    search call itself failed) -- same never-fatal contract as every other
    enrichment step in this file.
    """
    matched = 0
    total_reviews = 0
    for row in master_rows:
        name = (row.get("bbb_name") or "").strip()
        phone = row.get("bbb_phone") or None
        city_name = (row.get("bbb_city") or "").strip()
        state = (row.get("bbb_state") or "").strip()
        row["mapquest_url"] = ""
        row["mapquest_review_count"] = ""
        row["mapquest_reviews"] = ""
        row["mapquest_rating_provider"] = ""
        row["mapquest_rating_value"] = ""
        if not name or not city_name or not state:
            continue

        city = city_directory.get(city_name, state)
        if city is None:
            continue

        try:
            candidates = client.search(name, latitude=city.lat, longitude=city.lon)
            match = find_business(candidates, name=name, phone=phone)
        except Exception:
            logger.exception("MapQuest search failed for %r", name)
            continue

        if match is None:
            row["mapquest_reviews"] = "[]"
            continue

        matched += 1
        total_reviews += len(match.reviews)
        row["mapquest_url"] = match.url or ""
        row["mapquest_review_count"] = match.review_count
        row["mapquest_reviews"] = json.dumps([asdict(r) for r in match.reviews], ensure_ascii=False)
        row["mapquest_rating_provider"] = match.rating_provider or ""
        row["mapquest_rating_value"] = match.rating_value if match.rating_value is not None else ""

    return matched, total_reviews


def _open_sentiment(enabled: bool) -> tuple[OllamaClient | None, str | None]:
    """One shared OllamaClient for the whole batch -- same "resolve once,
    best-effort" shape as _open_mapquest. Checks is_available() first (a
    cheap /api/tags call) rather than just trying to construct the client
    and hoping -- Ollama not running is a completely normal state (it's a
    local server on Nick's own machine, not always-on infrastructure),
    and finding that out should never cost the ~44s cold-start penalty of
    a real failed /api/generate call."""
    if not enabled:
        return None, None
    if not is_available():
        return None, "Ollama isn't reachable (checked its own /api/tags) -- is it running?"
    try:
        client = OllamaClient()
    except Exception:
        logger.exception("Sentiment analysis setup failed -- disabling it for this batch")
        return None, "setup failed (see log)"
    return client, None


def _enrich_metro_with_sentiment(
    master_rows: list[dict], client: OllamaClient,
) -> tuple[list[dict], int, int, int]:
    """Best-effort local sentiment analysis for one metro's already-built
    master rows -- writes review_sentiment plus 5 aggregate columns
    (review_sentiment_analyzed_count/_negative_count,
    most_recent_review_date, most_recent_negative_review_date,
    avg_review_gap_days) onto every row that has any already-captured
    review text (mapquest_reviews/bbb_reviews/angi_reviews) -- a post-hoc
    column addition like MapQuest above.

    Unlike MapQuest, this DOES feed lead_priority_score (see
    bbb_scraper/match/merge.py's _review_sentiment_signal and
    _review_gap_flag) -- Nick's explicit ask, 2026-09-15 ("add into
    score"), unlike MapQuest's "just in the dataset, nothing yet
    different for the website." So every analyzed row is run back through
    recompute_intel() to refresh every derived-intelligence column with
    the new sentiment data actually in it -- same reason
    bbb_scraper.angi.enrich.enrich_with_angi does this, and returns a NEW
    list rather than mutating in place for the identical reason:
    recompute_intel returns a new dict, it doesn't mutate the one you
    pass it.

    Returns (new_rows, businesses_with_reviews, total_reviews_analyzed,
    total_negative) for the caller's own progress line. Never raises: a
    single business's analysis failing (Ollama down mid-run, one bad
    review) is logged and just leaves that row without sentiment columns,
    never taking any other row -- or the rest of the metro -- down with it.
    """
    out: list[dict] = []
    businesses_with_reviews = 0
    total_reviews = 0
    total_negative = 0
    for row in master_rows:
        row = dict(row)
        has_reviews = any(
            row.get(c) not in (None, "", "[]") for c in ("mapquest_reviews", "bbb_reviews", "angi_reviews")
        )
        if has_reviews:
            try:
                results, aggregate = analyze_business_reviews(row, client)
            except Exception:
                logger.exception("Sentiment analysis failed for %r", row.get("bbb_name"))
                results = None
            if results is not None:
                row["review_sentiment"] = json.dumps([asdict(r) for r in results], ensure_ascii=False)
                for key, value in aggregate.items():
                    row[key] = value if value is not None else ""
                businesses_with_reviews += 1
                total_reviews += len(results)
                total_negative += aggregate["review_sentiment_negative_count"]
                row = recompute_intel(row)
        out.append(row)
    return out, businesses_with_reviews, total_reviews, total_negative


def rebuild_all_metros_file(industry_slug: str) -> Path:
    checkpoint_files = sorted(BATCH_DIR.glob(f"{industry_slug}--*.csv"))
    all_rows: list[dict] = []
    for path in checkpoint_files:
        with path.open(newline="", encoding="utf-8") as f:
            all_rows.extend(csv.DictReader(f))

    out_path = REPO_ROOT / "data" / "processed" / f"{industry_slug}_all_metros.csv"
    if all_rows:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = sorted({key for row in all_rows for key in row})
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)
    return out_path


# Ordered, human-labeled pipeline steps -- written into progress.json
# (metro_states[i]["step"] + the top-level "active_steps" list) so a UI can
# render a real per-metro checklist + fraction-complete bar while a metro is
# "running", not just a generic spinner (2026-09-15, Nick's ask: "include
# sections that are completed and a loading bar"). Single source of truth
# for the labels -- Streamlit just renders whatever "active_steps" says
# rather than keeping its own copy of this list in sync by hand.
_STEP_LABELS = {
    "scraping": "Scraping BBB + Angi",
    "check_websites": "Checking websites",
    "yelp": "Matching Yelp",
    "angi_merge": "Merging Angi",
    "mapquest": "Fetching MapQuest reviews",
    "sentiment": "Analyzing review sentiment",
    "checkpoint": "Writing checkpoint",
    "publish": "Publishing to site",
    "deploy": "Deploying live",
}
_STEP_ORDER = list(_STEP_LABELS)


def _active_steps(*, check_websites: bool, yelp: bool, angi: bool, mapquest: bool, sentiment: bool,
                   publish: bool, deploy: bool) -> list[dict]:
    """Which of _STEP_ORDER this particular batch actually runs, in order --
    depends on which --no-X flags are set, so it's computed once per batch
    (every metro in one batch shares the same flags) rather than assumed
    fixed. "deploy" only appears when publish is also on, since a deploy
    can't happen without a publish first (see main()'s own nesting)."""
    enabled = {
        "scraping": True, "check_websites": check_websites, "yelp": yelp,
        "angi_merge": angi, "mapquest": mapquest, "sentiment": sentiment, "checkpoint": True,
        "publish": publish, "deploy": publish and deploy,
    }
    return [{"key": k, "label": _STEP_LABELS[k]} for k in _STEP_ORDER if enabled[k]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--industry", required=True, help='e.g. "Car Dealers"')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--metros", help="Comma-separated metro ids, e.g. miami-fl,tampa-fl")
    group.add_argument("--all-metros", action="store_true", help="Every metro in data/reference/metros.json")
    parser.add_argument("--radius", type=float, default=40.0, help="Metro sweep radius in miles (default: 40)")
    parser.add_argument("--min-population", type=int, default=25_000, help="Population floor (default: 25000)")
    parser.add_argument("--pages-per-place", type=int, default=15, help="Max pages per swept place (default: 15)")
    parser.add_argument(
        "--details", action="store_true",
        help="Fetch full contact details for every unique business (off by default -- "
        "roughly doubles time per metro; a broad first pass usually wants breadth over "
        "detail, enrich a specific subset with details later if needed)",
    )
    parser.add_argument(
        "--yelp", action=argparse.BooleanOptionalAction, default=True,
        help="Enrich each metro with a Yelp Fusion search (~5 API calls/metro) matched to "
        "the BBB rows (default: on). Best-effort: no key / low quota / a failed call just "
        "means BBB-only output for the rest of the batch, never an error.",
    )
    parser.add_argument(
        "--check-websites", action=argparse.BooleanOptionalAction, default=True,
        help="Check every business's own listed website for dead/404/parked (default: on) -- "
        "see bbb_scraper/webcheck. Unproxied (a normal one-off visit per business, a "
        "different host each time) and best-effort: a failure just means unchecked records "
        "for that metro, never fatal.",
    )
    parser.add_argument(
        "--angi", action=argparse.BooleanOptionalAction, default=True,
        help="Scrape Angi for the same industry+metro, concurrently with BBB (default: on) -- "
        "matched into the wide table by exact phone number (bbb_scraper/angi/enrich.py). "
        "Best-effort like Yelp: no matching Angi category / a scrape failure just means "
        "no Angi columns for this batch, never fatal.",
    )
    parser.add_argument(
        "--angi-category-name", default=None,
        help="Angi category display name, if different from --industry (default: same as "
        "--industry -- this already matches for anything picked from Streamlit's dropdown, "
        "which is seeded from Angi's own category list). See data/reference/angi_categories.json.",
    )
    parser.add_argument(
        "--angi-max-businesses", type=int, default=150,
        help="Cap Angi businesses fetched per metro (default: 150) -- some categories run into "
        "the thousands for one city; this bounds the concurrent Angi side to roughly the same "
        "order of magnitude of time as the BBB side, not an unbounded sweep.",
    )
    parser.add_argument(
        "--angi-use-proxy", action=argparse.BooleanOptionalAction, default=True,
        help="Route the Angi scrape through PROXY_* (default: on). Bare/rotating -- a fresh "
        "proxy connection for every single request, never a sticky session (see "
        "bbb_scraper/angi/client.py's own module docstring: an earlier sticky-session-based "
        "design 407'd 100%% of requests; fixed 2026-09-15, confirmed live). --no-angi-use-proxy "
        "for local debugging without a proxy configured.",
    )
    parser.add_argument(
        "--mapquest", action=argparse.BooleanOptionalAction, default=True,
        help="Fetch real Yelp-sourced review text/rating/date for each business via MapQuest's "
        "own unauthenticated GraphQL search (default: on) -- matched by name+phone within the "
        "business's own city (bbb_scraper/mapquest). Proxied by default (see "
        "--mapquest-use-proxy below) with a real 1.5-3.0s delay between requests on top of "
        "that. Best-effort: a setup failure disables it for the whole batch, a single "
        "business's search/match failure just leaves that row's mapquest_* columns empty, "
        "neither is ever fatal. Checkpoint/dataset only -- not wired into scoring or the "
        "public site yet.",
    )
    parser.add_argument(
        "--mapquest-use-proxy", action=argparse.BooleanOptionalAction, default=True,
        help="Route the MapQuest search through PROXY_* (default: on). Bare/rotating -- a fresh "
        "proxy connection for every single request, never a sticky session (see "
        "bbb_scraper/mapquest/client.py's own module docstring for the same incident/fix "
        "--angi-use-proxy's own docstring describes). --no-mapquest-use-proxy for local "
        "debugging without a proxy configured.",
    )
    parser.add_argument(
        "--sentiment", action=argparse.BooleanOptionalAction, default=True,
        help="Run local sentiment analysis (Ollama, zero-shot -- no training/fine-tuning) over "
        "every review already captured (mapquest_reviews/bbb_reviews/angi_reviews) for each "
        "business (default: on) -- writes review_sentiment plus 5 aggregate columns, and DOES "
        "feed lead_priority_score (unlike MapQuest's raw columns). Best-effort: Ollama not "
        "reachable disables this for the whole batch (checked once, up front, never fatal), a "
        "single business's analysis failing just leaves that row's columns empty. See "
        "bbb_scraper/sentiment/client.py's own module docstring and scripts/"
        "analyze_review_sentiment.py for backfilling a checkpoint that predates this.",
    )
    parser.add_argument(
        "--publish", action=argparse.BooleanOptionalAction, default=True,
        help="Publish each metro's BBB fields to site/ as soon as it's done (default: on)",
    )
    parser.add_argument(
        "--deploy", action=argparse.BooleanOptionalAction, default=True,
        help="Push site/ live (S3 sync + CloudFront invalidation) right after each metro "
        "publishes locally, so it's live within seconds instead of waiting on the rest of the "
        "batch (default: on). Needs the AWS CLI configured -- see scripts/deploy_site.py; a "
        "missing/broken AWS setup prints a message here and the batch still finishes normally, "
        "that metro just doesn't go live. --no-deploy to only publish locally.",
    )
    parser.add_argument("--force", action="store_true", help="Redo metros that already have a checkpoint file")
    parser.add_argument(
        "--progress-file", default=None,
        help="Optional path to write structured per-metro JSON progress to, for a UI to poll "
        "instead of tailing the raw log (the Streamlit batch page uses this). Omit for a plain "
        "CLI run -- has no effect on the scrape itself either way.",
    )
    args = parser.parse_args()

    directory = MetroDirectory.load()
    if args.all_metros:
        metros = directory.all()
    else:
        ids = [m.strip() for m in args.metros.split(",") if m.strip()]
        metros = []
        for metro_id in ids:
            metro = directory.get(metro_id)
            if metro is None:
                print(f"No metro matched '{metro_id}'. Run scripts/run_search.py --list-metros to see valid ids.")
                return 1
            metros.append(metro)

    if not metros:
        print("No metros selected.")
        return 1

    category = Category(id=slugify(args.industry), name=_resolve_bbb_category_name(args.industry))
    industry_slug = slugify(args.industry)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)

    # Resolved once for the whole batch, same shape as Yelp's open_yelp_
    # enrichment below: a missing/ambiguous match just disables Angi for
    # every metro (best-effort), never kills the batch. Every metro in one
    # batch run shares one industry, so there's exactly one category to
    # resolve, not one per metro.
    angi_enabled = args.angi
    angi_category_slug = angi_category_label = None
    angi_reason_off = None
    if angi_enabled:
        resolved = _resolve_angi_category(args.angi_category_name or args.industry)
        if resolved is None:
            angi_enabled = False
            angi_reason_off = (
                f"no confident Angi category match for "
                f"{args.angi_category_name or args.industry!r} -- see "
                f"data/reference/angi_categories.json, or pass --angi-category-name"
            )
        else:
            angi_category_slug, angi_category_label = resolved

    progress_path = Path(args.progress_file) if args.progress_file else None
    # One entry per metro, pre-built so a UI polling this file always sees
    # every metro (including ones not yet started) rather than a list that
    # only grows as the batch progresses.
    metro_states: list[dict] = [
        {
            "id": metro.id, "name": metro.name,
            "status": "skipped" if (BATCH_DIR / f"{industry_slug}--{metro.id}.csv").exists() and not args.force else "pending",
            "step": None,
            "businesses": None, "yelp_matched": None, "angi_businesses": None, "angi_matched": None,
            "mapquest_matched": None, "mapquest_reviews": None,
            "sentiment_analyzed": None, "sentiment_negative": None,
            "top_lead_score": None, "websites_dead": None,
            "elapsed_s": None, "error": None,
        }
        for metro in metros
    ]

    yelp_state = open_yelp_enrichment(args.yelp)
    yelp_note = "on" if yelp_state.enabled else f"off ({yelp_state.reason_off})"
    angi_note = f"on ({angi_category_label})" if angi_enabled else f"off ({angi_reason_off or 'disabled'})"
    mapquest_client, mapquest_city_directory, mapquest_reason_off = _open_mapquest(
        args.mapquest, use_proxy=args.mapquest_use_proxy,
    )
    mapquest_enabled = mapquest_client is not None
    mapquest_note = (f"on, proxy={'on' if args.mapquest_use_proxy else 'off'}" if mapquest_enabled
                      else f"off ({mapquest_reason_off or 'disabled'})")

    sentiment_client, sentiment_reason_off = _open_sentiment(args.sentiment)
    sentiment_enabled = sentiment_client is not None
    sentiment_note = "on" if sentiment_enabled else f"off ({sentiment_reason_off or 'disabled'})"

    active_steps = _active_steps(
        check_websites=args.check_websites, yelp=args.yelp, angi=angi_enabled,
        mapquest=mapquest_enabled, sentiment=sentiment_enabled, publish=args.publish, deploy=args.deploy,
    )

    def _snapshot(finished: bool = False) -> dict:
        return {"industry": args.industry, "total_metros": len(metros), "metros": metro_states,
                "active_steps": active_steps, "finished": finished, "updated_at": time.time()}

    print(f"Batch: {len(metros)} metro(s), industry={args.industry!r}, "
          f"radius={args.radius}mi, min_population={args.min_population}, "
          f"pages_per_place={args.pages_per_place}, details={args.details}, "
          f"yelp={yelp_note}, angi={angi_note}, mapquest={mapquest_note}, sentiment={sentiment_note}, "
          f"check_websites={args.check_websites}, publish={args.publish}, deploy={args.deploy}")
    _write_progress(progress_path, _snapshot())

    done = 0
    skipped = 0
    for i, metro in enumerate(metros, 1):
        checkpoint_path = BATCH_DIR / f"{industry_slug}--{metro.id}.csv"
        if checkpoint_path.exists() and not args.force:
            print(f"[{i}/{len(metros)}] {metro.name}: already done (checkpoint exists) -- skipping. Use --force to redo.")
            skipped += 1
            continue

        # Recoverable snapshot written periodically during a long --details
        # loop (see _write_partial_checkpoint) -- not resume logic, just
        # insurance against a hard kill losing the whole metro. A leftover
        # file here means a previous attempt at this exact metro died before
        # finishing; it's not read back in (no resume-skip logic yet), just
        # flagged so it isn't silently overwritten without a word.
        partial_path = BATCH_DIR / "_partial" / f"{industry_slug}--{metro.id}.csv"
        if args.details and partial_path.exists():
            print(f"[{i}/{len(metros)}] {metro.name}: found leftover partial progress from an "
                  f"interrupted run at {partial_path} -- re-scraping this metro from scratch "
                  f"(recover that file by hand first if you want its data too).")

        start = time.monotonic()
        print(f"[{i}/{len(metros)}] {metro.name}: starting...")
        metro_states[i - 1]["status"] = "running"
        metro_states[i - 1]["step"] = "scraping"
        _write_progress(progress_path, _snapshot())
        stats = RunStats()
        try:
            records, angi_rows = scrape_one_metro_bbb_and_angi(
                category, metro,
                radius_miles=args.radius, min_population=args.min_population,
                max_pages_per_place=args.pages_per_place, fetch_details=args.details,
                stats=stats, partial_checkpoint_path=partial_path if args.details else None,
                angi_enabled=angi_enabled, angi_category_slug=angi_category_slug,
                angi_category_label=angi_category_label, angi_max_businesses=args.angi_max_businesses,
                angi_use_proxy=args.angi_use_proxy,
            )
        except Exception:
            logger.exception("Metro %r failed -- skipping to the next one", metro.name)
            print(f"[{i}/{len(metros)}] {metro.name}: FAILED (see log) -- continuing with the rest")
            metro_states[i - 1]["status"] = "failed"
            metro_states[i - 1]["error"] = "Scrape failed -- see the full log for the traceback."
            _write_progress(progress_path, _snapshot())
            continue

        websites_dead = None
        if args.check_websites:
            metro_states[i - 1]["step"] = "check_websites"
            _write_progress(progress_path, _snapshot())
            records = _check_metro_websites(records)
            websites_dead = sum(1 for r in records if r.get("website_dead"))

        # Everything from here on (checkpoint, shared sinks, publish) is
        # wrapped: a batch runs unattended for hours across many metros, so
        # a bug in this post-scrape step (there was one -- see below) must
        # never be allowed to kill metros still queued behind it. Whatever
        # already reached disk (checkpoint, businesses.csv, the site JSON)
        # stays written either way; only this metro's `done` count and
        # summary line are skipped on failure.
        try:
            metro_states[i - 1]["step"] = "yelp"
            _write_progress(progress_path, _snapshot())
            master_rows = enrich_bbb_with_yelp(records, args.industry, metro.seed_location, yelp_state)

            angi_matched = None
            if angi_enabled:
                if angi_rows:
                    metro_states[i - 1]["step"] = "angi_merge"
                    _write_progress(progress_path, _snapshot())
                    _write_angi_checkpoint(ANGI_DIR / f"{angi_category_slug}--{metro.id}.csv", angi_rows)
                    master_rows = enrich_with_angi(master_rows, angi_rows)
                    angi_matched = sum(1 for r in master_rows if r.get("on_angi"))
                else:
                    angi_matched = 0

            mapquest_matched = mapquest_reviews_count = None
            if mapquest_enabled:
                metro_states[i - 1]["step"] = "mapquest"
                _write_progress(progress_path, _snapshot())
                mapquest_matched, mapquest_reviews_count = _enrich_metro_with_mapquest(
                    master_rows, mapquest_client, mapquest_city_directory,
                )
                print(f"    MapQuest: {mapquest_matched}/{len(master_rows)} matched, "
                      f"{mapquest_reviews_count} reviews captured")

            sentiment_analyzed = sentiment_negative = None
            if sentiment_enabled:
                metro_states[i - 1]["step"] = "sentiment"
                _write_progress(progress_path, _snapshot())
                master_rows, sentiment_businesses, sentiment_analyzed, sentiment_negative = (
                    _enrich_metro_with_sentiment(master_rows, sentiment_client)
                )
                print(f"    Sentiment: {sentiment_businesses} business(es) analyzed, "
                      f"{sentiment_analyzed} reviews ({sentiment_negative} negative/mixed)")

            metro_states[i - 1]["step"] = "checkpoint"
            _write_progress(progress_path, _snapshot())
            CSVSink(checkpoint_path).load(master_rows)
            # The real checkpoint just landed -- any partial snapshot from
            # this metro's detail loop is superseded, remove it so it can't
            # be mistaken for still-relevant leftover data next run.
            partial_path.unlink(missing_ok=True)

            for sink in build_sinks_from_settings():
                try:
                    sink.load(records)  # BBB records only -- businesses.csv stays a pure BBB log
                except Exception:
                    logger.exception("Shared sink %r failed to load", sink.name)

            elapsed = time.monotonic() - start
            matched = sum(r.get("match_status") == "matched" for r in master_rows)
            print(f"[{i}/{len(metros)}] {metro.name}: {len(records)} BBB businesses"
                  f"{f', {matched} matched to Yelp' if yelp_state.enabled else ''}"
                  f"{f', {len(angi_rows)} Angi ({angi_matched} matched by phone)' if angi_enabled else ''}"
                  f"{f', {mapquest_matched} MapQuest ({mapquest_reviews_count} reviews)' if mapquest_enabled else ''}"
                  f"{f', {sentiment_analyzed} sentiment ({sentiment_negative} negative)' if sentiment_enabled else ''}"
                  f"{f', {websites_dead} dead websites' if websites_dead is not None else ''} "
                  f"({elapsed:.0f}s, {stats.as_dict().get('requests_sent')} BBB requests)")
            metro_states[i - 1].update({
                "status": "done", "businesses": len(records),
                "yelp_matched": matched if yelp_state.enabled else None,
                "angi_businesses": len(angi_rows) if angi_enabled else None,
                "angi_matched": angi_matched,
                "mapquest_matched": mapquest_matched,
                "mapquest_reviews": mapquest_reviews_count,
                "sentiment_analyzed": sentiment_analyzed,
                "sentiment_negative": sentiment_negative,
                "websites_dead": websites_dead,
                "elapsed_s": round(elapsed),
            })
            _write_progress(progress_path, _snapshot())

            if args.publish:
                metro_states[i - 1]["step"] = "publish"
                _write_progress(progress_path, _snapshot())
                # master_rows, not `records` -- so the site gets our derived
                # intelligence columns too. publish_master_rows only carries
                # the matched business's yelp_name/rating/review_count/url
                # onto the public site (plus our derived columns) -- every
                # other raw yelp_* field stays local (_YELP_SITE_FIELDS).
                entry = publish_master_rows(master_rows, args.industry, metro.name)
                print(f"    published -> site/data/{entry['file']} "
                      f"({entry['yelp_matched']} matched to Yelp, top lead {entry['top_lead_score']})")
                metro_states[i - 1]["top_lead_score"] = entry.get("top_lead_score")
                _write_progress(progress_path, _snapshot())

                if args.deploy:
                    # Deploy right after this metro's local publish, not
                    # batched up for the very end -- so a metro is live
                    # within seconds of finishing rather than sitting on
                    # disk for however long the rest of the batch takes
                    # (that's exactly what happened before this changed:
                    # a completed metro sat local-only for hours). Its own
                    # try/except on purpose: a deploy problem here must
                    # never undo the fact that this metro's scrape +
                    # checkpoint + local publish already succeeded, so it
                    # doesn't set off the outer except or skip `done += 1`.
                    metro_states[i - 1]["step"] = "deploy"
                    _write_progress(progress_path, _snapshot())
                    print("    deploying to the live site...")
                    try:
                        rc = deploy_site.main()
                    except Exception:
                        logger.exception("Deploy step raised unexpectedly")
                        rc = 1
                    if rc != 0:
                        print(f"    deploy failed (see above) -- {metro.name} is still fully "
                              f"scraped + published locally; re-run `python scripts/deploy_site.py` "
                              f"to retry, or it'll go out with the next metro's deploy anyway.")
        except Exception:
            logger.exception("Metro %r: checkpoint/publish step failed", metro.name)
            print(f"[{i}/{len(metros)}] {metro.name}: scraped OK but the checkpoint/publish step "
                  f"FAILED (see log) -- continuing with the rest. Re-run with --force to redo this metro.")
            metro_states[i - 1]["status"] = "failed"
            metro_states[i - 1]["error"] = "Scraped OK but checkpoint/publish failed -- see the full log."
            _write_progress(progress_path, _snapshot())
            continue

        done += 1

    all_metros_path = rebuild_all_metros_file(industry_slug)
    print(f"\nBatch complete: {done} metro(s) run, {skipped} skipped (already done).")
    if yelp_state.reason_off and args.yelp:
        print(f"Note: Yelp enrichment stopped partway -- {yelp_state.reason_off}")
    if angi_reason_off:
        print(f"Note: Angi enrichment was off for this whole batch -- {angi_reason_off}")
    if mapquest_reason_off:
        print(f"Note: MapQuest enrichment was off for this whole batch -- {mapquest_reason_off}")
    if sentiment_reason_off:
        print(f"Note: Sentiment analysis was off for this whole batch -- {sentiment_reason_off}")
    print(f"Compiled file: {all_metros_path}")
    _write_progress(progress_path, _snapshot(finished=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
