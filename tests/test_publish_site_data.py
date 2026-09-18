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
        "lead_priority_score": "81.0",
        "reputation_divergence_flag": "0", "low_review_volume_flag": "1",
    }
    rec = select_public_fields_from_master(row)
    assert rec["name"] == "Italy Blue Auto Sales LLC"
    assert rec["last_updated"] == "2026-09-03"
    assert rec["on_yelp"] is True
    assert rec["yelp_name"] == "Italy Blue Auto Sales"
    assert rec["yelp_rating"] == 1.9
    assert rec["yelp_review_count"] == 9  # int, not 9.0
    assert rec["lead_priority_score"] == 81.0
    assert rec["low_review_volume_flag"] == 1  # int flag, not 1.0
    assert rec["categories"] == ["Used Car Dealers"]
    assert "yelp_phone" not in rec and "yelp_id" not in rec
    assert callable(publish_master_rows)


def test_mapquest_confirmed_yelp_data_fills_in_the_yelp_columns_when_unmatched():
    """2026-09-17, Nick's call: a business with no official Fusion API match
    (bbb_only) but a real MapQuest match whose rating_provider genuinely
    confirms Yelp should still show up as on_yelp with real rating/review
    data on the published site -- confirmed common in real data (24 such
    businesses vs. 18 official matches in one real test metro), not a rare
    edge case worth leaving blank. yelp_url comes from mapquest_rating_url,
    a real yelp.com link MapQuest's GraphQL API returns alongside the
    rating (confirmed live via schema-validation probing), not the
    mapquest.com page -- yelp_via_mapquest still flags that it was found
    via MapQuest rather than an official Fusion API match."""
    row = {
        "match_status": "bbb_only",
        "bbb_name": "Wyattworks Plumbing, Inc.", "bbb_rating": "A+",
        "bbb_scraped_at": "2026-09-17T12:00:00+00:00",
        "mapquest_rating_provider": "YELP", "mapquest_rating_value": "4",
        "mapquest_review_count": "46",
        "mapquest_url": "https://www.mapquest.com/us/north-carolina/wyattworks-plumbing-303858671",
        "mapquest_rating_url": "https://www.yelp.com/biz/wyattworks-plumbing-charlotte?utm_source=mapquest",
    }
    rec = select_public_fields_from_master(row)
    assert rec["on_yelp"] is True
    assert rec["yelp_rating"] == 4
    assert rec["yelp_review_count"] == 46
    assert rec["yelp_url"] == "https://www.yelp.com/biz/wyattworks-plumbing-charlotte?utm_source=mapquest"
    assert rec["yelp_via_mapquest"] is True
    assert rec["yelp_name"] == ""  # no such field exists via MapQuest -- left blank, not guessed


def test_official_yelp_match_takes_precedence_over_mapquest_at_publish_time():
    row = {
        "match_status": "matched",
        "bbb_name": "Co", "bbb_rating": "A+", "bbb_scraped_at": "2026-09-17T12:00:00+00:00",
        "yelp_name": "Real Co", "yelp_rating": "4.5", "yelp_review_count": "200",
        "yelp_url": "https://www.yelp.com/biz/real-co",
        "mapquest_rating_provider": "YELP", "mapquest_rating_value": "1.0",
        "mapquest_review_count": "40", "mapquest_url": "https://www.mapquest.com/us/x/co-1",
    }
    rec = select_public_fields_from_master(row)
    assert rec["yelp_rating"] == 4.5  # the official value, not MapQuest's disagreeing one
    assert rec["yelp_review_count"] == 200
    assert rec["yelp_url"] == "https://www.yelp.com/biz/real-co"
    assert rec["yelp_via_mapquest"] is False


