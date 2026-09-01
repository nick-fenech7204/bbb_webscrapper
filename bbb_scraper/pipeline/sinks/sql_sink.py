"""SQL sink. Requires the `sql` extra (`pip install -e ".[sql]"`) -- imported
lazily so the core package doesn't require SQLAlchemy.

Simple append-only insert via SQLAlchemy Core (no ORM models to maintain).
Good enough for "land raw-ish records in a table" today; swap for a proper
upsert (ON CONFLICT / MERGE) once there's a real target database and you
care about updating existing rows rather than just appending.
"""
from __future__ import annotations

from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.pipeline.base import Sink
from bbb_scraper.utils.flatten import flatten_record

logger = get_logger(__name__)


class SQLSink(Sink):
    name = "sql"

    def __init__(self, database_url: str, table_name: str):
        if not database_url:
            raise ValueError("SQLSink requires a database_url (see SQL_DATABASE_URL in .env)")
        try:
            import sqlalchemy as sa
        except ImportError as exc:
            raise ImportError(
                "SQLSink requires the 'sql' extra: pip install -e \".[sql]\""
            ) from exc

        self._sa = sa
        self.engine = sa.create_engine(database_url)
        self.table_name = table_name

    def load(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "SQLSink currently uses pandas.to_sql -- install the 'excel' extra "
                "(bundles pandas) or install pandas directly"
            ) from exc

        # JSON-encode list/dict values (categories, contacts, socials,
        # reviews_complaints, ...) -- most SQL dialects don't have a native
        # list/object type via plain to_sql.
        df = pd.DataFrame([flatten_record(r) for r in records])

        df.to_sql(self.table_name, self.engine, if_exists="append", index=False)
        logger.info("SQLSink wrote %d record(s) to table %s", len(records), self.table_name)
        return len(records)
