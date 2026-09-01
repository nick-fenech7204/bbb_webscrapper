import csv
import json

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


def test_csv_sink_json_encodes_list_and_dict_values(tmp_path):
    """A cell holding a list/dict must be real, parseable JSON -- not
    Python's str() repr (single-quoted, True/None instead of true/null),
    which looks similar enough to pass a glance but silently isn't JSON.
    """
    sink = CSVSink(tmp_path / "out.csv")
    sink.load([{
        "id": "1",
        "name": "Acme",
        "categories": ["Plumbers", "HVAC"],
        "contacts": [{"name": "Jane Doe", "title": "Owner", "is_principal": True}],
        "reviews_complaints": {"reviews_total": 3, "complaints_total": None},
    }])

    rows = _read_rows(tmp_path / "out.csv")
    row = rows[0]

    assert json.loads(row["categories"]) == ["Plumbers", "HVAC"]
    assert json.loads(row["contacts"]) == [
        {"name": "Jane Doe", "title": "Owner", "is_principal": True}
    ]
    assert json.loads(row["reviews_complaints"]) == {"reviews_total": 3, "complaints_total": None}


def test_csv_sink_handles_nested_values_without_crashing_on_append(tmp_path):
    sink = CSVSink(tmp_path / "out.csv")
    sink.load([{"id": "1", "name": "Acme", "categories": ["Plumbers"]}])
    sink.load([{"id": "2", "name": "Beta", "categories": ["HVAC", "Roofing"]}])

    rows = _read_rows(tmp_path / "out.csv")
    assert json.loads(rows[0]["categories"]) == ["Plumbers"]
    assert json.loads(rows[1]["categories"]) == ["HVAC", "Roofing"]


def test_csv_sink_returns_zero_for_empty_batch(tmp_path):
    sink = CSVSink(tmp_path / "out.csv")
    assert sink.load([]) == 0
    assert not (tmp_path / "out.csv").exists()
