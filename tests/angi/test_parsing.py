"""Real captured (reassembled) Angi pages, not hand-written HTML -- see
tests/fixtures/angi_*.txt and bbb_scraper/angi/__init__.py's docstring for
why fixtures are pre-reassembled flight data rather than raw page source."""
from pathlib import Path

from bbb_scraper.angi.parsing import parse_business_detail, parse_listing_page

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- listing pages -----------------------------------------------------------

def test_listing_page_result_count_and_pagination():
    listing = parse_listing_page(_load("angi_listing_sample.txt"))
    assert listing.result_count == 1091
    assert listing.page_size == 10
    assert listing.page == 1


def test_listing_page_finds_exactly_ten_deduped_profile_urls():
    listing = parse_listing_page(_load("angi_listing_sample.txt"))
    assert len(listing.profile_urls) == 10
    assert len(set(listing.profile_urls)) == 10  # no duplicates despite each appearing twice in the payload


def test_listing_page_profile_urls_are_real_detail_paths():
    listing = parse_listing_page(_load("angi_listing_sample.txt"))
    assert (
        "/companylist/us/il/barrington/all-affordable-plumbing-inc-reviews-6480784.htm"
        in listing.profile_urls
    )
    for url in listing.profile_urls:
        assert url.startswith("/companylist/us/")
        assert "-reviews-" in url


def test_empty_blob_gives_empty_listing_not_a_crash():
    listing = parse_listing_page("")
    assert listing.profile_urls == []
    assert listing.result_count is None


# --- business detail pages: the clean/complete case ---------------------------

_DETAIL_URL = "https://www.angi.com/companylist/us/il/barrington/all-affordable-plumbing-inc-reviews-6480784.htm"


def test_detail_page_core_fields():
    detail = parse_business_detail(_load("angi_business_detail_sample.txt"), _DETAIL_URL)
    assert detail.name == "All Affordable Plumbing Inc"
    assert detail.phone == "8473210338"
    assert detail.website == "http://www.allaffordable.org"
    assert detail.profile_url == _DETAIL_URL  # always the URL actually fetched, even if parsing fails


def test_detail_page_address_is_split_correctly():
    detail = parse_business_detail(_load("angi_business_detail_sample.txt"), _DETAIL_URL)
    assert detail.address == "22099 N Bertha Ln, Barrington, IL 60010"
    assert detail.street == "22099 N Bertha Ln"
    assert detail.city == "Barrington"
    assert detail.state == "IL"
    assert detail.zip_code == "60010"


def test_detail_page_ratings_and_reviews():
    detail = parse_business_detail(_load("angi_business_detail_sample.txt"), _DETAIL_URL)
    assert round(detail.overall_rating, 2) == 4.93
    assert detail.review_count == 166
    assert len(detail.rating_breakdown) == 5
    five_star = next(b for b in detail.rating_breakdown if b.star == 5)
    assert five_star.count == 156


def test_detail_page_written_reviews():
    """Real review text, embedded in the same page already fetched for
    everything else -- confirmed 2026-09-15 while investigating review-text
    availability for a planned local-sentiment pass. Newest first (real
    dateLabel values descend month over month across the fixture)."""
    detail = parse_business_detail(_load("angi_business_detail_sample.txt"), _DETAIL_URL)
    assert len(detail.reviews) == 25  # this fixture's own pageSize
    first = detail.reviews[0]
    assert first.text == "Called to let me know he was on his way.  Was very polite."
    assert first.rating == 5
    assert first.reviewer_name == "Carl S."
    assert first.date_label == "April 2026"
    assert first.is_verified is True
    assert first.job_label == "Sump Pump or Interior Foundation Drains - Install"


def test_detail_page_review_text_decodes_html_entities():
    """A real captured review's text contains a literal `&#39;` -- must come
    through as a real apostrophe, not the raw entity, for a sentiment model
    to read it naturally."""
    detail = parse_business_detail(_load("angi_business_detail_sample.txt"), _DETAIL_URL)
    decoded = [r for r in detail.reviews if r.text and "I've retained" in r.text]
    assert decoded, "expected the real fixture review with an HTML-entity apostrophe to decode cleanly"


def test_detail_page_review_business_response_is_captured():
    detail = parse_business_detail(_load("angi_business_detail_sample.txt"), _DETAIL_URL)
    with_response = [r for r in detail.reviews if r.business_response_text]
    assert with_response
    assert "Thanks for posting" in with_response[0].business_response_text


