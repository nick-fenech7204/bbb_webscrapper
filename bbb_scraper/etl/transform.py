"""
Transform: pure, unit-testable functions that turn a parsed BusinessSummary /
BusinessDetail into a flat dict ready for loading into a sink.

Kept deliberately free of I/O (no network, no disk, no logging side effects
beyond what's passed in) so each function can be tested in isolation without
fixtures or mocks -- just construct a model instance and assert on the dict.
"""
from __future__ import annotations

import re
from typing import Any

from bbb_scraper.etl.identifiers import generate_business_id
from bbb_scraper.parsing.models import BusinessDetail, BusinessSummary


def normalize_whitespace(text: str | None) -> str | None:
    if text is None:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed or None


def normalize_phone(phone: str | None) -> str | None:
    """Strip to digits and format as (XXX) XXX-XXXX for 10-digit US numbers,
    otherwise just return the cleaned digit string (or None).
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    return digits or None


def transform_summary(summary: BusinessSummary) -> dict[str, Any]:
    record = {
        "id": generate_business_id(
            bbb_id=summary.bbb_id,
            name=summary.name,
            address=summary.address,
            phone=summary.phone,
        ),
        "bbb_id": summary.bbb_id,
        "name": normalize_whitespace(summary.name),
        "profile_url": summary.profile_url,
        "phone": normalize_phone(summary.phone),
        "address": normalize_whitespace(summary.address),
        "city": normalize_whitespace(summary.city),
        "state": summary.state,
        "postal_code": summary.postal_code,
        "rating": summary.rating,
        "accredited": summary.accredited,
        "categories": summary.categories,
        "search_query": summary.search_query,
        "search_location": summary.search_location,
        "source_page": summary.source_page,
        "scraped_at": summary.scraped_at.isoformat(),
        "record_type": "summary",
    }
    return record


def transform_detail(detail: BusinessDetail) -> dict[str, Any]:
    record = {
        "id": generate_business_id(
            bbb_id=detail.bbb_id,
            name=detail.name,
            address=detail.address,
            phone=detail.phone,
        ),
        "bbb_id": detail.bbb_id,
        "name": normalize_whitespace(detail.name),
        "profile_url": detail.profile_url,
        "phone": normalize_phone(detail.phone),
        "website": detail.website,
        "address": normalize_whitespace(detail.address),
        "city": normalize_whitespace(detail.city),
        "state": detail.state,
        "postal_code": detail.postal_code,
        "rating": detail.rating,
        "accredited": detail.accredited,
        "accreditation_status": detail.accreditation_status,
        "years_in_business": detail.years_in_business,
        "bbb_file_opened": detail.bbb_file_opened,
        "business_started": detail.business_started,
        "principal_contact": detail.principal_contact,
        "categories": detail.categories,
        "scraped_at": detail.scraped_at.isoformat(),
        "record_type": "detail",
    }
    return record
