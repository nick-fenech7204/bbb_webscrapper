"""analyze_business_reviews / _aggregate -- normalization across the three
real review-source shapes (bbb/mapquest exact dates, angi month+year) and
the aggregate facts that feed lead scoring. No real Ollama server needed
-- OllamaClient is faked."""
from __future__ import annotations

import json
from dataclasses import asdict

from bbb_scraper.sentiment.analyze import _parse_date, _top_complaint, analyze_business_reviews
from bbb_scraper.sentiment.models import ReviewSentiment


class _FakeOllamaClient:
    """Returns a canned analysis per call, or None to simulate a failed
    analysis -- a spy so tests can assert on what text/rating it was
    actually called with."""

    def __init__(self, responses: dict[str, dict | None] | None = None, default: dict | None = None):
        self.responses = responses or {}
        self.default = default or {"sentiment": "negative", "severity": 3, "theme": "other",
                                    "actionable_for_pitch": True, "summary": "s"}
        self.calls: list[tuple[str, float | None]] = []

    def analyze_review(self, text, *, rating=None):
        self.calls.append((text, rating))
        if text in self.responses:
            return self.responses[text]
        return self.default


def _row(**overrides) -> dict:
    row = {"mapquest_reviews": "", "bbb_reviews": "", "angi_reviews": ""}
    row.update(overrides)
    return row


# --- _parse_date -------------------------------------------------------------

def test_parse_date_exact_iso():
    assert _parse_date("2022-08-16").isoformat() == "2022-08-16"


def test_parse_date_angi_month_year_full_name():
    assert _parse_date("April 2019").isoformat() == "2019-04-01"


def test_parse_date_angi_month_year_abbreviated():
    assert _parse_date("Apr 2019").isoformat() == "2019-04-01"


def test_parse_date_none_and_garbage_return_none():
    assert _parse_date(None) is None
    assert _parse_date("") is None
    assert _parse_date("not a date") is None


# --- analyze_business_reviews / _aggregate -----------------------------------

def test_analyzes_every_review_across_all_three_sources():
    row = _row(
        mapquest_reviews=json.dumps([{"text": "great job", "rating": 5.0, "date": "2024-01-10"}]),
        bbb_reviews=json.dumps([{"text": "terrible", "rating": 1, "date": "2024-02-01"}]),
        angi_reviews=json.dumps([{"text": "fine I guess", "rating": 3, "date_label": "March 2024"}]),
    )
    client = _FakeOllamaClient()

    results, aggregate = analyze_business_reviews(row, client)

    assert len(results) == 3
    assert {r.source for r in results} == {"mapquest", "bbb", "angi"}
    assert aggregate["review_sentiment_analyzed_count"] == 3


def test_empty_or_missing_review_columns_produce_no_results():
    row = _row()
    results, aggregate = analyze_business_reviews(row, _FakeOllamaClient())

    assert results == []
    assert aggregate == {
        "review_sentiment_analyzed_count": 0,
        "review_sentiment_negative_count": 0,
        "most_recent_review_date": None,
        "most_recent_review_source": None,
        "most_recent_negative_review_date": None,
        "most_recent_negative_review_source": None,
        "avg_review_gap_days": None,
        "top_complaint_theme": None,
        "top_complaint_summary": None,
    }


def test_review_with_no_text_is_skipped_without_calling_ollama():
    row = _row(mapquest_reviews=json.dumps([{"text": None, "rating": 3.0, "date": "2024-01-01"}]))
    client = _FakeOllamaClient()

    results, _ = analyze_business_reviews(row, client)

    assert results == []
    assert client.calls == []


def test_a_failed_analysis_excludes_that_review_but_not_others():
    row = _row(mapquest_reviews=json.dumps([
        {"text": "good one", "rating": 5.0, "date": "2024-01-01"},
        {"text": "ollama chokes on this one", "rating": 1.0, "date": "2024-01-02"},
    ]))
    client = _FakeOllamaClient(responses={"ollama chokes on this one": None})

    results, aggregate = analyze_business_reviews(row, client)

    assert len(results) == 1
    assert results[0].date == "2024-01-01"
    assert aggregate["review_sentiment_analyzed_count"] == 1


