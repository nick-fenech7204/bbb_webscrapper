"""scripts/check_dead_websites.py -- CSV I/O and CLI behavior. The actual
liveness-checking logic is bbb_scraper.webcheck's job (see tests/webcheck/);
check_websites() is monkeypatched here so nothing makes a real request."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_dead_websites as cdw


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _fake_check_websites(records, *, website_field="website", **kwargs):
    """Stands in for the real bbb_scraper.webcheck.enrich.check_websites --
    marks every other row dead, deterministically, so output assertions
    don't depend on network state."""
    out = []
    for i, r in enumerate(records):
        row = dict(r)
        row[f"{website_field}_dead"] = i % 2 == 0
        row[f"{website_field}_status"] = "dead_404" if i % 2 == 0 else "ok"
        row[f"{website_field}_checked_at"] = "2026-09-14T00:00:00+00:00"
        out.append(row)
    return out


def test_missing_input_file_reports_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["check_dead_websites.py", str(tmp_path / "nope.csv")])
    assert cdw.main() == 1
    assert "No such file" in capsys.readouterr().out


def test_missing_website_column_reports_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "in.csv"
    _write_csv(csv_path, ["name"], [{"name": "A Co"}])
    monkeypatch.setattr(sys, "argv", ["check_dead_websites.py", str(csv_path)])
    assert cdw.main() == 1
    assert "isn't a column" in capsys.readouterr().out


def test_default_output_writes_a_new_file_not_the_original(tmp_path, monkeypatch):
    monkeypatch.setattr(cdw, "check_websites", _fake_check_websites)
    csv_path = tmp_path / "businesses.csv"
    _write_csv(csv_path, ["name", "website"], [
        {"name": "A Co", "website": "https://a.com"},
        {"name": "B Co", "website": "https://b.com"},
    ])
    monkeypatch.setattr(sys, "argv", ["check_dead_websites.py", str(csv_path)])
    assert cdw.main() == 0

    expected_output = tmp_path / "businesses--webcheck.csv"
    assert expected_output.exists()
    original_rows = _read_csv(csv_path)
    assert "website_dead" not in original_rows[0]  # original file untouched

    rows = _read_csv(expected_output)
    assert rows[0]["website_dead"] == "True"
    assert rows[0]["website_status"] == "dead_404"
    assert rows[1]["website_dead"] == "False"
    assert rows[1]["website_status"] == "ok"


def test_in_place_overwrites_and_backs_up_first(tmp_path, monkeypatch):
    monkeypatch.setattr(cdw, "check_websites", _fake_check_websites)
    monkeypatch.setattr(cdw, "REPO_ROOT", tmp_path)  # keep the backup out of the real repo
    csv_path = tmp_path / "batch.csv"
    _write_csv(csv_path, ["bbb_name", "bbb_website"], [
        {"bbb_name": "A Co", "bbb_website": "https://a.com"},
    ])
    monkeypatch.setattr(sys, "argv", [
        "check_dead_websites.py", str(csv_path), "--website-field", "bbb_website", "--in-place",
    ])
    assert cdw.main() == 0

    rows = _read_csv(csv_path)
    assert rows[0]["bbb_website_dead"] == "True"
    assert rows[0]["bbb_website_status"] == "dead_404"

    backups = list((tmp_path / "data" / "processed" / "archive").glob("batch.pre-webcheck-*.csv"))
    assert len(backups) == 1
    backed_up_rows = _read_csv(backups[0])
    assert "bbb_website_dead" not in backed_up_rows[0]  # backup is the pre-check original


def test_bbb_prefixed_field_produces_bbb_prefixed_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(cdw, "check_websites", _fake_check_websites)
    csv_path = tmp_path / "checkpoint.csv"
    _write_csv(csv_path, ["bbb_name", "bbb_website"], [
        {"bbb_name": "A Co", "bbb_website": "https://a.com"},
    ])
    monkeypatch.setattr(sys, "argv", [
        "check_dead_websites.py", str(csv_path), "--website-field", "bbb_website",
    ])
    assert cdw.main() == 0

    rows = _read_csv(tmp_path / "checkpoint--webcheck.csv")
    assert "bbb_website_dead" in rows[0]
    assert "website_dead" not in rows[0]  # not the bare version
