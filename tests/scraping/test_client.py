"""
Construction-level smoke tests for HttpClient's curl_cffi transport.

No network calls here (that's what the live tests in this session covered)
-- just making sure the curl_cffi.requests.Session swap actually wires up
the way client.py assumes: .headers/.cookies/.proxies behave like dicts,
impersonate is accepted, and RunStats/rate-limiter get attached correctly.
"""
from __future__ import annotations

from pathlib import Path

from curl_cffi import requests as curl_requests

from bbb_scraper.config import Settings
from bbb_scraper.scraping.client import HttpClient
from bbb_scraper.utils.stats import RunStats


def _cfg(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        proxy_enabled=False,
        bbb_session_file=tmp_path / "no_such_session.json",  # missing on purpose
        http_impersonate="chrome150",
        http_min_delay_seconds=0.0,
        http_max_delay_seconds=0.0,
    )
    base.update(overrides)
    return Settings(**base)


def test_http_client_constructs_a_curl_cffi_session(tmp_path):
    client = HttpClient(_cfg(tmp_path))
    assert isinstance(client.session, curl_requests.Session)
    client.close()


def test_http_client_applies_default_headers(tmp_path):
    client = HttpClient(_cfg(tmp_path))
    headers = dict(client.session.headers)
    assert headers.get("accept-language") == "en-US,en;q=0.9"
    assert "user-agent" in headers
    client.close()


def test_http_client_uses_configured_impersonation_target(tmp_path):
    client = HttpClient(_cfg(tmp_path, http_impersonate="chrome131"))
    assert client.session.impersonate == "chrome131"
    client.close()


def test_http_client_attaches_stats_and_rate_limiter(tmp_path):
    stats = RunStats()
    client = HttpClient(_cfg(tmp_path), stats=stats)
    assert client.stats is stats
    assert client.rate_limiter is not None
    client.close()


def test_http_client_works_as_a_context_manager(tmp_path):
    with HttpClient(_cfg(tmp_path)) as client:
        assert isinstance(client.session, curl_requests.Session)
