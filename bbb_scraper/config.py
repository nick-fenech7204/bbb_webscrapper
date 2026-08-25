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

    # --- Proxy (IPRoyal) -------------------------------------------------
    proxy_enabled: bool = Field(default=True, alias="PROXY_ENABLED")
    iproyal_host: str = Field(default="", alias="IPROYAL_HOST")
    iproyal_port: int = Field(default=0, alias="IPROYAL_PORT")
    iproyal_username: str = Field(default="", alias="IPROYAL_USERNAME")
    iproyal_password: str = Field(default="", alias="IPROYAL_PASSWORD")
    iproyal_protocol: str = Field(default="http", alias="IPROYAL_PROTOCOL")
    iproyal_country: str | None = Field(default=None, alias="IPROYAL_COUNTRY")
    iproyal_session_prefix: str = Field(default="bbb", alias="IPROYAL_SESSION_PREFIX")

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

    # --- Storage -------------------------------------------------------------
    raw_data_dir: Path = Field(default=Path("data/raw"), alias="RAW_DATA_DIR")
    processed_data_dir: Path = Field(default=Path("data/processed"), alias="PROCESSED_DATA_DIR")

    # --- Reference data -----------------------------------------------------
    categories_file: Path = Field(
        default=Path("data/reference/categories.json"), alias="CATEGORIES_FILE"
    )

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
