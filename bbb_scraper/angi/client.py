"""HTTP fetching for angi.com -- no BBB-session machinery (irrelevant here,
that's bbb.org-specific), but paced, retried, and (2026-09-14, since a real
run drew real 429s) proxied and rotated by default, since unlike
bbb_scraper/webcheck (one request per different host) this is repeated
requests to *one* host.

**A real gotcha, found by testing, not assumed:** Angi appears to run a
server-side experiment that serves two different page compositions for
the exact same URL -- one with the full business-profile data, one without
it entirely (confirmed: the "without" version isn't smaller/truncated,
it's a differently-sized, differently-shaped response, not a loading
state). Which one you get is decided by a cookie Angi sets on first
response (`HA-loggedIn` was the one confirmed sticky across repeat
requests) -- a plain `requests`/curl_cffi `Session` that persists cookies
the normal way can get *permanently* stuck on the empty variant for its
entire lifetime once it happens to land there once, confirmed by testing:
4/4 repeat requests through one persistent, cookie-carrying session all
came back empty, while 4 independent cookie-less requests to the identical
URL flipped between variants (2 empty, 2 full). The fix here is to clear
the session's cookies before every single request -- confirmed this
restores real variance (5/8 full in one real run) rather than a permanent
lock-in. Costs nothing (nothing here actually needs cross-request cookie
continuity) and combines with scraper.py's own retry-on-empty-parse for a
high effective success rate.
"""
from __future__ import annotations

import time

from curl_cffi import requests as curl_requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.exceptions import RateLimitedError, ScrapeError
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.scraping.proxies import get_proxies, new_session_id
from bbb_scraper.utils.rate_limit import RateLimiter

logger = get_logger(__name__)

# A real 429 showed up in a real run at the original, faster pacing -- see
# config.py's angi_min/max_delay_seconds. Responding to an actual rate-limit
# signal from the site with a real cooldown, not just a quick generic
# exponential backoff, is the point here (see the ethical-scraping-boundary
# practice this project holds to: slow down for real, don't just retry
# faster/harder around the signal).
_RATE_LIMIT_COOLDOWN_SECONDS = 25.0


class AngiClient:
    def __init__(self, cfg: Settings | None = None, *, use_proxy: bool = True, rotate_every: int | None = None):
        """Proxied by default now -- a real, sustained run of 429s (3/3
        consecutive requests, no recovery even after a 25s cooldown each)
        showed up on a direct IP that had already made a lot of legitimate
        one-off requests earlier the same day (see the module docstring).
        `use_proxy=False` remains available for local debugging without a
        proxy configured.

        `rotate_every` (default cfg.angi_proxy_rotate_every) periodically
        swaps in a fresh proxy session id -- and therefore, typically, a
        fresh exit IP -- rather than one session/IP carrying an entire
        run's request volume. This is the *opposite* of proxies.py's
        city-targeting use (there, a stable session id across requests is
        the point, so Decodo keeps routing to the same named city); here,
        nothing needs geographic consistency, so spreading requests across
        several residential IPs over a long run is strictly the more
        polite choice -- the same total request volume, distributed rather
        than concentrated on one IP."""
        self.cfg = cfg or default_settings
        self.use_proxy = use_proxy
        self.rotate_every = rotate_every if rotate_every is not None else self.cfg.angi_proxy_rotate_every
        self._requests_since_rotation = 0
        self.rate_limiter = RateLimiter(self.cfg.angi_min_delay_seconds, self.cfg.angi_max_delay_seconds)
        self.session = curl_requests.Session(impersonate=self.cfg.http_impersonate or None)
        if self.use_proxy:
            self._rotate_proxy()

    def _rotate_proxy(self) -> None:
        proxies = get_proxies(self.cfg, session_id=new_session_id())
        if proxies:
            self.session.proxies.update(proxies)
        else:
            logger.warning("angi: use_proxy=True but get_proxies() returned nothing -- check PROXY_* in .env")
        self._requests_since_rotation = 0

    def get(self, path_or_url: str) -> str:
        """Returns the response body text. `path_or_url` may be a relative
        path (e.g. a listing card's `profileUrl`) or an absolute URL."""
        url = path_or_url if path_or_url.startswith("http") else self.cfg.angi_base_url + path_or_url
        cfg = self.cfg

        @retry(
            reraise=True,
            stop=stop_after_attempt(cfg.angi_max_retries),
            wait=wait_exponential(multiplier=1.5, min=1, max=20),
            retry=retry_if_exception_type((curl_requests.exceptions.RequestException, RateLimitedError)),
        )
        def _do_request():
            if self.use_proxy and self._requests_since_rotation >= self.rotate_every:
                self._rotate_proxy()
            self.rate_limiter.wait()
            self.session.cookies.clear()  # see module docstring -- avoids getting stuck on the empty variant
            logger.info("GET %s", url)
            response = self.session.get(url, timeout=cfg.angi_timeout_seconds)
            self._requests_since_rotation += 1
            if response.status_code == 429:
                logger.warning(
                    "angi: 429 for %s -- cooling down %.0fs before retrying (see client.py docstring)",
                    url, _RATE_LIMIT_COOLDOWN_SECONDS,
                )
                if self.use_proxy:
                    self._rotate_proxy()  # a 429 is exactly what rotation exists to route around
                time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
                raise RateLimitedError(f"Angi returned 429 for {url}")
            response.raise_for_status()
            return response

        try:
            response = _do_request()
        except (curl_requests.exceptions.RequestException, RateLimitedError) as exc:
            raise ScrapeError(f"Request to {url} failed after retries: {exc}") from exc
        return response.text

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> AngiClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
