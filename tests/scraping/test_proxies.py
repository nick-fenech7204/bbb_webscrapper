import re

import pytest

from bbb_scraper.config import Settings
from bbb_scraper.scraping.proxies import build_proxy_username, get_proxies


def _cfg(**overrides) -> Settings:
    base = {
        "proxy_enabled": True,
        "proxy_host": "",
        "proxy_port": 0,
        "proxy_username": "",
        "proxy_password": "",
        "proxy_protocol": "http",
        "proxy_country": None,
        "proxy_session_prefix": "bbb",
    }
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


# --- city targeting (Decodo-confirmed 2026-09-14, see the module docstring
# for the real end-to-end verification: ip.decodo.com self-report + a real
# angi.com request both matched the requested city) -----------------------

def test_city_embeds_in_the_username():
    cfg = _cfg(proxy_username="user")
    username = build_proxy_username(cfg, city="orlando", session_id="fixed123")
    assert username == "user-city-orlando-session-fixed123"


def test_city_without_a_session_id_gets_one_auto_generated():
    """The real gotcha this project found by testing: city-targeting alone,
    with no unique session component, silently reuses whatever city the
    proxy's sticky session already resolved to -- confirmed against Decodo
    directly (3 different requested cities, identical exit IP each time)
    with no session id, then fixed by adding one. Must never be possible to
    end up with a "looks city-targeted but isn't" username."""
    cfg = _cfg(proxy_username="user")
    username = build_proxy_username(cfg, city="orlando")
    assert re.match(r"^user-city-orlando-session-\S+$", username), username


def test_city_and_session_id_together_keep_the_given_session_id():
    cfg = _cfg(proxy_username="user")
    username = build_proxy_username(cfg, city="chicago", session_id="my-explicit-id")
    assert username == "user-city-chicago-session-my-explicit-id"


def test_city_with_country_orders_country_before_city():
    cfg = _cfg(proxy_username="user", proxy_country="us")
    username = build_proxy_username(cfg, city="seattle", session_id="s1")
    assert username == "user-country-us-city-seattle-session-s1"


def test_get_proxies_city_flows_through_to_the_full_url():
    cfg = _cfg(proxy_host="gate.decodo.com", proxy_port=10000, proxy_username="user", proxy_password="pass")
    proxies = get_proxies(cfg, city="miami")
    assert re.match(
        r"^http://user-city-miami-session-\S+:pass@gate\.decodo\.com:10000$", proxies["http"]
    ), proxies["http"]


def test_no_city_no_auto_session_id():
    """Confirms the auto-generation is specifically tied to city= -- an
    ordinary call with neither city nor session_id must still produce the
    plain, unadorned username (no regression on the existing behavior)."""
    cfg = _cfg(proxy_username="user")
    assert build_proxy_username(cfg) == "user"
