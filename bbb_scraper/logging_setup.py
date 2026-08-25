"""
Logging configuration.

Gives every module `get_logger(__name__)` with:
  - console output (human readable)
  - rotating file output under settings.log_dir (optionally JSON lines, good
    for shipping into something like ELK/CloudWatch later)

Call `configure_logging()` once, early, from any entrypoint (scripts, CLI,
pipeline runner). Importing this module never has side effects on its own.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path

from bbb_scraper.config import settings

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Allow callers to attach structured context via `extra={...}`.
        for key, value in record.__dict__.items():
            if key in payload or key in logging.LogRecord.__dict__ or key.startswith("_"):
                continue
            if key in (
                "args", "asctime", "created", "exc_text", "filename", "funcName",
                "levelno", "lineno", "module", "msecs", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info", "thread",
                "threadName", "taskName",
            ):
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            payload[key] = value
        return json.dumps(payload)


def configure_logging(level: str | None = None) -> None:
    """Idempotent: safe to call multiple times (e.g. once per script)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = getattr(logging, (level or settings.log_level).upper(), logging.INFO)
    log_dir: Path = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )
    root.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "bbb_scraper.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(
        JsonFormatter() if settings.log_json
        else logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )
    root.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
