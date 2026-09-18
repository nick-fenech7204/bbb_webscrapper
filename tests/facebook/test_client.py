"""FacebookClient -- a mocked session (no real network, no real sleeps),
same shape as tests/mapquest/test_client.py since the client mirrors
MapQuestClient's proxy/retry/rate-limit architecture."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from curl_cffi import requests as curl_requests

from bbb_scraper.config import Settings
from bbb_scraper.exceptions import ScrapeError
from bbb_scraper.facebook.client import FacebookClient


def _cfg(**overrides) -> Settings:
    base = {"facebook_max_retries": 3, "facebook_min_delay_seconds": 0.0, "facebook_max_delay_seconds": 0.0}
    base.update(overrides)
    return Settings(**base)


def _proxy_cfg(**overrides) -> Settings:
    base = {
        "facebook_max_retries": 3, "facebook_min_delay_seconds": 0.0, "facebook_max_delay_seconds": 0.0,
        "proxy_host": "gate.decodo.com", "proxy_port": 10000,
        "proxy_username": "user", "proxy_password": "pass", "proxy_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)


def _ok_response(text: str = "<html><head></head><body></body></html>") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.text = text
    return resp


def _rate_limited_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 429
    return resp


@patch("bbb_scraper.facebook.client.curl_requests.Session")
@patch("bbb_scraper.facebook.client.get_proxies")
def test_fetch_profile_parses_the_response_body(mock_get_proxies, mock_session_cls):
    mock_get_proxies.return_value = {"http": "http://user:pass@gate.decodo.com:10000"}
    html = (
        '<html><head><meta property="og:title" content="Real Co | City ST" />'
        '<meta property="og:description" content="Real Co, City. 10 followers." />'
        '</head><body></body></html>'
    )
    mock_session_cls.return_value.get.return_value = _ok_response(html)

    client = FacebookClient(_proxy_cfg())
    profile = client.fetch_profile("https://www.facebook.com/RealCo/")

    assert profile.status == "ok"
    assert profile.name == "Real Co"
    assert profile.followers_count == 10


@patch("bbb_scraper.facebook.client.curl_requests.Session")
@patch("bbb_scraper.facebook.client.get_proxies")
def test_no_cookies_are_ever_sent(mock_get_proxies, mock_session_cls):
    """The whole point of going anonymous instead of replaying a personal
    session (see module docstring) -- confirm this client genuinely never
    attaches a cookie to the session or the request."""
    mock_get_proxies.return_value = {}
    mock_session_cls.return_value.get.return_value = _ok_response()

    client = FacebookClient(_cfg(), use_proxy=False)
    client.fetch_profile("https://www.facebook.com/RealCo/")

    session = mock_session_cls.return_value
    assert not session.cookies.set.called
    _, kwargs = session.get.call_args
    assert "cookies" not in kwargs


@patch("bbb_scraper.facebook.client.curl_requests.Session")
@patch("bbb_scraper.facebook.client.get_proxies")
def test_every_request_gets_a_fresh_session(mock_get_proxies, mock_session_cls):
    mock_get_proxies.return_value = {"http": "http://proxied"}
    mock_session_cls.return_value.get.return_value = _ok_response()

    client = FacebookClient(_proxy_cfg())
    client.fetch_profile("https://www.facebook.com/A/")
    client.fetch_profile("https://www.facebook.com/B/")

    # __init__ builds one, fetch_profile rebuilds one on each of its 2 calls
    assert mock_session_cls.call_count == 3


@patch("bbb_scraper.facebook.client.curl_requests.Session")
@patch("bbb_scraper.facebook.client.get_proxies")
def test_429_cooldown_then_a_retry_succeeds(mock_get_proxies, mock_session_cls, monkeypatch):
    monkeypatch.setattr("bbb_scraper.facebook.client.time.sleep", lambda *_: None)
    monkeypatch.setattr("bbb_scraper.facebook.client._RATE_LIMIT_COOLDOWN_SECONDS", 0.0)
    mock_get_proxies.return_value = {"http": "http://proxied"}
    session = mock_session_cls.return_value
    ok_html = '<html><head><meta property="og:title" content="Real Co | City ST" /></head></html>'
    session.get.side_effect = [_rate_limited_response(), _ok_response(ok_html)]

    client = FacebookClient(_proxy_cfg())
    profile = client.fetch_profile("https://www.facebook.com/A/")

    assert profile.status == "ok"
    assert session.get.call_count == 2


@patch("bbb_scraper.facebook.client.curl_requests.Session")
@patch("bbb_scraper.facebook.client.get_proxies")
def test_exhausted_retries_raise_scrape_error(mock_get_proxies, mock_session_cls):
    mock_get_proxies.return_value = {}
    mock_session_cls.return_value.get.side_effect = curl_requests.exceptions.RequestException("boom")

    client = FacebookClient(_cfg(facebook_max_retries=1), use_proxy=False)
    with pytest.raises(ScrapeError):
        client.fetch_profile("https://www.facebook.com/A/")
