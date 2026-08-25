"""Shared exception types used across scraping / parsing / ETL layers."""


class BBBScraperError(Exception):
    """Base class for all errors raised by this project."""


class ScrapeError(BBBScraperError):
    """Raised when an HTTP request ultimately fails after retries."""


class BlockedError(ScrapeError):
    """Raised when BBB responds with a block/challenge signal (403, captcha, etc.)."""


class RateLimitedError(ScrapeError):
    """Raised on HTTP 429 responses."""


class ParsingError(BBBScraperError):
    """Raised when expected structure (JSON script tag, preloaded state, etc.) is missing."""
