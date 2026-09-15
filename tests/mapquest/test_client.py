"""MapQuestClient -- parsing against real captured search responses (see
tests/fixtures/mapquest_search_*.json), plus a mocked search() call (no
real network, no real sleeps)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

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

def test_search_returns_only_real_business_nodes(monkeypatch):
    fixture = _load("mapquest_search_walsh.json")
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = fixture

    client = MapQuestClient(cfg=_cfg())
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)

    results = client.search("Walsh Crawlspace & Structural Repair, LLC", latitude=35.2271, longitude=-80.8431)

    assert len(results) == 2
    assert all(isinstance(r, MapQuestMatch) for r in results)


def test_search_raises_scrape_error_on_graphql_errors(monkeypatch):
    from bbb_scraper.exceptions import ScrapeError

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"errors": [{"message": "Cannot query field \"bogus\""}]}

    client = MapQuestClient(cfg=_cfg())
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)

    try:
        client.search("anything", latitude=0.0, longitude=0.0)
        raised = False
    except ScrapeError:
        raised = True
    assert raised


def test_search_empty_results_is_an_empty_list_not_a_crash(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"data": {"search": {"nodes": []}}}

    client = MapQuestClient(cfg=_cfg())
    monkeypatch.setattr(client.session, "post", lambda *a, **k: resp)

    assert client.search("nothing matches this", latitude=0.0, longitude=0.0) == []
