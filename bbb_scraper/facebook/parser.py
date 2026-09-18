"""
Parses a fetched Facebook business page into a FacebookProfile.

**How this was found, 2026-09-18 -- confirmed against 3 real, different
business pages already in this project's own captured data (Dental Care on
Yellow Bluff, West Vinings Dental Aesthetics, Ponce Dental Group -- see
tests/fixtures/facebook_page_sample*.html, all real, trimmed down to just
the load-bearing <head>/<script> content, not synthesized), not guessed
from one lucky sample:**

  - The About-panel fields (category, address, phone, email, website,
    hours, rating summary) are NOT in the page's visible rendered text --
    they're in one of ~86 `<script type="application/json">` blocks
    Facebook's Comet frontend embeds (bbb_scraper.parsing.json_extract's
    existing extract_json_scripts() already handles pulling these out --
    reused here, not reimplemented). Which numbered block holds them is
    NOT stable (confirmed: block 74 for one page, a different index for
    another) -- this walks every blob looking for the real, stable
    signal instead: a dict carrying both a `renderer` key and a sibling
    `timeline_context_list_item_type` key. That type string is a clean,
    self-documenting discriminator (INTRO_CARD_PROFILE_PHONE,
    INTRO_CARD_PROFILE_EMAIL, INTRO_CARD_RATING, ...) -- confirmed by
    reverse-engineering it against known-real values (the exact phone/
    email/address this project already had on file for these businesses
    from BBB), not guessed from field names alone.
  - Two real renderer types share the SAME generic `__typename`
    (ContextItemDefaultRenderer) -- phone and email are only
    distinguishable by `timeline_context_list_item_type`, not by
    `__typename` alone. Don't drop that field if this ever gets
    "simplified."
  - Category can be more than one value ("Dentist & Dental Office ·
    Cosmetic Dentist · Teeth Whitening Service", confirmed live) --
    always a list here, never assumed singular.
  - Rating is NOT always a percentage: a real page with only 4 reviews
    (Ponce Dental Group) showed "Not yet rated (4 reviews)" instead of
    "NN% recommend (N reviews)" -- review_count is still real and present
    either way, recommend_percentage is None for exactly this case, not
    a parse failure.
  - INTRO_CARD_OTHER_ACCOUNT is real and useful -- confirmed live
    (Ponce Dental Group links Instagram/X/YouTube accounts this way).
    The visible `text` is just a display label ("SmileGeneration"), not
    a URL -- the real external URL lives one level deeper, at
    `context_item.title.ranges[0].entity.external_url`.
  - `website` text can still carry the business's own UTM/tracking query
    string even though it reads as a clean domain visually (confirmed
    live, Ponce Dental Group) -- stripped here at parse time.
  - A page can carry TWO `INTRO_CARD_WEBSITE` cards (confirmed live,
    SignatureNrgy LLC: "signaturenrgy.com" then a second one reading
    literally "gmail.com", almost certainly Facebook rendering their
    email's own domain as a second website-shaped card) -- first one
    found wins, not last, since the real business domain consistently
    came first across every real multi-website page seen.
  - Confirmed against 15 real, different businesses (dentists,
    electricians, plumbers, solar, real estate, foundation repair --
    tests/fixtures/facebook_page_sample*.html covers a representative
    3 of them) that not every field/card is present on every page --
    e.g. a real 70k-follower national brand page had no phone card and
    no rating card at all (genuinely absent, not a parse miss), some
    pages had zero social_links, and phone vs. rating vs. hours are all
    independently optional. Three more real card types turned up this
    way: INTRO_CARD_CONFIRMED_OWNER_LABEL (a legal-entity-name badge --
    "Erie Construction Mid-West Inc." for a page display-named "Erie
    Home"), INTRO_CARD_BUSINESS_PRICE ("Price Range · $$"), and
    INTRO_CARD_BUSINESS_SERVICE_AREA (a list of cities/counties served --
    seen on service-area trades like solar, not on fixed-location ones
    like dentists, in what's been tested so far).
  - One real business's page (StellarPlumbing, out of the 15 tested)
    came back with NO `og:title`/`og:description` and no About-card data
    at all -- just a login-prompt UI component
    (CAALoginCometHeaderLoginForm.react) -- confirmed this is a real page
    state, not a fluke (retried with/without a trailing slash, same
    result both times). Absence of `og:title` specifically is what this
    module treats as the signal (present on all 14 other real pages
    tested, absent only on this one) -- see FacebookProfile.status.
  - Follower/talking-about/checkin counts are NOT in the About-card JSON
    at all -- they're in the page's own `<meta property="og:description">`
    tag as plain text ("216 followers · 13 talking about this · 42 were
    here. <bio>"), which is a far more stable extraction point than the
    internal JSON (this exact tag format is what every external
    link-preview tool -- Slack, iMessage, etc. -- already depends on
    Facebook keeping stable). Confirmed live: followers/talking-about/
    checkins are each INDEPENDENTLY optional (a real quiet page had
    followers + were-here but no talking-about at all) -- parsed
    separately, never assumed to appear together.
  - Individual review TEXT is NOT in this page's plain HTTP response at
    all (confirmed live: a real logged-out browser visit to the
    /reviews sub-page shows real review text, but a plain curl_cffi GET
    of the same URL does not -- it's lazy-loaded client-side after
    initial render). Getting it would need either real browser
    automation (nothing else in this project uses that -- everything
    else is curl_cffi + raw HTML/JSON, a meaningfully different and
    heavier architecture) or reverse-engineering Facebook's own paginated
    GraphQL call, which may itself require a logged-in session (the
    exact account-risk problem the About-card data avoids). Not
    attempted here -- this module only surfaces what a plain,
    unauthenticated page fetch actually contains.
"""
from __future__ import annotations

