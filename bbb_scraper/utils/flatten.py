"""Shared helper for any destination that can only hold flat, scalar values
-- CSV cells, SQL columns, Excel cells, a dataframe rendered in a UI, ...

Pulled out of pipeline/sinks/csv_sink.py once a second consumer (the
Streamlit UI) needed the exact same behavior -- better one shared, tested
implementation than two that could quietly drift apart.
"""
from __future__ import annotations

import json
from typing import Any


def flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    """JSON-encode any list/dict value in `record` so a flat destination
    gets real, parseable JSON (`["Plumbers", "HVAC"]`) instead of Python's
    str() repr (`"['Plumbers', 'HVAC']"` -- looks similar, isn't valid JSON).
    Scalar values pass through unchanged.
    """
    return {
        key: json.dumps(value, default=str) if isinstance(value, (list, dict)) else value
        for key, value in record.items()
    }
