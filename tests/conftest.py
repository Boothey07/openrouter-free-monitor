"""Shared test fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_catalog_payload() -> dict[str, Any]:
    """A small OpenRouter /models payload with both free and paid models."""
    return json.loads((FIXTURES_DIR / "sample_catalog.json").read_text())


@pytest.fixture
def tmp_store_path(tmp_path: Path) -> Path:
    """A scratch SQLite path for tests."""
    return tmp_path / "snapshots.db"
