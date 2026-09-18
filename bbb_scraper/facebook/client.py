"""
Facebook business-page fetch client.

**How this was found and validated, 2026-09-18 -- confirmed against 15 real,
different businesses already in this project's own captured data (dentists,
electricians, plumbers, solar, real estate, foundation repair -- see
parser.py's own module docstring for the field-level findings), not guessed:**

  - Nick's first idea was a captured curl command replaying his own logged-in
    Facebook session cookies. That request was never run here -- Claude
    Code's own safety layer blocked it (a scripted request replaying a real
    personal session cookie against a third-party site matches how a
    session-hijacking/credential-replay attack looks, regardless of whose
    account it is or the actual intent). Turned out not to matter: a plain,
    fully anonymous, logged-out request gets the same About-card data
    (category/address/phone/email/website/hours/rating) that a logged-in
    browser view shows. No personal account, no session cookie, no account-
    ban risk -- this client never sends any cookie at all.
  - Uses curl_cffi with browser-TLS impersonation (`impersonate="chrome"`),
    same reason as everywhere else in this project: not because Facebook
    needs bypassing for this data specifically (it doesn't -- no login wall
    on 14 of 15 real pages tested), but so a bot-detection false positive
    doesn't get misread as "this business has no Facebook presence."
  - Individual review TEXT is NOT reachable this way (confirmed live: a real
    browser shows it, a plain HTTP GET of the same /reviews URL doesn't --
    it's lazy-loaded client-side). Not attempted here. See parser.py's
    module docstring for the full reasoning; this client only ever fetches
    the main profile URL, never /reviews.
  - One real page out of 15 tested came back login-walled on a plain
    anonymous request (no og:title/og:description, a login-prompt component
    instead of profile data) -- confirmed real (retried with/without a
    trailing slash, same result), not a fluke or something proxying/
    impersonation would fix. Surfaces as FacebookProfile.status ==
    "unavailable", not an exception -- a real state to report, not to guess
    around.
"""
from __future__ import annotations

import time

from curl_cffi import requests as curl_requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.exceptions import RateLimitedError, ScrapeError
from bbb_scraper.facebook.models import FacebookProfile
from bbb_scraper.facebook.parser import parse_profile
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.scraping.proxies import get_proxies
from bbb_scraper.utils.rate_limit import RateLimiter

logger = get_logger(__name__)

_RATE_LIMIT_COOLDOWN_SECONDS = 25.0  # never observed live yet (see module docstring) -- defensive, same value used elsewhere in this project

_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
}


class FacebookClient:
    def __init__(self, cfg: Settings | None = None, *, use_proxy: bool = True):
        self.cfg = cfg or default_settings
        self.use_proxy = use_proxy
        self.rate_limiter = RateLimiter(self.cfg.facebook_min_delay_seconds, self.cfg.facebook_max_delay_seconds)
        self.session = self._new_session()

    def _new_session(self) -> curl_requests.Session:
        """A fresh Session per fetch (see fetch_profile) -- same reasoning as
        bbb_scraper.mapquest.client.MapQuestClient._new_session: a new
        connection is what actually earns a new proxy exit IP, and closing
        the old one before replacing it matters (a real leaked-connection
        incident already hit MapQuest's client this same way -- see its
        module docstring)."""
        session = curl_requests.Session(impersonate="chrome")
        session.headers.update(_HEADERS)
        if self.use_proxy:
            proxies = get_proxies(self.cfg)
            if proxies:
                session.proxies.update(proxies)
            else:
                logger.warning("facebook: use_proxy=True but get_proxies() returned nothing -- check PROXY_* in .env")
        return session

    def fetch_profile(self, url: str) -> FacebookProfile:
        """Fetch and parse one business's Facebook page. Raises ScrapeError
        on a real request failure (network/HTTP) after retries -- a
        successfully-fetched but login-walled or otherwise unrecognized page
        is NOT an error, it comes back as a normal FacebookProfile with
        status="unavailable" (see parser.parse_profile), since that's a
        real page state, not a request failure. Caller (enrich.py) decides
        whether to treat that as best-effort-skip, same as everywhere else
        third-party enrichment can come back empty in this project."""

        @retry(reraise=True, stop=stop_after_attempt(self.cfg.facebook_max_retries),
               wait=wait_exponential(multiplier=1.5, min=1, max=15),
               retry=retry_if_exception_type((curl_requests.exceptions.RequestException, RateLimitedError)))
        def _do_request():
            self.session.close()
            self.session = self._new_session()
            self.rate_limiter.wait()
            response = self.session.get(url, timeout=self.cfg.facebook_timeout_seconds)
            if response.status_code == 429:
                logger.warning("facebook: 429 for %r -- cooling down %.0fs before retrying",
                                url, _RATE_LIMIT_COOLDOWN_SECONDS)
                time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
                raise RateLimitedError(f"Facebook returned 429 for {url!r}")
            response.raise_for_status()
            return response

        try:
            response = _do_request()
        except (curl_requests.exceptions.RequestException, RateLimitedError) as exc:
            raise ScrapeError(f"Facebook page fetch failed for {url!r}: {exc}") from exc

        return parse_profile(response.text, url=url)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> FacebookClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
