"""bbb_scraper.webcheck.checker -- no real network calls; a fake session
stands in for curl_cffi so these run offline like every other test here.
Deliberately covers every status bucket, not just the happy path -- see
the module docstring for why 403/5xx are NOT treated as dead."""
from __future__ import annotations

from curl_cffi import requests as curl_requests

from bbb_scraper.webcheck.checker import (
    DEAD_STATUSES,
    WebsiteCheck,
    _looks_parked,
    _normalize_url,
    check_website,
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeSession:
    """Give it either a canned response or an exception to raise -- never
    makes a real request."""

    def __init__(self, response: _FakeResponse | None = None, raises: Exception | None = None):
        self._response = response
        self._raises = raises
        self.closed = False

    def get(self, url, timeout=None, allow_redirects=True):
        if self._raises:
            raise self._raises
        return self._response

    def close(self):
        self.closed = True


def _check(response=None, raises=None, url="example.com"):
    session = _FakeSession(response=response, raises=raises)
    return check_website(url, session=session)


# --- URL normalization -----------------------------------------------------

def test_normalize_adds_https_scheme_when_missing():
    assert _normalize_url("example.com") == "https://example.com"


def test_normalize_leaves_an_explicit_scheme_alone():
    assert _normalize_url("http://example.com") == "http://example.com"


def test_normalize_blank_is_blank():
    assert _normalize_url("") == ""
    assert _normalize_url("   ") == ""


# --- no website / malformed -------------------------------------------------

def test_no_url_is_no_website_not_dead():
    result = check_website("", session=_FakeSession())
    assert result.status == "no_website"
    assert result.dead is False


def test_empty_netloc_after_normalizing_is_dead_unreachable():
    # "https://" round-trips through _normalize_url unchanged (already has
    # a scheme) but urlparse gives it an empty netloc -- nothing to even
    # attempt a request against.
    result = check_website("https://", session=_FakeSession())
    assert result.status == "dead_unreachable"
    assert result.dead is True


def test_garbage_hostname_fails_via_the_normal_connection_error_path():
    """No special-cased "malformed URL" pre-check needed for most garbage --
    urlparse is lenient (a string like "not a url!!" still parses to some
    netloc), so it just reaches sess.get() and fails there like any other
    bad host would, in a real run. Confirms that path still classifies it
    as dead_unreachable rather than raising."""
    exc = curl_requests.exceptions.ConnectionError("Could not resolve host")
    result = _check(raises=exc, url="not a url at all!!")
    assert result.status == "dead_unreachable"
    assert result.dead is True


def test_a_broken_response_object_fails_open_as_check_failed_not_a_crash():
    """Response *processing* (not just the request) is inside the
    never-raises guarantee -- a response that doesn't behave as expected
    must come back as check_failed, not propagate an exception up through
    a whole batch of other businesses still waiting to be checked."""
    class _WeirdResponse:
        status_code = 200

        @property
        def text(self):
            raise ValueError("simulated: response body couldn't be decoded")

    result = _check(response=_WeirdResponse())
    assert result.status == "check_failed"
    assert result.dead is False


# --- unreachable (the unambiguous case) -------------------------------------

def test_connection_error_is_dead_unreachable():
    exc = curl_requests.exceptions.ConnectionError("Could not resolve host: example.com")
    result = _check(raises=exc)
    assert result.status == "dead_unreachable"
    assert result.dead is True
    assert "DNS" in result.detail


def test_timeout_is_dead_unreachable():
    exc = curl_requests.exceptions.ConnectionError("Connection timed out after 10000ms")
    result = _check(raises=exc)
    assert result.status == "dead_unreachable"
    assert "timed out" in result.detail


# --- explicit 404/410 --------------------------------------------------------

def test_404_is_dead():
    result = _check(response=_FakeResponse(404))
    assert result.status == "dead_404"
    assert result.dead is True
    assert result.http_status == 404


def test_410_gone_is_dead():
    result = _check(response=_FakeResponse(410))
    assert result.status == "dead_404"
    assert result.dead is True


# --- ambiguous statuses: NOT asserted dead -----------------------------------

def test_403_is_blocked_not_dead():
    """A real, working small-business site can legitimately block a non-
    browser client -- this must never come back as 'the site is dead'."""
    result = _check(response=_FakeResponse(403))
    assert result.status == "blocked"
    assert result.dead is False


def test_429_is_blocked_not_dead():
    result = _check(response=_FakeResponse(429))
    assert result.status == "blocked"
    assert result.dead is False


def test_500_is_server_error_not_dead():
    """A 5xx might be a transient blip on an otherwise-live site -- don't
    tell a business its website is down over a momentary hiccup."""
    result = _check(response=_FakeResponse(503))
    assert result.status == "server_error"
    assert result.dead is False


# --- parked/for-sale domain --------------------------------------------------

def test_parked_domain_page_is_dead():
    body = "<html><body>This domain may be for sale. Buy this domain at GoDaddy.com/domains</body></html>"
    result = _check(response=_FakeResponse(200, text=body))
    assert result.status == "dead_parked"
    assert result.dead is True


def test_normal_200_is_ok():
    body = "<html><body><h1>Ray's Plumbing</h1><p>Call us for a quote!</p></body></html>"
    result = _check(response=_FakeResponse(200, text=body))
    assert result.status == "ok"
    assert result.dead is False


def test_a_real_site_mentioning_domains_isnt_falsely_parked():
    """The heuristic looks for specific parking-page phrases, not the bare
    word 'domain' -- a real business site that happens to mention domains
    (e.g. a web-hosting company) shouldn't get misclassified."""
    body = "<html><body>We help you register a custom domain for your business.</body></html>"
    assert _looks_parked(body) is False


# --- DEAD_STATUSES stays in sync with what check_website actually returns ---

def test_dead_statuses_set_matches_dead_true_outcomes():
    cases = [
        (_check(response=_FakeResponse(404)), True),
        (_check(response=_FakeResponse(403)), False),
        (_check(response=_FakeResponse(500)), False),
        (_check(response=_FakeResponse(200, text="buy this domain")), True),
        (_check(raises=curl_requests.exceptions.ConnectionError("refused")), True),
        (_check(response=_FakeResponse(200, text="hello")), False),
    ]
    for result, expected_dead in cases:
        assert result.dead == expected_dead
        assert (result.status in DEAD_STATUSES) == expected_dead


def test_unexpected_exception_fails_open_not_dead():
    """An error in our own code/session shouldn't accuse the business's
    site of being down."""
    result = _check(raises=RuntimeError("something in our own plumbing broke"))
    assert result.status == "check_failed"
    assert result.dead is False


def test_result_is_a_plain_dataclass():
    result = _check(response=_FakeResponse(200, text="hi"))
    assert isinstance(result, WebsiteCheck)
    assert result.checked_at  # non-empty ISO timestamp
