"""Extractor.extract_business_reviews -- the pagination orchestration around
parse_business_reviews_page (see tests/parsing/test_business_parser.py for
the parsing itself). A lightweight fake stands in for a real Extractor
(business_client + stats only) so this never touches HttpClient/the
network -- Extractor's own __init__ isn't exercised here at all, only the
method under test, called unbound against the fake."""
from __future__ import annotations

from types import SimpleNamespace

from bbb_scraper.etl.extract import Extractor, _strip_address_id
from bbb_scraper.parsing.models import BBBReview, BBBReviewsPage
from bbb_scraper.utils.stats import RunStats

PROFILE_URL = "https://www.bbb.org/us/or/hillsboro/profile/roofing-contractors/dabella-1296-22828301"


class _FakeBusinessClient:
    """Returns canned HTML per call, recording every URL fetched -- the
    HTML itself is never really parsed (parse_business_reviews_page is
    monkeypatched too), it's just a placeholder the real signature expects."""

    def __init__(self):
        self.fetched_urls: list[str] = []

    def fetch(self, url: str, *, referer: str | None = None):
        self.fetched_urls.append(url)
        return SimpleNamespace(html=f"<html>page for {url}</html>")


def _fake_extractor(client: _FakeBusinessClient) -> SimpleNamespace:
    return SimpleNamespace(business_client=client, stats=RunStats())


def _page(reviews: list[BBBReview], *, total_pages: int | None) -> BBBReviewsPage:
    return BBBReviewsPage(reviews=reviews, page=1, page_size=10, total_pages=total_pages, num_found=None)


# --- _strip_address_id -----------------------------------------------------
# Real bug, caught by an actual live smoke test against real checkpoint
# URLs: a /addressId/N-suffixed profile_url (the majority case for a real
# BBB record) 404s if /customer-reviews is appended straight onto the end
# -- reviews are a company-level list, confirmed by reading the real
# customer-reviews link off a real business's own profile page.

def test_strip_address_id_removes_a_real_suffix():
    url = "https://www.bbb.org/us/az/tempe/profile/fire-water-damage-restoration/zona-restoration-llc-1126-1000031171/addressId/140070"
    assert _strip_address_id(url) == (
        "https://www.bbb.org/us/az/tempe/profile/fire-water-damage-restoration/zona-restoration-llc-1126-1000031171"
    )


def test_strip_address_id_is_a_noop_without_a_suffix():
    url = "https://www.bbb.org/us/or/hillsboro/profile/roofing-contractors/dabella-1296-22828301"
    assert _strip_address_id(url) == url


def test_extract_business_reviews_uses_the_stripped_url(monkeypatch):
    monkeypatch.setattr(
        "bbb_scraper.etl.extract.parse_business_reviews_page",
        lambda html, stats=None: _page([], total_pages=1),
    )
    client = _FakeBusinessClient()
    suffixed_url = f"{PROFILE_URL}/addressId/999888"
    Extractor.extract_business_reviews(_fake_extractor(client), suffixed_url, max_reviews=None)

    assert client.fetched_urls == [f"{PROFILE_URL}/customer-reviews?page=1"]


def test_stops_when_max_reviews_reached(monkeypatch):
    """3 reviews/page, max_reviews=7 -> stops partway through page 3, and
    trims the overshoot (7, not 9) rather than returning a whole extra page."""
    pages = [
        _page([BBBReview(review_id=f"p1-{i}") for i in range(3)], total_pages=5),
        _page([BBBReview(review_id=f"p2-{i}") for i in range(3)], total_pages=5),
        _page([BBBReview(review_id=f"p3-{i}") for i in range(3)], total_pages=5),
    ]
    monkeypatch.setattr("bbb_scraper.etl.extract.parse_business_reviews_page", lambda html, stats=None: pages.pop(0))

    client = _FakeBusinessClient()
    reviews = Extractor.extract_business_reviews(_fake_extractor(client), PROFILE_URL, max_reviews=7)

    assert len(reviews) == 7
    assert len(client.fetched_urls) == 3  # needed all 3 pages to reach 7


def test_stops_when_total_pages_is_exhausted(monkeypatch):
    pages = [
        _page([BBBReview(review_id="p1-0")], total_pages=2),
        _page([BBBReview(review_id="p2-0")], total_pages=2),
    ]
    monkeypatch.setattr("bbb_scraper.etl.extract.parse_business_reviews_page", lambda html, stats=None: pages.pop(0))

    client = _FakeBusinessClient()
    reviews = Extractor.extract_business_reviews(_fake_extractor(client), PROFILE_URL, max_reviews=None)

    assert len(reviews) == 2
    assert len(client.fetched_urls) == 2  # never requested a 3rd page beyond total_pages=2


def test_stops_when_a_page_comes_back_with_zero_reviews(monkeypatch):
    """A business with fewer real reviews than total_pages implied (or a
    genuinely exhausted history) -- stop cleanly, don't loop forever."""
    pages = [_page([BBBReview(review_id="p1-0")], total_pages=1000), _page([], total_pages=1000)]
    monkeypatch.setattr("bbb_scraper.etl.extract.parse_business_reviews_page", lambda html, stats=None: pages.pop(0))

    client = _FakeBusinessClient()
    reviews = Extractor.extract_business_reviews(_fake_extractor(client), PROFILE_URL, max_reviews=None)

    assert len(reviews) == 1
    assert len(client.fetched_urls) == 2


def test_a_failed_page_fetch_stops_and_keeps_whatever_was_already_gathered(monkeypatch):
    call_count = {"n": 0}

    def _fetch(url, *, referer=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return SimpleNamespace(html="<html>page 1</html>")
        raise RuntimeError("simulated: e.g. a timeout on page 2")

    monkeypatch.setattr(
        "bbb_scraper.etl.extract.parse_business_reviews_page",
        lambda html, stats=None: _page([BBBReview(review_id="p1-0")], total_pages=10),
    )
    client = SimpleNamespace(fetch=_fetch)
    reviews = Extractor.extract_business_reviews(_fake_extractor(client), PROFILE_URL, max_reviews=None)

    assert len(reviews) == 1  # page 1's review is kept, not lost because page 2 failed
    assert call_count["n"] == 2


def test_requests_use_the_page_query_param_pattern(monkeypatch):
    monkeypatch.setattr(
        "bbb_scraper.etl.extract.parse_business_reviews_page",
        lambda html, stats=None: _page([], total_pages=1),
    )
    client = _FakeBusinessClient()
    Extractor.extract_business_reviews(_fake_extractor(client), PROFILE_URL, max_reviews=None)

    assert client.fetched_urls == [f"{PROFILE_URL}/customer-reviews?page=1"]


def test_default_referer_is_the_profile_url_itself(monkeypatch):
    monkeypatch.setattr(
        "bbb_scraper.etl.extract.parse_business_reviews_page",
        lambda html, stats=None: _page([], total_pages=1),
    )
    seen_referers = []
    client = SimpleNamespace(fetch=lambda url, *, referer=None: (seen_referers.append(referer), SimpleNamespace(html=""))[1])
    Extractor.extract_business_reviews(_fake_extractor(client), PROFILE_URL, max_reviews=None)

    assert seen_referers == [PROFILE_URL]