def test_negative_and_mixed_both_count_toward_negative_count():
    row = _row(mapquest_reviews=json.dumps([
        {"text": "a", "rating": 1.0, "date": "2024-01-01"},
        {"text": "b", "rating": 3.0, "date": "2024-01-02"},
        {"text": "c", "rating": 5.0, "date": "2024-01-03"},
    ]))
    client = _FakeOllamaClient(responses={
        "a": {"sentiment": "negative", "severity": 5, "theme": "x", "actionable_for_pitch": True, "summary": "s"},
        "b": {"sentiment": "mixed", "severity": 3, "theme": "x", "actionable_for_pitch": True, "summary": "s"},
        "c": {"sentiment": "positive", "severity": 1, "theme": "x", "actionable_for_pitch": False, "summary": "s"},
    })

    _, aggregate = analyze_business_reviews(row, client)

    assert aggregate["review_sentiment_analyzed_count"] == 3
    assert aggregate["review_sentiment_negative_count"] == 2


def test_most_recent_dates_and_avg_gap_computed_correctly():
    """3 reviews 30 days apart each -> avg gap 30, most recent is the
    latest date regardless of input order."""
    row = _row(mapquest_reviews=json.dumps([
        {"text": "a", "rating": 1.0, "date": "2024-03-01"},  # negative, latest
        {"text": "b", "rating": 5.0, "date": "2024-01-01"},  # positive, earliest
        {"text": "c", "rating": 5.0, "date": "2024-02-01"},  # positive, middle
    ]))
    client = _FakeOllamaClient(responses={
        "a": {"sentiment": "negative", "severity": 4, "theme": "x", "actionable_for_pitch": True, "summary": "s"},
        "b": {"sentiment": "positive", "severity": 1, "theme": "x", "actionable_for_pitch": False, "summary": "s"},
        "c": {"sentiment": "positive", "severity": 1, "theme": "x", "actionable_for_pitch": False, "summary": "s"},
    })

    _, aggregate = analyze_business_reviews(row, client)

    assert aggregate["most_recent_review_date"] == "2024-03-01"
    assert aggregate["most_recent_review_source"] == "mapquest"
    assert aggregate["most_recent_negative_review_date"] == "2024-03-01"
    assert aggregate["most_recent_negative_review_source"] == "mapquest"
    assert aggregate["avg_review_gap_days"] == 30.0  # Jan->Feb (31d) and Feb->Mar (29d) averaged


def test_no_negative_reviews_leaves_most_recent_negative_date_none():
    row = _row(mapquest_reviews=json.dumps([{"text": "great", "rating": 5.0, "date": "2024-01-01"}]))
    client = _FakeOllamaClient(default={"sentiment": "positive", "severity": 1, "theme": "x",
                                         "actionable_for_pitch": False, "summary": "s"})

    _, aggregate = analyze_business_reviews(row, client)

    assert aggregate["most_recent_review_date"] == "2024-01-01"
    assert aggregate["most_recent_negative_review_date"] is None


def test_bbb_reviews_are_pooled_equally_with_yelp_angi_in_both_date_fields():
    """2026-09-17, Nick's revised call: BBB is now treated the same as
    Yelp/Angi for both date fields, not second-class -- a real, dated BBB
    review can win the most-recent-(negative) slot on its own merits, same
    as any other source. (Through earlier the same day, BBB was excluded
    entirely here; reversed once bbb_reviews became a real, automatically-
    populated source via scripts/batch_scrape_metros.py's --bbb-reviews
    step -- see this module's own _aggregate docstring.)"""
    row = _row(
        mapquest_reviews=json.dumps([{"text": "yelp-sourced, older", "rating": 2.0, "date": "2024-01-01"}]),
        bbb_reviews=json.dumps([{"text": "bbb, newest and worst", "rating": 1.0, "date": "2024-06-01"}]),
    )
    client = _FakeOllamaClient(default={"sentiment": "negative", "severity": 5, "theme": "x",
                                         "actionable_for_pitch": True, "summary": "s"})

    _, aggregate = analyze_business_reviews(row, client)

    assert aggregate["review_sentiment_analyzed_count"] == 2
    assert aggregate["review_sentiment_negative_count"] == 2
    # The BBB review is genuinely the most recent AND most negative by date
    # -- it should win both fields now, not be silently passed over for
    # the older mapquest one.
    assert aggregate["most_recent_review_date"] == "2024-06-01"
    assert aggregate["most_recent_review_source"] == "bbb"
    assert aggregate["most_recent_negative_review_date"] == "2024-06-01"
    assert aggregate["most_recent_negative_review_source"] == "bbb"


