"""
HTTP client wrapper: proxying, retries, rate limiting, browser TLS/HTTP2
impersonation, and consistent logging/stats for every outbound request.

This is the one place that talks to `curl_cffi` directly. Search/business
fetchers (search.py / business.py) should go through `HttpClient.get`
instead of using an HTTP library themselves, so retry/proxy/rate-limit
behavior stays uniform.

Why curl_cffi instead of `requests`: confirmed 2026-09-02 that plain
`requests`/urllib3 gets Cloudflare-403'd on BBB business-profile pages even
with a completely valid, unexpired, correctly-cookied session -- the
`requests` library's TLS ClientHello doesn't match a real browser's no
matter what headers claim, and BBB's bot management fingerprints that.
`curl_cffi` wraps a curl build patched to replicate real browsers' TLS +
HTTP/2 fingerprints (the same technique as the curl-impersonate project);
swapping to it, same cookies, same everything else, immediately turned a
403 into a 200 on the exact URL that was blocked. Its `requests`-shaped API
(Session, .headers/.cookies/.proxies, .get/.post, Response.status_code/
.text/.json()/.raise_for_status()) is close enough to the stdlib `requests`
library that this file is the only one that needed to change.
"""
from __future__ import annotations

import logging

from curl_cffi import requests as curl_requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.exceptions import BlockedError, RateLimitedError, ScrapeError
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.scraping.proxies import get_proxies
from bbb_scraper.scraping.session import load_bbb_session
from bbb_scraper.utils.rate_limit import RateLimiter
from bbb_scraper.utils.stats import REQUESTS_FAILED, REQUESTS_RETRIED, REQUESTS_SENT, RunStats

logger = get_logger(__name__)

# Status codes that should never be retried blindly -- they usually mean
# "you've been detected", so surface them distinctly rather than hammering.
_BLOCKED_STATUS_CODES = {403}
_RATE_LIMITED_STATUS_CODES = {429}


class HttpClient:
    def __init__(
        self,
        cfg: Settings | None = None,
        *,
        session_id: str | None = None,
        stats: RunStats | None = None,
    ):
        self.cfg = cfg or default_settings
        self.stats = stats or RunStats()
        self.rate_limiter = RateLimiter(
            self.cfg.http_min_delay_seconds, self.cfg.http_max_delay_seconds
        )
        self.session = curl_requests.Session(impersonate=self.cfg.http_impersonate or None)
        self.session.headers.update(
            {
                "User-Agent": self.cfg.http_user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        # Overlay real captured browser headers/cookies on top of the generic
        # defaults above -- these are what actually get bbb.org's Cloudflare
        # bot management to let a request through. See scraping/session.py
        # for the file this comes from and its expiry/IP-binding caveats.
        bbb_session = load_bbb_session(self.cfg.bbb_session_file)
        if bbb_session["headers"]:
            self.session.headers.update(bbb_session["headers"])
        if bbb_session["cookies"]:
            self.session.cookies.update(bbb_session["cookies"])

        proxies = get_proxies(self.cfg, session_id=session_id)
        if proxies:
            self.session.proxies.update(proxies)

    def get(self, url: str, **kwargs) -> curl_requests.Response:
        return self._request_with_retry("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> curl_requests.Response:
        return self._request_with_retry("POST", url, **kwargs)

    def _request_with_retry(self, method: str, url: str, **kwargs) -> curl_requests.Response:
        cfg = self.cfg

        @retry(
            reraise=True,
            stop=stop_after_attempt(cfg.http_max_retries),
            wait=wait_exponential(multiplier=cfg.http_backoff_factor, min=1, max=30),
            retry=retry_if_exception_type(
                (curl_requests.exceptions.RequestException, RateLimitedError)
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        def _do_request() -> curl_requests.Response:
            self.rate_limiter.wait()
            self.stats.incr(REQUESTS_SENT)
            logger.info("%s %s", method, url)
            response = self.session.request(
                method, url, timeout=cfg.http_timeout_seconds, **kwargs
            )

            if response.status_code in _BLOCKED_STATUS_CODES:
                self.stats.incr(REQUESTS_FAILED)
                raise BlockedError(
                    f"BBB returned {response.status_code} for {url} -- likely blocked/challenged"
                )
            if response.status_code in _RATE_LIMITED_STATUS_CODES:
                self.stats.incr(REQUESTS_RETRIED)
                raise RateLimitedError(f"BBB returned 429 for {url}")

            response.raise_for_status()
            return response

        try:
            return _do_request()
        except BlockedError:
            raise
        except curl_requests.exceptions.RequestException as exc:
            self.stats.incr(REQUESTS_FAILED)
            raise ScrapeError(f"Request to {url} failed after retries: {exc}") from exc
        except RateLimitedError as exc:
            self.stats.incr(REQUESTS_FAILED)
            raise ScrapeError(f"Request to {url} was rate-limited after retries: {exc}") from exc

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
