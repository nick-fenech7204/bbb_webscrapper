from bbb_scraper.match.matcher import match_datasets


def _rec(**kw):
    base = {"name": "X", "phone": None, "city": "Miami", "state": "FL",
            "postal_code": "33125", "lat": 25.78, "lon": -80.24}
    base.update(kw)
    return base


_bbb = _rec
_yelp = _rec


def test_exact_phone_and_name_is_confident():
    out = match_datasets(
        [_bbb(name="Machado Auto Sales, LLC", phone="(305) 642-4409")],
        [_yelp(name="Machado Auto Sales", phone="+13056424409")],
    )
    assert len(out.pairs) == 1
    p = out.pairs[0]
    assert p.band == "confident"
    assert p.signals["phone"] == 1.0
    assert p.confidence >= 0.9


def test_name_plus_geo_without_phone_still_matches():
    out = match_datasets(
        [_bbb(name="Bird Road Auto Sales", phone=None, lat=25.734, lon=-80.30)],
        [_yelp(name="Bird Road Auto Sales", phone=None, lat=25.735, lon=-80.301)],
    )
    assert len(out.pairs) == 1
    assert "phone" not in out.pairs[0].signals  # neither side had a phone
    assert out.pairs[0].confidence >= 0.6


def test_same_zip_different_business_does_not_match():
    out = match_datasets(
        [_bbb(name="Green Light Auto Sales", phone="(305) 111-1111")],
        [_yelp(name="Bird Road Motors", phone="(305) 222-2222")],
    )
    assert out.pairs == []
    assert len(out.bbb_only) == 1
    assert len(out.yelp_only) == 1


def test_assignment_is_one_to_one():
    """Two BBB rows, one plausible Yelp row -> only the better BBB row wins it."""
    out = match_datasets(
        [
            _bbb(name="Colon Auto Sales Inc", phone="(305) 333-3333"),
            _bbb(name="Colon Auto Sales", phone="(305) 333-3333"),
        ],
        [_yelp(name="Colon Auto Sales", phone="+13053333333")],
    )
    assert len(out.pairs) == 1
    assert len(out.bbb_only) == 1


def test_missing_geo_does_not_dilute_phone_plus_name():
    out = match_datasets(
        [_bbb(name="Repo Auto Brokers LLC", phone="(305) 444-4444", lat=None, lon=None)],
        [_yelp(name="Repo Auto Brokers", phone="+13054444444", lat=None, lon=None)],
    )
    assert len(out.pairs) == 1
    assert "geo" not in out.pairs[0].signals
    assert out.pairs[0].confidence >= 0.9


def test_summary_counts():
    out = match_datasets(
        [_bbb(name="A Co", phone="(305) 555-5555"), _bbb(name="B Co", phone="(305) 666-6666")],
        [_yelp(name="A Co", phone="+13055555555"), _yelp(name="Z Co", phone="(999) 999-9999", postal_code="99999")],
    )
    s = out.summary
    assert s["bbb_total"] == 2
    assert s["yelp_total"] == 2
    assert s["matched"] == 1
    assert s["bbb_only"] == 1
    assert s["yelp_only"] == 1