def test_most_recent_date_sources_are_tracked_independently_per_field():
    """2026-09-17, Nick's call: the site shows which platform (Yelp, via
    mapquest_reviews -- see this module's own _aggregate docstring -- or
    Angi) found the latest (negative) review, on hover. The overall-latest
    and latest-negative dates can legitimately come from different
    platforms, so each needs its own source, not one shared value."""
    row = _row(
        mapquest_reviews=json.dumps([{"text": "positive, newest overall", "rating": 5.0, "date": "2024-03-01"}]),
        angi_reviews=json.dumps([{"text": "negative, older", "rating": 1, "date_label": "January 2024"}]),
    )
    client = _FakeOllamaClient(responses={
        "positive, newest overall": {"sentiment": "positive", "severity": 1, "theme": "x",
                                      "actionable_for_pitch": False, "summary": "s"},
        "negative, older": {"sentiment": "negative", "severity": 4, "theme": "x",
                             "actionable_for_pitch": True, "summary": "s"},
    })

    _, aggregate = analyze_business_reviews(row, client)

    assert aggregate["most_recent_review_date"] == "2024-03-01"
    assert aggregate["most_recent_review_source"] == "mapquest"
    assert aggregate["most_recent_negative_review_date"] == "2024-01-01"
    assert aggregate["most_recent_negative_review_source"] == "angi"


def test_only_bbb_sourced_reviews_still_sets_both_date_fields():
    """A business with ONLY BBB reviews (no Yelp/Angi at all) must not show
    blank dates despite having real, analyzed review activity -- the exact
    gap that motivated treating BBB equally here."""
    row = _row(bbb_reviews=json.dumps([{"text": "bbb only", "rating": 1.0, "date": "2024-01-01"}]))
    client = _FakeOllamaClient(default={"sentiment": "negative", "severity": 5, "theme": "x",
                                         "actionable_for_pitch": True, "summary": "s"})

    _, aggregate = analyze_business_reviews(row, client)

    assert aggregate["review_sentiment_analyzed_count"] == 1
    assert aggregate["most_recent_review_date"] == "2024-01-01"
    assert aggregate["most_recent_review_source"] == "bbb"
    assert aggregate["most_recent_negative_review_date"] == "2024-01-01"
    assert aggregate["most_recent_negative_review_source"] == "bbb"


def test_a_single_review_has_no_computable_gap():
    row = _row(mapquest_reviews=json.dumps([{"text": "only one", "rating": 3.0, "date": "2024-01-01"}]))
    _, aggregate = analyze_business_reviews(row, _FakeOllamaClient())

    assert aggregate["avg_review_gap_days"] is None  # need >= 2 dated reviews for a gap


def test_undated_angi_review_is_still_analyzed_but_excluded_from_date_math():
    row = _row(angi_reviews=json.dumps([{"text": "no date on this one", "rating": 2, "date_label": None}]))
    client = _FakeOllamaClient()

    results, aggregate = analyze_business_reviews(row, client)

    assert len(results) == 1
    assert results[0].date is None
    assert aggregate["review_sentiment_analyzed_count"] == 1
    assert aggregate["most_recent_review_date"] is None  # nothing dated to compute from


def test_malformed_review_json_is_skipped_not_fatal():
    row = _row(mapquest_reviews="{not valid json", angi_reviews=json.dumps([
        {"text": "still works", "rating": 4, "date_label": "May 2024"},
    ]))
    results, _ = analyze_business_reviews(row, _FakeOllamaClient())

    assert len(results) == 1
    assert results[0].source == "angi"


def test_ollama_receives_the_review_text_and_rating():
    row = _row(mapquest_reviews=json.dumps([{"text": "check the args", "rating": 2.5, "date": "2024-01-01"}]))
    client = _FakeOllamaClient()

    analyze_business_reviews(row, client)

    assert client.calls == [("check the args", 2.5)]


def test_result_is_a_real_reviewsentiment_dataclass():
    row = _row(mapquest_reviews=json.dumps([{"text": "x", "rating": 1.0, "date": "2024-01-01"}]))
    results, _ = analyze_business_reviews(row, _FakeOllamaClient())

    assert isinstance(results[0], ReviewSentiment)
    assert results[0].rating == 1.0


# --- caching: don't re-pay Ollama for a review already analyzed -------------

def test_a_previously_analyzed_review_is_not_re_sent_to_ollama():
    row = _row(mapquest_reviews=json.dumps([{"text": "already done", "rating": 1.0, "date": "2024-01-01"}]))
    client = _FakeOllamaClient()
    first_results, _ = analyze_business_reviews(row, client)
    assert len(client.calls) == 1

    # Simulate a second run over the same row, now carrying the first
    # run's own review_sentiment output -- same shape build_master_table/
    # the batch would actually produce.
    row["review_sentiment"] = json.dumps([asdict(r) for r in first_results])

    second_results, second_aggregate = analyze_business_reviews(row, client)

    assert len(client.calls) == 1  # still just the one call -- the cache hit, no second call
    assert second_results == first_results
    assert second_aggregate["review_sentiment_analyzed_count"] == 1


