import pytest

from bbb_scraper.parsing.business_parser import parse_business_page

PROFILE_URL = (
    "https://www.bbb.org/us/fl/the-villages/profile/financial-services/"
    "baum-financial-services-inc-0733-90122435/addressId/38205"
)

ACCREDITED_PROFILE_URL = (
    "https://www.bbb.org/us/wa/renton/profile/heating-and-air-conditioning/"
    "rescue-rooter-1296-500666/addressId/892874"
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

    # Full contact list (not just the collapsed principal_contact string).
    assert detail.contacts == [
        {"name": "Gerald Baum", "title": "President",
         "is_principal": True, "is_management": True, "is_primary": True},
    ]

    # Social media links.
    assert detail.socials == [
        {"platform": "facebook", "url": "https://www.facebook.com/baumfinancialservices/?fref=ts"},
        {"platform": "pinterest", "url": "https://www.pinterest.com/BaumFinancial/_created/"},
    ]

    # Review/complaint counts (this business has none of either).
    assert detail.reviews_complaints == {
        "reviews_total": 0,
        "average_rating": 0,
        "complaints_total": 0,
        "complaints_closed_past_3yr": 0,
        "complaints_closed_past_12mo": 0,
    }


def test_parse_business_page_preserves_unmapped_fields_in_raw_extra(load_fixture):
    html = load_fixture("business_page_sample.html")
    detail = parse_business_page(html, profile_url=PROFILE_URL)

    # Raw (still-obfuscated) email kept alongside the decoded one, in case
    # the obfuscation scheme changes and decoding needs revisiting.
    assert detail.raw_extra["contactInformation"]["email_raw"].startswith("!~xK_bL!")
    # Promoted to `contacts` -- shouldn't also linger, duplicated, in raw_extra.
    assert "contacts" not in detail.raw_extra["contactInformation"]

    # Regulatory/license detail isn't promoted to a first-class field, but
    # must not be silently dropped.
    license_info = detail.raw_extra["orgDetails"]["license"]
    assert license_info["details"][0]["licenseNumber"] == "L059365"

    # Promoted to `socials` -- shouldn't also linger, duplicated, in raw_extra.
    assert "socialMediaList" not in detail.raw_extra["display"]

    # reviewsComplaintsSummary is fully promoted to `reviews_complaints` now,
    # not left in raw_extra at all.
    assert "reviewsComplaintsSummary" not in detail.raw_extra

    assert detail.raw_extra["id"] == "0_38205"  # BBB's own composite id for this listing


def test_map_contacts_skips_entries_with_neither_name_nor_title():
    from bbb_scraper.parsing.business_parser import _map_contacts

    contacts = [
        {"name": {"first": "Jane", "last": "Doe"}, "title": "Owner", "isPrincipal": True},
        {"name": {}, "title": None},  # neither -- should be dropped
    ]
    mapped = _map_contacts(contacts)
    assert len(mapped) == 1
    assert mapped[0]["name"] == "Jane Doe"
    assert mapped[0]["is_principal"] is True
    assert mapped[0]["is_management"] is False  # not flagged -> False, not None


def test_map_contacts_handles_empty_and_none():
    from bbb_scraper.parsing.business_parser import _map_contacts

    assert _map_contacts(None) == []
    assert _map_contacts([]) == []


def test_map_socials_skips_entries_without_a_url():
    from bbb_scraper.parsing.business_parser import _map_socials

    socials = [
        {"type": "facebook", "url": "https://facebook.com/x"},
        {"type": "twitter", "url": None},
    ]
    assert _map_socials(socials) == [{"platform": "facebook", "url": "https://facebook.com/x"}]


def test_accreditation_status_extracts_customtext_from_dicts():
    from bbb_scraper.parsing.business_parser import _accreditation_status

    accreditation = {"text": [{"customText": "First block."}, {"customText": "Second block."}]}
    assert _accreditation_status(accreditation) == "First block. Second block."


def test_accreditation_status_handles_empty_and_missing_text():
    from bbb_scraper.parsing.business_parser import _accreditation_status

    assert _accreditation_status({"text": []}) is None
    assert _accreditation_status({}) is None


def test_accreditation_status_skips_items_missing_customtext():
    from bbb_scraper.parsing.business_parser import _accreditation_status

    accreditation = {"text": [{"customText": "Real one."}, {"position": None}]}
    assert _accreditation_status(accreditation) == "Real one."


def test_parse_business_page_handles_accreditation_text_as_dicts_not_strings(load_fixture):
    """Regression test: accreditationInformation.text is a list of dicts
    with a customText field, not a list of plain strings. The fixture used
    everywhere else in this file happens to have it as an empty list, which
    silently hid the real shape -- this fixture (confirmed 2026-09-02,
    caught mid a 233-business real batch: 3 failures, all this exact
    TypeError) has real non-empty text and must not raise.
    """
    html = load_fixture("business_page_sample_accredited.html")
    detail = parse_business_page(html, profile_url=ACCREDITED_PROFILE_URL)

    assert detail.name == "Rescue Rooter"
    assert detail.accredited is True
    assert detail.accreditation_status is not None
    assert "BBB Accredited Business" in detail.accreditation_status


NO_ADDRESS_SUFFIX_PROFILE_URL = (
    "https://www.bbb.org/us/fl/miami/profile/used-car-dealers/jcb-auto-sales-0633-92026452"
)


def test_parse_business_page_derives_address_id_without_url_suffix(load_fixture):
    """Real captured page (2026-09-02, Miami car-dealer run) reached via its
    canonical URL -- no /addressId/N suffix anywhere, the common case for a
    single-location business, confirmed to be ~90% of a real 200-business
    batch. Regression test for a real bug: bbb_id used to silently drop the
    address segment here, producing "0633_92026452" instead of matching the
    search API's own raw id for this exact business
    ("0633_92026452_164490") -- which broke dedupe between a business's
    summary and detail records, since they no longer shared an id. Confirmed
    against businessProfile.id ("0_164490") in the raw capture that this is
    the same address id search finds a different way.
    """
    html = load_fixture("business_page_sample_no_address_suffix.html")

    detail = parse_business_page(html, profile_url=NO_ADDRESS_SUFFIX_PROFILE_URL)

    assert detail.name == "JC&B Auto Sales"
    assert detail.bbb_id == "0633_92026452_164490"
    assert detail.business_id == "92026452"
    assert detail.bbb_office_id == "0633"


def test_parse_business_page_raises_when_state_missing():
    with pytest.raises(ValueError):
        parse_business_page("<html><body>no preloaded state</body></html>")


def test_parse_business_page_raises_when_business_profile_missing():
    html = '<script>window.__PRELOADED_STATE__ = {"user": {}, "page": {}};</script>'
    with pytest.raises(ValueError):
        parse_business_page(html)
