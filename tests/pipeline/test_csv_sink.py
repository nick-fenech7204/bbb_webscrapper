import csv

from bbb_scraper.pipeline.sinks.csv_sink import CSVSink


def _read_rows(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_csv_sink_writes_header_and_rows(tmp_path):
    sink = CSVSink(tmp_path / "out.csv")
    written = sink.load([{"id": "1", "name": "Acme"}, {"id": "2", "name": "Beta"}])
    assert written == 2

    rows = _read_rows(tmp_path / "out.csv")
    assert rows == [{"id": "1", "name": "Acme"}, {"id": "2", "name": "Beta"}]


def test_csv_sink_appends_when_batch_columns_are_a_subset(tmp_path):
    sink = CSVSink(tmp_path / "out.csv")
    sink.load([{"id": "1", "name": "Acme", "city": "Austin"}])
    sink.load([{"id": "2", "name": "Beta"}])  # no 'city' this batch

    rows = _read_rows(tmp_path / "out.csv")
    assert rows == [
        {"id": "1", "name": "Acme", "city": "Austin"},
        {"id": "2", "name": "Beta", "city": ""},
    ]


def test_csv_sink_does_not_corrupt_columns_when_batch_has_fewer_fields(tmp_path):
    """Regression test: appending a batch with a different field set used to
    write values under whatever fieldnames *that* batch computed, not the
    file's actual on-disk header -- silently shifting values into the wrong
    columns (confirmed 2026-09-02: a row's 'city' value ended up holding an
    'id' value). This is the exact scenario that caused it.
    """
    sink = CSVSink(tmp_path / "out.csv")
    sink.load([{"id": "1", "name": "Acme", "city": "Austin"}])
    sink.load([{"id": "2", "name": "Beta"}])

    rows = _read_rows(tmp_path / "out.csv")
    assert rows[1]["id"] == "2"
    assert rows[1]["name"] == "Beta"
    assert rows[1]["city"] == ""  # not "2", not corrupted


def test_csv_sink_grows_header_when_batch_has_new_fields(tmp_path):
    sink = CSVSink(tmp_path / "out.csv")
    sink.load([{"id": "1", "name": "Acme"}])
    sink.load([{"id": "2", "name": "Beta", "website": "https://beta.example.com"}])

    rows = _read_rows(tmp_path / "out.csv")
    assert rows == [
        {"id": "1", "name": "Acme", "website": ""},
        {"id": "2", "name": "Beta", "website": "https://beta.example.com"},
    ]


def test_csv_sink_ignores_extra_keys_not_in_union(tmp_path):
    sink = CSVSink(tmp_path / "out.csv")
    sink.load([{"id": "1", "name": "Acme", "raw_extra": {"nested": "dropped is fine here"}}])
    # raw_extra is a dict -- csv can't represent it cleanly, but the sink
    # shouldn't blow up on it; it's just another column value (stringified
    # by csv.writer, not our concern to validate the string shape here).
    rows = _read_rows(tmp_path / "out.csv")
    assert rows[0]["id"] == "1"


def test_csv_sink_returns_zero_for_empty_batch(tmp_path):
    sink = CSVSink(tmp_path / "out.csv")
    assert sink.load([]) == 0
    assert not (tmp_path / "out.csv").exists()
