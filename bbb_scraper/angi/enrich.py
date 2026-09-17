"""
Angi enrichment of an already-built BBB(+Yelp) master table -- AND, since
2026-09-17 (Nick's call), a real discovery source in its own right: an Angi
business with no matching BBB row becomes a genuinely new "angi_only" row
(match_status), not silently dropped the way it was before. Angi's own
coverage is nothing like BBB's (167 home-services categories vs. BBB's much
broader taxonomy -- see data/reference/README.md), so this still isn't
going to surface every possible business, the same way not every BBB row
matches a Yelp listing either -- but a real Angi business with no BBB
listing at all no longer just vanishes.

Matched by exact phone number first (Nick's original 2026-09-14 call --
phone is decisive on its own, no probabilistic scoring needed for an exact
10-digit match). A phone miss now falls back to a name+city/zip check
(_same_business) before concluding it's really a new business: Angi
profiles carry no lat/lon, so the full weighted BBB<->Yelp-style matcher's
geo signal isn't available here, and without SOME fallback, a business
that changed phone numbers on one platform but not the other -- or was
never phone-matchable to begin with -- would get counted twice: once as
its real BBB row, once as a spurious angi_only duplicate of the same real
business. Real motivation, not hypothetical: one Charlotte Plumbers test
had a 5/84 phone-match rate against 714 real Angi businesses -- a low bar
that would otherwise turn a real dedup problem into hundreds of duplicate
rows per metro.
"""
from __future__ import annotations

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.match.merge import ANGI_FIELDS, BBB_FIELDS, YELP_FIELDS, recompute_intel
from bbb_scraper.match.normalize import city_key, name_similarity, phone_key, zip5

logger = get_logger(__name__)

# Confident enough to treat a phone-miss as the same business anyway --
# same city or zip (Angi has no lat/lon for a real distance check) AND a
# high name_similarity (which already hard-gates on shared-generic-words-
# only names and on zero distinctive-word overlap -- see its own
# docstring). Chosen close to the BBB<->Yelp matcher's own "confident" band
# (0.80) while being a narrower two-signal check (name + place, no phone/
# zip/geo blend) than that matcher's full weighted score -- deliberately
# conservative: a false MERGE here silently drops a real, distinct business
# from the list entirely, which is worse than the false SPLIT it's meant
# to prevent (a duplicate row is visible and correctable; a wrongly-merged
# business is just gone).
_NAME_MATCH_THRESHOLD = 0.72


def _same_business(angi: dict, row: dict) -> bool:
    """Name+city/zip fallback, used only when phone didn't match -- see the
    module docstring for why phone alone isn't enough to rule out a
    duplicate. `row` is always a BBB-anchored row at the call site (this
    runs before any angi_only rows exist), so its own identity is read
    straight from bbb_city/bbb_postal_code/bbb_name."""
    angi_city, angi_zip = city_key(angi.get("city")), zip5(angi.get("zip_code"))
    row_city, row_zip = city_key(row.get("bbb_city")), zip5(row.get("bbb_postal_code"))
    same_place = (angi_city and angi_city == row_city) or (angi_zip and angi_zip == row_zip)
    if not same_place:
        return False
    return name_similarity(angi.get("name"), row.get("bbb_name")) >= _NAME_MATCH_THRESHOLD


def _new_angi_only_row(angi: dict) -> dict:
    """A genuinely new business, found ONLY by Angi. bbb_*/yelp_* fields
    are left blank -- never faked from Angi's own data -- and everything
    that needs this business's real name/phone/city regardless of source
    reads it through merge.py's _effective_* accessors instead. yelp_*
    stays blank here specifically because the official Fusion API matcher
    already ran, against the BBB-only list, before this row ever existed;
    MapQuest's own separate enrichment pass still reaches it normally
    (driven by _effective_name/_phone/_city, not a hardcoded bbb_ prefix --
    see batch_scrape_metros.py's _enrich_metro_with_mapquest), so a real
    Yelp rating can still surface here through that fallback door even
    though the Fusion matcher itself never saw it."""
    row: dict = {"match_status": "angi_only", "match_confidence": "", "match_band": "", "match_signals": ""}
    for field in BBB_FIELDS:
        row[f"bbb_{field}"] = ""
    for field in YELP_FIELDS:
        row[f"yelp_{field}"] = ""
    for field in ANGI_FIELDS:
        row[f"angi_{field}"] = angi.get(field, "")
    return recompute_intel(row)


def enrich_with_angi(master_rows: list[dict], angi_records: list[dict]) -> list[dict]:
    """`master_rows`: build_master_table output (or a master CSV read back
    in) -- reads `bbb_phone`/`bbb_city`/`bbb_postal_code`/`bbb_name`.
    `angi_records`: dicts shaped like bbb_scraper.angi.models.BusinessDetail's
    fields (what scripts/scrape_angi_category.py's CSV rows already look
    like once read with csv.DictReader -- `phone`, `name`, `overall_rating`, ...).

    Returns NEW row dicts (doesn't mutate the input): every original
    master row, `angi_<field>` filled in wherever a match was found (by
    phone, or by the name+city/zip fallback), PLUS one new `angi_only` row
    per real Angi business that matched nothing -- see the module and
    _new_angi_only_row docstrings. Every _INTEL column is recomputed on
    every row this touches, so lead_priority_score/etc. reflect the new
    angi_* fields (and, for brand-new rows, exist at all) immediately -- a
    caller never needs to remember to call recompute_intel separately.

    A phone number shared by more than one Angi record (rare -- a national
    call-center brand operating under several listing names, say) is
    treated as the same business throughout: the first one seen is what
    phone-matched rows get, and the rest are never eligible to become a
    "new" row of their own either (they're the same real business, just
    listed twice).
    """
    angi_by_phone: dict[str, dict] = {}
    used: set[int] = set()  # id() of angi_records dicts already accounted for by SOME row
    dupe_phones = 0
    for record in angi_records:
        key = phone_key(record.get("phone"))
        if not key:
            continue
        if key in angi_by_phone:
            dupe_phones += 1
            used.add(id(record))  # same business as whichever record already claimed this phone
            continue
        angi_by_phone[key] = record
    if dupe_phones:
        logger.info("angi enrich: %d Angi record(s) shared a phone already claimed by another", dupe_phones)

    out: list[dict] = []
    matched_by_phone = 0
    matched_by_fallback = 0
    for row in master_rows:
        row = dict(row)
        key = phone_key(row.get("bbb_phone"))
        angi = angi_by_phone.get(key) if key else None
        if angi is not None:
            matched_by_phone += 1
        else:
            for candidate in angi_records:
                if id(candidate) in used:
                    continue
                if _same_business(candidate, row):
                    angi = candidate
                    matched_by_fallback += 1
                    break
        if angi is not None:
            used.add(id(angi))
        for field in ANGI_FIELDS:
            row[f"angi_{field}"] = (angi or {}).get(field, "")
        out.append(recompute_intel(row))

    new_rows = [_new_angi_only_row(record) for record in angi_records if id(record) not in used]

    logger.info(
        "angi enrich: matched %d/%d master row(s) to Angi (%d by phone, %d by name+city/zip fallback); "
        "%d new angi_only row(s) from %d Angi record(s) with no BBB match",
        matched_by_phone + matched_by_fallback, len(master_rows), matched_by_phone, matched_by_fallback,
        len(new_rows), len(angi_records),
    )
    return out + new_rows