def test_non_yelp_mapquest_provider_does_not_leak_into_yelp_columns():
    row = {
        "match_status": "bbb_only",
        "bbb_name": "Co", "bbb_rating": "A+", "bbb_scraped_at": "2026-09-17T12:00:00+00:00",
        "mapquest_rating_provider": "TRIPADVISOR", "mapquest_rating_value": "1.0",
        "mapquest_review_count": "40",
    }
    rec = select_public_fields_from_master(row)
    assert rec["on_yelp"] is False
    assert rec["yelp_rating"] is None
    assert rec["yelp_via_mapquest"] is False


def test_most_recent_review_source_is_labeled_yelp_angi_or_bbb_for_display():
    """2026-09-17, Nick's call: the site shows which platform found the
    latest (negative) review, on hover. bbb_scraper.sentiment.analyze
    stores the raw ReviewSentiment.source vocabulary ("mapquest"/"angi"/
    "bbb") -- this is the one place that translates "mapquest" to the
    "Yelp" label the rest of the site already uses for MapQuest-sourced
    data, and "bbb" to "BBB" now that BBB is a real, equally-eligible
    source for these fields too (previously excluded entirely)."""
    row = {
        "match_status": "bbb_only",
        "bbb_name": "Co", "bbb_rating": "A+", "bbb_scraped_at": "2026-09-17T12:00:00+00:00",
        "most_recent_review_date": "2026-08-01", "most_recent_review_source": "mapquest",
        "most_recent_negative_review_date": "2026-07-01", "most_recent_negative_review_source": "angi",
    }
    rec = select_public_fields_from_master(row)
    assert rec["most_recent_review_source"] == "Yelp"
    assert rec["most_recent_negative_review_source"] == "Angi"


def test_raw_review_data_reaches_the_published_record_for_full_export():
    """2026-09-18, Nick's ask: an Excel/CSV export ships the FULL record
    per app.js's own exportableRows() design -- but the raw per-review
    data (individual review text/rating/date, not just the aggregate
    top_complaint/most_recent_* facts) never actually reached the
    published record, so "full export" wasn't full. These are JSON-string
    columns written post-hoc by the batch (bbb/mapquest/angi review
    fetch, sentiment analysis) -- same decode as categories/contacts/
    socials, parsed into real lists here, not left as JSON text."""
    row = {
        "match_status": "bbb_only",
        "bbb_name": "Co", "bbb_rating": "A+", "bbb_scraped_at": "2026-09-17T12:00:00+00:00",
        "bbb_reviews": '[{"text": "Great work", "rating": 5, "date": "2026-01-01"}]',
        "mapquest_reviews": '[{"text": "Terrible", "rating": 1.0, "date": "2026-02-01"}]',
        "angi_reviews": '[{"text": "Fine", "rating": 3, "date_label": "March 2026"}]',
        "review_sentiment": '[{"source": "bbb", "sentiment": "positive", "severity": 1}]',
    }
    rec = select_public_fields_from_master(row)
    assert rec["bbb_reviews"] == [{"text": "Great work", "rating": 5, "date": "2026-01-01"}]
    assert rec["mapquest_reviews"] == [{"text": "Terrible", "rating": 1.0, "date": "2026-02-01"}]
    assert rec["angi_reviews"] == [{"text": "Fine", "rating": 3, "date_label": "March 2026"}]
    assert rec["review_sentiment"] == [{"source": "bbb", "sentiment": "positive", "severity": 1}]


def test_missing_raw_review_data_publishes_as_empty_lists_not_a_crash():
    row = {
        "match_status": "bbb_only",
        "bbb_name": "Co", "bbb_rating": "A+", "bbb_scraped_at": "2026-09-17T12:00:00+00:00",
    }
    rec = select_public_fields_from_master(row)
    assert rec["bbb_reviews"] == []
    assert rec["mapquest_reviews"] == []
    assert rec["angi_reviews"] == []
    assert rec["review_sentiment"] == []

    row["most_recent_negative_review_source"] = "bbb"
    rec = select_public_fields_from_master(row)
    assert rec["most_recent_negative_review_source"] == "BBB"


