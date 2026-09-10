import bbb_scraper.match.enrich as enrich_mod
from bbb_scraper.match.enrich import (
    YelpEnrichmentState,
    enrich_bbb_with_yelp,
    open_yelp_enrichment,
)
from bbb_scraper.yelp.client import YelpConfigError

_BBB = [
    {"name": "Machado Auto Sales", "phone": "(305) 642-4409", "city": "Miami",
     "state": "FL", "postal_code": "33125", "lat": 25.78, "lon": -80.24, "rating": "A+"},
    {"name": "Nowhere Motors", "phone": "(305) 000-0000", "city": "Miami",
     "state": "FL", "postal_code": "33199", "lat": 25.70, "lon": -80.40, "rating": "B"},
]


class _FakeClient:
    def __init__(self, remaining=250):
        self.last_rate_limit = {"remaining": remaining}
        self.calls = 0

    def search(self, *, term, location=None, limit, offset=0, sort_by=None, **kw):
        self.calls += 1
        if offset > 0:
            return {"businesses": [], "total": 1}
        return {
            "total": 1,
            "businesses": [
                {"id": "y1", "name": "Machado Auto Sales",
                 "phone": "+13056424409", "location": {"city": "Miami", "state": "FL",
                 "zip_code": "33125"}, "coordinates": {"latitude": 25.78, "longitude": -80.24},
                 "rating": 4.5, "review_count": 40},
            ],
        }


def test_disabled_state_returns_bbb_only_rows():
    state = YelpEnrichmentState(enabled=False)
    rows = enrich_bbb_with_yelp(_BBB, "car dealers", "Miami, FL", state)
    assert len(rows) == 2
    assert all(r["match_status"] == "bbb_only" for r in rows)
    assert all(r["yelp_name"] == "" for r in rows)
    assert rows[0]["bbb_name"] == "Machado Auto Sales"


def test_happy_path_matches_and_appends_yelp_columns():
    state = YelpEnrichmentState(enabled=True, client=_FakeClient())
    rows = enrich_bbb_with_yelp(_BBB, "car dealers", "Miami, FL", state)
    assert len(rows) == 2  # BBB-primary: no yelp_only tail even though every Yelp biz here matched
    matched = [r for r in rows if r["match_status"] == "matched"]
    assert len(matched) == 1
    assert matched[0]["yelp_name"] == "Machado Auto Sales"
    assert matched[0]["yelp_rating"] == 4.5
    assert matched[0]["review_need_score"] is not None


def test_low_quota_disables_without_calling():
    fake = _FakeClient(remaining=5)
    state = YelpEnrichmentState(enabled=True, client=fake)
    rows = enrich_bbb_with_yelp(_BBB, "car dealers", "Miami, FL", state)
    assert fake.calls == 0
    assert state.enabled is False
    assert "quota" in state.reason_off
    assert all(r["match_status"] == "bbb_only" for r in rows)


def test_call_failure_disables_for_rest_of_batch():
    class _Boom(_FakeClient):
        def search(self, **kw):
            raise RuntimeError("429 Too Many Requests")

    state = YelpEnrichmentState(enabled=True, client=_Boom())
    rows = enrich_bbb_with_yelp(_BBB, "car dealers", "Miami, FL", state)
    assert state.enabled is False
    assert "call failed" in state.reason_off
    assert all(r["match_status"] == "bbb_only" for r in rows)
    # a subsequent metro also comes back BBB-only, no further calls attempted
    rows2 = enrich_bbb_with_yelp(_BBB, "car dealers", "Tampa, FL", state)
    assert all(r["match_status"] == "bbb_only" for r in rows2)


def test_open_yelp_enrichment_no_key(monkeypatch):
    def _boom(*a, **k):
        raise YelpConfigError("YELP_API_KEY is not set")

    monkeypatch.setattr(enrich_mod, "YelpClient", _boom)
    state = open_yelp_enrichment(enabled=True)
    assert state.enabled is False
    assert "YELP_API_KEY" in state.reason_off


def test_open_yelp_enrichment_disabled_flag():
    assert open_yelp_enrichment(enabled=False).enabled is False
