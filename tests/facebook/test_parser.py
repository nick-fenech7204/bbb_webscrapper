"""bbb_scraper.facebook.parser -- against 3 real, different captured Facebook
business pages (tests/fixtures/facebook_page_sample*.html, trimmed to just
the load-bearing <head>/<script> content, not synthesized -- see parser.py's
own module docstring for how these were found and what they cover)."""
from __future__ import annotations

from pathlib import Path

from bbb_scraper.facebook.parser import parse_profile

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- the "everything present" case -------------------------------------------

def test_real_page_with_full_about_card_and_all_three_og_stats():
    html = _load("facebook_page_sample.html")
    p = parse_profile(html, url="https://www.facebook.com/DentalCareOnYellowBluff/")

    assert p.status == "ok"
    assert p.name == "Dental Care on Yellow Bluff"
    assert p.categories == ["Dentist & Dental Office"]
    assert p.address == "12517 Yellow Bluff Rd, Jacksonville, FL, United States, Florida"
    assert p.phone == "(904) 204-7496"
    assert p.email == "DCYellowBluff@mydentalmail.com"
    assert p.website == "dentalcareonyellowbluff.com"
    assert p.hours_status == "Open now"
    assert p.recommend_percentage == 56
    assert p.review_count == 9
    assert p.reviews_url == "https://www.facebook.com/DentalCareOnYellowBluff/reviews"
    assert p.followers_count == 216
    assert p.talking_about_count == 13
    assert p.checkins_count == 42
    assert p.bio == "Your local Jacksonville,FL dental office, offering dentistry for the whole family."


# --- multi-category + a stat that's missing (no "talking about") ------------

def test_real_page_with_multiple_categories_and_a_missing_og_stat():
    html = _load("facebook_page_sample_multi_category.html")
    p = parse_profile(html, url="https://www.facebook.com/WestViningsDentalAesthetics/")

    assert p.categories == ["Dentist & Dental Office", "Cosmetic Dentist", "Teeth Whitening Service"]
    assert p.recommend_percentage == 100
    assert p.review_count == 9
    assert p.followers_count == 495
    assert p.talking_about_count is None  # genuinely absent on this real page, not a parse miss
    assert p.checkins_count == 8


# --- "Not yet rated" + INTRO_CARD_OTHER_ACCOUNT social links ----------------

def test_real_page_not_yet_rated_still_has_review_count_and_social_links():
    html = _load("facebook_page_sample_not_yet_rated.html")
    p = parse_profile(html, url="https://www.facebook.com/PonceDentalGroup/")

    assert p.recommend_percentage is None  # Facebook itself shows "Not yet rated", not a percentage
    assert p.review_count == 4  # still real and present even without a percentage
    assert {"platform": "instagram", "url": "https://www.instagram.com/SmileGeneration"} in p.social_links
    assert {"platform": "twitter", "url": "https://x.com/SmileGen"} in p.social_links
    assert {"platform": "youtube", "url": "https://youtube.com/@SmileGeneration"} in p.social_links
    # Confirmed live: the website card can still carry a tracking query string
    # even in the "clean" plaintext_title field -- stripped at parse time.
    assert p.website == "PonceDentalGroup.com/"
    assert "sc_cid" not in p.website


# --- a page Facebook won't render anonymously (login wall) ------------------

def test_login_walled_page_reports_unavailable_not_empty_fields():
    """Confirmed live, 2026-09-18: a real business's page (StellarPlumbing,
    1 of 15 tested) rendered no og:title/og:description at all, just a
    login-prompt component. Reproduced minimally here (real pages are much
    larger) -- the actual signal this module keys off is simply the absence
    of og:title, confirmed present on all 14 other real pages tested."""
    html = "<html><head><title>Facebook</title></head><body>log in to see more</body></html>"
    p = parse_profile(html, url="https://www.facebook.com/SomePage/")

    assert p.status == "unavailable"
    assert p.name is None
    assert p.phone is None
    assert p.categories == []


# --- a page with no About-card JSON but real OG tags (never crashes) --------

def test_page_with_og_tags_but_no_about_card_data_never_crashes():
    html = (
        '<html><head>'
        '<meta property="og:title" content="Some Business | City ST" />'
        '<meta property="og:description" content="Some Business, City. 5 followers." />'
        '</head><body></body></html>'
    )
    p = parse_profile(html, url="https://www.facebook.com/SomeBusiness/")

    assert p.status == "ok"
    assert p.name == "Some Business"
    assert p.followers_count == 5
    assert p.phone is None
    assert p.categories == []


# --- rating regex edge cases --------------------------------------------------

def test_rating_text_with_zero_percent_is_still_a_real_percentage_not_none():
    from bbb_scraper.facebook.parser import _RATING_RE
    m = _RATING_RE.search("0% recommend (3 reviews)")
    assert m.group(1) == "0"
    assert m.group(2) == "3"


def test_website_first_card_wins_over_a_second_one():
    """Confirmed live: SignatureNrgy LLC's page carried TWO INTRO_CARD_WEBSITE
    cards ("signaturenrgy.com" then a stray "gmail.com", almost certainly
    Facebook rendering their email's own domain as a second website-shaped
    card) -- the real business domain came first, so first-found wins."""

    # Minimal reproduction of the real shape (two context items, same
    # timeline_context_list_item_type) rather than shipping another full
    # ~1MB captured page as a fixture just for this one case.
    html = """
    <html><head>
    <meta property="og:title" content="Test Co | City ST" />
    </head><body>
    <script type="application/json">
    {"items": [
      {"renderer": {"context_item": {"plaintext_title": {"text": "real-domain.com"}}},
       "timeline_context_list_item_type": "INTRO_CARD_WEBSITE"},
      {"renderer": {"context_item": {"plaintext_title": {"text": "gmail.com"}}},
       "timeline_context_list_item_type": "INTRO_CARD_WEBSITE"}
    ]}
    </script>
    </body></html>
    """
    p = parse_profile(html, url="https://www.facebook.com/TestCo/")
    assert p.website == "real-domain.com"
