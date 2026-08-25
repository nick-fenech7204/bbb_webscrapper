import pytest

from bbb_scraper.parsing.business_parser import parse_business_page


def test_parse_business_page_against_fixture(load_fixture):
    html = load_fixture("business_page_sample.html")

    detail = parse_business_page(html, profile_url="https://www.bbb.org/example")

    assert detail.bbb_id == "0865-90012345"
    assert detail.name == "Acme Plumbing Co"
    assert detail.profile_url == "https://www.bbb.org/example"
    assert detail.phone == "(512) 555-0134"
    assert detail.website == "https://acmeplumbing.example.com"
    assert detail.address == "123 Main St"
    assert detail.city == "Austin"
    assert detail.state == "TX"
    assert detail.postal_code == "78701"
    assert detail.rating == "A+"
    assert detail.accredited is True
    assert detail.years_in_business == "11"
    assert detail.principal_contact == "Jane Doe, Owner"
    assert "Plumbers" in detail.categories

    # Fields not explicitly mapped yet (e.g. "hours", "description") should
    # still be present in raw_extra rather than silently discarded.
    assert "hours" in detail.raw_extra
    assert "leaky" in detail.raw_extra["description"]


def test_parse_business_page_raises_when_state_missing():
    with pytest.raises(ValueError):
        parse_business_page("<html><body>no preloaded state</body></html>")
