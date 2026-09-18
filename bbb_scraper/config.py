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
    # 2026-09-17, Nick's call, same reasoning as Angi's own delay removal
    # below: sync, single-threaded, no reason to keep an artificial delay
    # on top of whatever real request/response time already exists. One
    # real difference from Angi/MapQuest worth knowing: HttpClient reuses
    # ONE Session for its whole life (see __init__ below) rather than
    # opening a fresh proxy connection per request, so it doesn't have that
    # same automatic floor from connection-teardown-and-rebuild overhead --
    # confirmed by a real, live, isolated test (logs/batch/bbb-pacing-
    # test.log, 160 real detail-page requests, Pest Control/Denver, every
    # other enrichment off): real achieved rate was ~1.45 req/s (160
    # requests in 110s), faster than Angi/MapQuest's ~0.6-1 req/s ceiling,
    # as expected given the reused connection. Also saw 2 real 403s
    # ("likely blocked/challenged") in those 160 requests (1.25%) --
    # BBB, unlike Angi, has a real, confirmed bot-wall (see
    # http_impersonate's own comment), so this is a genuine, non-zero cost
    # of the faster rate, not a hypothetical one. Both were isolated (not
    # a sustained lockout), non-fatal (that one business's data was
    # skipped, nothing else affected), and neither retried -- BlockedError
    # isn't currently in this client's retry path. Worth re-checking this
    # rate on a real production-scale run (thousands of requests, not 160)
    # before trusting the 1.25% figure precisely.
    http_min_delay_seconds: float = Field(default=0.0, alias="HTTP_MIN_DELAY_SECONDS")
    http_max_delay_seconds: float = Field(default=0.0, alias="HTTP_MAX_DELAY_SECONDS")
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
    # obscure an identified caller. No bot-detection to out-wait here, just
    # a quota (300 calls/day) -- and search_area already only makes ~5
    # calls per metro (one location+category sweep, not per-business), so
    # pacing was never the real constraint. 2026-09-17, Nick's call:
    # dropped to 0 along with the others -- the real limiter stays the
    # daily quota either way, same as before.
    yelp_min_delay_seconds: float = Field(default=0.0, alias="YELP_MIN_DELAY_SECONDS")
    yelp_max_delay_seconds: float = Field(default=0.0, alias="YELP_MAX_DELAY_SECONDS")

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
    # Slowed down in response at the time -- see the ethical-scraping-
    # boundary practice this project holds to.
    #
    # 2026-09-17, Nick's call: dropped to ~0 -- his read is that the original
    # 429s were more likely from the retry multiplication (up to 3x/business)
    # than from pacing alone, and a fresh proxied connection per request
    # already costs ~1-1.5s of real overhead on top of whatever this is set
    # to (confirmed against real logs/batch/pest-control-5-metros.log: at the
    # old 1.5-3.0s setting, actual observed gaps between requests ran
    # 3.5-3.8s), so the real achieved rate here is roughly 1 request/second,
    # not a burst pattern. Live-tested before shipping (see
    # logs/batch/angi-pacing-test-*.log) -- re-check that log or re-run this
    # test before trusting this comment if it's been a while.
    angi_base_url: str = Field(default="https://www.angi.com", alias="ANGI_BASE_URL")
    angi_timeout_seconds: float = Field(default=20.0, alias="ANGI_TIMEOUT_SECONDS")
    angi_max_retries: int = Field(default=3, alias="ANGI_MAX_RETRIES")
    angi_min_delay_seconds: float = Field(default=0.0, alias="ANGI_MIN_DELAY_SECONDS")
    angi_max_delay_seconds: float = Field(default=0.0, alias="ANGI_MAX_DELAY_SECONDS")
    # No rotate-every-N setting -- 2026-09-15, AngiClient builds a fresh,
    # bare (never sticky) proxy connection for every single request now,
    # not periodically. See bbb_scraper/angi/client.py's own module
    # docstring for the real incident (a 100% 407 failure rate) that a
    # `-session-{id}`-based "rotate every N" design caused.

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
    # sample-metro run, at the delay below). Proxy went through two real,
    # live-tested iterations the same day before landing correctly -- see
    # bbb_scraper/mapquest/client.py's own module docstring for the full
    # incident (a periodic sticky-session design 407'd 100% of requests on
    # the first real batch run; fixed by going bare + a fresh connection
    # per request, Decodo's own "rotating" mode, never sticky). No
    # rotate-every-N setting -- every request gets its own fresh proxy
    # connection now, not periodically.
    mapquest_graphql_url: str = Field(
        default="https://graphql-42a6517.aws.mapquest.com/", alias="MAPQUEST_GRAPHQL_URL"
    )
    mapquest_timeout_seconds: float = Field(default=15.0, alias="MAPQUEST_TIMEOUT_SECONDS")
    mapquest_max_retries: int = Field(default=3, alias="MAPQUEST_MAX_RETRIES")
    # 2026-09-17, Nick's call, same reasoning and same architecture as
    # Angi's own delay removal: MapQuestClient closes and rebuilds its
    # Session on every single request (see _new_session, called fresh in
    # search() below) for a new proxy exit IP, exactly like AngiClient --
    # so the same real floor from connection-teardown-and-rebuild overhead
    # applies here too, not just asserted by analogy.
    mapquest_min_delay_seconds: float = Field(default=0.0, alias="MAPQUEST_MIN_DELAY_SECONDS")
    mapquest_max_delay_seconds: float = Field(default=0.0, alias="MAPQUEST_MAX_DELAY_SECONDS")

    # --- Facebook business-page enrichment (bbb_scraper.facebook) --------------
    # Not searching -- BBB's own `socials` field (business_parser.py's
    # _map_socials) already captures a real facebook.com URL per business when
    # one exists, so this just fetches that URL directly, same "given a known
    # URL, fetch it" shape as bbb_scraper.webcheck, not the fuzzy name+location
    # matching MapQuest/Angi need. Proxied like BBB/MapQuest/Angi (not
    # webcheck) since this is the repeated-single-host pattern (facebook.com,
    # every request, across a whole batch), not webcheck's many-different-
    # hosts-once-each pattern -- see webcheck/checker.py's own module
    # docstring for why THAT one deliberately goes unproxied.
    #
    # Confirmed live, 2026-09-18, plain unauthenticated request (no personal
    # login/session cookies -- see bbb_scraper/facebook/client.py's own module
    # docstring for the full investigation) against 15 real, different
    # businesses: 14 rendered normally, 1 came back login-walled (a real page
    # state, not a bug -- see FacebookProfile.status). No rate-limit/429
    # ever observed in this small a sample -- delay defaults follow the same
    # "start conservative, only loosen after a real proved-out batch"
    # philosophy as MapQuest/Angi's own history, not copied blind.
    facebook_timeout_seconds: float = Field(default=20.0, alias="FACEBOOK_TIMEOUT_SECONDS")
    facebook_max_retries: int = Field(default=3, alias="FACEBOOK_MAX_RETRIES")
    facebook_min_delay_seconds: float = Field(default=1.0, alias="FACEBOOK_MIN_DELAY_SECONDS")
    facebook_max_delay_seconds: float = Field(default=2.5, alias="FACEBOOK_MAX_DELAY_SECONDS")
    # Same reasoning as webcheck_cache_ttl_days: a business's Facebook About
    # info/follower count last month is still probably right today, so a
    # re-run within the TTL reads from disk instead of hitting Facebook again.
    facebook_cache_ttl_days: int = Field(default=30, alias="FACEBOOK_CACHE_TTL_DAYS")

    # --- Local sentiment analysis (bbb_scraper/sentiment, Ollama) ---------------
    # A local model on Nick's own machine, not a hosted API -- no key, no
    # proxy (nothing to evade, it's a loopback call), no per-request cost.
    # Zero-shot prompting against an already-installed instruction-tuned
    # model (llama3.2, confirmed live 2026-09-15) -- no training/fine-tuning
    # involved; see bbb_scraper/sentiment/client.py's own module docstring.
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="llama3.2:latest", alias="OLLAMA_MODEL")
    # Generous on purpose: a cold model load measured ~44s in real testing;
    # once warm, real calls run ~2-4s regardless of review length. This
    # timeout has to cover the worst case (a cold first call), not the
    # typical case.
    ollama_timeout_seconds: float = Field(default=90.0, alias="OLLAMA_TIMEOUT_SECONDS")
    ollama_max_retries: int = Field(default=2, alias="OLLAMA_MAX_RETRIES")

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
    # BBB's real pageSize (confirmed against captured responses, see
    # bbb_scraper/parsing/search_parser.py) is 15, not a round 20 -- this
    # caps a (query, location) search at 15 pages x its real page size
    # (225 today), read dynamically off the response rather than assumed.
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