def test_a_new_review_added_since_the_last_run_is_still_analyzed():
    row = _row(mapquest_reviews=json.dumps([{"text": "old one", "rating": 5.0, "date": "2024-01-01"}]))
    client = _FakeOllamaClient()
    first_results, _ = analyze_business_reviews(row, client)

    row["review_sentiment"] = json.dumps([asdict(r) for r in first_results])
    row["mapquest_reviews"] = json.dumps([
        {"text": "old one", "rating": 5.0, "date": "2024-01-01"},
        {"text": "brand new review", "rating": 1.0, "date": "2024-06-01"},
    ])

    _, aggregate = analyze_business_reviews(row, client)

    assert len(client.calls) == 2  # the old one cached, the new one really analyzed
    assert aggregate["review_sentiment_analyzed_count"] == 2


def test_cache_is_keyed_by_source_too_not_just_text_hash():
    """Same review text appearing under two different sources (unlikely in
    practice, but the cache key must not conflate them) is treated as two
    independent reviews."""
    row = _row(
        mapquest_reviews=json.dumps([{"text": "same text", "rating": 5.0, "date": "2024-01-01"}]),
        angi_reviews=json.dumps([{"text": "same text", "rating": 1.0, "date_label": "January 2024"}]),
    )
    client = _FakeOllamaClient()

    results, _ = analyze_business_reviews(row, client)

    assert len(client.calls) == 2
    assert {r.source for r in results} == {"mapquest", "angi"}


# --- _top_complaint ----------------------------------------------------------

def _rs(source="mapquest", sentiment="negative", severity=3, actionable=True,
        summary="s", theme="quality of work", date="2024-06-01") -> ReviewSentiment:
    return ReviewSentiment(source=source, text_hash="h", date=date, rating=1.0,
                            sentiment=sentiment, severity=severity, theme=theme,
                            actionable_for_pitch=actionable, summary=summary)


def test_top_complaint_none_when_nothing_negative():
    reviews = [_rs(sentiment="positive"), _rs(sentiment="neutral")]
    assert _top_complaint(reviews) == {"top_complaint_theme": None, "top_complaint_summary": None}


def test_top_complaint_prefers_actionable_over_more_severe_non_actionable():
    not_actionable_but_severe = _rs(actionable=False, severity=5, theme="pricing", summary="A")
    actionable_but_milder = _rs(actionable=True, severity=2, theme="communication", summary="B")
    out = _top_complaint([not_actionable_but_severe, actionable_but_milder])
    assert out == {"top_complaint_theme": "communication", "top_complaint_summary": "B"}


def test_top_complaint_prefers_higher_severity_when_actionable_ties():
    mild = _rs(severity=2, theme="scheduling", summary="A")
    severe = _rs(severity=5, theme="quality of work", summary="B")
    out = _top_complaint([mild, severe])
    assert out == {"top_complaint_theme": "quality of work", "top_complaint_summary": "B"}


def test_top_complaint_prefers_more_recent_when_actionable_and_severity_tie():
    old = _rs(date="2020-01-01", theme="pricing", summary="old one")
    recent = _rs(date="2026-08-01", theme="communication", summary="recent one")
    out = _top_complaint([old, recent])
    assert out == {"top_complaint_theme": "communication", "top_complaint_summary": "recent one"}


def test_top_complaint_prefers_mapquest_as_final_tiebreak():
    """Nick's explicit call, 2026-09-16: when actionable/severity/date all
    tie, MapQuest-sourced content wins -- see _top_complaint's own
    docstring for why (it's already this project's dominant real review-
    content source, not an arbitrary preference)."""
    bbb_one = _rs(source="bbb", theme="pricing", summary="from bbb")
    mapquest_one = _rs(source="mapquest", theme="communication", summary="from mapquest")
    out = _top_complaint([bbb_one, mapquest_one])
    assert out == {"top_complaint_theme": "communication", "top_complaint_summary": "from mapquest"}


def test_top_complaint_skips_a_negative_review_missing_a_summary():
    """A review whose LLM analysis didn't produce a usable summary can't
    lead a pitch -- fall through to the next real candidate instead of
    surfacing an empty string."""
    no_summary = _rs(summary=None, severity=5, theme="pricing")
    has_summary = _rs(summary="usable", severity=1, theme="communication")
    out = _top_complaint([no_summary, has_summary])
    assert out == {"top_complaint_theme": "communication", "top_complaint_summary": "usable"}
