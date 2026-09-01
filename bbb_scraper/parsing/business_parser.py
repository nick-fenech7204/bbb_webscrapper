"""
Parse an individual BBB business-profile page into a BusinessDetail.

Confirmed 2026-09-01 against a real captured page (see
tests/fixtures/business_page_sample.html) -- `PRELOADED_STATE_VAR` and the
field mapping below are no longer a guess. The page embeds:

    window.__PRELOADED_STATE__ = {
      "user": {...}, "page": {...},
      "businessProfile": {
        "bbbId": ..., "businessId": ..., "isMultiLocation": ...,
        "contactInformation": {...}, "location": {...},
        "accreditationInformation": {...}, "names": {...},
        "orgDetails": {...}, "rating": {...}, "dates": {...},
        "categories": {...}, "urls": {...}, "localBbbData": {...},
        ... (display/media/reviews/etc, not all promoted -- see raw_extra)
      }
    }

`businessProfile` is a much richer object than the search API returns --
this mapping promotes a curated subset to first-class BusinessDetail fields
(see its docstring) and keeps everything else, per source block, in
raw_extra so nothing is silently dropped.
"""
from __future__ import annotations

import re
from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.parsing.json_extract import extract_window_assignment
from bbb_scraper.parsing.models import BusinessDetail
from bbb_scraper.utils.stats import RunStats, PAGES_PARSED, PARSE_FAILURES, RECORDS_EXTRACTED

logger = get_logger(__name__)

PRELOADED_STATE_VAR = "__PRELOADED_STATE__"

_ADDRESS_ID_RE = re.compile(r"/addressId/(\d+)")
_EMAIL_OBFUSCATION_WRAPPER = "!~xK_bL!"


def parse_business_page(
    html: str,
    *,
    profile_url: str | None = None,
    stats: RunStats | None = None,
) -> BusinessDetail:
    stats = stats or RunStats()

    state = extract_window_assignment(html, PRELOADED_STATE_VAR)
    if state is None:
        stats.incr(PARSE_FAILURES)
        raise ValueError(
            f"Could not find `{PRELOADED_STATE_VAR}` in business page "
            f"(check PRELOADED_STATE_VAR in business_parser.py) url={profile_url}"
        )

    try:
        detail = _map_business_state(state, profile_url=profile_url)
    except Exception:
        stats.incr(PARSE_FAILURES)
        raise

    stats.incr(PAGES_PARSED)
    stats.incr(RECORDS_EXTRACTED)
    return detail


def _omit(d: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k not in keys}


def _extract_address_id(urls: dict[str, Any]) -> str | None:
    """The page's own urls.localProfile carries /addressId/N when this page
    is a non-canonical branch -- same field BBB's real URLs use (confirmed
    against the captured request, which fetched exactly such a URL).
    """
    local_profile = urls.get("localProfile") or ""
    match = _ADDRESS_ID_RE.search(local_profile)
    return match.group(1) if match else None


def _deobfuscate_email(raw: str | None) -> str | None:
    """BBB wraps emails as "!~xK_bL!user__at__domain__dot__tld!~xK_bL!" in
    the page's JSON, decoded client-side by their own frontend before
    display. This reverses the same substitution -- not bypassing any
    access control, the email is already public contact info shown to every
    visitor. If this stops producing a plausible address, BBB likely
    changed the scheme -- check a fresh capture and update the substitution.
    """
    if not raw:
        return None
    text = raw.replace(_EMAIL_OBFUSCATION_WRAPPER, "")
    text = text.replace("__at__", "@").replace("__dot__", ".")
    return text or None


def _principal_contact(contacts: list[dict[str, Any]] | None) -> str | None:
    for contact in contacts or []:
        if not contact.get("isPrincipal"):
            continue
        name = contact.get("name") or {}
        full_name = " ".join(p for p in (name.get("first"), name.get("last")) if p)
        title = contact.get("title")
        if full_name and title:
            return f"{full_name}, {title}"
        return full_name or title or None
    return None


