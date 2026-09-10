import json
from types import SimpleNamespace

import pytest

from bbb_scraper.yelp.client import YelpClient, YelpConfigError


def _cfg(tmp_path, *, key="test-key"):
    """Minimal duck-typed Settings -- avoids loading the real .env so the
    test is hermetic regardless of what's configured locally."""
    return SimpleNamespace(
        yelp_api_key=key,
        yelp_api_base_url="https://api.yelp.com/v3",
        yelp_min_delay_seconds=0.0,
        yelp_max_delay_seconds=0.0,
        raw_data_dir=tmp_path,
        http_timeout_seconds=5.0,
    )


def test_missing_key_raises():
    with pytest.raises(YelpConfigError):
        YelpClient(_cfg(None, key=""))


def test_read_through_cache_serves_without_network(tmp_path):
    client = YelpClient(_cfg(tmp_path))
    params = {"limit": 50, "offset": 0, "term": "car dealers", "location": "Jacksonville, FL"}
    cache_path = client._cache_path("businesses/search", params)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"businesses": [{"id": "x", "name": "Cached Co"}], "total": 1}))

    out = client.search(term="car dealers", location="Jacksonville, FL")

    assert client.calls_made == 0  # never touched the network
    assert out["businesses"][0]["name"] == "Cached Co"


def test_cache_path_is_stable_and_param_order_independent(tmp_path):
    client = YelpClient(_cfg(tmp_path))
    a = client._cache_path("businesses/search", {"term": "x", "location": "y", "limit": 50})
    b = client._cache_path("businesses/search", {"limit": 50, "location": "y", "term": "x"})
    assert a == b


def test_search_requires_location_or_latlng(tmp_path):
    client = YelpClient(_cfg(tmp_path))
    with pytest.raises(ValueError):
        client.search(term="car dealers")
