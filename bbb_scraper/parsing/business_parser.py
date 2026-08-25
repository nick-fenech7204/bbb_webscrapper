"""
Parse an individual BBB business-profile page into a BusinessDetail.

TODO(you): same deal as search_parser.py -- `PRELOADED_STATE_VAR` and
`_map_business_state` encode a placeholder schema
(see tests/fixtures/business_page_sample.html). Once you've captured a real
profile page:
  1. Confirm the actual variable name (window.__PRELOADED_STATE__? something
     else?) and update PRELOADED_STATE_VAR.
  2. Adjust `_map_business_state`'s field paths to match the real nested
     structure.
  3. tests/parsing/test_business_parser.py will tell you when it's right.
"""
from __future__ import annotations

from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.parsing.json_extract import extract_window_assignment
from bbb_scraper.parsing.models import BusinessDetail
from bbb_scraper.utils.stats import RunStats, PAGES_PARSED, PARSE_FAILURES, RECORDS_EXTRACTED

logger = get_logger(__name__)

# Placeholder: adjust once real markup is inspected.
PRELOADED_STATE_VAR = "__PRELOADED_STATE__"


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


def _map_business_state(state: dict[str, Any], *, profile_url: str | None) -> BusinessDetail:
    """Placeholder mapping, assuming a shape like:

        {"business": {"id": ..., "name": ..., "contact": {...}, ...}}

    Adjust to match the real preloaded-state structure once inspected.
    """
    business = state.get("business", state)

    contact = business.get("contact", {}) if isinstance(business.get("contact"), dict) else {}
    address = contact.get("address", {}) if isinstance(contact.get("address"), dict) else {}

    known_top_keys = {"id", "name", "website", "rating", "accredited", "categories", "contact"}

    detail = BusinessDetail(
        bbb_id=business.get("id"),
        name=business.get("name") or "UNKNOWN",
        profile_url=profile_url,
        phone=contact.get("phone"),
        website=business.get("website"),
        address=address.get("line1"),
        city=address.get("city"),
        state=address.get("state"),
        postal_code=address.get("postalCode"),
        rating=business.get("rating"),
        accredited=business.get("accredited"),
        accreditation_status=business.get("accreditationStatus"),
        years_in_business=business.get("yearsInBusiness"),
        bbb_file_opened=business.get("fileOpenedDate"),
        business_started=business.get("businessStartedDate"),
        principal_contact=business.get("principalContact"),
        categories=business.get("categories") or [],
        raw_extra={k: v for k, v in business.items() if k not in known_top_keys},
    )
    return detail
