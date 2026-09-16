"""
Best-effort Yelp enrichment of BBB records, for the batch scraper.

Yelp here is *supplementary*: the free Fusion tier is 300 calls/day, so a
batch of many metros can run out. A genuinely permanent-looking failure --
no API key, quota nearly spent, a 429, a 401/403 (bad/revoked key) --
switches enrichment off for the rest of the run and lets metros come
through BBB-only. Never raises.

**2026-09-16, real incident:** a transient Yelp 500 ("Something went wrong
internally, please try again later" -- Yelp's own words) on the very
FIRST call of a real 5-metro batch disabled Yelp for all five, costing
hundreds of businesses their Yelp match for the rest of a run that kept
going for over an hour. A one-off server hiccup is not evidence of quota
exhaustion, so it shouldn't get the same permanent, whole-batch
consequence -- see _is_permanent below for exactly which failures still
do vs. which ones now just cost the one metro they happened on.

The state object is meant to live for one whole batch so the daily quota
is tracked across metros and a genuinely permanent failure disables the
rest.
"""
from __future__ import annotations

from dataclasses import dataclass

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.match.dedupe import dedupe_by_phone
from bbb_scraper.match.matcher import match_datasets
from bbb_scraper.match.merge import build_master_table
from bbb_scraper.yelp.client import YelpAPIError, YelpClient, YelpConfigError
from bbb_scraper.yelp.extract import YelpExtractor

logger = get_logger(__name__)

# Stop making Yelp calls once the daily quota drops this low -- leaves a
# little headroom rather than running it to exactly zero mid-batch.
YELP_MIN_REMAINING = 15


def _is_permanent(exc: Exception) -> bool:
    """Worth disabling Yelp for the REST of the batch over, vs. just this
    one metro. A 429 (real rate-limit signal) or a 401/403 (bad/revoked
    key -- retrying won't fix it) are permanent; retrying won't help
    either. Everything else -- a 5xx, a timeout, a dropped connection --
    is an ordinary transient failure every other client in this project
    already shrugs off per-item, so Yelp shouldn't be the one exception
    that turns a single blip into losing the rest of a multi-hour batch
    (see this module's own docstring for the real incident)."""
    if isinstance(exc, YelpAPIError):
        return exc.status_code in (429, 401, 403)
    return False


@dataclass
class YelpEnrichmentState:
    enabled: bool
    client: YelpClient | None = None
    reason_off: str | None = None

    def disable(self, reason: str) -> None:
        if self.enabled:
            logger.warning("Yelp enrichment off for the rest of the batch: %s", reason)
        self.enabled = False
        self.reason_off = reason


def open_yelp_enrichment(enabled: bool) -> YelpEnrichmentState:
    """Build the state once, at the start of a batch. `enabled=False`
    (a --no-yelp run) or a missing key both yield a disabled state -- not
    an error."""
    if not enabled:
        return YelpEnrichmentState(enabled=False, reason_off="disabled")
    try:
        return YelpEnrichmentState(enabled=True, client=YelpClient())
    except YelpConfigError as exc:
        return YelpEnrichmentState(enabled=False, reason_off=str(exc))


def enrich_bbb_with_yelp(
    bbb_records: list[dict], term: str, location: str, state: YelpEnrichmentState
) -> list[dict]:
    """BBB records -> the wide BBB|Yelp master table, BBB-primary (matched +
    bbb_only rows, no yelp_only tail). If Yelp is off / exhausted / errors,
    every row just comes back `bbb_only`.

    Both sides are deduped by phone number first (dedupe_by_phone -- a no-op
    if the caller already did it, e.g. the batch scraper's scrape_one_metro;
    kept here too so this function is correct on its own for any caller).
    """
    bbb_records = dedupe_by_phone(bbb_records)

    yelp_rows: list[dict] = []
    if state.enabled and state.client is not None:
        remaining = state.client.last_rate_limit.get("remaining")
        if remaining is not None and remaining < YELP_MIN_REMAINING:
            state.disable(f"daily quota down to {remaining}")
        else:
            try:
                businesses = YelpExtractor(state.client).search_area(term, location)
                yelp_rows = dedupe_by_phone([b.to_match_dict() for b in businesses])
                logger.info(
                    "Yelp: %d businesses for %r (quota remaining: %s)",
                    len(yelp_rows), location,
                    state.client.last_rate_limit.get("remaining"),
                )
            except Exception as exc:  # noqa: BLE001 -- any failure -> BBB-only, keep going
                if _is_permanent(exc):
                    state.disable(f"call failed ({exc!s})")
                else:
                    # Transient -- this one metro comes back BBB-only
                    # (yelp_rows stays []), but `state` is untouched so the
                    # NEXT metro gets a fresh attempt rather than being
                    # written off for a blip that had nothing to do with it.
                    logger.warning(
                        "Yelp call failed for %r, treating as transient -- BBB-only for this "
                        "metro, still enabled for the next one: %s", location, exc,
                    )

    outcome = match_datasets(bbb_records, yelp_rows)
    if yelp_rows:
        s = outcome.summary
        logger.info(
            "  matched %d/%d BBB rows to Yelp (%d confident)",
            s["matched"], s["bbb_total"], s["confident"],
        )
    return build_master_table(outcome, include_yelp_only=False)
