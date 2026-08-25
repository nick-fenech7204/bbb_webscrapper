from bbb_scraper.parsing.models import BusinessSummary
from bbb_scraper.parsing.search_parser import parse_search_results


def test_parse_search_results_against_fixture(load_fixture):
    html = load_fixture("search_listing_sample.html")

    records = parse_search_results(html, query="plumbers", location="Austin, TX", page=1)

    assert len(records) == 2
    assert all(isinstance(r, BusinessSummary) for r in records)

    first = records[0]
    assert first.bbb_id == "0865-90012345"
    assert first.name == "Acme Plumbing Co"
    assert first.phone == "(512) 555-0134"
    assert first.city == "Austin"
    assert first.state == "TX"
    assert first.rating == "A+"
    assert first.accredited is True
    assert "Plumbers" in first.categories
    assert first.search_query == "plumbers"
    assert first.search_location == "Austin, TX"
    assert first.source_page == 1
    # Unmapped fields should survive in raw_extra rather than being dropped.
    assert first.raw_extra.get("distanceMiles") == 1.2

    second = records[1]
    assert second.name == "Bluebonnet Plumbing LLC"
    assert second.accredited is False


def test_parse_search_results_returns_empty_list_on_no_match():
    html = "<html><body>no json here</body></html>"
    assert parse_search_results(html) == []
