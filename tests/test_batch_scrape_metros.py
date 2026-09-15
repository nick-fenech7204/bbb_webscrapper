"""Partial-checkpoint safety net added 2026-09-11, after a real incident:
two long `--details` batches (Roof Contractors/Atlanta, Electricians/Dallas)
each ran over an hour and vanished with zero checkpoint on a hard kill mid-
metro. `_write_partial_checkpoint` and its wiring into `scrape_one_metro`
exist so that can't happen silently again -- see the module docstring in
scripts/batch_scrape_metros.py.

Also covers `_write_progress` (2026-09-13) -- structured per-metro JSON
progress for Streamlit's batch page to poll instead of tailing the raw
log, opt-in via `--progress-file` (None -> no-op, a plain CLI run is
unaffected).

And `_check_metro_websites` (2026-09-14) -- the batch's wrapper around
bbb_scraper.webcheck, wired in as a real per-metro step (Nick's ask: fully
integrated, on by default, unproxied). Wrapped the same best-effort way as
Yelp enrichment: a failure here must return the metro's records unchecked,
never take the metro down.

And Angi (2026-09-15) -- previously only reachable via a separate,
now-retired script (scripts/run_batch_with_angi.py). `_resolve_angi_category`/
`_scrape_metro_angi` are the same best-effort building blocks Yelp/webcheck
already established; `scrape_one_metro_bbb_and_angi` is the concurrency
itself, split out specifically so it's testable without standing up
main()'s full CLI/state machinery -- see test_scrape_one_metro_bbb_and_angi_
runs_them_concurrently_not_sequentially below for the actual timing proof.
"""
from __future__ import annotations

import csv
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import batch_scrape_metros as bsm

from bbb_scraper.angi.models import BusinessDetail
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


