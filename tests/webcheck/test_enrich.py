"""bbb_scraper.webcheck.enrich -- dedup, caching, and the bare-vs-bbb_-
prefixed field naming, using a fake check_website (no real network, no
real curl_cffi session) via monkeypatch, same style as tests/match/
test_enrich.py's _FakeClient for Yelp."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bbb_scraper.webcheck.enrich as enrich_mod
from bbb_scraper.webcheck.checker import WebsiteCheck
from bbb_scraper.webcheck.enrich import WebsiteCheckCache, check_websites, derive_output_fields

# --- derive_output_fields ----------------------------------------------------

def test_derive_output_fields_bare():
    assert derive_output_fields("website") == ("website_dead", "website_status", "website_checked_at")


def test_derive_output_fields_bbb_prefixed():
    assert derive_output_fields("bbb_website") == (
        "bbb_website_dead", "bbb_website_status", "bbb_website_checked_at",
    )


def test_derive_output_fields_unrelated_field_name_has_no_prefix():
    # Not expected in real use, but shouldn't do anything surprising either.
    assert derive_output_fields("homepage") == ("website_dead", "website_status", "website_checked_at")


# --- WebsiteCheckCache -------------------------------------------------------

def test_cache_save_and_load_round_trip(tmp_path):
    path = tmp_path / "cache.json"
    cache = WebsiteCheckCache(path)
    check = WebsiteCheck(url="https://example.com", status="ok", dead=False,
                          http_status=200, checked_at=datetime.now(timezone.utc).isoformat())
    cache.set("https://example.com", check)
    cache.save()

    reloaded = WebsiteCheckCache(path)
    entry = reloaded.get("https://example.com")
    assert entry["status"] == "ok"
    assert entry["dead"] is False


def test_cache_missing_file_starts_empty(tmp_path):
    cache = WebsiteCheckCache(tmp_path / "does-not-exist.json")
    assert cache.get("https://example.com") is None
    assert cache.is_fresh("https://example.com", ttl_days=30) is False


def test_cache_corrupt_file_starts_empty_not_a_crash(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not valid json", encoding="utf-8")
    cache = WebsiteCheckCache(path)  # must not raise
    assert cache.get("https://example.com") is None


def test_cache_is_fresh_respects_ttl(tmp_path):
    cache = WebsiteCheckCache(tmp_path / "cache.json")
    fresh = WebsiteCheck(url="u", status="ok", dead=False, http_status=200,
                          checked_at=datetime.now(timezone.utc).isoformat())
    stale = WebsiteCheck(url="u", status="ok", dead=False, http_status=200,
                          checked_at=(datetime.now(timezone.utc) - timedelta(days=60)).isoformat())
    cache.set("fresh-url", fresh)
    cache.set("stale-url", stale)
    assert cache.is_fresh("fresh-url", ttl_days=30) is True
    assert cache.is_fresh("stale-url", ttl_days=30) is False


# --- check_websites -----------------------------------------------------------

def _fake_check_website_factory(calls: list[str]):
    def _fake(url, *, cfg=None, timeout=None, session=None):
        calls.append(url)
        return WebsiteCheck(url=url, status="dead_404", dead=True, http_status=404,
                             checked_at=datetime.now(timezone.utc).isoformat())
    return _fake


def test_no_website_field_never_triggers_a_check(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(enrich_mod, "check_website", _fake_check_website_factory(calls))

    records = [{"name": "No Site Co"}]  # no "website" key at all
    out = check_websites(records, cache_path=tmp_path / "cache.json")

    assert calls == []
    assert out[0]["website_dead"] is False
    assert out[0]["website_status"] == "no_website"


def test_two_records_sharing_a_url_only_check_it_once(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(enrich_mod, "check_website", _fake_check_website_factory(calls))

    records = [
        {"name": "Branch A", "website": "https://acme.com"},
        {"name": "Branch B", "website": "https://acme.com"},
    ]
    out = check_websites(records, cache_path=tmp_path / "cache.json")

    assert calls == ["https://acme.com"]  # deduped -- one network call for two rows
    assert out[0]["website_dead"] is True
    assert out[1]["website_dead"] is True


def test_a_fresh_cache_entry_skips_a_real_check(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(enrich_mod, "check_website", _fake_check_website_factory(calls))

    cache_path = tmp_path / "cache.json"
    cache = WebsiteCheckCache(cache_path)
    cache.set("https://already-checked.com", WebsiteCheck(
        url="https://already-checked.com", status="ok", dead=False, http_status=200,
        checked_at=datetime.now(timezone.utc).isoformat(),
    ))
    cache.save()

    records = [{"name": "Already Checked Co", "website": "https://already-checked.com"}]
    out = check_websites(records, cache_path=cache_path, ttl_days=30)

    assert calls == []  # never called check_website -- served straight from cache
    assert out[0]["website_dead"] is False
    assert out[0]["website_status"] == "ok"


def test_stale_cache_entry_does_get_rechecked(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(enrich_mod, "check_website", _fake_check_website_factory(calls))

    cache_path = tmp_path / "cache.json"
    cache = WebsiteCheckCache(cache_path)
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    cache.set("https://stale.com", WebsiteCheck(
        url="https://stale.com", status="ok", dead=False, http_status=200, checked_at=old,
    ))
    cache.save()

    records = [{"name": "Stale Co", "website": "https://stale.com"}]
    out = check_websites(records, cache_path=cache_path, ttl_days=30)

    assert calls == ["https://stale.com"]
    assert out[0]["website_dead"] is True  # the fake always returns dead_404


def test_does_not_mutate_the_input_records(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich_mod, "check_website", _fake_check_website_factory([]))
    original = {"name": "Co", "website": "https://example.com"}
    records = [original]
    check_websites(records, cache_path=tmp_path / "cache.json")
    assert "website_dead" not in original


def test_bbb_prefixed_field_writes_bbb_prefixed_output(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich_mod, "check_website", _fake_check_website_factory([]))
    records = [{"bbb_name": "Co", "bbb_website": "https://example.com"}]
    out = check_websites(records, website_field="bbb_website", cache_path=tmp_path / "cache.json")
    assert out[0]["bbb_website_dead"] is True
    assert "website_dead" not in out[0]


def test_on_progress_called_with_final_totals(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich_mod, "check_website", _fake_check_website_factory([]))
    records = [{"website": "https://a.com"}, {"website": "https://b.com"}]
    seen: list[tuple[int, int]] = []
    check_websites(records, cache_path=tmp_path / "cache.json", on_progress=lambda d, t: seen.append((d, t)))
    assert seen[-1] == (2, 2)


def test_cache_saves_periodically_not_just_once_at_the_end(tmp_path, monkeypatch):
    """A long sweep (thousands of unique domains) used to lose every result
    checked so far if killed partway through -- only the unconditional
    save() after the whole loop ever ran. Confirms the periodic save
    actually fires mid-run, not just at completion."""
    monkeypatch.setattr(enrich_mod, "check_website", _fake_check_website_factory([]))
    monkeypatch.setattr(enrich_mod, "CACHE_SAVE_EVERY", 2)

    save_calls = []
    original_save = enrich_mod.WebsiteCheckCache.save
    def _counting_save(self):
        save_calls.append(len(self._data))
        return original_save(self)
    monkeypatch.setattr(enrich_mod.WebsiteCheckCache, "save", _counting_save)

    records = [{"website": f"https://site{i}.example"} for i in range(5)]
    check_websites(records, cache_path=tmp_path / "cache.json")

    # 5 URLs, saving every 2 -> mid-run saves at 2 and 4, plus the final
    # unconditional one at 5 -- more than the single end-of-run save this
    # used to be.
    assert len(save_calls) >= 3
    assert save_calls[0] < 5  # at least one save happened before everything finished
