"""
Raw response capture, for debugging and as the ETL "extract" system of
record.

Every fetch (search page, business page) should be saved to disk *before*
any parsing happens, so that:
  - parser development/iteration never requires re-hitting BBB
  - when BBB changes markup, you have the exact raw payload that broke
  - you can rebuild derived data later without re-scraping

Layout on disk:
    {raw_data_dir}/{kind}/{YYYY-MM-DD}/{identifier}__{hash}.{ext}

A JSONL manifest (`{raw_data_dir}/manifest.jsonl`) records one line per
capture with metadata, so you can grep/query captures without touching the
raw files themselves.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bbb_scraper.config import settings
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.utils.hashing import sha256_hex

logger = get_logger(__name__)


@dataclass
class CaptureResult:
    path: Path
    kind: str
    identifier: str
    url: str | None
    status_code: int | None
    captured_at: datetime


class RawCapture:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or settings.raw_data_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        kind: str,
        identifier: str,
        content: str,
        ext: str = "html",
        url: str | None = None,
        status_code: int | None = None,
    ) -> CaptureResult:
        now = datetime.now(timezone.utc)
        day_dir = self.base_dir / kind / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        content_hash = sha256_hex(content)
        safe_identifier = "".join(c if c.isalnum() or c in "-_." else "_" for c in identifier)[:120]
        file_path = day_dir / f"{safe_identifier}__{content_hash}.{ext}"
        file_path.write_text(content, encoding="utf-8")

        result = CaptureResult(
            path=file_path,
            kind=kind,
            identifier=identifier,
            url=url,
            status_code=status_code,
            captured_at=now,
        )
        self._append_manifest(result)
        logger.debug("Captured raw %s -> %s", kind, file_path, extra={"url": url})
        return result

    def _append_manifest(self, result: CaptureResult) -> None:
        manifest_path = self.base_dir / "manifest.jsonl"
        record = {
            "path": str(result.path),
            "kind": result.kind,
            "identifier": result.identifier,
            "url": result.url,
            "status_code": result.status_code,
            "captured_at": result.captured_at.isoformat(),
        }
        with manifest_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
