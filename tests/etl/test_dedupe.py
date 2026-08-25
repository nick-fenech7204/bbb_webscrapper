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
