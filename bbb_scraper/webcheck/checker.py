"""
Single-URL "is this business's website actually live" check.

Deliberately conservative about what counts as `dead=True` -- a false
"your website is down" is worse than missing a real one, since it's the
opening line of an actual sales pitch. Only three signals are trusted:

  1. Unreachable -- DNS doesn't resolve, connection refused/times out, TLS
     fails, or the URL is too malformed to even try. Unambiguous: a real
     visitor gets nothing either.
  2. HTTP 404 / 410 on the URL BBB has on file for them -- unambiguous.
  3. A parked/for-sale domain -- the domain still resolves and returns 200,
     but the page is a registrar's parking/marketplace page, not the
     business's real site. Just as sellable a signal as a 404, and common:
     a lapsed domain often free-falls to a parking page rather than erroring.

Everything else (403/401/429 "blocked", a 5xx that might be a transient
blip, any other surprise) is reported as its own status but NOT flagged
dead -- plenty of real, working small-business sites block non-browser
traffic or hiccup occasionally, and this project's whole ethos is not
asserting things it isn't confident about (see the reputation-scoring
code's own "don't overclaim" stance).

Uses curl_cffi with browser impersonation, same as the BBB client -- not
because these sites need bypassing (most don't), but specifically so a
small site's basic bot-check doesn't get misread as "the business's
website is dead" when it's really just "not a browser". No proxy: this is
one request per unique domain across many different hosts, not the
repeated-same-host pattern that needs BBB's residential IP.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.logging_setup import get_logger

logger = get_logger(__name__)

# Statuses that assert the site is genuinely dead -- see module docstring
# for why these three specifically, and no others.
DEAD_STATUSES = frozenset({"dead_unreachable", "dead_404", "dead_parked"})

# Case-insensitive substrings seen on registrar parking / domain-marketplace
# pages -- a lapsed domain often lands here instead of erroring outright.
# Deliberately specific phrases (not bare "domain" or "for sale") to avoid
# false-flagging a real site that happens to mention selling domains.
_PARKED_MARKERS = [
    "domain may be for sale", "this domain is for sale", "buy this domain",
    "the domain has expired", "domain has expired", "this domain has expired",
    "domain parking", "related searches",
    "godaddy.com/domains", "namecheap.com/domains", "sedo.com",
    "hugedomains.com", "dan.com", "afternic.com", "parkingcrew",
]


@dataclass
class WebsiteCheck:
    url: str  # the normalized URL actually requested ("" if none given)
    status: str
    dead: bool
    http_status: int | None
    checked_at: str  # ISO 8601
    detail: str = ""  # short human-readable reason, for hovering/debugging


def _normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = f"https://{raw}"
    return raw


def _looks_parked(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _PARKED_MARKERS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_website(
    raw_url: str,
    *,
    cfg: Settings | None = None,
    timeout: float | None = None,
    session: curl_requests.Session | None = None,
) -> WebsiteCheck:
    """Check one URL. Never raises -- any unexpected failure comes back as
    status="check_failed", dead=False (fail open: don't accuse a business's
    site of being dead over our own network hiccup). Pass `session` to reuse
    one curl_cffi Session across many calls (see enrich.py) instead of
    paying connection-setup cost per business."""
    cfg = cfg or default_settings
    checked_at = _now_iso()
    url = _normalize_url(raw_url)
    if not url:
        return WebsiteCheck(url="", status="no_website", dead=False,
                             http_status=None, checked_at=checked_at)

    parsed = urlparse(url)
    if not parsed.netloc:
        return WebsiteCheck(url=url, status="dead_unreachable", dead=True,
                             http_status=None, checked_at=checked_at,
                             detail="malformed URL")

    own_session = session is None
    sess = session or curl_requests.Session(impersonate=cfg.http_impersonate or None)
    try:
        # Everything -- issuing the request AND reading the response -- is
        # inside this one try/except, not just the request itself: a
        # never-raises guarantee that only covers `sess.get()` and lets a
        # response-parsing surprise (a weird body encoding, a mocked/odd
        # response object in a test, anything) escape uncaught would still
        # be a real way to take down a whole batch's website check over one
        # site. Everything below is response classification, not I/O, so
        # there's no politeness/rate-limit reason to keep it out of scope.
        resp = sess.get(url, timeout=timeout or cfg.webcheck_timeout_seconds,
                         allow_redirects=True)
        code = resp.status_code
        if code in (404, 410):
            return WebsiteCheck(url=url, status="dead_404", dead=True,
                                 http_status=code, checked_at=checked_at,
                                 detail=f"HTTP {code}")
        if code in (401, 403, 429):
            return WebsiteCheck(url=url, status="blocked", dead=False,
                                 http_status=code, checked_at=checked_at,
                                 detail=f"HTTP {code} -- likely bot-blocked, not necessarily down")
        if code >= 500:
            return WebsiteCheck(url=url, status="server_error", dead=False,
                                 http_status=code, checked_at=checked_at,
                                 detail=f"HTTP {code} -- may be transient")
        if code >= 400:
            return WebsiteCheck(url=url, status="dead_404", dead=True,
                                 http_status=code, checked_at=checked_at,
                                 detail=f"HTTP {code}")

        body = resp.text if isinstance(resp.text, str) else ""
        if _looks_parked(body):
            return WebsiteCheck(url=url, status="dead_parked", dead=True,
                                 http_status=code, checked_at=checked_at,
                                 detail="looks like a parked/for-sale domain page")
        return WebsiteCheck(url=url, status="ok", dead=False,
                             http_status=code, checked_at=checked_at)
    except curl_requests.exceptions.RequestException as exc:
        reason = _classify_connection_error(exc)
        return WebsiteCheck(url=url, status="dead_unreachable", dead=True,
                             http_status=None, checked_at=checked_at, detail=reason)
    except Exception as exc:  # noqa: BLE001 -- fail open, never crash a batch over one site
        logger.warning("Unexpected error checking %s: %s", url, exc)
        return WebsiteCheck(url=url, status="check_failed", dead=False,
                             http_status=None, checked_at=checked_at, detail=str(exc)[:200])
    finally:
        if own_session:
            sess.close()


def _classify_connection_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "could not resolve host" in text or "name or service not known" in text or "getaddrinfo" in text:
        return "DNS lookup failed"
    if "timed out" in text or "timeout" in text:
        return "connection timed out"
    if "ssl" in text or "certificate" in text:
        return "TLS/certificate error"
    if "connection refused" in text or "connection reset" in text:
        return "connection refused"
    return "connection failed"
