"""Pure parsing: a reassembled flight-data blob (see flight_data.py) in,
ListingPage / BusinessDetail out. No network calls here -- see client.py /
scraper.py for the I/O side. Tested against real captured pages, not
hand-written HTML (see tests/angi/).

**Why balanced-JSON-object extraction, not field-by-field regex.** Angi's
component props are real JSON objects sitting in a predictable wrapper --
every one seen so far has the shape `["$","$L<id>",null,{...props...}]`.
So rather than regex each field out individually (fragile: breaks on field
reordering, and multiple components can reuse the same key name for
different things -- `subHeaderDescriptions` holds address+website on one
component and the About-Us bio on a completely different one), this finds
a component by a distinctive anchor string inside it, then parses its
*whole* props object as real JSON via a brace-depth scan (respecting
string literals, so a `{`/`}` inside a quoted value doesn't miscount). One
real `dict`, not a pile of regexes that each have to be individually right.
"""
from __future__ import annotations

import html
import json
import re

from bbb_scraper.angi.models import BusinessDetail, ListingPage, RatingBreakdown, Review

# --- shared low-level JSON-object extraction --------------------------------


def _extract_object_at(text: str, open_brace_idx: int) -> dict | None:
    """`text[open_brace_idx]` must be '{'. Scans forward tracking string
    literals (braces inside a JSON string don't affect depth) to find the
    matching close brace, then json.loads the span. None if it never
    balances (truncated response) or doesn't parse as JSON."""
    if open_brace_idx < 0 or open_brace_idx >= len(text) or text[open_brace_idx] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(open_brace_idx, len(text)):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[open_brace_idx : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _component_props(blob: str, anchor: str, *, occurrence: int = 0) -> dict | None:
    """Find the props object of the `["$","$L<id>",null,{...}]` component
    chunk containing `anchor` (a substring distinctive to that component).
    `occurrence` picks which match when `anchor` isn't unique (0 = first)."""
    idx = -1
    for _ in range(occurrence + 1):
        idx = blob.find(anchor, idx + 1)
        if idx == -1:
            return None
    marker = ",null,{"
    brace_pos = blob.rfind(marker, 0, idx)
    if brace_pos == -1:
        return None
    return _extract_object_at(blob, brace_pos + len(marker) - 1)


# --- listing pages -----------------------------------------------------------

# "resultCount":19,"limit":10,"page":1 -- seen with 1x and what reads like 2x
# backslash-escaping depending on the page before reassembly; matching on the
# reassembled (already-unescaped) blob, plain quotes, no backslashes to worry
# about here.
_RESULT_COUNT_RE = re.compile(r'"resultCount":(\d+),"limit":(\d+),"page":(\d+)')
_PROFILE_URL_RE = re.compile(r'"profileUrl":"(/companylist/[^"]+)"')


def parse_listing_page(blob: str) -> ListingPage:
    """One companylist search-results page. `profile_urls` is deduped and
    order-preserving -- confirmed each real page's ~10 businesses show up
    twice in the payload (once per rendering path), never assume 1:1."""
    seen: set[str] = set()
    profile_urls: list[str] = []
    for url in _PROFILE_URL_RE.findall(blob):
        if url not in seen:
            seen.add(url)
            profile_urls.append(url)

    m = _RESULT_COUNT_RE.search(blob)
    if m:
        result_count, page_size, page = (int(g) for g in m.groups())
    else:
        result_count = page_size = page = None

    return ListingPage(
        profile_urls=profile_urls, result_count=result_count, page_size=page_size, page=page
    )


# --- business detail pages ----------------------------------------------------

_ADDRESS_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)
_CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _clean_str(v) -> str | None:
    """React server components serialize a prop that legitimately has no
    value as the literal string "$undefined" (a real value in the payload,
    not Python/JS undefined) -- confirmed by testing, not assumed: one
    business's website came back as the 11-character string "$undefined"
    rather than being absent. Must never be passed through as if it were
    real data. A bare "-" is the same idea with a different spelling --
    seen used as a street address placeholder by a business that only
    listed a service city, not a real address."""
    if not isinstance(v, str):
        return v
    v = v.strip()
    if not v or v in ("$undefined", "-"):
        return None
    return v


def _clean_review_text(v) -> str | None:
    """Same as _clean_str, plus HTML-entity decoding -- confirmed by
    testing, not assumed: real review text comes through with literal
    entities (`&#39;` for an apostrophe seen in a real captured review),
    presumably whatever encoding step rendered the page's HTML doesn't get
    reversed for free the way the rest of this parsing (real JSON values,
    no markup) doesn't need it. Only applied to free-text review bodies --
    every other field here is a controlled value (a name, a category, a
    rating) with no realistic entity content."""
    v = _clean_str(v)
    return html.unescape(v) if v is not None else None


def _split_address(raw: str) -> tuple[str | None, str | None, str | None, str | None]:
    """"22099 N Bertha Ln, Barrington, IL 60010" -> its 4 parts. Falls back
    to a street-less "City, ST 00000" form, then gives up (all None) rather
    than guess at a shape that hasn't actually been seen -- a business that
    only lists a service area, not a real address, still shouldn't get its
    raw string mis-sliced into the wrong fields."""
    m = _ADDRESS_RE.match(raw)
    if m:
        return _clean_str(m["street"]), m["city"], m["state"], m["zip"]
    m = _CITY_STATE_ZIP_RE.match(raw)
    if m:
        return None, m["city"], m["state"], m["zip"]
    return None, None, None, None


