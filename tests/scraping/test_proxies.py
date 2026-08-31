import pytest

from bbb_scraper.config import Settings
from bbb_scraper.scraping.proxies import get_proxies


def _cfg(**overrides) -> Settings:
    base = dict(
        proxy_enabled=True,
        proxy_host="",
        proxy_port=0,
        proxy_username="",
        proxy_password="",
        proxy_protocol="http",
        proxy_country=None,
        proxy_session_prefix="bbb",
    )
    base.update(overrides)
    return Settings(**base)


def test_get_proxies_returns_empty_dict_when_disabled():
    cfg = _cfg(proxy_enabled=False)
    assert get_proxies(cfg) == {}


def test_get_proxies_raises_when_host_or_port_missing():
    cfg = _cfg(proxy_host="", proxy_port=0)
    with pytest.raises(ValueError):
        get_proxies(cfg)


def test_get_proxies_builds_url_with_username_password():
    cfg = _cfg(proxy_host="gate.decodo.com", proxy_port=10000, proxy_username="user", proxy_password="pass")
    proxies = get_proxies(cfg)
    assert proxies["http"] == "http://user:pass@gate.decodo.com:10000"
    assert proxies["https"] == proxies["http"]


def test_get_proxies_builds_url_without_credentials_when_whitelisted():
    """No username configured -> assume IP-whitelist auth, no creds in the URL."""
    cfg = _cfg(proxy_host="gate.decodo.com", proxy_port=10000, proxy_username="")
    proxies = get_proxies(cfg)
    assert proxies["http"] == "http://gate.decodo.com:10000"


def test_get_proxies_embeds_session_id_only_when_provided():
    cfg = _cfg(proxy_host="gate.decodo.com", proxy_port=10000, proxy_username="user", proxy_password="pass")
    proxies = get_proxies(cfg, session_id="abc123")
    assert proxies["http"] == "http://user-session-abc123:pass@gate.decodo.com:10000"
