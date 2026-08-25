from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> str:
        path = FIXTURES_DIR / name
        return path.read_text(encoding="utf-8")

    return _load
