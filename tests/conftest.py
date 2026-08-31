from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> str:
        path = FIXTURES_DIR / name
        return path.read_text(encoding="utf-8")

    return _load


@pytest.fixture
def load_json_fixture(load_fixture):
    def _load(name: str) -> Any:
        return json.loads(load_fixture(name))

    return _load
