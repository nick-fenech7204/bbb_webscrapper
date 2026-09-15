"""HTTP fetching for angi.com -- no BBB-session machinery (irrelevant here,
that's bbb.org-specific), but paced, retried, and (2026-09-14, since a real
run drew real 429s) proxied by default, since unlike bbb_scraper/webcheck
(one request per different host) this is repeated requests to *one* host.

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
URL flipped between variants (2 empty, 2 full). 2026-09-15's fresh-
session-per-request change (below) makes this a non-issue for free -- a
brand-new Session has no cookies to get stuck with in the first place, so
the old explicit `session.cookies.clear()` workaround is gone; nothing
here ever needed cross-request cookie continuity.

**Proxy: bare/rotating, one fresh Session (and therefore one fresh
connection) per request -- never a sticky session, 2026-09-15.** Two real
incidents, same day, led here:
  1. The original design rotated to a *new sticky session id* every
     `rotate_every` requests (get_proxies(session_id=new_session_id())).
     That's not "rotating" in Decodo's own terms -- a `-session-{id}`
     suffix is specifically Decodo's *sticky*-session mechanism (pins one
     exit IP for ~10min; see help.decodo.com's sticky-vs-rotating docs).
     Manufacturing a brand-new one-off sticky session this often, for the
     whole lifetime of a real batch, is almost certainly what tripped a
     concurrent-sticky-session account limit: the very first real batch
     run through bbb_scraper/mapquest's identical pattern failed 100% of
     requests with `curl: (7) CONNECT tunnel failed, response 407` (see
     that module's own docstring) -- and a live re-test the same day
     confirmed Angi's original session-based rotation 407s exactly as
     consistently. Nick's own call once this was traced down: no sticky
     sessions at all, ever, for either client -- go bare, which Decodo's
     own docs confirm is "rotating" by default.
  2. Bare alone isn't enough, though -- confirmed live: 6 sequential
     requests through one *reused* Session, bare proxy, came back with
     the exact same exit IP all 6 times (even with a `Connection: close`
     header forced, which didn't help either); 6 requests each through a
     *fresh* Session came back with 6 different real IPs. Decodo rotates
     per new connection to its gateway, not per HTTP request over an
     already-open one -- so a client that builds one Session and reuses
     it for its whole life (which is what every client here always did)
     never actually rotates in bare mode, proxied or not. The fix is
     _new_session() below, called at the top of every retried request
     attempt (a retry gets a fresh IP too, not just a fresh backoff) --
     "a new proxy per request," Nick's own words, confirmed to actually
     require exactly this, not just dropping the session id.
"""
from __future__ import annotations

import time

from curl_cffi import requests as curl_requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.exceptions import RateLimitedError, ScrapeError
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.scraping.proxies import get_proxies
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
    def __init__(self, cfg: Settings | None = None, *, use_proxy: bool = True):
        """Proxied by default now -- a real, sustained run of 429s (3/3
        consecutive requests, no recovery even after a 25s cooldown each)
        showed up on a direct IP that had already made a lot of legitimate
        one-off requests earlier the same day (see the module docstring).
        `use_proxy=False` remains available for local debugging without a
        proxy configured.

        A fresh Session (see _new_session) is built for every single
        request, bare/no-session-id -- "a new proxy per request", not
        periodic rotation -- see the module docstring for why (both the
        sticky-session 407 this replaces, and why bare-but-reused doesn't
        actually rotate)."""
        self.cfg = cfg or default_settings
        self.use_proxy = use_proxy
        self.rate_limiter = RateLimiter(self.cfg.angi_min_delay_seconds, self.cfg.angi_max_delay_seconds)
        self.session = self._new_session()

    def _new_session(self) -> curl_requests.Session:
        """A brand-new Session -- and therefore, when proxied, a brand-new
        connection to the proxy gateway, which is what actually earns a
        fresh exit IP (see module docstring; changing .proxies on a
        *reused* Session does not). Bare proxy username, no session_id --
        Decodo's own "rotating" mode, never sticky."""
        session = curl_requests.Session(impersonate=self.cfg.http_impersonate or None)
        if self.use_proxy:
            proxies = get_proxies(self.cfg)
            if proxies:
                session.proxies.update(proxies)
            else:
                logger.warning("angi: use_proxy=True but get_proxies() returned nothing -- check PROXY_* in .env")
        return session

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
            # A fresh session (-> fresh proxy connection -> fresh exit IP,
            # see module docstring) on every attempt, retries included --
            # a retry riding a different IP than the one that just failed
            # is strictly better odds, not just a slower version of the
            # same attempt. Also incidentally sidesteps the page-variant
            # cookie gotcha (module docstring) for free: a new Session has
            # no cookies to get stuck with.
            self.session = self._new_session()
            self.rate_limiter.wait()
            logger.info("GET %s", url)
            response = self.session.get(url, timeout=cfg.angi_timeout_seconds)
            if response.status_code == 429:
                logger.warning(
                    "angi: 429 for %s -- cooling down %.0fs before retrying (see client.py docstring)",
                    url, _RATE_LIMIT_COOLDOWN_SECONDS,
                )
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
