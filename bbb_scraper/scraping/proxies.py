"""
IPRoyal proxy wiring.

IPRoyal (and most residential proxy providers) let you control routing by
encoding options into the proxy *username*, e.g.:

    username-country-us-session-abc123-lifetime-10m

The exact tokens (`country`, `session`, `lifetime`, ...) depend on which
IPRoyal product you're on (residential / rotating / static). Verify the
precise syntax against your IPRoyal dashboard -- `build_iproyal_username`
below implements the common pattern and is the one place to adjust if yours
differs.

Sessions: pass a `session_id` to pin the same exit IP across a sequence of
requests (useful for a search -> business-page click-through that should look
like one visitor), or leave it None to let IPRoyal rotate freely per request.
"""
from __future__ import annotations

import uuid

from bbb_scraper.config import Settings, settings as default_settings


def new_session_id(prefix: str | None = None) -> str:
    prefix = prefix or default_settings.iproyal_session_prefix
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def build_iproyal_username(cfg: Settings, session_id: str | None = None) -> str:
    parts = [cfg.iproyal_username]
    if cfg.iproyal_country:
        parts += ["country", cfg.iproyal_country]
    if session_id:
        parts += ["session", session_id]
    return "-".join(parts)


def get_proxies(cfg: Settings | None = None, session_id: str | None = None) -> dict[str, str]:
    """Return a `requests`-style proxies dict, or {} if proxying is disabled."""
    cfg = cfg or default_settings
    if not cfg.proxy_enabled:
        return {}
    if not (cfg.iproyal_host and cfg.iproyal_port and cfg.iproyal_username):
        raise ValueError(
            "PROXY_ENABLED is true but IPROYAL_HOST/IPROYAL_PORT/IPROYAL_USERNAME "
            "are not fully configured -- check your .env"
        )

    username = build_iproyal_username(cfg, session_id=session_id)
    auth = f"{username}:{cfg.iproyal_password}"
    proxy_url = f"{cfg.iproyal_protocol}://{auth}@{cfg.iproyal_host}:{cfg.iproyal_port}"
    return {"http": proxy_url, "https": proxy_url}
