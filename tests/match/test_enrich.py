import bbb_scraper.match.enrich as enrich_mod
from bbb_scraper.match.enrich import (
    YelpEnrichmentState,
    enrich_bbb_with_yelp,
    open_yelp_enrichment,
)
from bbb_scraper.yelp.client import YelpAPIError, YelpConfigError

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


def test_real_429_disables_for_rest_of_batch():
    """A genuine rate-limit response -- retrying won't help, this really is
    quota exhaustion, so the old whole-batch-off behavior is still correct
    here."""
    class _Boom(_FakeClient):
        def search(self, **kw):
            raise YelpAPIError(429, "Too Many Requests", "https://api.yelp.com/v3/businesses/search")

    state = YelpEnrichmentState(enabled=True, client=_Boom())
    rows = enrich_bbb_with_yelp(_BBB, "car dealers", "Miami, FL", state)
    assert state.enabled is False
    assert "call failed" in state.reason_off
    assert all(r["match_status"] == "bbb_only" for r in rows)
    # a subsequent metro also comes back BBB-only, no further calls attempted
    rows2 = enrich_bbb_with_yelp(_BBB, "car dealers", "Tampa, FL", state)
    assert all(r["match_status"] == "bbb_only" for r in rows2)


def test_bad_key_disables_for_rest_of_batch():
    """401/403 -- the key itself is the problem, retrying the same call on
    the next metro can't fix that either."""
    class _Boom(_FakeClient):
        def search(self, **kw):
            raise YelpAPIError(401, "Unauthorized", "https://api.yelp.com/v3/businesses/search")

    state = YelpEnrichmentState(enabled=True, client=_Boom())
    enrich_bbb_with_yelp(_BBB, "car dealers", "Miami, FL", state)
    assert state.enabled is False


def test_transient_5xx_does_not_disable_for_rest_of_batch():
    """2026-09-16 real incident: a transient Yelp 500 on metro 1 of a real
    5-metro batch disabled Yelp for all five. A one-off server hiccup
    isn't evidence of quota exhaustion -- this metro comes back BBB-only,
    but the state must stay enabled so the NEXT metro gets a real,
    independent attempt."""
    call_count = {"n": 0}

    class _FlakyOnce(_FakeClient):
        def search(self, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise YelpAPIError(500, "Something went wrong internally, please try again later.",
                                    "https://api.yelp.com/v3/businesses/search")
            return super().search(**kw)

    state = YelpEnrichmentState(enabled=True, client=_FlakyOnce())
    rows = enrich_bbb_with_yelp(_BBB, "car dealers", "Miami, FL", state)
    assert state.enabled is True  # NOT disabled by a transient failure
    assert all(r["match_status"] == "bbb_only" for r in rows)  # this metro still lost Yelp though

    rows2 = enrich_bbb_with_yelp(_BBB, "car dealers", "Tampa, FL", state)
    matched = [r for r in rows2 if r["match_status"] == "matched"]
    assert len(matched) == 1  # the next metro got a real, independent attempt and it worked


def test_non_api_exception_is_also_treated_as_transient():
    """A network-level failure (timeout, connection reset, ...) never even
    reaches a YelpAPIError -- must not be treated as permanent just because
    it's not explicitly recognized as transient."""
    class _Boom(_FakeClient):
        def search(self, **kw):
            raise ConnectionError("connection reset by peer")

    state = YelpEnrichmentState(enabled=True, client=_Boom())
    enrich_bbb_with_yelp(_BBB, "car dealers", "Miami, FL", state)
    assert state.enabled is True


def test_open_yelp_enrichment_no_key(monkeypatch):
    def _boom(*a, **k):
        raise YelpConfigError("YELP_API_KEY is not set")

    monkeypatch.setattr(enrich_mod, "YelpClient", _boom)
    state = open_yelp_enrichment(enabled=True)
    assert state.enabled is False
    assert "YELP_API_KEY" in state.reason_off


def test_open_yelp_enrichment_disabled_flag():
    assert open_yelp_enrichment(enabled=False).enabled is False


def test_bbb_branch_listings_sharing_a_phone_collapse_before_matching():
    """Two BBB rows for one company (different branch addresses, one
    phone) -> one lead, not two -- enrich_bbb_with_yelp phone-dedupes its
    own input so it's correct even if a caller forgot to."""
    branches = [
        {"name": "Goode Plumbing", "phone": "(773) 930-3451", "city": "Evanston",
         "state": "IL", "postal_code": "60201", "rating": "NR"},
        {"name": "Goode Plumbing", "phone": "(773) 930-3451", "city": "Chicago",
         "state": "IL", "postal_code": "60625", "rating": "NR"},
    ]
    state = YelpEnrichmentState(enabled=False)
    rows = enrich_bbb_with_yelp(branches, "plumbers", "Chicago, IL", state)
    assert len(rows) == 1
    assert rows[0]["bbb_city"] == "Evanston"  # first seen wins