def parse_business_detail(blob: str, profile_url: str) -> BusinessDetail:
    """`profile_url` should be the absolute URL actually fetched -- it's the
    record's own stable identifier, kept even if every other field below
    comes back empty (a changed page structure loses data, never the
    ability to at least say "this URL was visited")."""
    detail = BusinessDetail(profile_url=profile_url)

    hero = _component_props(blob, '"data-testid":"businessProfileHero"')
    if hero:
        # _clean_str on *every* field pulled off hero, not just the ones
        # that are "obviously" strings -- confirmed by testing, not
        # assumed: a business with zero reviews yet has overallRating and
        # reviewCount come back as the literal string "$undefined" too
        # (there's no average to report), the same sentinel seen on string
        # fields elsewhere. Left unguarded, that string would have flowed
        # straight into a numeric CSV column as if it were a real rating.
        detail.name = _clean_str(hero.get("name"))
        detail.phone = _clean_str(hero.get("phoneNumber"))
        detail.overall_rating = _clean_str(hero.get("overallRating"))
        detail.review_count = _clean_str(hero.get("reviewCount"))
        detail.is_paid_pro = _clean_str(hero.get("isPaidPro"))
        detail.is_corporate_account = _clean_str(hero.get("isCorporateAccount"))
        detail.is_super_service_award_winner = _clean_str(hero.get("isSuperServiceAwardWinner"))
        detail.categories = [
            c["name"] for c in (hero.get("categories") or []) if isinstance(c, dict) and c.get("name")
        ]

    # Two different components both use the exact same
    # `"subHeaderDescriptions":[{"subHeader"...` shape -- confirmed by
    # testing, not assumed: the Amenities table ("Emergency Services" /
    # "Yes", "Warranties" / "Yes") renders through the identical generic
    # component as the real address+website block. Only the real one's
    # entries carry a "link" key (the website is a clickable link; a plain
    # amenity value isn't) -- checked on the parsed dict itself, not by
    # guessing a more specific text anchor that might just as easily start
    # matching some *other* reused component on a future page.
    for occurrence in range(6):
        candidate = _component_props(blob, '"subHeaderDescriptions":[{"subHeader"', occurrence=occurrence)
        if candidate is None:
            break
        entries = candidate.get("subHeaderDescriptions") or []
        if entries and "link" in entries[0]:
            raw_address = _clean_str(entries[0].get("subHeader"))
            detail.website = _clean_str(entries[0].get("description"))
            if raw_address:
                detail.address = raw_address
                detail.street, detail.city, detail.state, detail.zip_code = _split_address(raw_address)
            break

    about_block = _component_props(blob, '"highlights":[{"typeId"')
    if about_block:
        entries = about_block.get("subHeaderDescriptions") or []
        if entries:
            detail.about_us = _clean_str(entries[0].get("description"))
        # Kept raw (typeId codes), not translated to guessed labels --
        # see the module docstring for why: no confirmed typeId->label
        # mapping, and a wrong guess is worse than an honest raw code.
        detail.highlights = [
            f"typeId:{h['typeId']}" + (f"={h['value']}" if h.get("value") is not None else "")
            for h in (about_block.get("highlights") or [])
            if isinstance(h, dict) and "typeId" in h
        ]

    licensing_block = _component_props(blob, '"licenses":[')
    if licensing_block:
        detail.bonded = _clean_str(licensing_block.get("bonded"))
        detail.insured = _clean_str(licensing_block.get("insured"))
        detail.licenses = [
            lic if isinstance(lic, str) else json.dumps(lic)
            for lic in (licensing_block.get("licenses") or [])
        ]

    rating_block = _component_props(blob, '"distribution":[{"star"')
    if rating_block:
        detail.rating_breakdown = [
            RatingBreakdown(star=d["star"], count=d["count"], percentage=d["percentage"])
            for d in (rating_block.get("distribution") or [])
            if isinstance(d, dict) and {"star", "count", "percentage"} <= d.keys()
        ]
        # A page's rating_block is a reliable, better-precision source for
        # these two than the hero object on the rare page where hero's own
        # values are missing -- fill in, don't overwrite what hero already
        # gave (they describe the same business, so either is correct).
        if detail.overall_rating is None:
            detail.overall_rating = _clean_str(rating_block.get("averageRating"))
        if detail.review_count is None:
            detail.review_count = _clean_str(rating_block.get("reviewCount"))

    # Real written reviews (2026-09-15) -- confirmed present in the exact
    # same page already fetched for everything above, not a separate
    # request: real captured fixtures (tests/fixtures/angi_business_detail_
    # sample.txt) show up to `pageSize` (~25) reviews embedded directly,
    # newest first (real dateLabel values descend month over month in the
    # sample). Pagination beyond that first page isn't chased here -- the
    # component's own `pageLinkQuery` came back "$undefined" on every real
    # page seen so far, so there's no confirmed link pattern to a page 2,
    # and newest-first already serves a recency-weighted read directly.
    reviews_block = _component_props(blob, '"isShowVerifiedReview":true,"isShowRatings":true,"reviews":[')
    if reviews_block:
        detail.reviews = [
            Review(
                text=_clean_review_text(r.get("text")),
                rating=_clean_str(r.get("rating")),
                reviewer_name=_clean_str(r.get("reviewerName")),
                date_label=_clean_str(r.get("dateLabel")),
                is_verified=_clean_str(r.get("isVerified")),
                job_label=_clean_str(r.get("jobLabel")),
                business_response_text=_clean_review_text(r.get("responseText")),
                recommends=_clean_str(r.get("recommends")),
                cost_label=_clean_str(r.get("costLabel")),
                business_response_name=_clean_str(r.get("proResponseName")),
            )
            for r in (reviews_block.get("reviews") or [])
            if isinstance(r, dict)
        ]

    return detail
