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
