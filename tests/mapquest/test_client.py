"""MapQuestClient -- parsing against real captured search responses (see
tests/fixtures/mapquest_search_*.json), a mocked search() call, and proxy
rotation / 429 handling (no real network, no real sleeps) -- same shape as
tests/angi/test_client.py, since the client itself mirrors AngiClient."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from curl_cffi import requests as curl_requests

from bbb_scraper.config import Settings
from bbb_scraper.mapquest.client import MapQuestClient, _map_node
from bbb_scraper.mapquest.models import MapQuestMatch

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _cfg(**overrides) -> Settings:
    base = {"mapquest_max_retries": 3, "mapquest_min_delay_seconds": 0.0, "mapquest_max_delay_seconds": 0.0}
    base.update(overrides)
    return Settings(**base)


def _proxy_cfg(**overrides) -> Settings:
    base = {
        "mapquest_max_retries": 3,
        "mapquest_min_delay_seconds": 0.0,
        "mapquest_max_delay_seconds": 0.0,
        "proxy_host": "gate.decodo.com",
        "proxy_port": 10000,
        "proxy_username": "user",
        "proxy_password": "pass",
        "proxy_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)


def _ok_response(json_body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_body if json_body is not None else {"data": {"search": {"nodes": []}}}
    return resp


def _rate_limited_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 429
    return resp


# --- _map_node / _map_review, against real fixture data ---------------------

def test_map_node_against_real_walsh_response():
    data = _load("mapquest_search_walsh.json")
    nodes = data["data"]["search"]["nodes"]
    matches = [_map_node(n) for n in nodes]

    assert len(matches) == 2
    with_reviews = next(m for m in matches if m.mapquest_id == "423145406")
    assert with_reviews.name == "Walsh Crawl Space and Structural Repair"
    assert with_reviews.phone == "+19802233113"
    assert with_reviews.city == "Charlotte"
    assert with_reviews.state == "NC"
    assert with_reviews.review_count == 5
    assert len(with_reviews.reviews) == 5


def test_map_review_rating_is_halved_to_a_5_point_scale():
    """Confirmed live: MapQuest's own API returns review ratings 0-10, not
    the 0-5 scale everything else (BBB/Angi/Yelp) already uses."""
    data = _load("mapquest_search_walsh.json")
    nodes = data["data"]["search"]["nodes"]
    match = next(_map_node(n) for n in nodes if n["id"] == "423145406")

    ratings = {r.reviewer_name: r.rating for r in match.reviews}
    assert ratings["Mark H."] == 5.0  # raw 10 -> 5.0
    assert ratings["LaTora L."] == 1.0  # raw 2 -> 1.0


def test_map_review_captures_text_date_and_reviewer():
    data = _load("mapquest_search_walsh.json")
    nodes = data["data"]["search"]["nodes"]
    match = next(_map_node(n) for n in nodes if n["id"] == "423145406")

    mark = next(r for r in match.reviews if r.reviewer_name == "Mark H.")
    assert mark.date == "2022-08-16"
    assert "96 year old home" in mark.text


def test_map_node_zero_reviews_is_an_empty_list_not_a_crash():
    data = _load("mapquest_search_walsh.json")
    nodes = data["data"]["search"]["nodes"]
    match = next(_map_node(n) for n in nodes if n["id"] == "729458476")

    assert match.review_count == 0
    assert match.reviews == []


def test_map_node_against_real_platinum_exteriors_response():
    data = _load("mapquest_search_platinum_exteriors.json")
    nodes = data["data"]["search"]["nodes"]
    matches = [_map_node(n) for n in nodes]

    real = next(m for m in matches if m.review_count > 0)
    assert real.name == "Platinum Exteriors"
    assert real.phone == "+19283015529"
    assert real.review_count == 35


# --- search(), mocked HTTP -------------------------------------------------

@patch("bbb_scraper.mapquest.client.curl_requests.Session")
def test_search_returns_only_real_business_nodes(mock_session_cls):
    # A class-level patch, not monkeypatch.setattr(client.session, ...) --
    # 2026-09-15, every search() call now builds a brand-new Session (see
    # module docstring), so patching one already-constructed instance's
    # .post would silently stop being used after the first call, and the
    # test would start hitting the real live API instead (exactly what
    # this test used to do, undetected, until this was caught fixing this
    # same gotcha in the two tests below).
    fixture = _load("mapquest_search_walsh.json")
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = fixture
    mock_session_cls.return_value.post.return_value = resp

    client = MapQuestClient(cfg=_cfg(), use_proxy=False)
    results = client.search("Walsh Crawlspace & Structural Repair, LLC", latitude=35.2271, longitude=-80.8431)

    assert len(results) == 2
    assert all(isinstance(r, MapQuestMatch) for r in results)


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
def test_search_raises_scrape_error_on_graphql_errors(mock_session_cls):
    from bbb_scraper.exceptions import ScrapeError

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"errors": [{"message": "Cannot query field \"bogus\""}]}
    mock_session_cls.return_value.post.return_value = resp

    client = MapQuestClient(cfg=_cfg(), use_proxy=False)

    try:
        client.search("anything", latitude=0.0, longitude=0.0)
        raised = False
    except ScrapeError:
        raised = True
    assert raised


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
def test_search_empty_results_is_an_empty_list_not_a_crash(mock_session_cls):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"data": {"search": {"nodes": []}}}
    mock_session_cls.return_value.post.return_value = resp

    client = MapQuestClient(cfg=_cfg(), use_proxy=False)
    assert client.search("nothing matches this", latitude=0.0, longitude=0.0) == []


# --- proxy handling, mirroring tests/angi/test_client.py --------------------
# 2026-09-15: rewritten for the fresh-session-per-request design that
# replaced periodic sticky-session rotation -- see client.py's own module
# docstring for the real incident (a 100% 407 failure rate on the first
# real batch run) this fixes.

@pytest.fixture(autouse=True)
def _no_real_sleeps():
    with patch("bbb_scraper.mapquest.client.time.sleep"), patch("bbb_scraper.utils.rate_limit.time.sleep"):
        yield


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
@patch("bbb_scraper.mapquest.client.get_proxies")
def test_proxied_by_default(mock_get_proxies, mock_session_cls):
    mock_get_proxies.return_value = {"http": "http://user:pass@gate.decodo.com:10000"}
    mock_session_cls.return_value.post.return_value = _ok_response()

    MapQuestClient(_proxy_cfg())

    mock_get_proxies.assert_called_once()


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
@patch("bbb_scraper.mapquest.client.get_proxies")
def test_proxy_is_always_bare_no_session_id_ever(mock_get_proxies, mock_session_cls):
    """The actual point of the whole fix: a `-session-{id}` suffix is
    Decodo's *sticky*-session mechanism (confirmed against Decodo's own
    docs) -- get_proxies must never be called with a session_id, at
    construction or on any later request, or this regresses right back
    to the real 100%-407 incident."""
    mock_get_proxies.return_value = {"http": "http://user:pass@gate.decodo.com:10000"}
    mock_session_cls.return_value.post.return_value = _ok_response()

    client = MapQuestClient(_proxy_cfg())
    client.search("a", latitude=0.0, longitude=0.0)
    client.search("b", latitude=0.0, longitude=0.0)

    for call in mock_get_proxies.call_args_list:
        assert "session_id" not in call.kwargs
        assert len(call.args) <= 1  # only ever cfg, positionally -- never a second (session_id) positional arg


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
@patch("bbb_scraper.mapquest.client.get_proxies")
def test_use_proxy_false_never_calls_get_proxies(mock_get_proxies, mock_session_cls):
    mock_session_cls.return_value.post.return_value = _ok_response()

    client = MapQuestClient(_proxy_cfg(), use_proxy=False)
    client.search("anything", latitude=0.0, longitude=0.0)

    mock_get_proxies.assert_not_called()


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
@patch("bbb_scraper.mapquest.client.get_proxies")
def test_every_request_gets_a_fresh_session(mock_get_proxies, mock_session_cls):
    """Confirmed live 2026-09-15: a *reused* Session keeps the same exit
    IP regardless of proxy username (Decodo rotates per new connection,
    not per HTTP request over a kept-alive one) -- so "a new proxy per
    request" requires a genuinely new Session per request, not just a
    changed .proxies value on the same one. One Session at construction +
    one per .search() call."""
    mock_get_proxies.return_value = {"http": "http://user:pass@gate.decodo.com:10000"}
    mock_session_cls.return_value.post.return_value = _ok_response()

    client = MapQuestClient(_proxy_cfg())
    assert mock_session_cls.call_count == 1  # __init__ builds one

    client.search("a", latitude=0.0, longitude=0.0)
    assert mock_session_cls.call_count == 2

    client.search("b", latitude=0.0, longitude=0.0)
    assert mock_session_cls.call_count == 3


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
@patch("bbb_scraper.mapquest.client.get_proxies")
def test_old_session_is_closed_before_being_replaced(mock_get_proxies, mock_session_cls):
    """Real incident, 2026-09-15: skipping this leaked one abandoned
    curl_cffi connection per request -- curl_cffi wraps a real libcurl
    connection that isn't released just because the Python reference is
    dropped. Over real volume (~1,100 businesses) this wedged a real
    production batch solid (memory climbing, dead sockets stuck in
    CloseWait, CPU time barely moving). Every session but the current one
    must have had .close() called on it."""
    mock_get_proxies.return_value = {"http": "http://user:pass@gate.decodo.com:10000"}
    sessions = [MagicMock() for _ in range(3)]
    for s in sessions:
        s.post.return_value = _ok_response()
    mock_session_cls.side_effect = sessions

    client = MapQuestClient(_proxy_cfg())  # consumes sessions[0]
    client.search("a", latitude=0.0, longitude=0.0)  # closes sessions[0], moves to sessions[1]
    client.search("b", latitude=0.0, longitude=0.0)  # closes sessions[1], moves to sessions[2]

    sessions[0].close.assert_called_once()
    sessions[1].close.assert_called_once()
    sessions[2].close.assert_not_called()  # still the live one


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
@patch("bbb_scraper.mapquest.client.get_proxies")
def test_429_cooldown_then_a_retry_succeeds(mock_get_proxies, mock_session_cls):
    mock_get_proxies.return_value = {"http": "http://proxied"}
    session = mock_session_cls.return_value
    session.post.side_effect = [_rate_limited_response(), _ok_response()]

    client = MapQuestClient(_proxy_cfg())
    sessions_after_init = mock_session_cls.call_count

    with patch("bbb_scraper.mapquest.client.time.sleep") as mock_sleep:
        results = client.search("x", latitude=0.0, longitude=0.0)

    assert results == []
    # the retry (tenacity, after the 429 raised RateLimitedError) got its
    # own fresh session too, same as any other attempt
    assert mock_session_cls.call_count == sessions_after_init + 2  # the 429 attempt + the retry
    # the 25s cooldown really was requested (tenacity's own exponential
    # backoff between attempts sleeps too -- that call is separate and
    # expected, not what this assertion is pinning)
    assert 25.0 in [call.args[0] for call in mock_sleep.call_args_list]


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
@patch("bbb_scraper.mapquest.client.get_proxies")
def test_missing_proxy_credentials_warns_but_does_not_raise(mock_get_proxies, mock_session_cls, caplog):
    mock_get_proxies.return_value = {}  # get_proxies() with no PROXY_HOST etc. returns {}
    mock_session_cls.return_value.post.return_value = _ok_response()

    MapQuestClient(_proxy_cfg())  # must not raise

    assert any("use_proxy=True but get_proxies()" in r.message for r in caplog.records)


@patch("bbb_scraper.mapquest.client.curl_requests.Session")
@patch("bbb_scraper.mapquest.client.get_proxies")
def test_request_exception_after_retries_becomes_scrape_error(mock_get_proxies, mock_session_cls):
    from bbb_scraper.exceptions import ScrapeError

    mock_get_proxies.return_value = {"http": "http://proxied"}
    mock_session_cls.return_value.post.side_effect = curl_requests.exceptions.RequestException("boom")

    client = MapQuestClient(_proxy_cfg())
    with pytest.raises(ScrapeError):
        client.search("x", latitude=0.0, longitude=0.0)
