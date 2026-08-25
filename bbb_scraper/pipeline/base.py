"""
Sink interface: the seam between "we have clean records" and "records go
somewhere". The ETL pipeline (etl/pipeline.py) only ever talks to this
interface, never to a specific destination -- that's what keeps the scraper
decoupled from wherever the data eventually lands (CSV today, SQL + Power BI
+ an enrichment API tomorrow, all at once if you want).

To add a new destination: subclass Sink, implement `load`, and register it
in pipeline/registry.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Sink(ABC):
    name: str = "sink"

    @abstractmethod
    def load(self, records: list[dict[str, Any]]) -> int:
        """Persist records to this destination. Returns the number written."""
        raise NotImplementedError
