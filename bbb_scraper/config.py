"""
Central configuration.

All settings are pulled from environment variables (optionally loaded from a
.env file via python-dotenv). Nothing secret is hardcoded here -- see
.env.example for the full list of variables and sane defaults.

Import `settings` from this module everywhere else in the codebase instead of
calling os.environ directly, so there's exactly one place that knows how
configuration is sourced.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Proxy ---------------------------------------------------------------
    # Provider-agnostic: works with Decodo, IPRoyal, or anything else that
    # hands out a plain host/port + username/password. Point these at
    # whichever provider you're using -- no code changes needed to switch.
    proxy_enabled: bool = Field(default=True, alias="PROXY_ENABLED")
    proxy_host: str = Field(default="", alias="PROXY_HOST")
    proxy_port: int = Field(default=0, alias="PROXY_PORT")
    proxy_username: str = Field(default="", alias="PROXY_USERNAME")
    proxy_password: str = Field(default="", alias="PROXY_PASSWORD")
    proxy_protocol: str = Field(default="http", alias="PROXY_PROTOCOL")
    # Optional: some rotating-residential providers (IPRoyal, Decodo, others)
    # support embedding sticky-session / country targeting into the
    # username, e.g. "user-session-abc123-country-us". Leave unset unless
    # you've confirmed your provider uses this convention.
    proxy_country: str | None = Field(default=None, alias="PROXY_COUNTRY")
    proxy_session_prefix: str = Field(default="bbb", alias="PROXY_SESSION_PREFIX")

    # --- HTTP client -------------------------------------------------------
    http_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        alias="HTTP_USER_AGENT",
    )
    http_timeout_seconds: float = Field(default=20.0, alias="HTTP_TIMEOUT_SECONDS")
    http_max_retries: int = Field(default=3, alias="HTTP_MAX_RETRIES")
    http_backoff_factor: float = Field(default=1.5, alias="HTTP_BACKOFF_FACTOR")
    http_min_delay_seconds: float = Field(default=0.1, alias="HTTP_MIN_DELAY_SECONDS")
    http_max_delay_seconds: float = Field(default=0.2, alias="HTTP_MAX_DELAY_SECONDS")
    # Browser TLS/HTTP2 fingerprint to impersonate via curl_cffi (see
    # scraping/client.py) -- confirmed 2026-09-02 this is what actually gets
    # past Cloudflare on BBB business-profile pages, where cookie/header
    # replay alone wasn't enough. Empty string disables impersonation
    # (falls back to curl_cffi's default handshake, not a real requests/
    # urllib3 one -- there's currently no path back to plain `requests`).
    http_impersonate: str = Field(default="chrome150", alias="HTTP_IMPERSONATE")

    # --- Storage -------------------------------------------------------------
    raw_data_dir: Path = Field(default=Path("data/raw"), alias="RAW_DATA_DIR")
    processed_data_dir: Path = Field(default=Path("data/processed"), alias="PROCESSED_DATA_DIR")

    # --- Reference data -----------------------------------------------------
    categories_file: Path = Field(
        default=Path("data/reference/categories.json"), alias="CATEGORIES_FILE"
    )
    us_cities_file: Path = Field(
        default=Path("data/reference/us_cities.csv"), alias="US_CITIES_FILE"
    )
    metros_file: Path = Field(
        default=Path("data/reference/metros.json"), alias="METROS_FILE"
    )
    # Angi's own companylist category taxonomy (167 entries, confirmed
    # 2026-09-14 global/canonical across cities) -- used to seed the
    # Streamlit industry picker so a chosen category is guaranteed to also
    # resolve on Angi. See data/reference/README.md and
    # scripts/fetch_angi_categories.py.
    angi_categories_file: Path = Field(
        default=Path("data/reference/angi_categories.json"), alias="ANGI_CATEGORIES_FILE"
    )
    # Only used by scripts/build_us_cities.py (a one-time reference-data build,
    # not the running app) -- free signup: https://api.census.gov/data/key_signup.html
    census_api_key: str = Field(default="", alias="CENSUS_API_KEY")

    # --- Yelp Fusion API ---------------------------------------------------
    # Official API (api.yelp.com), bearer-token auth -- NOT the same as
    # scraping yelp.com (which is DataDome-protected and blocks on request 1,
    # confirmed 2026-09-10). This is the sanctioned path. Free "Starter" tier
    # is quota-limited (the exact number comes back in every response's
    # RateLimit-* headers -- don't hardcode an assumption). Get a key at
    # https://www.yelp.com/developers/v3/manage_app
    yelp_api_key: str = Field(default="", alias="YELP_API_KEY")
    yelp_api_base_url: str = Field(
        default="https://api.yelp.com/v3", alias="YELP_API_BASE_URL"
    )
    # Yelp's API is authenticated and un-proxied on purpose -- routing it
    # through the residential proxy (which we need for bbb.org) would only
    # obscure an identified caller. Separate, gentler pacing than the
    # scraper's since there's no bot-detection to out-wait, just a quota.
    yelp_min_delay_seconds: float = Field(default=0.5, alias="YELP_MIN_DELAY_SECONDS")
    yelp_max_delay_seconds: float = Field(default=1.0, alias="YELP_MAX_DELAY_SECONDS")

    # --- Dead-website check (bbb_scraper/webcheck, scripts/check_dead_websites.py) --
    # Checks a business's own listed website, not BBB or Yelp -- a different
    # host per business, so no proxy (nothing to evade) and a bigger, browser-
    # realistic impersonated request (curl_cffi, like the BBB client) rather
    # than plain `requests`, specifically so a small site's basic bot-check
    # doesn't get misread as "dead" when it's just not a browser.
    webcheck_timeout_seconds: float = Field(default=10.0, alias="WEBCHECK_TIMEOUT_SECONDS")
    webcheck_max_workers: int = Field(default=10, alias="WEBCHECK_MAX_WORKERS")
    # How long a cached result is trusted before a URL gets re-checked --
    # sites don't flip dead/alive often enough to re-check every run.
    webcheck_cache_ttl_days: int = Field(default=30, alias="WEBCHECK_CACHE_TTL_DAYS")

    # --- Angi scraping (bbb_scraper/angi) ---------------------------------------
    # Unlike BBB, no bot-wall/challenge has been observed against angi.com --
    # but real HTTP 429s did show up in a real run at 0.4-0.8s pacing combined
    # with parsing.py's own up-to-3x-per-business retry (see client.py's
    # module docstring for *why* multiple attempts per business are needed).
    # Slowed down in response, not pushed through -- see the ethical-scraping-
    # boundary practice this project holds to.
    angi_base_url: str = Field(default="https://www.angi.com", alias="ANGI_BASE_URL")
    angi_timeout_seconds: float = Field(default=20.0, alias="ANGI_TIMEOUT_SECONDS")
    angi_max_retries: int = Field(default=3, alias="ANGI_MAX_RETRIES")
    angi_min_delay_seconds: float = Field(default=1.5, alias="ANGI_MIN_DELAY_SECONDS")
    angi_max_delay_seconds: float = Field(default=3.0, alias="ANGI_MAX_DELAY_SECONDS")
    # How many requests one proxy session (one exit IP) carries before
    # AngiClient swaps in a fresh session id -- spreads a run's volume
    # across several residential IPs rather than concentrating it on one.
    angi_proxy_rotate_every: int = Field(default=15, alias="ANGI_PROXY_ROTATE_EVERY")

    # --- MapQuest search (bbb_scraper/mapquest) ---------------------------------
    # Not scraping mapquest.com's rendered pages -- this calls the same
    # unauthenticated GraphQL API its own frontend calls (confirmed 2026-09-15
    # from a real captured browser request), which resolves a business name +
    # approximate coordinates straight to its MapQuest listing *and* its
    # Yelp-sourced reviews (real text, exact date, rating, reviewer) in one
    # response -- no separate page fetch needed, unlike BBB's own review
    # sub-page.
    #
    # 2026-09-15: proved out first at conservative pacing (100 real requests,
    # zero failures/429s -- see scripts/fetch_mapquest_reviews.py's real
    # sample-metro run), then Nick's explicit call for the full batch
    # integration: proxied + rotated (same shape as Angi's own client) instead
    # of a deliberate delay between requests -- running one metro's businesses
    # through this sequentially already spaces real requests out with real
    # network latency, and spreading them across rotating exit IPs is the
    # more useful politeness lever here than an *additional* sleep on top of
    # that, the same reasoning bbb_scraper/angi/client.py already uses for
    # its own rotation.
    mapquest_graphql_url: str = Field(
        default="https://graphql-42a6517.aws.mapquest.com/", alias="MAPQUEST_GRAPHQL_URL"
    )
    mapquest_timeout_seconds: float = Field(default=15.0, alias="MAPQUEST_TIMEOUT_SECONDS")
    mapquest_max_retries: int = Field(default=3, alias="MAPQUEST_MAX_RETRIES")
    mapquest_min_delay_seconds: float = Field(default=0.0, alias="MAPQUEST_MIN_DELAY_SECONDS")
    mapquest_max_delay_seconds: float = Field(default=0.0, alias="MAPQUEST_MAX_DELAY_SECONDS")
    mapquest_proxy_rotate_every: int = Field(default=15, alias="MAPQUEST_PROXY_ROTATE_EVERY")

    # --- Static site deployment (scripts/deploy_site.py only) -----------------
    # Not used by the running app itself -- only by the deploy script, which
    # shells out to the AWS CLI (never handles credentials directly; the CLI
    # reads its own local `aws configure` setup). See site/DEPLOY.md's
    # "Automating updates" section.
    aws_s3_bucket: str = Field(default="", alias="AWS_S3_BUCKET")
    aws_cloudfront_distribution_id: str = Field(default="", alias="AWS_CLOUDFRONT_DISTRIBUTION_ID")

    # --- BBB request settings -------------------------------------------------
    # Session cookies/headers captured from a real browser (gitignored, never
    # committed) -- see bbb_scraper/scraping/session.py.
    bbb_session_file: Path = Field(
        default=Path("data/secrets/bbb_session.json"), alias="BBB_SESSION_FILE"
    )
    bbb_search_url: str = Field(
        default="https://www.bbb.org/api/search", alias="BBB_SEARCH_URL"
    )
    bbb_find_country: str = Field(default="USA", alias="BBB_FIND_COUNTRY")
    # BBB caps search results at 300 (15 pages x 20) per (query, location).
    # Getting more than that for a broad query is a later problem: overlap
    # multiple narrower searches and dedupe -- not handled here yet.
    bbb_max_search_pages: int = Field(default=15, alias="BBB_MAX_SEARCH_PAGES")

    # --- Logging ---------------------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_dir: Path = Field(default=Path("logs"), alias="LOG_DIR")
    log_json: bool = Field(default=False, alias="LOG_JSON")

    # --- Output sinks ------------------------------------------------------------
    output_sinks: str = Field(default="csv", alias="OUTPUT_SINKS")
    csv_output_path: Path = Field(
        default=Path("data/processed/businesses.csv"), alias="CSV_OUTPUT_PATH"
    )
    excel_output_path: Path = Field(
        default=Path("data/processed/businesses.xlsx"), alias="EXCEL_OUTPUT_PATH"
    )
    sql_database_url: str | None = Field(default=None, alias="SQL_DATABASE_URL")
    sql_table_name: str = Field(default="bbb_businesses", alias="SQL_TABLE_NAME")
    http_sink_url: str | None = Field(default=None, alias="HTTP_SINK_URL")

    @property
    def output_sink_names(self) -> list[str]:
        return [s.strip() for s in self.output_sinks.split(",") if s.strip()]


settings = Settings()