def test_facebook_enrichment_data_reaches_the_published_record():
    """2026-09-18, Nick's ask: Facebook social links (for site/js/app.js's
    Excel per-platform columns), the rating signal that now feeds
    lead_priority_score, and email provenance all need to actually reach
    the published record, same as the other post-hoc enrichment columns
    above."""
    row = {
        "match_status": "bbb_only",
        "bbb_name": "Co", "bbb_rating": "A+", "bbb_scraped_at": "2026-09-17T12:00:00+00:00",
        "bbb_email": "found@facebook-only.com", "bbb_email_source": "facebook",
        "facebook_status": "ok",
        "facebook_social_links": '[{"platform": "instagram", "url": "https://instagram.com/co"}]',
        "facebook_recommend_percentage": 74, "facebook_review_count": 12,
    }
    rec = select_public_fields_from_master(row)
    assert rec["email"] == "found@facebook-only.com"
    assert rec["email_source"] == "facebook"
    assert rec["facebook_status"] == "ok"
    assert rec["facebook_social_links"] == [{"platform": "instagram", "url": "https://instagram.com/co"}]
    assert rec["facebook_recommend_percentage"] == 74
    assert rec["facebook_review_count"] == 12


def test_missing_facebook_data_publishes_as_empty_not_a_crash():
    row = {
        "match_status": "bbb_only",
        "bbb_name": "Co", "bbb_rating": "A+", "bbb_scraped_at": "2026-09-17T12:00:00+00:00",
    }
    rec = select_public_fields_from_master(row)
    assert rec["facebook_status"] == ""
    assert rec["facebook_social_links"] == []
    assert rec["facebook_recommend_percentage"] is None
    assert rec["facebook_review_count"] is None
    assert rec["email_source"] == ""


def test_missing_review_source_publishes_as_empty_string_not_none():
    row = {
        "match_status": "bbb_only",
        "bbb_name": "Co", "bbb_rating": "A+", "bbb_scraped_at": "2026-09-17T12:00:00+00:00",
    }
    rec = select_public_fields_from_master(row)
    assert rec["most_recent_review_source"] == ""
    assert rec["most_recent_negative_review_source"] == ""


def test_angi_only_row_publishes_with_identity_fields_from_angi():
    """2026-09-17, Nick's call: Angi is now a real discovery source (see
    bbb_scraper/angi/enrich.py's angi_only rows) -- the published record's
    core identity (name/phone/city/state/website/profile_url) falls back
    to the angi_ fields since there's no bbb_ equivalent at all, while
    BBB-specific concepts (grade, accreditation) correctly stay blank
    rather than being faked from Angi data."""
    row = {
        "match_status": "angi_only",
        "bbb_name": "", "bbb_phone": "", "bbb_city": "", "bbb_state": "",
        "bbb_website": "", "bbb_profile_url": "", "bbb_rating": "", "bbb_accredited": "",
        "angi_name": "BendFlow Plumbing", "angi_phone": "7044914939",
        "angi_city": "Indian Trail", "angi_state": "NC",
        "angi_website": "www.bendflowplumbing.com",
        "angi_profile_url": "https://www.angi.com/companylist/us/nc/indian-trail/bendflow-plumbing-reviews-1.htm",
        "angi_overall_rating": "5", "angi_review_count": "4",
        "lead_priority_score": "72.0",  # select_public_fields_from_master reads whatever
        # intel is already on the row -- its caller (publish_master_rows) is what calls
        # recompute_intel first; this fixture mirrors the other tests in this file that
        # set lead_priority_score by hand for the same reason.
    }
    rec = select_public_fields_from_master(row)
    assert rec["name"] == "BendFlow Plumbing"
    assert rec["phone"] == "7044914939"
    assert rec["city"] == "Indian Trail"
    assert rec["state"] == "NC"
    assert rec["website"] == "www.bendflowplumbing.com"
    assert rec["profile_url"] == "https://www.angi.com/companylist/us/nc/indian-trail/bendflow-plumbing-reviews-1.htm"
    assert rec["rating"] == ""  # no BBB grade -- never faked from Angi's own star rating
    assert rec["accredited"] is False
    assert rec["lead_priority_score"] == 72.0


