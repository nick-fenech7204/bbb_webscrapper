from bbb_scraper.etl.dedupe import dedupe_records
from bbb_scraper.utils.stats import RECORDS_DEDUPED, RunStats


def test_dedupe_records_keeps_first_occurrence():
    records = [
        {"id": "1", "name": "A"},
        {"id": "2", "name": "B"},
        {"id": "1", "name": "A duplicate with different name"},
    ]
    deduped = dedupe_records(records)
    assert len(deduped) == 2
    assert deduped[0]["name"] == "A"


def test_dedupe_records_tracks_stats():
    stats = RunStats()
    records = [{"id": "1"}, {"id": "1"}, {"id": "1"}]
    dedupe_records(records, stats=stats)
    assert stats.as_dict()[RECORDS_DEDUPED] == 2


def test_dedupe_records_prefers_whichever_list_comes_first():
    """Not BBB-specific behavior, but the contract etl/pipeline.py relies on:
    confirmed 2026-09-02 that a business's detail record can legitimately
    compute the same id as its own summary record (profile reached via an
    /addressId/N-suffixed URL, N also embedded in the search result's raw
    id) -- run_search() puts detail_records before summary_records for
    exactly this reason, so the richer detail record wins on collision
    rather than the sparser summary that happened to transform first.
    """
    summary = {"id": "bbb:0403_236014120_138333", "record_type": "summary", "website": None}
    detail = {"id": "bbb:0403_236014120_138333", "record_type": "detail", "website": "https://example.com"}

    deduped = dedupe_records([detail, summary])
    assert len(deduped) == 1
    assert deduped[0]["record_type"] == "detail"
    assert deduped[0]["website"] == "https://example.com"
