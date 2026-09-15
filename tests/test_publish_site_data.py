"""publish_site_data record-shaping (pure functions only -- no file I/O)."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish_site_data as psd
from publish_site_data import (
    _bbb_only_master,
    select_public_fields_from_master,
)


def _publish_one(bbb_record: dict) -> dict:
    return select_public_fields_from_master(_bbb_only_master([bbb_record])[0])


def test_bbb_only_record_gets_last_updated_and_bbb_scoring_but_no_yelp():
    rec = _publish_one({
        "name": "Ramirez Motors", "phone": "(305) 642-4409", "rating": "C",
        "years_in_business": "15", "accredited": "True",
        "categories": ["Used Car Dealers"], "scraped_at": "2026-09-03T15:43:00+00:00",
    })
    assert rec["name"] == "Ramirez Motors"
    assert rec["phone"] == "(305) 642-4409"
    assert rec["last_updated"] == "2026-09-03"
    assert rec["on_yelp"] is False
    assert rec["yelp_rating"] is None
    assert rec["categories"] == ["Used Car Dealers"]  # list fields survive the master round-trip
    # BBB-side intelligence is still computed
    assert rec["bbb_grade_num"] == 2.0
    assert rec["reputation_score"] is not None
    assert rec["lead_priority_score"] is not None
    assert rec["review_need_score"] is None  # Yelp-specific


def test_matched_master_row_carries_yelp_and_intel():
    from publish_site_data import publish_master_rows

    row = {
        "match_status": "matched",
        "bbb_name": "Italy Blue Auto Sales LLC", "bbb_rating": "B-",
        "bbb_scraped_at": "2026-09-03T12:00:00+00:00",
        "bbb_categories": '["Used Car Dealers"]',
        "yelp_name": "Italy Blue Auto Sales", "yelp_rating": "1.9",
        "yelp_review_count": "9", "yelp_url": "https://www.yelp.com/biz/italy-blue",
        "reputation_score": "50.1", "lead_priority_score": "81.0",
        "reputation_divergence_flag": "0", "low_review_volume_flag": "1",
    }
    rec = select_public_fields_from_master(row)
    assert rec["name"] == "Italy Blue Auto Sales LLC"
    assert rec["last_updated"] == "2026-09-03"
    assert rec["on_yelp"] is True
    assert rec["yelp_name"] == "Italy Blue Auto Sales"
    assert rec["yelp_rating"] == 1.9
    assert rec["yelp_review_count"] == 9  # int, not 9.0
    assert rec["reputation_score"] == 50.1
    assert rec["low_review_volume_flag"] == 1  # int flag, not 1.0
    assert rec["categories"] == ["Used Car Dealers"]
    assert "yelp_phone" not in rec and "yelp_id" not in rec
    assert callable(publish_master_rows)


def test_contact_readiness_fields_surface_on_the_published_record():
    reachable = _publish_one({
        "name": "Goode Plumbing", "rating": "B", "phone": "(773) 930-3451",
        "principal_contact": "Glenn Wright, Manager", "scraped_at": "2026-09-11T00:00:00+00:00",
    })
    assert reachable["has_phone"] == 1
    assert reachable["has_named_contact"] == 1
    assert reachable["contact_readiness"] == "Phone + named contact"
    assert reachable["contact_readiness_score"] == 100
    assert isinstance(reachable["has_phone"], int)  # not 1.0

    unreachable = _publish_one({
        "name": "No Way To Call LLC", "rating": "B", "scraped_at": "2026-09-11T00:00:00+00:00",
    })
    assert unreachable["has_phone"] == 0
    assert unreachable["contact_readiness"] == "No direct contact info"
    assert unreachable["contact_readiness_score"] == 0
    # missing phone should have pulled lead_priority_score down vs. the reachable twin
    assert unreachable["lead_priority_score"] < reachable["lead_priority_score"]


def test_bbb_complaints_surface_from_reviews_complaints_json():
    rec = _publish_one({
        "name": "Looks Fine Motors", "rating": "A+", "scraped_at": "2026-09-04T00:00:00+00:00",
        "reviews_complaints": '{"reviews_total": 1, "average_rating": 1, "complaints_total": 8}',
    })
    assert rec["bbb_complaints_total"] == 8
    assert rec["reputation_divergence_flag"] == 1  # clean grade, 8 complaints


def test_publish_master_rows_refreshes_stale_intel_columns(tmp_path, monkeypatch):
    """Regression: a master CSV written before a scoring change carries
    whatever intel columns existed when it was written. publish_master_rows
    must recompute them against today's formula, not just pass a stale
    snapshot through to the published site record."""
    monkeypatch.setattr(psd, "SITE_DATA_DIR", tmp_path)
    monkeypatch.setattr(psd, "MANIFEST_PATH", tmp_path / "manifest.json")

    from publish_site_data import publish_master_rows
    stale_row = {
        "match_status": "bbb_only", "bbb_name": "Goode Plumbing", "bbb_rating": "NR",
        "bbb_phone": "(773) 930-3451", "bbb_scraped_at": "2026-09-11T00:00:00+00:00",
        # this dataset predates has_phone/contact_readiness entirely, and
        # carries an intentionally-wrong lead_priority_score to prove it
        # gets overwritten rather than trusted as-is
        "lead_priority_score": 999,
    }
    entry = publish_master_rows([stale_row], "Plumbers", "Chicago, IL")
    published = json.loads((tmp_path / entry["file"]).read_text(encoding="utf-8"))
    assert published[0]["has_phone"] == 1
    assert published[0]["contact_readiness"] == "Phone only"
    assert published[0]["lead_priority_score"] != 999


def test_manifest_entry_has_exactly_the_keys_callers_rely_on(tmp_path, monkeypatch):
    """Regression test: batch_scrape_metros.py's per-metro summary print and
    this module's own CLI main() both read specific keys off the entry
    _write_dataset returns (file/record_count/yelp_matched/top_lead_score).
    2026-09-11: a rename (has_intel -> has_yelp) left both call sites
    reading the old key -- a KeyError that killed a live 9.5-hour batch run
    partway through (see bbb-scraper-status memory). Pin the shape here so
    a future rename fails fast in CI, not overnight in production."""
    monkeypatch.setattr(psd, "SITE_DATA_DIR", tmp_path)
    monkeypatch.setattr(psd, "MANIFEST_PATH", tmp_path / "manifest.json")

    entry = psd.publish_records(
        [{"name": "A Co", "rating": "A+", "scraped_at": "2026-09-11T00:00:00+00:00"}],
        "Plumbers", "Chicago, IL",
    )
    assert entry.keys() >= {"id", "industry", "metro", "record_count", "has_yelp",
                            "yelp_matched", "top_lead_score", "file"}
    # exactly what the two print f-strings dereference -- would KeyError otherwise
    entry["file"], entry["record_count"], entry["yelp_matched"], entry["top_lead_score"]


def test_cli_main_runs_end_to_end_without_crashing(tmp_path, monkeypatch, capsys):
    """Full main() smoke test -- the KeyError above only ever fired here and
    in the batch script, neither of which any prior test actually invoked."""
    monkeypatch.setattr(psd, "SITE_DATA_DIR", tmp_path)
    monkeypatch.setattr(psd, "MANIFEST_PATH", tmp_path / "manifest.json")

    csv_path = tmp_path / "bbb.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "rating", "scraped_at"])
        w.writeheader()
        w.writerow({"name": "A Co", "rating": "B", "scraped_at": "2026-09-11T00:00:00+00:00"})

    monkeypatch.setattr(sys, "argv", ["publish_site_data.py", str(csv_path),
                                      "--industry", "Plumbers", "--metro", "Chicago, IL"])
    assert psd.main() == 0
    assert "Published 1 record" in capsys.readouterr().out


def test_dead_website_flag_and_status_reach_the_public_record():
    """bbb_scraper.webcheck's output survives the master-table round-trip
    into what the site actually publishes -- website_status (the human-
    readable reason) and website_dead_flag (the signal the site badges/
    flags on)."""
    rec = _publish_one({
        "name": "Lapsed Domain Co", "phone": "(305) 555-0100", "rating": "B",
        "website": "http://lapseddomainco.com",
        "website_dead": True, "website_status": "dead_parked",
    })
    assert rec["website_status"] == "dead_parked"
    assert rec["website_dead_flag"] == 1


def test_no_webcheck_data_is_not_flagged():
    rec = _publish_one({"name": "Never Checked Co", "phone": "(305) 555-0100", "rating": "B"})
    assert rec["website_dead_flag"] == 0


# --- Angi (phone-matched by bbb_scraper/angi/enrich.py before this point) ---
# Constructed as a master-table row directly (bbb_*/angi_*/match_status,
# on_angi already computed) -- angi_* fields aren't BBB_FIELDS, so they
# can't flow through _bbb_only_master/_publish_one's raw-BBB-record path;
# same reason test_matched_master_row_carries_yelp_and_intel builds a row
# by hand instead of using _publish_one.

def test_angi_match_surfaces_rating_specialties_and_link():
    row = {
        "match_status": "bbb_only", "bbb_name": "Ace Plumbing", "bbb_rating": "A",
        "bbb_phone": "(773) 561-0867", "bbb_scraped_at": "2026-09-14T00:00:00+00:00",
        "on_angi": "1", "angi_name": "Ace Plumbing Co", "angi_overall_rating": "4.8",
        "angi_review_count": "35", "angi_profile_url": "https://www.angi.com/companylist/us/il/x/ace.htm",
        "angi_categories": "Drain Cleaning; Water Heater Install",
        "angi_is_super_service_award_winner": "True",
    }
    rec = select_public_fields_from_master(row)
    assert rec["on_angi"] == 1
    assert rec["angi_name"] == "Ace Plumbing Co"
    assert rec["angi_rating"] == 4.8
    assert rec["angi_review_count"] == 35
    assert rec["angi_url"] == "https://www.angi.com/companylist/us/il/x/ace.htm"
    assert rec["specialties"] == "Drain Cleaning; Water Heater Install"
    assert rec["angi_super_service_award"] is True


def test_no_angi_match_leaves_angi_fields_blank_not_stale():
    row = {
        "match_status": "bbb_only", "bbb_name": "No Angi Co", "bbb_rating": "B",
        "bbb_scraped_at": "2026-09-14T00:00:00+00:00", "on_angi": "0",
        # a stale angi_name could exist on an old row if a phone was ever
        # reused -- must not surface once on_angi says "not matched"
        "angi_name": "Stale Leftover Name",
    }
    rec = select_public_fields_from_master(row)
    assert rec["on_angi"] == 0
    assert rec["angi_name"] == ""
    assert rec["angi_rating"] is None
    assert rec["specialties"] == ""
    assert rec["angi_super_service_award"] is False


def test_accredited_and_years_in_business_are_real_types_not_csv_strings():
    """Regression, caught in a real diff review 2026-09-14: publish_dataset/
    publish_master_csv read rows through csv.DictReader, which stringifies
    *everything* -- a Python False/13 in the source CSV becomes the literal
    text "False"/"13". 14 already-published datasets got silently
    re-shipped that way by a --master re-publish (backfilling the
    dead-website check). Must come out correctly typed regardless of
    whether the value arrived as a real bool/int (in-memory scrape path) or
    already CSV-stringified (--master/csv_path path) -- both are real
    inputs to this same function."""
    from_strings = _publish_one({
        "name": "String Co", "rating": "A+", "accredited": "False",
        "years_in_business": "13", "scraped_at": "2026-09-11T00:00:00+00:00",
    })
    assert from_strings["accredited"] is False
    assert from_strings["years_in_business"] == 13
    assert isinstance(from_strings["years_in_business"], int)

    accredited_string = _publish_one({
        "name": "Accredited Co", "rating": "A+", "accredited": "True",
        "scraped_at": "2026-09-11T00:00:00+00:00",
    })
    assert accredited_string["accredited"] is True

    from_native = _publish_one({
        "name": "Native Co", "rating": "A+", "accredited": True,
        "years_in_business": 7, "scraped_at": "2026-09-11T00:00:00+00:00",
    })
    assert from_native["accredited"] is True
    assert from_native["years_in_business"] == 7
    assert isinstance(from_native["years_in_business"], int)


def test_missing_years_in_business_is_null_not_blank_string():
    rec = _publish_one({"name": "No Years Co", "rating": "NR", "scraped_at": "2026-09-11T00:00:00+00:00"})
    assert rec["years_in_business"] is None
