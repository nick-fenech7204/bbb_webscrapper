import pytest

from bbb_scraper.parsing.business_parser import parse_business_page

PROFILE_URL = (
    "https://www.bbb.org/us/fl/the-villages/profile/financial-services/"
    "baum-financial-services-inc-0733-90122435/addressId/38205"
)


def test_parse_business_page_against_real_fixture(load_fixture):
    """Fixture is a real captured BBB business-profile page -- this is
    checking the actual confirmed mapping against real data, not a
    placeholder guess.
    """
    html = load_fixture("business_page_sample.html")

    detail = parse_business_page(html, profile_url=PROFILE_URL)

    assert detail.bbb_id == "0733_90122435_38205"  # {bbbId}_{businessId}_{addressId}
    assert detail.business_id == "90122435"
    assert detail.bbb_office_id == "0733"
    assert detail.bbb_office_name == "BBB Serving Central Florida"
    assert detail.is_multi_location is True

    assert detail.name == "Baum Financial Services, Inc"
    assert detail.profile_url == PROFILE_URL
    assert detail.phone == "(352) 205-7966"
    # BBB obfuscates emails in the page JSON; this must come back decoded.
    assert detail.email == "info@baumfinancialservices.com"
    assert detail.website == "https://baumfinancialservices.com/"

    assert detail.address == "940 Lakeshore Dr STE 100"
    assert detail.city == "The Villages"
    assert detail.state == "FL"
    assert detail.postal_code == "32162-1690"
    assert detail.lat == 28.90195
    assert detail.lon == -81.97404

    # "NR" (Not Rated) is a real, valid value -- not missing data.
    assert detail.rating == "NR"
    assert detail.accredited is True
    assert detail.accreditation_status is None  # empty text list on this page

    assert detail.years_in_business == 24
    assert detail.bbb_file_opened == "2011-05-12T00:00:00"
    assert detail.business_started == "2002-07-23T00:00:00"
    assert detail.principal_contact == "Gerald Baum, President"

    assert "CPA" in detail.categories
    assert "Retirement Planning Services" in detail.categories
    assert detail.primary_category_name == "Retirement Planning Services"
    assert detail.primary_category_id == "20063-000"
    assert detail.entity_type == "Corporation"
    assert "retirement planning" in detail.organization_description.lower()


def test_parse_business_page_preserves_unmapped_fields_in_raw_extra(load_fixture):
    html = load_fixture("business_page_sample.html")
    detail = parse_business_page(html, profile_url=PROFILE_URL)

    # Raw (still-obfuscated) email kept alongside the decoded one, in case
    # the obfuscation scheme changes and decoding needs revisiting.
    assert detail.raw_extra["contactInformation"]["email_raw"].startswith("!~xK_bL!")

    # Regulatory/license detail isn't promoted to a first-class field, but
    # must not be silently dropped.
    license_info = detail.raw_extra["orgDetails"]["license"]
    assert license_info["details"][0]["licenseNumber"] == "L059365"

    assert detail.raw_extra["reviewsComplaintsSummary"]["reviewsTotal"] == 0
    assert detail.raw_extra["id"] == "0_38205"  # BBB's own composite id for this listing


def test_parse_business_page_raises_when_state_missing():
    with pytest.raises(ValueError):
        parse_business_page("<html><body>no preloaded state</body></html>")


def test_parse_business_page_raises_when_business_profile_missing():
    html = '<script>window.__PRELOADED_STATE__ = {"user": {}, "page": {}};</script>'
    with pytest.raises(ValueError):
        parse_business_page(html)
