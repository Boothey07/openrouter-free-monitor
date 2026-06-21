"""Tests for catalog.py: free-model detection and parsing."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from openrouter_free_monitor.catalog import _is_free, parse_catalog


def test_is_free_with_zero_string() -> None:
    assert _is_free({"prompt": "0", "completion": "0"}) is True


def test_is_free_with_float_zero() -> None:
    assert _is_free({"prompt": "0.0", "completion": "0.0"}) is True


def test_is_free_with_nonzero_pricing() -> None:
    assert _is_free({"prompt": "0.000003", "completion": "0"}) is False


def test_is_free_with_missing_field() -> None:
    assert _is_free({"prompt": "0"}) is False
    assert _is_free({}) is False
    assert _is_free(None) is False


def test_parse_catalog_filters_to_free_only(sample_catalog_payload) -> None:
    snap_at = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    free = parse_catalog(sample_catalog_payload, snapshot_at=snap_at)
    # 3 free models in fixture, 1 paid — paid must be excluded
    assert len(free) == 3
    ids = {m.id for m in free}
    assert "paid/model-x" not in ids
    assert "free/model-a:free" in ids
    assert "free/model-b:free" in ids
    assert "free/model-expiring-soon:free" in ids


def test_parse_catalog_preserves_expiration_date(sample_catalog_payload) -> None:
    snap_at = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    free = parse_catalog(sample_catalog_payload, snapshot_at=snap_at)
    by_id = {m.id: m for m in free}
    assert by_id["free/model-b:free"].expiration_date is not None
    assert by_id["free/model-b:free"].expiration_date.year == 2026
    assert by_id["free/model-b:free"].expiration_date.month == 12
    # model-a has no expiration
    assert by_id["free/model-a:free"].has_expiration is False


def test_parse_catalog_captures_modalities(sample_catalog_payload) -> None:
    snap_at = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    free = parse_catalog(sample_catalog_payload, snapshot_at=snap_at)
    by_id = {m.id: m for m in free}
    assert "image" in by_id["free/model-b:free"].modalities
    assert "text" in by_id["free/model-a:free"].modalities


def test_parse_catalog_handles_unix_timestamp_created(sample_catalog_payload) -> None:
    snap_at = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    free = parse_catalog(sample_catalog_payload, snapshot_at=snap_at)
    # model-a fixture has created=1735689600 (unix) → should parse to a real datetime
    by_id = {m.id: m for m in free}
    assert isinstance(by_id["free/model-a:free"].created_at, datetime)
    assert by_id["free/model-a:free"].created_at.year == 2025


def test_parse_catalog_results_sorted_by_id(sample_catalog_payload) -> None:
    snap_at = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    free = parse_catalog(sample_catalog_payload, snapshot_at=snap_at)
    ids = [m.id for m in free]
    assert ids == sorted(ids)
