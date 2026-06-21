"""Tests for store.py: snapshot persistence and querying."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from openrouter_free_monitor.catalog import parse_catalog
from openrouter_free_monitor.store import SnapshotStore


def _sample_free_models(sample_catalog_payload, snap_at):
    return parse_catalog(sample_catalog_payload, snapshot_at=snap_at)


def test_init_creates_schema(tmp_store_path) -> None:
    store = SnapshotStore(tmp_store_path)
    assert tmp_store_path.exists()
    # Should be queryable: latest_snapshot returns None on empty
    assert store.latest_snapshot() is None


def test_record_snapshot_returns_id(tmp_store_path, sample_catalog_payload) -> None:
    store = SnapshotStore(tmp_store_path)
    snap_at = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    models = _sample_free_models(sample_catalog_payload, snap_at)
    snap_id = store.record_snapshot(models, taken_at=snap_at)
    assert snap_id >= 1
    snap = store.latest_snapshot()
    assert snap is not None
    assert snap.id == snap_id
    assert snap.free_count == len(models)


def test_models_in_snapshot_returns_correct_rows(
    tmp_store_path, sample_catalog_payload
) -> None:
    store = SnapshotStore(tmp_store_path)
    snap_at = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    models = _sample_free_models(sample_catalog_payload, snap_at)
    snap_id = store.record_snapshot(models, taken_at=snap_at)
    rows = store.models_in_snapshot(snap_id)
    assert len(rows) == len(models)
    ids = {r["model_id"] for r in rows}
    assert "free/model-a:free" in ids


def test_log_event_persists(tmp_store_path) -> None:
    store = SnapshotStore(tmp_store_path)
    store.log_event("added", "free/test", detail={"ctx": 1000})
    events = store.list_events(kind="added")
    assert len(events) == 1
    assert events[0]["model_id"] == "free/test"


def test_list_snapshots_returns_newest_first(tmp_store_path, sample_catalog_payload) -> None:
    store = SnapshotStore(tmp_store_path)
    base = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    models = _sample_free_models(sample_catalog_payload, base)
    # Two snapshots with the same models
    store.record_snapshot(models, taken_at=base)
    store.record_snapshot(models, taken_at=base.replace(hour=13))
    snaps = store.list_snapshots(limit=10)
    assert len(snaps) == 2
    assert snaps[0].taken_at > snaps[1].taken_at
