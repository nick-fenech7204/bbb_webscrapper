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
        "reviewsComplaintsSummary": {...},
        "display": {"socialMediaList": [...], ...},
        ... (media/license/related-articles/etc, not all promoted -- see raw_extra)
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

    Returns None for the common case of a canonical/no-suffix URL (most
    single-location businesses, and even some multi-location ones) --
    callers should fall back to `_extract_address_id_from_profile_id`
    rather than treat None here as "this business has no address id".

    KNOWN REMAINING GAP (2026-09-02, not fixed by that fallback either):
    large national/chain businesses (e.g. "Wells Fargo") can carry a
    different {bbbId}_{businessId} pair on their SEARCH result than on
    their own detail PAGE -- search apparently points at some national/
    aggregate BBB record while the detail page renders under a regional
    BBB office's own numbering for that specific branch. Confirmed in a
    real 200-business Miami run: 3 Wells Fargo branches' summary bbb_id
    ("1116_11547_<addressId>_<extra>", 4 segments -- not even this
    module's usual 3) never matched their detail bbb_id
    ("0633_<regional businessId>_<addressId>"), so dedupe correctly kept
    both as "different" records for the same real branch. Rare in
    practice (0 non-chain businesses hit this in that run) but a real
    gap -- if it matters for your data, matching on (address_id, address)
    instead of the full bbb_id would likely reconcile these.
    """
    local_profile = urls.get("localProfile") or ""
    match = _ADDRESS_ID_RE.search(local_profile)
    return match.group(1) if match else None


def _extract_address_id_from_profile_id(bp: dict[str, Any]) -> str | None:
    """Fallback source for the address id when `_extract_address_id` finds
    nothing (confirmed 2026-09-02: the common case, not an edge case -- 179
    of 200 real businesses in a Miami car-dealer run had no /addressId/N in
    their page's own urls.localProfile at all). `businessProfile.id` itself
    is "{something}_{addressId}" (e.g. "0_164490") -- confirmed against two
    real captures where this segment exactly matched the addressId the
    search API's own raw `id` carried for the same business (search:
    "0633_92026452_164490" vs this page's businessProfile.id "0_164490").

    Without this fallback, BusinessDetail.bbb_id silently dropped the
    address segment for the majority of businesses, producing a *different*
    id than the matching BusinessSummary got and breaking dedupe between
    them -- both survived as if they were different businesses.
    """
    profile_id = bp.get("id") or ""
    if "_" in profile_id:
        candidate = profile_id.rsplit("_", 1)[-1]
        if candidate.isdigit():
            return candidate
    return None


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


def _full_name(name: dict[str, Any] | None) -> str:
    name = name or {}
    return " ".join(p for p in (name.get("first"), name.get("last")) if p)


def _principal_contact(contacts: list[dict[str, Any]] | None) -> str | None:
    for contact in contacts or []:
        if not contact.get("isPrincipal"):
            continue
        full_name = _full_name(contact.get("name"))
        title = contact.get("title")
        if full_name and title:
            return f"{full_name}, {title}"
        return full_name or title or None
    return None


def _accreditation_status(accreditation: dict[str, Any]) -> str | None:
    """accreditationInformation.text is a list of dicts with a `customText`
    field (confirmed 2026-09-02 against a real business with non-empty
    text -- "Rescue Rooter", see business_page_sample_accredited.html) --
    NOT a plain list of strings. The one fixture this mapping was first
    built against happened to have it as an empty list, which silently hid
    the real shape until a larger batch surfaced a TypeError on `" ".join()`
    against actual dicts.
    """
    texts = accreditation.get("text") or []
    parts = [t.get("customText") for t in texts if isinstance(t, dict) and t.get("customText")]
    return " ".join(parts) or None


def _map_contacts(contacts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Every listed contact, not just whichever one (if any) is flagged
    principal -- some businesses list several (owner + office manager,
    etc.), and "principal" isn't always set even when a name+title is.
    """
    mapped = []
    for contact in contacts or []:
        full_name = _full_name(contact.get("name"))
        title = contact.get("title")
        if not full_name and not title:
            continue
        mapped.append(
            {
                "name": full_name or None,
                "title": title,
                "is_principal": bool(contact.get("isPrincipal")),
                "is_management": bool(contact.get("isManagement")),
                "is_primary": bool(contact.get("isPrimary")),
            }
        )
    return mapped


def _map_socials(social_media_list: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {"platform": item.get("type"), "url": item.get("url")}
        for item in (social_media_list or [])
        if item.get("url")
    ]


def _map_reviews_complaints(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Counts only -- BBB doesn't expose individual review/complaint text
    anywhere we've found yet, just these aggregate numbers. Drops the
    UI-display-control flags (suppressReviews, displayReviewStarRating, ...)
    that live alongside the actual counts in the raw block.
    """
    summary = summary or {}
    return {
        "reviews_total": summary.get("reviewsTotal"),
        "average_rating": summary.get("averageOfReviewStarRatings"),
        "complaints_total": summary.get("complaintsTotal"),
        "complaints_closed_past_3yr": summary.get("totalClosedComplaintsPastThreeYears"),
        "complaints_closed_past_12mo": summary.get("totalClosedComplaintsPastTwelveMonths"),
    }


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
    address_id = _extract_address_id(urls) or _extract_address_id_from_profile_id(bp)
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
    display = bp.get("display") or {}

    raw_extra = {
        "id": bp.get("id"),
        "isLocalReport": bp.get("isLocalReport"),
        "isSystemWide": bp.get("isSystemWide"),
        "breadcrumbs": bp.get("breadcrumbs"),
        "dialogLocations": bp.get("dialogLocations"),
        "media": bp.get("media"),
        # socialMediaList promoted to `socials` -- omit it here so it's not
        # duplicated between raw_extra and the first-class field.
        "display": _omit(display, "socialMediaList"),
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
            **_omit(contact_info, "phoneNumber", "emailAddress", "contacts"),
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
        accreditation_status=_accreditation_status(accreditation),
        years_in_business=org.get("yearsInBusiness"),
        bbb_file_opened=dates.get("bbbFileOpened"),
        business_started=dates.get("businessStart"),
        principal_contact=_principal_contact(contact_info.get("contacts")),
        contacts=_map_contacts(contact_info.get("contacts")),
        socials=_map_socials(display.get("socialMediaList")),
        reviews_complaints=_map_reviews_complaints(bp.get("reviewsComplaintsSummary")),
        categories=categories,
        primary_category_name=categories_block.get("primaryCategoryName"),
        primary_category_id=categories_block.get("primaryTobId"),
        organization_description=org.get("organizationDescription"),
        entity_type=(org.get("typeOfEntity") or {}).get("name"),
        raw_extra=raw_extra,
    )
