"""
HTTP client wrapper: proxying, retries, rate limiting, and consistent
logging/stats for every outbound request.

This is the one place that talks to `requests` directly. Search/business
fetchers (search.py / business.py) should go through `HttpClient.get`
instead of using `requests` themselves, so retry/proxy/rate-limit behavior
stays uniform.
"""
from __future__ import annotations

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging

from bbb_scraper.config import Settings, settings as default_settings
from bbb_scraper.exceptions import BlockedError, RateLimitedError, ScrapeError
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.scraping.proxies import get_proxies
from bbb_scraper.utils.rate_limit import RateLimiter
from bbb_scraper.utils.stats import RunStats, REQUESTS_SENT, REQUESTS_FAILED, REQUESTS_RETRIED

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
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.cfg.http_user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        proxies = get_proxies(self.cfg, session_id=session_id)
        if proxies:
            self.session.proxies.update(proxies)

    def get(self, url: str, **kwargs) -> requests.Response:
        return self._request_with_retry("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self._request_with_retry("POST", url, **kwargs)

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        cfg = self.cfg

        @retry(
            reraise=True,
            stop=stop_after_attempt(cfg.http_max_retries),
            wait=wait_exponential(multiplier=cfg.http_backoff_factor, min=1, max=30),
            retry=retry_if_exception_type((requests.RequestException, RateLimitedError)),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        def _do_request() -> requests.Response:
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
        except requests.RequestException as exc:
            self.stats.incr(REQUESTS_FAILED)
            raise ScrapeError(f"Request to {url} failed after retries: {exc}") from exc
        except RateLimitedError as exc:
            self.stats.incr(REQUESTS_FAILED)
            raise ScrapeError(f"Request to {url} was rate-limited after retries: {exc}") from exc

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