def _map_business_state(state: dict[str, Any], *, profile_url: str | None) -> BusinessDetail:
    bp = state.get("businessProfile")
    if not isinstance(bp, dict):
        raise ValueError("`__PRELOADED_STATE__` did not contain a `businessProfile` object")

    contact_info = bp.get("contactInformation") or {}
    location = bp.get("location") or {}
    postal_address = location.get("postalAddress") or {}
    accreditation = bp.get("accreditationInformation") or {}
    names = bp.get("names") or {}
    org = bp.get("orgDetails") or {}
    rating = bp.get("rating") or {}
    dates = bp.get("dates") or {}
    categories_block = bp.get("categories") or {}
    urls = bp.get("urls") or {}
    local_bbb = bp.get("localBbbData") or {}

    bbb_office_id = bp.get("bbbId")
    business_id = bp.get("businessId")
    address_id = _extract_address_id(urls)
    bbb_id = None
    if bbb_office_id and business_id:
        bbb_id = (
            f"{bbb_office_id}_{business_id}_{address_id}"
            if address_id else f"{bbb_office_id}_{business_id}"
        )

    address = ", ".join(
        p for p in (postal_address.get("addressLine1"), postal_address.get("addressLine2")) if p
    ) or None

    categories = [c["title"] for c in (categories_block.get("links") or []) if c.get("title")]

    raw_extra = {
        "id": bp.get("id"),
        "isLocalReport": bp.get("isLocalReport"),
        "isSystemWide": bp.get("isSystemWide"),
        "breadcrumbs": bp.get("breadcrumbs"),
        "dialogLocations": bp.get("dialogLocations"),
        "media": bp.get("media"),
        "display": bp.get("display"),
        "reviewsComplaintsSummary": bp.get("reviewsComplaintsSummary"),
        "complaintQualificationQuestions": bp.get("complaintQualificationQuestions"),
        "requestAQuoteUrlId": bp.get("requestAQuoteUrlId"),
        "latestReviews": bp.get("latestReviews"),
        "names": _omit(names, "primary"),
        "location": _omit(location, "postalAddress", "latitude", "longitude"),
        "accreditationInformation": _omit(accreditation, "isAccredited"),
        # orgDetails.license holds structured regulatory/license data
        # (numbers + issuing agency) not promoted to a first-class field --
        # worth knowing it's here if you need it later.
        "orgDetails": _omit(org, "yearsInBusiness", "organizationDescription", "typeOfEntity"),
        "dates": _omit(dates, "bbbFileOpened", "businessStart"),
        "categories_meta": _omit(categories_block, "links", "primaryCategoryName", "primaryTobId"),
        "urls": _omit(urls, "primary"),
        "contactInformation": {
            **_omit(contact_info, "phoneNumber", "emailAddress"),
            "email_raw": contact_info.get("emailAddress"),
        },
    }

    return BusinessDetail(
        bbb_id=bbb_id,
        business_id=business_id,
        bbb_office_id=bbb_office_id,
        bbb_office_name=local_bbb.get("name"),
        is_multi_location=bp.get("isMultiLocation"),
        name=names.get("primary") or "UNKNOWN",
        profile_url=profile_url,
        phone=contact_info.get("phoneNumber"),
        email=_deobfuscate_email(contact_info.get("emailAddress")),
        website=urls.get("primary"),
        address=address,
        city=postal_address.get("city"),
        state=postal_address.get("stateCode"),
        postal_code=postal_address.get("zipCode"),
        lat=location.get("latitude"),
        lon=location.get("longitude"),
        rating=rating.get("bbbRating"),
        accredited=accreditation.get("isAccredited"),
        accreditation_status=" ".join(accreditation.get("text") or []) or None,
        years_in_business=org.get("yearsInBusiness"),
        bbb_file_opened=dates.get("bbbFileOpened"),
        business_started=dates.get("businessStart"),
        principal_contact=_principal_contact(contact_info.get("contacts")),
        categories=categories,
        primary_category_name=categories_block.get("primaryCategoryName"),
        primary_category_id=categories_block.get("primaryTobId"),
        organization_description=org.get("organizationDescription"),
        entity_type=(org.get("typeOfEntity") or {}).get("name"),
        raw_extra=raw_extra,
    )