def test_detail_page_review_recommends_and_cost_label():
    """recommends is real, independent signal from the star rating -- a
    real captured 5-star review in this fixture has recommends=False, so
    this isn't redundant with `rating` and is worth keeping distinct."""
    detail = parse_business_detail(_load("angi_business_detail_sample.txt"), _DETAIL_URL)
    first = detail.reviews[0]
    assert first.rating == 5
    assert first.recommends is False
    assert first.cost_label == "$$40,000"


def test_detail_page_categories_and_about_us():
    detail = parse_business_detail(_load("angi_business_detail_sample.txt"), _DETAIL_URL)
    assert len(detail.categories) == 26
    assert "Water Heater - Install or Replace" in detail.categories
    assert detail.about_us is not None
    assert "family owned" in detail.about_us


def test_detail_page_flags_and_licensing():
    detail = parse_business_detail(_load("angi_business_detail_sample.txt"), _DETAIL_URL)
    assert detail.is_paid_pro is True
    assert detail.is_corporate_account is False
    assert detail.is_super_service_award_winner is True
    assert detail.bonded is False
    assert detail.insured is False
    assert detail.licenses == []


# --- business detail pages: the edge-case business ("-" street placeholder) --

_EDGE_URL = "https://www.angi.com/companylist/us/il/grayslake/mario-and-sons-sewer%2C-llc-reviews-10869080.htm"


def test_dash_street_placeholder_becomes_none_not_a_literal_dash():
    """A business that only lists a service city (no real street address)
    shows "-" as its street on Angi -- must not leak into the parsed
    street field as if "-" were a real address."""
    detail = parse_business_detail(_load("angi_business_detail_edge_cases.txt"), _EDGE_URL)
    assert detail.street is None
    assert detail.city == "Grayslake"
    assert detail.state == "IL"
    assert detail.zip_code == "60030"


def test_dash_street_business_still_parses_everything_else():
    detail = parse_business_detail(_load("angi_business_detail_edge_cases.txt"), _EDGE_URL)
    assert detail.name == "Mario & Sons Sewer, LLC"
    assert detail.phone == "8473474079"
    assert detail.overall_rating == 5
    assert detail.is_super_service_award_winner is True


# --- undefined-value sentinel --------------------------------------------------

def test_dollar_undefined_sentinel_becomes_none():
    """React server components serialize an unset prop as the literal
    string "$undefined" -- confirmed on a real page (a business with no
    website filled in). Must never surface as if it were a real value."""
    from bbb_scraper.angi.parsing import _clean_str

    assert _clean_str("$undefined") is None
    assert _clean_str("") is None
    assert _clean_str("   ") is None
    assert _clean_str("-") is None
    assert _clean_str("Real Value") == "Real Value"
    assert _clean_str(None) is None
    assert _clean_str(42) == 42  # non-strings pass through untouched


def test_dollar_undefined_rating_on_a_zero_review_business_becomes_none():
    """Regression, caught rounding a real CSV's overall_rating column:
    a business with zero reviews has no average to report, and Angi
    represents that the *same* way as an unset string field -- the literal
    "$undefined" -- in what would otherwise be a numeric spot
    (overallRating/reviewCount). Confirmed on 8 of 92 real businesses in
    one real run. Must come out as None, not the literal 11-character
    string flowing into a numeric CSV column."""
    blob = (
        '["$","$L3d",null,{"name":"New Co","logoUrl":"$undefined",'
        '"overallRating":"$undefined","reviewCount":"$undefined",'
        '"isPaidPro":true,"isCorporateAccount":false,'
        '"isSuperServiceAwardWinner":false,"phoneNumber":"5551234567",'
        '"categories":[],"serviceProviderUuid":"abc",'
        '"data-testid":"businessProfileHero"}]'
    )
    detail = parse_business_detail(blob, "https://www.angi.com/companylist/us/il/x/new-co-reviews-1.htm")
    assert detail.name == "New Co"
    assert detail.overall_rating is None
    assert detail.review_count is None


# --- graceful degradation on a page with none of the expected components ------

def test_missing_components_leave_fields_none_not_a_crash():
    detail = parse_business_detail("no flight data at all here", "https://example.com/foo.htm")
    assert detail.profile_url == "https://example.com/foo.htm"
    assert detail.name is None
    assert detail.categories == []
    assert detail.rating_breakdown == []
    assert detail.reviews == []
