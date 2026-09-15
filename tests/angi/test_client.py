"""AngiClient's proxy rotation and 429 handling -- no real network, no real
sleeps (patches time.sleep in both this module and RateLimiter's)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from curl_cffi import requests as curl_requests

from bbb_scraper.angi.client import AngiClient
from bbb_scraper.config import Settings
from bbb_scraper.exceptions import ScrapeError


def _cfg(**overrides) -> Settings:
    base = {
        "angi_max_retries": 3,
        "angi_min_delay_seconds": 0.0,
        "angi_max_delay_seconds": 0.0,
        "angi_proxy_rotate_every": 2,
        "proxy_host": "gate.decodo.com",
        "proxy_port": 10000,
        "proxy_username": "user",
        "proxy_password": "pass",
        "proxy_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)


def _ok_response(text: str = "<html></html>") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


def _rate_limited_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 429
    return resp


@pytest.fixture(autouse=True)
def _no_real_sleeps():
    with patch("bbb_scraper.angi.client.time.sleep"), patch("bbb_scraper.utils.rate_limit.time.sleep"):
        yield


@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_proxied_by_default(mock_get_proxies, mock_session_cls):
    mock_get_proxies.return_value = {"http": "http://user-session-x:pass@gate.decodo.com:10000"}
    mock_session_cls.return_value.get.return_value = _ok_response()

    AngiClient(_cfg())

    mock_get_proxies.assert_called_once()


@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_use_proxy_false_never_calls_get_proxies(mock_get_proxies, mock_session_cls):
    mock_session_cls.return_value.get.return_value = _ok_response()

    client = AngiClient(_cfg(), use_proxy=False)
    client.get("https://www.angi.com/x.htm")

    mock_get_proxies.assert_not_called()


@patch("bbb_scraper.angi.client.new_session_id")
@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_rotates_after_configured_request_count(mock_get_proxies, mock_session_cls, mock_new_session_id):
    mock_new_session_id.side_effect = [f"sess-{i}" for i in range(10)]
    mock_get_proxies.return_value = {"http": "http://proxied"}
    mock_session_cls.return_value.get.return_value = _ok_response()

    client = AngiClient(_cfg(), rotate_every=2)
    # one rotation happened in __init__ already
    assert mock_get_proxies.call_count == 1

    client.get("https://www.angi.com/1.htm")
    client.get("https://www.angi.com/2.htm")
    # 2 requests made, rotate_every=2 -- the 3rd request should trigger a rotation first
    assert mock_get_proxies.call_count == 1
    client.get("https://www.angi.com/3.htm")
    assert mock_get_proxies.call_count == 2


@patch("bbb_scraper.angi.client.new_session_id")
@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_429_triggers_immediate_rotation_and_cooldown(mock_get_proxies, mock_session_cls, mock_new_session_id):
    mock_new_session_id.side_effect = [f"sess-{i}" for i in range(10)]
    mock_get_proxies.return_value = {"http": "http://proxied"}
    session = mock_session_cls.return_value
    session.get.side_effect = [_rate_limited_response(), _ok_response()]

    client = AngiClient(_cfg(), rotate_every=100)  # rotation from the count alone won't fire
    calls_after_init = mock_get_proxies.call_count

    with patch("bbb_scraper.angi.client.time.sleep") as mock_sleep:
        text = client.get("https://www.angi.com/x.htm")

    assert text == "<html></html>"
    # a 429-triggered rotation happened in addition to init's
    assert mock_get_proxies.call_count == calls_after_init + 1
    # the 25s cooldown really was requested (tenacity's own exponential
    # backoff between attempts sleeps too -- that call is separate and
    # expected, not what this assertion is pinning)
    assert 25.0 in [call.args[0] for call in mock_sleep.call_args_list]


@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_missing_proxy_credentials_warns_but_does_not_raise(mock_get_proxies, mock_session_cls, caplog):
    mock_get_proxies.return_value = {}  # get_proxies() with no PROXY_HOST etc. returns {}
    mock_session_cls.return_value.get.return_value = _ok_response()

    AngiClient(_cfg())  # must not raise

    assert any("use_proxy=True but get_proxies()" in r.message for r in caplog.records)


@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_request_exception_after_retries_becomes_scrape_error(mock_get_proxies, mock_session_cls):
    mock_get_proxies.return_value = {"http": "http://proxied"}
    mock_session_cls.return_value.get.side_effect = curl_requests.exceptions.RequestException("boom")

    client = AngiClient(_cfg())
    with pytest.raises(ScrapeError):
        client.get("https://www.angi.com/x.htm")
