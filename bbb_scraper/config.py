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
    http_min_delay_seconds: float = Field(default=1.0, alias="HTTP_MIN_DELAY_SECONDS")
    http_max_delay_seconds: float = Field(default=3.0, alias="HTTP_MAX_DELAY_SECONDS")
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
    # Only used by scripts/build_us_cities.py (a one-time reference-data build,
    # not the running app) -- free signup: https://api.census.gov/data/key_signup.html
    census_api_key: str = Field(default="", alias="CENSUS_API_KEY")

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
