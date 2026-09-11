"""Partial-checkpoint safety net added 2026-09-11, after a real incident:
two long `--details` batches (Roof Contractors/Atlanta, Electricians/Dallas)
each ran over an hour and vanished with zero checkpoint on a hard kill mid-
metro. `_write_partial_checkpoint` and its wiring into `scrape_one_metro`
exist so that can't happen silently again -- see the module docstring in
scripts/batch_scrape_metros.py.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import batch_scrape_metros as bsm

from bbb_scraper.reference.models import Category, Metro
from bbb_scraper.utils.stats import RunStats


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_write_partial_checkpoint_writes_deduped_rows(tmp_path):
    path = tmp_path / "partial.csv"
    records = [
        {"id": "1", "phone": "(555) 111-2222", "name": "Acme A"},
        {"id": "2", "phone": "(555) 111-2222", "name": "Acme B (branch, same phone)"},
        {"id": "3", "phone": "(555) 333-4444", "name": "Other Co"},
    ]

    bsm._write_partial_checkpoint(path, records)

    rows = _read_rows(path)
    assert len(rows) == 2  # phone-deduped, same rule as the real checkpoint
    assert rows[0]["name"] == "Acme A"  # first seen wins


def test_write_partial_checkpoint_overwrites_not_appends(tmp_path):
    path = tmp_path / "partial.csv"
    bsm._write_partial_checkpoint(path, [{"id": "1", "phone": "555-1111", "name": "First snapshot"}])
    bsm._write_partial_checkpoint(
        path,
        [
            {"id": "1", "phone": "555-1111", "name": "First snapshot"},
            {"id": "2", "phone": "555-2222", "name": "Second snapshot"},
        ],
    )

    rows = _read_rows(path)
    assert len(rows) == 2  # the second write fully replaces the first, not appends onto it
    assert {r["name"] for r in rows} == {"First snapshot", "Second snapshot"}


def test_write_partial_checkpoint_skips_when_no_records_yet(tmp_path):
    path = tmp_path / "partial.csv"
    bsm._write_partial_checkpoint(path, [])
    assert not path.exists()  # nothing fetched yet -- no empty file to be mistaken for real progress


def test_write_partial_checkpoint_never_leaves_a_half_written_file(tmp_path):
    """Written via a temp file + atomic replace() -- readers never see a
    partially-flushed CSV, only the previous complete version or the new
    complete version."""
    path = tmp_path / "partial.csv"
    bsm._write_partial_checkpoint(path, [{"id": "1", "phone": "555-1111", "name": "A"}])
    assert not path.with_suffix(path.suffix + ".tmp").exists()  # temp file cleaned up via replace()


class _FakeExtractor:
    """Stands in for `with Extractor(stats=stats) as extractor:` -- returns
    canned summaries, and records every extract_business() call so the test
    can assert on fetch order/count."""

    def __init__(self, summaries, *, stats=None):
        self._summaries = summaries
        self.fetched: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_search_metro_coverage(self, category, metro, **kw):
        return self._summaries

    def extract_business(self, profile_url, referer=None):
        self.fetched.append(profile_url)
        return profile_url  # transform_detail below just echoes it back


def test_scrape_one_metro_snapshots_partial_progress_during_a_long_details_run(tmp_path, monkeypatch):
    summaries = [
        SimpleNamespace(profile_url=f"https://bbb.example/biz-{i}", source_page=1)
        for i in range(1, 6)  # 5 businesses
    ]
    extractor_holder: list[_FakeExtractor] = []

    def _fake_extractor_factory(*, stats=None):
        fake = _FakeExtractor(summaries, stats=stats)
        extractor_holder.append(fake)
        return fake

    monkeypatch.setattr(bsm, "Extractor", _fake_extractor_factory)
    monkeypatch.setattr(bsm, "transform_summary", lambda s: {"id": s.profile_url, "phone": s.profile_url, "name": "summary"})
    monkeypatch.setattr(bsm, "transform_detail", lambda url: {"id": url, "phone": url, "name": "detail"})
    monkeypatch.setattr(bsm, "build_referer", lambda *a, **k: "referer")

    partial_path = tmp_path / "partial.csv"
    category = Category(id="electricians", name="Electricians")
    metro = Metro(id="dallas-tx", name="Dallas, TX", seed_location="Dallas, TX")

    result = bsm.scrape_one_metro(
        category, metro,
        radius_miles=10, min_population=0, max_pages_per_place=1,
        fetch_details=True, stats=RunStats(),
        partial_checkpoint_path=partial_path, partial_every=2,
    )

    # A snapshot exists mid-run (after business #2 and #4) as well as the
    # final one after #5 -- simulate "the process died right after business
    # #4" by checking a snapshot was on disk at that point, not just at the
    # very end.
    assert partial_path.exists()
    final_rows = _read_rows(partial_path)
    assert len(final_rows) == 5  # nothing lost by the time it finished normally
    assert len(result) == 5


def test_scrape_one_metro_details_mode_without_partial_path_does_not_require_it(monkeypatch):
    """partial_checkpoint_path is optional -- the CLI's non---details path
    (and any future caller) must keep working without passing it."""
    summaries = [SimpleNamespace(profile_url="https://bbb.example/biz-1", source_page=1)]
    monkeypatch.setattr(bsm, "Extractor", lambda *, stats=None: _FakeExtractor(summaries, stats=stats))
    monkeypatch.setattr(bsm, "transform_summary", lambda s: {"id": s.profile_url, "phone": s.profile_url, "name": "summary"})
    monkeypatch.setattr(bsm, "transform_detail", lambda url: {"id": url, "phone": url, "name": "detail"})
    monkeypatch.setattr(bsm, "build_referer", lambda *a, **k: "referer")

    category = Category(id="electricians", name="Electricians")
    metro = Metro(id="dallas-tx", name="Dallas, TX", seed_location="Dallas, TX")

    result = bsm.scrape_one_metro(
        category, metro,
        radius_miles=10, min_population=0, max_pages_per_place=1,
        fetch_details=True, stats=RunStats(),
    )
    assert len(result) == 1
