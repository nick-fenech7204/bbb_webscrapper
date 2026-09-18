"""bbb_scraper.facebook.enrich -- dedup, caching, bare-vs-prefixed field
naming, and the no-facebook-link case, using a fake client (no real network)
injected via the `client=` param, same style as tests/match/test_enrich.py's
_FakeClient for Yelp."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from bbb_scraper.facebook.enrich import FacebookCache, enrich_with_facebook
from bbb_scraper.facebook.models import FacebookProfile


class _FakeClient:
    def __init__(self, profiles: dict[str, FacebookProfile] | None = None):
        self.profiles = profiles or {}
        self.calls: list[str] = []

    def fetch_profile(self, url: str) -> FacebookProfile:
        self.calls.append(url)
        if url in self.profiles:
            return self.profiles[url]
        return FacebookProfile(url=url, status="ok", name="Fake Business", review_count=5)


def _record(name: str, fb_url: str | None) -> dict:
    """A JSON-STRING socials field, matching what a real master row (from
    match.merge.build_master_table's own _row()) actually looks like --
    NOT a live Python list. Real bug, caught 2026-09-18 by a live batch
    test: every test in this file originally built `socials` as a real
    list, which never would have caught the JSON-encoded-string reality
    of production data (see _facebook_url's own docstring in enrich.py)."""
    socials = [{"platform": "facebook", "url": fb_url}] if fb_url else []
    return {"name": name, "socials": json.dumps(socials)}


def test_no_facebook_link_never_triggers_a_fetch(tmp_path):
    fake = _FakeClient()
    records = [_record("No FB Co", None)]
    out = enrich_with_facebook(records, cache_path=tmp_path / "cache.json", client=fake)

    assert fake.calls == []
    assert out[0]["facebook_status"] == "no_facebook_link"
    assert out[0]["facebook_name"] is None


def test_two_records_sharing_a_facebook_url_only_fetch_it_once(tmp_path):
    fake = _FakeClient()
    records = [
        _record("Branch A", "https://www.facebook.com/acme"),
        _record("Branch B", "https://www.facebook.com/acme"),
    ]
    out = enrich_with_facebook(records, cache_path=tmp_path / "cache.json", client=fake)

    assert fake.calls == ["https://www.facebook.com/acme"]
    assert out[0]["facebook_name"] == "Fake Business"
    assert out[1]["facebook_name"] == "Fake Business"


def test_fresh_cache_entry_skips_a_real_fetch(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache = FacebookCache(cache_path)
    cache.set("https://www.facebook.com/already", FacebookProfile(
        url="https://www.facebook.com/already", status="ok", name="Already Fetched Co",
    ))
    cache.save()

    fake = _FakeClient()
    records = [_record("Already Fetched Co", "https://www.facebook.com/already")]
    out = enrich_with_facebook(records, cache_path=cache_path, client=fake, ttl_days=30)

    assert fake.calls == []  # served straight from cache
    assert out[0]["facebook_name"] == "Already Fetched Co"


def test_stale_cache_entry_does_get_refetched(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache = FacebookCache(cache_path)
    old_profile = FacebookProfile(url="https://www.facebook.com/stale", status="ok", name="Old Name")
    cache.set("https://www.facebook.com/stale", old_profile)
    cache._data["https://www.facebook.com/stale"]["checked_at"] = (
        datetime.now(timezone.utc) - timedelta(days=90)
    ).isoformat()
    cache.save()

    fake = _FakeClient({"https://www.facebook.com/stale": FacebookProfile(
        url="https://www.facebook.com/stale", status="ok", name="New Name",
    )})
    records = [_record("Co", "https://www.facebook.com/stale")]
    out = enrich_with_facebook(records, cache_path=cache_path, client=fake, ttl_days=30)

    assert fake.calls == ["https://www.facebook.com/stale"]
    assert out[0]["facebook_name"] == "New Name"


def test_does_not_mutate_the_input_records(tmp_path):
    original = _record("Co", "https://www.facebook.com/x")
    records = [original]
    enrich_with_facebook(records, cache_path=tmp_path / "cache.json", client=_FakeClient())
    assert "facebook_name" not in original


def test_bbb_prefixed_socials_field_still_writes_plain_facebook_prefixed_output(tmp_path):
    """facebook_* is always the output prefix, regardless of whether the
    input socials field was "socials" or "bbb_socials" -- Facebook is its
    own top-level source (like angi_*), not a sub-field of whichever BBB
    column happened to supply the URL."""
    records = [{"bbb_name": "Co", "bbb_socials": json.dumps([{"platform": "facebook", "url": "https://www.facebook.com/x"}])}]
    out = enrich_with_facebook(
        records, socials_field="bbb_socials", cache_path=tmp_path / "cache.json", client=_FakeClient(),
    )
    assert out[0]["facebook_name"] == "Fake Business"
    assert "bbb_facebook_name" not in out[0]


def test_blank_bbb_email_gets_backfilled_from_facebook(tmp_path):
    fake = _FakeClient({"https://www.facebook.com/x": FacebookProfile(
        url="https://www.facebook.com/x", status="ok", email="found@facebook-only.com",
    )})
    records = [{"bbb_name": "Co", "bbb_email": "", "bbb_socials": json.dumps([{"platform": "facebook", "url": "https://www.facebook.com/x"}])}]
    out = enrich_with_facebook(records, socials_field="bbb_socials", cache_path=tmp_path / "cache.json", client=fake)

    assert out[0]["bbb_email"] == "found@facebook-only.com"
    assert out[0]["bbb_email_source"] == "facebook"


def test_existing_bbb_email_is_never_overwritten(tmp_path):
    fake = _FakeClient({"https://www.facebook.com/x": FacebookProfile(
        url="https://www.facebook.com/x", status="ok", email="facebook@example.com",
    )})
    records = [{"bbb_name": "Co", "bbb_email": "already-on-file@bbb.com",
                "bbb_socials": json.dumps([{"platform": "facebook", "url": "https://www.facebook.com/x"}])}]
    out = enrich_with_facebook(records, socials_field="bbb_socials", cache_path=tmp_path / "cache.json", client=fake)

    assert out[0]["bbb_email"] == "already-on-file@bbb.com"
    assert "bbb_email_source" not in out[0]


def test_no_backfill_when_facebook_has_no_email_either(tmp_path):
    records = [{"bbb_name": "Co", "bbb_email": "",
                "bbb_socials": json.dumps([{"platform": "facebook", "url": "https://www.facebook.com/x"}])}]
    out = enrich_with_facebook(records, socials_field="bbb_socials", cache_path=tmp_path / "cache.json", client=_FakeClient())

    assert out[0]["bbb_email"] == ""  # _FakeClient's default profile has no email
    assert "bbb_email_source" not in out[0]


def test_non_facebook_social_entries_are_ignored(tmp_path):
    records = [{"name": "Co", "socials": json.dumps([{"platform": "instagram", "url": "https://instagram.com/co"}])}]
    fake = _FakeClient()
    out = enrich_with_facebook(records, cache_path=tmp_path / "cache.json", client=fake)

    assert fake.calls == []
    assert out[0]["facebook_status"] == "no_facebook_link"


def test_a_real_python_list_socials_value_still_works(tmp_path):
    """Defensive, not the real production shape (see _record's own docstring
    above) -- some other future caller might reasonably pass a live list
    directly rather than a master row's JSON-string form, and this
    shouldn't silently break for them either."""
    fake = _FakeClient()
    records = [{"name": "Co", "socials": [{"platform": "facebook", "url": "https://www.facebook.com/x"}]}]
    out = enrich_with_facebook(records, cache_path=tmp_path / "cache.json", client=fake)
    assert fake.calls == ["https://www.facebook.com/x"]
    assert out[0]["facebook_status"] == "ok"


def test_malformed_socials_json_never_crashes(tmp_path):
    records = [{"name": "Co", "socials": "{not valid json"}]
    out = enrich_with_facebook(records, cache_path=tmp_path / "cache.json", client=_FakeClient())
    assert out[0]["facebook_status"] == "no_facebook_link"


def test_a_real_client_fetch_exception_becomes_check_failed_not_a_crash(tmp_path):
    class _RaisingClient:
        def fetch_profile(self, url):
            raise RuntimeError("boom")

    records = [_record("Co", "https://www.facebook.com/x")]
    out = enrich_with_facebook(records, cache_path=tmp_path / "cache.json", client=_RaisingClient())
    assert out[0]["facebook_status"] == "check_failed"


def test_on_progress_called_with_final_totals(tmp_path):
    records = [_record("A", "https://www.facebook.com/a"), _record("B", "https://www.facebook.com/b")]
    seen: list[tuple[int, int]] = []
    enrich_with_facebook(
        records, cache_path=tmp_path / "cache.json", client=_FakeClient(),
        on_progress=lambda d, t: seen.append((d, t)),
    )
    assert seen[-1] == (2, 2)
