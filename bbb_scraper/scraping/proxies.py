"""
Proxy wiring -- provider-agnostic.

Built against Decodo, but works with any provider that hands out plain
`username:password@host:port` credentials -- just fill in
PROXY_HOST/PROXY_PORT/PROXY_USERNAME/PROXY_PASSWORD in .env, no code changes
needed. Nothing here is Decodo-specific unless noted.

Country targeting: confirmed against Decodo (2026-08-31) that this is done
via HOSTNAME, not a username suffix -- e.g. `us.decodo.com` routes through a
US exit node, `gate.decodo.com` is undirected/random-country. So for Decodo,
just set PROXY_HOST to the country-specific hostname; no code here needs to
change.

**Sticky sessions can fail independently of the plain proxy, found
2026-09-14 while checking the Angi scraper before a deploy.** A bare
username (no `session-{id}` suffix at all -- what get_proxies(cfg) with no
session_id/city produces, which is *every* real BBB call site's actual
usage: HttpClient/Extractor are never constructed with a session_id
anywhere in scripts/batch_scrape_metros.py or the rest of the pipeline)
kept working fine (confirmed live, 200 from ip.decodo.com and bbb.org).
But *any* username with a `-session-{id}` suffix -- with or without a
city, a fixed id or a fresh uuid, didn't matter -- failed 3/3 with `curl:
(7) CONNECT tunnel failed, response 407`, isolated with a minimal
reproduction outside any of this project's scraping code. Read as
Decodo's sticky-session feature specifically hitting some account-level
limit (likely from a heavy day of exactly that feature: this session's own
city-targeting verification plus a real Angi run that rotated sessions
repeatedly), not a code bug and not a country/city/plan-wide outage --
worth checking the Decodo dashboard for a sticky-session-specific quota
before assuming the account is broken outright. Bottom line: BBB scraping
was never at risk (it never uses a session id at all); anything that
*does* rely on session/city targeting (bbb_scraper/angi/client.py's
rotation, or a future find_loc city sweep) should have a fallback to
plain, session-less proxying (or no proxy) ready for exactly this failure
mode, not assume the sticky-session path is always available.

**City targeting, confirmed working 2026-09-14** (from Decodo's own
dashboard, then verified for real: `ip.decodo.com/json` self-report matched
the requested city 6/6 times across Orlando/Chicago/Seattle/Miami, and
end-to-end against a real third-party site -- angi.com's IP-geolocated
"near me" pages showed exactly the requested city). Embed
`city-{slug}` (lowercase, e.g. "orlando", "chicago" -- untested so far for
multi-word city names, confirm the exact slug format before relying on one)
into the username, same idea as the generic session-suffix mechanism below
but this specific `city-` key is Decodo-specific and now verified, not a
guess. Works through the already-configured PROXY_HOST (tested against both
`us.decodo.com` and `gate.decodo.com`) -- no host/port change needed to use
it.

Cross-checked against an *independent* geolocation provider too (not just
Decodo's own self-report about itself) -- 5/5 requested cities matched
exactly via a separate lookup on the delivered IP. One honest nuance from
that same check, worth knowing before assuming pixel-perfect targeting: two
of the five landed on a real *nearby suburb* (Kent, WA for a "seattle"
request; Boynton Beach, FL for "miami") rather than the exact city -- the
independent source confirmed the IP really is there, so this isn't Decodo
mis-reporting, it's just how a residential IP pool is actually distributed
across a metro. Expect "the real metro area, usually the named city,
sometimes an immediate neighbor" -- not guaranteed exact-city precision.

**The real gotcha, found by testing, not assumed:** city targeting alone is
NOT reliable -- three back-to-back requests for three different cities
(same process, no explicit session id) all silently came back as whatever
city the *first* request resolved to, using both curl_cffi and plain
`requests` (so it's Decodo's session stickiness, not a client-library
quirk). A `sessionduration-N` suffix alone (Decodo's own dashboard example)
did NOT fix this either. What did: pairing `city-{slug}` with a *unique*
`session-{id}` per request -- then 6/6 requests landed on the right city.
So `build_proxy_username` auto-generates a session id whenever `city` is
given and no `session_id` was passed -- silently reusing the wrong city's
IP is a much worse failure mode than an unrequested extra sticky session.

Sticky sessions / country targeting via username generally: some
rotating-residential providers additionally let you control routing by
embedding options into the proxy username, e.g. `user-session-abc123-
country-us`. That path is kept here as opt-in (only applied if you pass a
session_id, set PROXY_COUNTRY, or pass city=) -- the `session`/`country`
keys themselves are still unverified as generic/portable beyond Decodo,
confirm against your own provider's docs; prefer the hostname approach
above for country-only targeting if your provider supports it (it doesn't
require username/password auth, which matters if you're relying on IP
whitelisting instead of credentials).
"""
from __future__ import annotations

import uuid

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings


def new_session_id(prefix: str | None = None) -> str:
    prefix = prefix or default_settings.proxy_session_prefix
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def build_proxy_username(
    cfg: Settings, session_id: str | None = None, city: str | None = None
) -> str:
    """`city`, if given, needs a real session id to actually take effect --
    see the module docstring's gotcha. Auto-generates one rather than
    silently producing a username that looks city-targeted but isn't."""
    if city and not session_id:
        session_id = new_session_id()

    parts = [cfg.proxy_username]
    if cfg.proxy_country:
        parts += ["country", cfg.proxy_country]
    if city:
        parts += ["city", city]
    if session_id:
        parts += ["session", session_id]
    return "-".join(parts)


def get_proxies(
    cfg: Settings | None = None, session_id: str | None = None, city: str | None = None
) -> dict[str, str]:
    """Return a `requests`-style proxies dict, or {} if proxying is disabled.

    `city` (Decodo-confirmed, e.g. "orlando", "chicago") deliberately targets
    that city's residential IP pool -- see the module docstring for the real
    gotcha (it silently does nothing without a unique session id, which this
    function adds for you automatically when `city` is set).

    PROXY_USERNAME/PROXY_PASSWORD are optional -- some providers authorize by
    whitelisting your egress IP instead of (or in addition to) username/
    password, in which case a bare `host:port` proxy URL is correct and no
    credentials belong in the URL at all. (`city` has no effect in that case
    -- it's embedded in the username, which won't exist.)
    """
    cfg = cfg or default_settings
    if not cfg.proxy_enabled:
        return {}
    if not (cfg.proxy_host and cfg.proxy_port):
        raise ValueError(
            "PROXY_ENABLED is true but PROXY_HOST/PROXY_PORT are not configured "
            "-- check your .env"
        )

    if cfg.proxy_username:
        username = build_proxy_username(cfg, session_id=session_id, city=city)
        auth = f"{username}:{cfg.proxy_password}@"
    else:
        auth = ""

    proxy_url = f"{cfg.proxy_protocol}://{auth}{cfg.proxy_host}:{cfg.proxy_port}"
    return {"http": proxy_url, "https": proxy_url}
