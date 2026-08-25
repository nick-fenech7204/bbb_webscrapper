"""
Build the list of active Sink instances from settings.OUTPUT_SINKS
(comma-separated names). This is the one place that maps a config string to
an actual destination -- adding a new destination means adding one entry
here plus the Sink subclass itself.
"""
from __future__ import annotations

from bbb_scraper.config import Settings, settings as default_settings
from bbb_scraper.pipeline.base import Sink
from bbb_scraper.pipeline.sinks.csv_sink import CSVSink
from bbb_scraper.pipeline.sinks.excel_sink import ExcelSink
from bbb_scraper.pipeline.sinks.http_sink import HTTPSink
from bbb_scraper.pipeline.sinks.json_sink import JSONSink
from bbb_scraper.pipeline.sinks.null_sink import NullSink
from bbb_scraper.pipeline.sinks.sql_sink import SQLSink


def build_sink(name: str, cfg: Settings) -> Sink:
    if name == "csv":
        return CSVSink(cfg.csv_output_path)
    if name == "excel":
        return ExcelSink(cfg.excel_output_path)
    if name == "sql":
        return SQLSink(cfg.sql_database_url, cfg.sql_table_name)
    if name == "json":
        return JSONSink(cfg.processed_data_dir / "businesses.jsonl")
    if name == "http":
        return HTTPSink(cfg.http_sink_url)
    if name == "null":
        return NullSink()
    raise ValueError(f"Unknown sink name: {name!r} (see pipeline/registry.py)")


def build_sinks_from_settings(cfg: Settings | None = None) -> list[Sink]:
    cfg = cfg or default_settings
    return [build_sink(name, cfg) for name in cfg.output_sink_names]