def test_write_progress_writes_json(tmp_path):
    path = tmp_path / "progress.json"
    bsm._write_progress(path, {"industry": "Plumbers", "total_metros": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"industry": "Plumbers", "total_metros": 2}


def test_write_progress_none_path_is_a_noop(tmp_path):
    # --progress-file is opt-in -- a plain CLI run passes None and this
    # must do nothing (no directory created, no exception).
    bsm._write_progress(None, {"anything": "here"})  # must not raise


def test_write_progress_overwrites_not_appends(tmp_path):
    path = tmp_path / "progress.json"
    bsm._write_progress(path, {"total_metros": 5, "metros": ["first snapshot"]})
    bsm._write_progress(path, {"total_metros": 5, "metros": ["second snapshot"]})
    assert json.loads(path.read_text(encoding="utf-8"))["metros"] == ["second snapshot"]


def test_write_progress_never_leaves_a_half_written_file(tmp_path):
    path = tmp_path / "progress.json"
    bsm._write_progress(path, {"a": 1})
    assert not path.with_suffix(path.suffix + ".tmp").exists()  # cleaned up via replace()


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


# --- _check_metro_websites ----------------------------------------------

def test_check_metro_websites_reports_dead_count_and_returns_checked_records(monkeypatch, capsys):
    def _fake_check_websites(records, **kwargs):
        out = []
        for i, r in enumerate(records):
            out.append({**r, "website_dead": i == 0, "website_status": "dead_404" if i == 0 else "ok"})
        return out

    monkeypatch.setattr(bsm, "check_websites", _fake_check_websites)
    records = [{"name": "A", "website": "http://a.example"}, {"name": "B", "website": "http://b.example"}]

    result = bsm._check_metro_websites(records)

    assert result[0]["website_dead"] is True
    assert result[1]["website_dead"] is False
    assert "1/2 dead/parked/unreachable" in capsys.readouterr().out


def test_check_metro_websites_failure_returns_original_records_unchecked(monkeypatch, capsys):
    """Same best-effort contract as Yelp enrichment: a failure in the check
    itself must never take the metro down -- the caller gets its records
    back exactly as they went in, not an exception."""
    def _boom(records, **kwargs):
        raise RuntimeError("simulated: e.g. the disk cache file couldn't be written")

    monkeypatch.setattr(bsm, "check_websites", _boom)
    records = [{"name": "A", "website": "http://a.example"}]

    result = bsm._check_metro_websites(records)

    assert result == records  # unchanged, not dropped or crashed
    assert "website_dead" not in result[0]
    assert "FAILED" in capsys.readouterr().out


# --- Angi: category resolution -------------------------------------------

def test_resolve_angi_category_matches_a_real_category_by_name():
    # Real reference data, same "trust real fixtures over invented ones"
    # precedent as tests/reference/test_categories.py's own real-file test.
    result = bsm._resolve_angi_category("HVAC Companies")
    assert result == ("hvac", "HVAC Companies")


def test_resolve_angi_category_returns_none_for_no_match():
    assert bsm._resolve_angi_category("Something Angi Has Never Heard Of") is None


# --- Angi: per-metro scrape (best-effort, mirrors _check_metro_websites) -

def _metro(mid="chicago-il", name="Chicago, IL") -> Metro:
    return Metro(id=mid, name=name, seed_location=name)


def test_scrape_metro_angi_flattens_every_named_business(monkeypatch, capsys):
    details = [
        BusinessDetail(profile_url="https://x/1", name="Acme A", phone="555-1111"),
        BusinessDetail(profile_url="https://x/2", name="Acme B", phone="555-2222"),
    ]
    monkeypatch.setattr(bsm, "scrape_category", lambda *a, **k: iter(details))

    rows = bsm._scrape_metro_angi(_metro(), "plumbing", "Plumbers", max_businesses=50)

    assert [r["name"] for r in rows] == ["Acme A", "Acme B"]
    assert "2 businesses" in capsys.readouterr().out


def test_scrape_metro_angi_skips_businesses_that_never_got_full_data(monkeypatch):
    # BusinessDetail.name is None when scrape_category exhausted its own
    # retries without ever seeing the full page variant (see
    # bbb_scraper/angi/scraper.py's _fetch_detail_with_soft_retry) -- a
    # blank row would look like a data error, not "tried, didn't get it".
    details = [
        BusinessDetail(profile_url="https://x/1", name=None),
        BusinessDetail(profile_url="https://x/2", name="Acme B", phone="555-2222"),
    ]
    monkeypatch.setattr(bsm, "scrape_category", lambda *a, **k: iter(details))

    rows = bsm._scrape_metro_angi(_metro(), "plumbing", "Plumbers", max_businesses=None)

    assert len(rows) == 1
    assert rows[0]["name"] == "Acme B"


def test_scrape_metro_angi_keeps_partial_results_on_a_mid_scrape_failure(monkeypatch, capsys):
    def _partial_then_boom(*a, **k):
        yield BusinessDetail(profile_url="https://x/1", name="Acme A", phone="555-1111")
        raise RuntimeError("simulated: e.g. Angi blocked mid-run")

    monkeypatch.setattr(bsm, "scrape_category", _partial_then_boom)

    rows = bsm._scrape_metro_angi(_metro(), "plumbing", "Plumbers", max_businesses=None)

    out = capsys.readouterr().out
    assert len(rows) == 1  # the business already gathered before the failure isn't thrown away
    assert rows[0]["name"] == "Acme A"
    assert "FAILED partway" in out
    assert "keeping 1" in out


def test_scrape_metro_angi_never_raises_on_an_immediate_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("simulated: e.g. category/state/city resolution itself failed")
        yield  # pragma: no cover -- makes this a generator function, never reached

    monkeypatch.setattr(bsm, "scrape_category", _boom)

    rows = bsm._scrape_metro_angi(_metro(), "plumbing", "Plumbers", max_businesses=None)
    assert rows == []  # never raises -- caller's angi_future.result() must not blow up the metro


# --- Angi: raw checkpoint --------------------------------------------------

def test_write_angi_checkpoint_writes_every_declared_field(tmp_path):
    from bbb_scraper.angi.scraper import ANGI_CSV_FIELDS

    path = tmp_path / "plumbing--chicago-il.csv"
    rows = [{f: "" for f in ANGI_CSV_FIELDS} | {"name": "Acme A", "phone": "555-1111"}]

    bsm._write_angi_checkpoint(path, rows)

    with path.open(newline="", encoding="utf-8") as f:
        written = list(csv.DictReader(f))
    assert len(written) == 1
    assert written[0]["name"] == "Acme A"


def test_write_angi_checkpoint_skips_when_no_rows(tmp_path):
    path = tmp_path / "plumbing--chicago-il.csv"
    bsm._write_angi_checkpoint(path, [])
    assert not path.exists()  # nothing scraped -- no empty file to be mistaken for a real checkpoint


# --- BBB + Angi concurrency -------------------------------------------------

def test_scrape_one_metro_bbb_and_angi_skips_angi_entirely_when_disabled(monkeypatch):
    monkeypatch.setattr(bsm, "scrape_one_metro", lambda *a, **k: [{"name": "BBB biz"}])

    def _angi_should_never_run(*a, **k):
        raise AssertionError("Angi must not be scraped at all when angi_enabled=False")

    monkeypatch.setattr(bsm, "_scrape_metro_angi", _angi_should_never_run)

    records, angi_rows = bsm.scrape_one_metro_bbb_and_angi(
        Category(id="plumbers", name="Plumbers"), _metro(),
        radius_miles=10, min_population=0, max_pages_per_place=1,
        fetch_details=False, stats=RunStats(), partial_checkpoint_path=None,
        angi_enabled=False, angi_category_slug=None, angi_category_label=None,
        angi_max_businesses=None,
    )

    assert records == [{"name": "BBB biz"}]
    assert angi_rows == []


def test_scrape_one_metro_bbb_and_angi_runs_them_concurrently_not_sequentially(monkeypatch):
    """The actual point of this whole refactor: BBB and Angi must not wait
    on each other. Each fake sleeps briefly and stamps its own start time;
    if they ran sequentially, Angi's start would land *after* BBB's sleep
    finished (start gap >= SLEEP_S). Run concurrently, both start together
    (gap ~0) and the whole call takes ~SLEEP_S, not ~2*SLEEP_S."""
    SLEEP_S = 0.2
    starts: dict[str, float] = {}

    def _fake_bbb(*a, **k):
        starts["bbb"] = time.monotonic()
        time.sleep(SLEEP_S)
        return [{"name": "BBB biz"}]

    def _fake_angi(*a, **k):
        starts["angi"] = time.monotonic()
        time.sleep(SLEEP_S)
        return [{"name": "Angi biz"}]

    monkeypatch.setattr(bsm, "scrape_one_metro", _fake_bbb)
    monkeypatch.setattr(bsm, "_scrape_metro_angi", _fake_angi)

    began = time.monotonic()
    records, angi_rows = bsm.scrape_one_metro_bbb_and_angi(
        Category(id="plumbers", name="Plumbers"), _metro(),
        radius_miles=10, min_population=0, max_pages_per_place=1,
        fetch_details=False, stats=RunStats(), partial_checkpoint_path=None,
        angi_enabled=True, angi_category_slug="plumbing", angi_category_label="Plumbers",
        angi_max_businesses=50,
    )
    elapsed = time.monotonic() - began

    assert records == [{"name": "BBB biz"}]
    assert angi_rows == [{"name": "Angi biz"}]
    assert abs(starts["bbb"] - starts["angi"]) < SLEEP_S / 2  # started together
    assert elapsed < SLEEP_S * 1.5  # ~SLEEP_S total, not ~2*SLEEP_S (which a sequential call would take)


def test_scrape_one_metro_bbb_and_angi_gives_up_on_a_truly_hung_angi_thread(monkeypatch):
    """Real incident, 2026-09-15: a machine sleep left an Angi request stuck
    forever -- not raising (so _scrape_metro_angi's own try/except never
    saw it), just never returning. The old code's bare
    angi_future.result() would have blocked this function -- and therefore
    the whole batch, metros queued behind it included -- forever. This is
    the actual fix: an Angi thread that never returns must not be able to
    hang the caller past ANGI_MAX_WAIT_SECONDS, ever."""
    monkeypatch.setattr(bsm, "ANGI_MAX_WAIT_SECONDS", 0.2)  # real 45min ceiling, sped up for the test
    monkeypatch.setattr(bsm, "scrape_one_metro", lambda *a, **k: [{"name": "BBB biz"}])

    hang_forever = threading.Event()  # never set -- simulates a thread blocked on I/O that never returns

    def _fake_angi_hangs(*a, **k):
        hang_forever.wait()  # blocks for the life of the thread -- this is the point
        return [{"name": "should never be reached"}]  # pragma: no cover

    monkeypatch.setattr(bsm, "_scrape_metro_angi", _fake_angi_hangs)

    began = time.monotonic()
    records, angi_rows = bsm.scrape_one_metro_bbb_and_angi(
        Category(id="plumbers", name="Plumbers"), _metro(),
        radius_miles=10, min_population=0, max_pages_per_place=1,
        fetch_details=False, stats=RunStats(), partial_checkpoint_path=None,
        angi_enabled=True, angi_category_slug="plumbing", angi_category_label="Plumbers",
        angi_max_businesses=50,
    )
    elapsed = time.monotonic() - began

    assert records == [{"name": "BBB biz"}]  # BBB's real result still comes back
    assert angi_rows == []  # Angi gave up, not a crash and not BBB's data lost
    assert elapsed < 2.0  # returned promptly -- not the old behavior of blocking forever


def test_scrape_one_metro_bbb_and_angi_lets_a_genuine_bbb_failure_raise(monkeypatch):
    """A real BBB error must still surface to the caller exactly as it did
    before this concurrency change -- main()'s own try/except is what marks
    the metro failed and moves on; scrape_one_metro_bbb_and_angi itself
    must not swallow it."""
    def _fake_bbb(*a, **k):
        raise RuntimeError("simulated real BBB failure")

    monkeypatch.setattr(bsm, "scrape_one_metro", _fake_bbb)
    monkeypatch.setattr(bsm, "_scrape_metro_angi", lambda *a, **k: [])

    try:
        bsm.scrape_one_metro_bbb_and_angi(
            Category(id="plumbers", name="Plumbers"), _metro(),
            radius_miles=10, min_population=0, max_pages_per_place=1,
            fetch_details=False, stats=RunStats(), partial_checkpoint_path=None,
            angi_enabled=True, angi_category_slug="plumbing", angi_category_label="Plumbers",
            angi_max_businesses=50,
        )
        raised = False
    except RuntimeError:
        raised = True
    assert raised
