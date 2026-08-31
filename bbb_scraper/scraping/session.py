"""
BBB session secrets: cookies + headers captured from a real browser session,
loaded from a local gitignored JSON file (never committed, never hardcoded).

Why this exists: bbb.org sits behind Cloudflare bot management. Plain
requests (no cookies at all) get challenged. A captured `cf_clearance` +
`CF_Authorization` + the site's own session cookies (see
data/secrets/bbb_session.example.json for the shape) let us look like a
continuation of a real browser session instead.

Known fragility -- read before assuming a stale session "should" work:
  - `CF_Authorization` is a JWT with a real expiry (~24h in the sample this
    was modeled on). Once it expires, requests will start failing/getting
    challenged again and the file needs refreshing from a new browser
    session.
  - `cf_clearance` is typically issued for, and checked against, the IP (and
    sometimes TLS fingerprint) that earned it. A session captured from your
    own machine may simply not validate once requests are routed through a
    proxy IP -- there's no code fix for that here, it's a real constraint to
    design around (e.g. capturing/refreshing a session per sticky proxy
    session, or solving the challenge through the proxy in the first place).
  - Plain `requests`/urllib3 has a TLS handshake fingerprint that doesn't
    match real Chrome even when headers claim to be Chrome. If cookie/header
    replay alone stops being enough, that fingerprint mismatch is the next
    thing to suspect.
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
    session file is configured/found. Missing file is a warning, not an
    error -- request-building/pagination logic should still be testable
    without real secrets on disk (e.g. in CI).
    """
    path = Path(path) if path else settings.bbb_session_file
    if not path.exists():
        logger.warning(
            "No BBB session file at %s -- requests will go out with no "
            "site cookies and will likely get Cloudflare-challenged. See "
            "data/secrets/bbb_session.example.json.",
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