def test_bbb_identity_wins_over_angi_when_both_present():
    """A matched row (both BBB and Angi present) should use BBB's own
    identity fields, not silently prefer Angi's -- the fallback is for
    when BBB is genuinely absent, not a general preference."""
    row = {
        "match_status": "matched",
        "bbb_name": "Real BBB Name", "bbb_phone": "3055550100", "bbb_city": "Charlotte",
        "angi_name": "Different Angi Listing Name", "angi_phone": "7045559999", "angi_city": "Monroe",
    }
    rec = select_public_fields_from_master(row)
    assert rec["name"] == "Real BBB Name"
    assert rec["phone"] == "3055550100"
    assert rec["city"] == "Charlotte"


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
        # a real complaint history (2026-09-15's complaints-based signal,
        # see merge.py's _bbb_complaints_signal) so this NR-graded row has
        # a genuine, non-null score and survives publish-time curation
        # (bbb_scraper/curate.py) -- an all-null-signal row is exactly what
        # curation is *supposed* to drop, which would be a false failure
        # here, not a real one; this fixture should look like a real row.
        "bbb_reviews_complaints": '{"complaints_total": 3}',
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


def test_manifest_entry_published_at_is_a_real_timestamp(tmp_path, monkeypatch):
    """2026-09-17, Nick's ask: the site's lists-view "Most recent" sort
    needs a per-dataset timestamp -- the manifest array's own order was
    never usable for that (re-sorted (metro, industry) on every publish,
    see _write_dataset). Every publish -- first time or a republish --
    must set this to a real, parseable ISO timestamp, not leave it stale
    or blank."""
    monkeypatch.setattr(psd, "SITE_DATA_DIR", tmp_path)
    monkeypatch.setattr(psd, "MANIFEST_PATH", tmp_path / "manifest.json")

    entry = psd.publish_records(
        [{"name": "A Co", "rating": "A+", "scraped_at": "2026-09-11T00:00:00+00:00"}],
        "Plumbers", "Chicago, IL",
    )
    from datetime import datetime
    datetime.fromisoformat(entry["published_at"])  # raises if not a real timestamp

    # A republish of the SAME dataset must refresh it, not keep the old one.
    first_published_at = entry["published_at"]
    entry = psd.publish_records(
        [{"name": "A Co", "rating": "A+", "scraped_at": "2026-09-11T00:00:00+00:00"}],
        "Plumbers", "Chicago, IL",
    )
    assert entry["published_at"] >= first_published_at


def test_cli_main_runs_end_to_end_without_crashing(tmp_path, monkeypatch, capsys):
    """Full main() smoke test -- the KeyError above only ever fired here and
    in the batch script, neither of which any prior test actually invoked."""
    monkeypatch.setattr(psd, "SITE_DATA_DIR", tmp_path)
    monkeypatch.setattr(psd, "MANIFEST_PATH", tmp_path / "manifest.json")

    csv_path = tmp_path / "bbb.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "rating", "phone", "scraped_at"])
        w.writeheader()
        # A phone number + a mid-grade (C, in the "salvageable middle" band)
        # so this real-shaped row clears curate_for_publish's score cutoff
        # (2026-09-16: raised to 50 -- a B grade with no phone, the old
        # fixture, now correctly gets curated out at publish time, same as
        # it should for a real business with no reachability and no
        # demonstrated fixable problem).
        w.writerow({"name": "A Co", "rating": "C", "phone": "3125550100",
                    "scraped_at": "2026-09-11T00:00:00+00:00"})

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
