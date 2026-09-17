"""scripts/analyze_review_sentiment.py -- CSV I/O, row selection, and the
score-refresh fix added 2026-09-16: a checkpoint's lead_priority_score (and
every other _INTEL column) is computed by build_master_table before
sentiment analysis has run, so a business with no other usable signal can
sit at score=None right up until this script gives it one. Without calling
recompute_intel after writing the new sentiment columns, that stale None/
pre-sentiment score would ride along in the CSV forever -- the exact same
"newly-added signal never reaches lead_priority_score" gap
batch_scrape_metros.py's own inline sentiment step and
bbb_scraper.angi.enrich.enrich_with_angi both already guard against.
analyze_business_reviews itself is monkeypatched throughout -- Ollama is
never actually called."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import analyze_review_sentiment as ars

from bbb_scraper.sentiment.models import ReviewSentiment


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_FIELDNAMES = [
    "bbb_name", "bbb_rating", "bbb_phone", "bbb_accredited", "match_status",
    "mapquest_reviews", "bbb_reviews", "angi_reviews",
    "lead_priority_score", "review_sentiment_signal", "review_gap_flag",
    "reputation_divergence_flag", "accredited_but_low_rated",
]


def _checkpoint_row(**overrides) -> dict:
    row = {
        "bbb_name": "Acme Plumbing", "bbb_rating": "NR", "bbb_phone": "555-1234",
        "bbb_accredited": "False", "match_status": "bbb_only",
        "mapquest_reviews": '[{"text": "bad job", "rating": 1.0, "date": "2024-01-01"}]',
        "bbb_reviews": "", "angi_reviews": "",
        # Pre-sentiment master-table state: no other signal at all, so
        # lead_priority_score is genuinely None until sentiment gives it one.
        "lead_priority_score": "", "review_sentiment_signal": "",
        "review_gap_flag": "0", "reputation_divergence_flag": "0",
        "accredited_but_low_rated": "0",
    }
    row.update(overrides)
    return row


def _fake_analyze_business_reviews(row, client):
    results = [ReviewSentiment(
        source="mapquest", text_hash="abc123", date="2024-01-01", rating=1.0,
        sentiment="negative", severity=5, theme="quality of work",
        actionable_for_pitch=True, summary="bad job, missed the fix entirely",
    )]
    aggregate = {
        "review_sentiment_analyzed_count": 1, "review_sentiment_negative_count": 1,
        "most_recent_review_date": "2024-01-01", "most_recent_review_source": "mapquest",
        "most_recent_negative_review_date": "2024-01-01", "most_recent_negative_review_source": "mapquest",
        "avg_review_gap_days": None,
        "top_complaint_theme": "quality of work",
        "top_complaint_summary": "bad job, missed the fix entirely",
    }
    return results, aggregate


def test_in_place_run_recomputes_lead_priority_score_from_new_sentiment(tmp_path, monkeypatch):
    monkeypatch.setattr(ars, "is_available", lambda: True)
    monkeypatch.setattr(ars, "OllamaClient", lambda: _FakeClient())
    monkeypatch.setattr(ars, "analyze_business_reviews", _fake_analyze_business_reviews)

    path = tmp_path / "checkpoint.csv"
    _write_csv(path, _FIELDNAMES, [_checkpoint_row()])

    monkeypatch.setattr(sys, "argv", ["analyze_review_sentiment.py", str(path), "--in-place"])
    assert ars.main() == 0

    out_rows = _read_csv(path)
    assert len(out_rows) == 1
    row = out_rows[0]

    # The sentiment columns landed (already covered by the 2026-09-16
    # _NEW_COLUMNS fix) ...
    assert row["review_sentiment_negative_count"] == "1"
    assert row["top_complaint_theme"] == "quality of work"
    # ... including most_recent_(negative_)review_source, added 2026-09-17
    # -- the exact same _NEW_COLUMNS gotcha this file's own docstring
    # describes, guarded against for these two fields too.
    assert row["most_recent_review_source"] == "mapquest"
    assert row["most_recent_negative_review_source"] == "mapquest"

    # ... and, the actual fix under test: the score-dependent _INTEL columns
    # were refreshed in the SAME write, not left at their stale pre-sentiment
    # values. This business had zero other usable signal, so a real fix
    # means going from unscored to scored, not just a changed number.
    assert row["lead_priority_score"] not in ("", None)
    assert float(row["lead_priority_score"]) > 0
    assert row["review_sentiment_signal"] not in ("", None)


def test_in_place_run_writes_intel_columns_missing_from_an_older_checkpoint(tmp_path, monkeypatch):
    """A checkpoint written before a newer _INTEL column existed (e.g.
    review_sentiment_signal/review_gap_flag, added 2026-09-15/16 -- a real
    example found auditing an actual checkpoint file) has no such column in
    its own fieldnames. recompute_intel still sets it in the in-memory row,
    but the CSV writer's fieldnames list must also grow to include it, or
    extrasaction="ignore" silently drops it right back out on write -- the
    same failure mode _NEW_COLUMNS already guards against, just for a wider
    set of columns than that fixed tuple covers."""
    monkeypatch.setattr(ars, "is_available", lambda: True)
    monkeypatch.setattr(ars, "OllamaClient", lambda: _FakeClient())
    monkeypatch.setattr(ars, "analyze_business_reviews", _fake_analyze_business_reviews)

    old_fieldnames = [f for f in _FIELDNAMES if f not in ("review_sentiment_signal", "review_gap_flag")]
    old_row = _checkpoint_row()
    del old_row["review_sentiment_signal"]
    del old_row["review_gap_flag"]
    path = tmp_path / "checkpoint.csv"
    _write_csv(path, old_fieldnames, [old_row])

    monkeypatch.setattr(sys, "argv", ["analyze_review_sentiment.py", str(path), "--in-place"])
    assert ars.main() == 0

    out_rows = _read_csv(path)
    row = out_rows[0]
    assert "review_sentiment_signal" in row
    assert row["review_sentiment_signal"] not in ("", None)
    assert "review_gap_flag" in row


def test_in_place_run_does_not_touch_rows_it_did_not_select(tmp_path, monkeypatch):
    """A row with no review text at all is never sent to analyze_business_reviews
    and must come out with its original (stale-but-untouched) intel columns,
    not a spurious recompute -- recompute_intel is scoped to analyzed rows only."""
    monkeypatch.setattr(ars, "is_available", lambda: True)
    monkeypatch.setattr(ars, "OllamaClient", lambda: _FakeClient())

    def _should_never_be_called(row, client):
        raise AssertionError("must not analyze a row with no review columns at all")
    monkeypatch.setattr(ars, "analyze_business_reviews", _should_never_be_called)

    path = tmp_path / "checkpoint.csv"
    untouched = _checkpoint_row(
        mapquest_reviews="", bbb_reviews="", angi_reviews="",
        lead_priority_score="42.0",
    )
    _write_csv(path, _FIELDNAMES, [untouched])

    monkeypatch.setattr(sys, "argv", ["analyze_review_sentiment.py", str(path), "--in-place"])
    assert ars.main() == 0

    out_rows = _read_csv(path)
    assert out_rows[0]["lead_priority_score"] == "42.0"


class _FakeClient:
    calls_made = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
