"""
BBB session secrets: cookies + headers captured from a real browser session,
loaded from a local gitignored JSON file (never committed, never hardcoded).

Status as of 2026-09-02: this file is now OPTIONAL, not required. It was
originally built on the assumption that a captured `cf_clearance` +
`CF_Authorization` were what let requests through Cloudflare. Once
scraping/client.py switched to `curl_cffi` (browser TLS/HTTP2
impersonation) to fix the profile-page 403s (see client.py's module
docstring), a direct test confirmed neither is actually load-bearing for
that: the exact same requests, with cookies removed entirely, still
returned clean 200s -- against both /api/search and a business-profile page
that had never been fetched before (ruled out response caching via
`cf-cache-status: DYNAMIC` on both). Location personalization doesn't
depend on cookies either -- the search response's `location` field, null in
earlier cookie'd captures, comes back fully populated once `find_loc`/
`find_latlng` are passed as request params regardless of cookies.

Practical upshot: there's no evidence a fresh HttpClient needs this file at
all anymore. It's kept as an available option (real cookies can't hurt, and
might matter for something not yet identified, or if BBB tightens its
Cloudflare posture later -- e.g. to an interactive JS challenge, which
curl_cffi's fingerprint impersonation alone could not solve, unlike a real
browser) -- just not a prerequisite the way it once looked.

What's still real, independent of any of the above:
  - `CF_Authorization` is a JWT with a real expiry (~24h in the sample this
    was modeled on) if you do rely on a captured session for something.
  - `cf_clearance` is typically issued for, and checked against, the IP (and
    sometimes TLS fingerprint) that earned it -- same caveat if used.
  - This has only been verified over one session's testing window, not
    "this will always be true" -- Cloudflare configs change, and BBB's own
    protection posture could tighten. If profile pages start getting
    blocked again even with curl_cffi, this file (refreshed) is the first
    thing to try bringing back into the mix.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bbb_scraper.config import settings
from bbb_scraper.logging_setup import get_logger

logger = get_logger(__name__)


def load_bbb_session(path: Path | str | None = None) -> dict[str, Any]:
    """Return {"cookies": {...}, "headers": {...}}, or both empty if no
    session file is configured/found. Missing file is expected/fine, not an
    error -- see this module's docstring; curl_cffi's browser impersonation
    has been sufficient on its own in testing.
    """
    path = Path(path) if path else settings.bbb_session_file
    if not path.exists():
        logger.info(
            "No BBB session file at %s -- proceeding without one (curl_cffi's "
            "browser impersonation has been sufficient on its own in testing; "
            "see this module's docstring). Optional: data/secrets/bbb_session.example.json.",
            path,
        )
        return {"cookies": {}, "headers": {}}

    raw = json.loads(path.read_text(encoding="utf-8"))
    cookies = raw.get("cookies", {})
    headers = raw.get("headers", {})
    logger.info(
        "Loaded BBB session from %s (%d cookies, %d headers)",
        path, len(cookies), len(headers),
    )
    return {"cookies": cookies, "headers": headers}
