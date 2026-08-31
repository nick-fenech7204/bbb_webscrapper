"""
Proxy wiring -- provider-agnostic.

Built against Decodo (gate.decodo.com), but works with any provider that
hands out plain `username:password@host:port` credentials -- just fill in
PROXY_HOST/PROXY_PORT/PROXY_USERNAME/PROXY_PASSWORD in .env, no code changes
needed. Nothing here is Decodo-specific.

Sticky sessions / country targeting: some rotating-residential providers
(IPRoyal, Decodo, others) let you control routing by embedding options into
the proxy *username*, e.g. `user-session-abc123-country-us`. That's opt-in
here (only applied if you pass a session_id or set PROXY_COUNTRY) since it's
not part of the plain auth flow and the exact syntax varies by provider --
confirm against your provider's docs before relying on it.
"""
from __future__ import annotations

import uuid

from bbb_scraper.config import Settings, settings as default_settings


def new_session_id(prefix: str | None = None) -> str:
    prefix = prefix or default_settings.proxy_session_prefix
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def build_proxy_username(cfg: Settings, session_id: str | None = None) -> str:
    parts = [cfg.proxy_username]
    if cfg.proxy_country:
        parts += ["country", cfg.proxy_country]
    if session_id:
        parts += ["session", session_id]
    return "-".join(parts)


def get_proxies(cfg: Settings | None = None, session_id: str | None = None) -> dict[str, str]:
    """Return a `requests`-style proxies dict, or {} if proxying is disabled.

    PROXY_USERNAME/PROXY_PASSWORD are optional -- some providers authorize by
    whitelisting your egress IP instead of (or in addition to) username/
    password, in which case a bare `host:port` proxy URL is correct and no
    credentials belong in the URL at all.
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
        username = build_proxy_username(cfg, session_id=session_id)
        auth = f"{username}:{cfg.proxy_password}@"
    else:
        auth = ""

    proxy_url = f"{cfg.proxy_protocol}://{auth}{cfg.proxy_host}:{cfg.proxy_port}"
    return {"http": proxy_url, "https": proxy_url}
