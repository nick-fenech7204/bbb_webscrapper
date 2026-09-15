"""
Bolt-on Angi enrichment of an already-built BBB(+Yelp) master table, the
same shape as bbb_scraper/webcheck's relationship to a batch run: a
separate, additive pass over records that already exist, not a rewrite of
match_datasets/build_master_table's own BBB<->Yelp matching.

Matched by exact phone number only (Nick's call, 2026-09-14) -- not
Yelp-style fuzzy name/geo/zip scoring. Phone is decisive enough on its own
for this: two businesses sharing a real 10-digit number are the same
business, full stop, and Angi profiles don't carry lat/lon anyway (so the
geo signal Yelp matching leans on wouldn't be available even if wanted
here). Simpler is also more honest about what this actually is -- a strong,
narrow signal, not a probabilistic best guess.

Angi's own coverage is nothing like BBB's (167 home-services categories vs.
BBB's much broader taxonomy -- see data/reference/README.md), so this is
never going to match every row, the same way not every BBB row matches a
Yelp listing either. Unmatched rows keep every angi_* field blank and score
exactly as they did before -- see merge.py's _reputation_score docstring
for why adding Angi as a signal doesn't move any unmatched row's score.
"""
from __future__ import annotations

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.match.merge import ANGI_FIELDS, recompute_intel
from bbb_scraper.match.normalize import phone_key

logger = get_logger(__name__)


def enrich_with_angi(master_rows: list[dict], angi_records: list[dict]) -> list[dict]:
    """`master_rows`: build_master_table output (or a master CSV read back
    in) -- reads `bbb_phone`. `angi_records`: dicts shaped like
    bbb_scraper.angi.models.BusinessDetail's fields (what
    scripts/scrape_angi_category.py's CSV rows already look like once read
    with csv.DictReader -- `phone`, `name`, `overall_rating`, ...).

    Returns NEW row dicts (doesn't mutate the input) with `angi_<field>`
    added for every field in merge.ANGI_FIELDS, blank on an unmatched row,
    and every _INTEL column recomputed so reputation_score/lead_priority_
    score/etc. reflect the new angi_* fields immediately -- a caller never
    needs to remember to call recompute_intel separately after this.

    A phone number shared by more than one Angi record (rare -- a national
    call-center brand operating under several listing names, say) matches
    whichever one was seen first; every other master row with that same
    BBB phone gets the same Angi match, which is correct (they *are* the
    same phone).
    """
    angi_by_phone: dict[str, dict] = {}
    dupe_phones = 0
    for record in angi_records:
        key = phone_key(record.get("phone"))
        if not key:
            continue
        if key in angi_by_phone:
            dupe_phones += 1
            continue
        angi_by_phone[key] = record
    if dupe_phones:
        logger.info("angi enrich: %d Angi record(s) shared a phone already claimed by another", dupe_phones)

    out: list[dict] = []
    matched = 0
    for row in master_rows:
        row = dict(row)
        key = phone_key(row.get("bbb_phone"))
        angi = angi_by_phone.get(key) if key else None
        for field in ANGI_FIELDS:
            row[f"angi_{field}"] = (angi or {}).get(field, "")
        if angi:
            matched += 1
        out.append(recompute_intel(row))

    logger.info("angi enrich: matched %d/%d master row(s) to %d Angi record(s) by phone",
                matched, len(master_rows), len(angi_records))
    return out
