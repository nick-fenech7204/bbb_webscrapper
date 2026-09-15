"""AngiClient's proxy handling and 429 cooldown -- no real network, no real
sleeps (patches time.sleep in both this module and RateLimiter's).

2026-09-15: rewritten for the fresh-session-per-request design that
replaced periodic sticky-session rotation -- see client.py's own module
docstring for the real incident (a 100% 407 failure rate on the first real
batch run) this fixes. The old rotate_every/new_session_id-based tests are
gone; what matters now is (1) every request gets its own fresh Session,
(2) that Session's proxy is always bare -- no session_id, ever sticky."""
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
    mock_get_proxies.return_value = {"http": "http://user:pass@gate.decodo.com:10000"}
    mock_session_cls.return_value.get.return_value = _ok_response()

    AngiClient(_cfg())

    mock_get_proxies.assert_called_once()


@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_proxy_is_always_bare_no_session_id_ever(mock_get_proxies, mock_session_cls):
    """The actual point of the whole 2026-09-15 fix: a `-session-{id}`
    suffix is Decodo's *sticky*-session mechanism (confirmed against
    Decodo's own docs) -- get_proxies must never be called with a
    session_id, at construction or on any later request, or this
    regresses right back to the real 100%-407 incident."""
    mock_get_proxies.return_value = {"http": "http://user:pass@gate.decodo.com:10000"}
    mock_session_cls.return_value.get.return_value = _ok_response()

    client = AngiClient(_cfg())
    client.get("https://www.angi.com/1.htm")
    client.get("https://www.angi.com/2.htm")

    for call in mock_get_proxies.call_args_list:
        assert "session_id" not in call.kwargs
        assert len(call.args) <= 1  # only ever cfg, positionally -- never a second (session_id) positional arg


@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_use_proxy_false_never_calls_get_proxies(mock_get_proxies, mock_session_cls):
    mock_session_cls.return_value.get.return_value = _ok_response()

    client = AngiClient(_cfg(), use_proxy=False)
    client.get("https://www.angi.com/x.htm")

    mock_get_proxies.assert_not_called()


@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_every_request_gets_a_fresh_session(mock_get_proxies, mock_session_cls):
    """Confirmed live 2026-09-15: a *reused* Session keeps the same exit
    IP regardless of proxy username (Decodo rotates per new connection,
    not per HTTP request over a kept-alive one) -- so "a new proxy per
    request" requires a genuinely new Session per request, not just a
    changed .proxies value on the same one. One Session at construction +
    one per .get() call."""
    mock_get_proxies.return_value = {"http": "http://user:pass@gate.decodo.com:10000"}
    mock_session_cls.return_value.get.return_value = _ok_response()

    client = AngiClient(_cfg())
    assert mock_session_cls.call_count == 1  # __init__ builds one

    client.get("https://www.angi.com/1.htm")
    assert mock_session_cls.call_count == 2

    client.get("https://www.angi.com/2.htm")
    assert mock_session_cls.call_count == 3


@patch("bbb_scraper.angi.client.curl_requests.Session")
@patch("bbb_scraper.angi.client.get_proxies")
def test_429_cooldown_then_a_retry_succeeds(mock_get_proxies, mock_session_cls):
    mock_get_proxies.return_value = {"http": "http://proxied"}
    session = mock_session_cls.return_value
    session.get.side_effect = [_rate_limited_response(), _ok_response()]

    client = AngiClient(_cfg())
    sessions_after_init = mock_session_cls.call_count

    with patch("bbb_scraper.angi.client.time.sleep") as mock_sleep:
        text = client.get("https://www.angi.com/x.htm")

    assert text == "<html></html>"
    # the retry (tenacity, after the 429 raised RateLimitedError) got its
    # own fresh session too, same as any other attempt
    assert mock_session_cls.call_count == sessions_after_init + 2  # the 429 attempt + the retry
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