import html as html_lib
import re
from typing import Any
from urllib.parse import urlparse

from bbb_scraper.facebook.models import FacebookProfile
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.parsing.json_extract import extract_json_scripts

logger = get_logger(__name__)

_MIDDLE_DOT = "·"

_RATING_RE = re.compile(r"(?:(\d+)%\s*recommend|Not yet rated)\s*\((\d+)\s*reviews?\)", re.IGNORECASE)
_OG_DESC_STAT_CLAUSE_RE = re.compile(
    r"^[^.]*\.\s*(?:[\d,]+\s+(?:followers?|talking about this|were here)\s*(?:" + _MIDDLE_DOT + r"\s*)?)+\.\s*"
)

_PLATFORM_BY_HOST = {
    "instagram.com": "instagram",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "youtube.com": "youtube",
    "tiktok.com": "tiktok",
    "linkedin.com": "linkedin",
    "pinterest.com": "pinterest",
}


def _find_context_items(blobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every dict anywhere in `blobs` that carries both `renderer` and
    `timeline_context_list_item_type` -- schema-agnostic on the exact
    nesting path on purpose (see module docstring: the array indices
    getting there are not stable across pages)."""
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "timeline_context_list_item_type" in node and "renderer" in node:
                out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for blob in blobs:
        walk(blob)
    return out


def _item_text(context_item: dict[str, Any]) -> str | None:
    title = context_item.get("title") or context_item.get("plaintext_title") or {}
    text = title.get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def _first_range_external_url(context_item: dict[str, Any]) -> str | None:
    title = context_item.get("title") or {}
    for rng in title.get("ranges") or []:
        entity = (rng or {}).get("entity") or {}
        url = entity.get("external_url")
        if url:
            return url
    return None


def _infer_platform(url: str) -> str:
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    if host in _PLATFORM_BY_HOST:
        return _PLATFORM_BY_HOST[host]
    return host.split(".")[0] if host else "other"


def _split_dot_list(text: str) -> list[str]:
    """Facebook renders several of these About-card values as one string
    joined with a middle dot ("Page · Dentist · Cosmetic Dentist",
    "Price Range · $$", a service-area list, ...) -- shared by category,
    price, and service-area parsing. Drops a leading "Page" label when
    present (only the category card has one)."""
    parts = [p.strip() for p in text.split(_MIDDLE_DOT)]
    return [p for p in parts if p and p.lower() != "page"]


def _clean_website(text: str) -> str:
    return re.split(r"[?#]", text.strip(), maxsplit=1)[0]


def _search_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None


def _parse_og_description(desc: str) -> tuple[int | None, int | None, int | None, str | None]:
    followers = _search_int(r"([\d,]+)\s+followers?", desc)
    talking_about = _search_int(r"([\d,]+)\s+talking about this", desc)
    checkins = _search_int(r"([\d,]+)\s+were here", desc)
    stat_match = _OG_DESC_STAT_CLAUSE_RE.search(desc)
    bio = desc[stat_match.end():].strip() if stat_match else None
    return followers, talking_about, checkins, (bio or None)


def _meta_content(html: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta\s+(?:property|name)="{re.escape(prop)}"\s+content="([^"]*)"', html, re.IGNORECASE
    )
    return html_lib.unescape(m.group(1)) if m else None


def parse_profile(html: str, url: str) -> FacebookProfile:
    """Never raises -- an unrecognized/changed page shape comes back as a
    FacebookProfile with mostly-None fields (only `url` is guaranteed),
    same fail-open philosophy as webcheck.check_website. Caller (client.py)
    decides what an all-empty result means (page genuinely has nothing
    on it vs. Facebook changed its markup)."""
    profile = FacebookProfile(url=url)

    og_title = _meta_content(html, "og:title")
    if not og_title:
        # Confirmed live, 2026-09-18 (StellarPlumbing): a real login-walled
        # page has neither og:title nor og:description nor any About-card
        # data -- every other field below would just stay at its default,
        # which would misleadingly look identical to "this business simply
        # has no phone/email/etc." Flag it instead of guessing further.
        profile.status = "unavailable"
        return profile
    profile.name = og_title.split(" | ")[0].strip() or None

    og_desc = _meta_content(html, "og:description")
    if og_desc:
        followers, talking_about, checkins, bio = _parse_og_description(og_desc)
        profile.followers_count = followers
        profile.talking_about_count = talking_about
        profile.checkins_count = checkins
        profile.bio = bio

    try:
        blobs = extract_json_scripts(html)
    except Exception:
        logger.warning("facebook parser: failed to extract embedded JSON for %s", url, exc_info=True)
        blobs = []

    for item in _find_context_items(blobs):
        litype = item.get("timeline_context_list_item_type")
        context_item = (item.get("renderer") or {}).get("context_item") or {}
        text = _item_text(context_item)
        if not text:
            continue

        if litype == "INTRO_CARD_INFLUENCER_CATEGORY":
            profile.categories = _split_dot_list(text)
        elif litype == "INTRO_CARD_ADDRESS":
            profile.address = text
        elif litype == "INTRO_CARD_PROFILE_PHONE":
            profile.phone = text
        elif litype == "INTRO_CARD_PROFILE_EMAIL":
            profile.email = text
        elif litype == "INTRO_CARD_WEBSITE":
            if profile.website is None:  # first card wins -- see module docstring
                profile.website = _clean_website(text)
        elif litype == "INTRO_CARD_BUSINESS_HOURS":
            profile.hours_status = text
        elif litype == "INTRO_CARD_CONFIRMED_OWNER_LABEL":
            profile.confirmed_owner = text
        elif litype == "INTRO_CARD_BUSINESS_PRICE":
            parts = _split_dot_list(text)
            profile.price_range = parts[-1] if parts else text
        elif litype == "INTRO_CARD_BUSINESS_SERVICE_AREA":
            profile.service_areas = _split_dot_list(text)
        elif litype == "INTRO_CARD_RATING":
            m = _RATING_RE.search(text)
            if m:
                profile.recommend_percentage = int(m.group(1)) if m.group(1) else None
                profile.review_count = int(m.group(2))
            profile.reviews_url = context_item.get("url")
        elif litype == "INTRO_CARD_OTHER_ACCOUNT":
            link_url = _first_range_external_url(context_item)
            if link_url:
                profile.social_links.append({"platform": _infer_platform(link_url), "url": link_url})

    return profile
