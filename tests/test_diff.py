"""Tests for diff.py: snapshot event detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openrouter_free_monitor.catalog import FreeModel, parse_catalog
from openrouter_free_monitor.diff import diff_snapshots
from openrouter_free_monitor.store import SnapshotStore


def _build_model(mid: str, *, expires: str | None = None) -> dict:
    """Construct a raw OpenRouter-shaped dict for one free model."""
    raw = {
        "id": mid,
        "canonical_slug": mid,
        "name": mid,
        "created": 1735689600,
        "description": "diff test",
        "context_length": 8192,
        "architecture": {"modality": "text->text", "input_modalities": ["text"], "output_modalities": ["text"]},
        "pricing": {"prompt": "0", "completion": "0"},
        "top_provider": {"name": "Test"},
        "expiration_date": expires,
        "knowledge_cutoff": None,
    }
    return raw


def _record(store: SnapshotStore, models: list[dict], taken_at) -> int:
    parsed = parse_catalog({"data": models}, snapshot_at=taken_at)
    return store.record_snapshot(parsed, taken_at=taken_at)


def test_diff_no_change(tmp_store_path) -> None:
    store = SnapshotStore(tmp_store_path)
    t = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    _record(store, [_build_model("a"), _build_model("b")], t)
    _record(store, [_build_model("a"), _build_model("b")], t + timedelta(hours=1))
    diff = diff_snapshots(store, now=t + timedelta(hours=1))
    assert not diff.has_events


def test_diff_detects_added_model(tmp_store_path) -> None:
    store = SnapshotStore(tmp_store_path)
    t = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    _record(store, [_build_model("a")], t)
    _record(store, [_build_model("a"), _build_model("b")], t + timedelta(hours=1))
    diff = diff_snapshots(store, now=t + timedelta(hours=1))
    assert len(diff.added) == 1
    assert diff.added[0].model_id == "b"


def test_diff_detects_removed_model(tmp_store_path) -> None:
    store = SnapshotStore(tmp_store_path)
    t = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    _record(store, [_build_model("a"), _build_model("b")], t)
    _record(store, [_build_model("a")], t + timedelta(hours=1))
    diff = diff_snapshots(store, now=t + timedelta(hours=1))
    assert len(diff.removed) == 1
    assert diff.removed[0].model_id == "b"


def test_diff_detects_expiring_soon(tmp_store_path) -> None:
    store = SnapshotStore(tmp_store_path)
    t = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    # Snapshot 1: no expiration
    _record(store, [_build_model("a")], t)
    # Snapshot 2: expiration 3 days from "now"
    expiration_iso = (t + timedelta(days=3, hours=1)).isoformat()
    _record(store, [_build_model("a", expires=expiration_iso)], t + timedelta(hours=1))
    diff = diff_snapshots(
        store,
        expiration_horizon=timedelta(days=7),
        now=t + timedelta(hours=1),
    )
    assert any(e.kind == "expiring_soon" for e in diff.all_events())
    ev = next(e for e in diff.expiring_soon if e.model_id == "a")
    assert ev.detail["days_left"] == 3


def test_diff_detects_expiration_set(tmp_store_path) -> None:
    store = SnapshotStore(tmp_store_path)
    t = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    _record(store, [_build_model("a")], t)
    expiration_iso = (t + timedelta(days=30)).isoformat()
    _record(store, [_build_model("a", expires=expiration_iso)], t + timedelta(hours=1))
    diff = diff_snapshots(store, expiration_horizon=timedelta(days=7), now=t + timedelta(hours=1))
    # Outside the 7-day horizon, so should be expiration_set, not expiring_soon
    assert any(e.kind == "expiration_set" for e in diff.all_events())
    assert all(e.kind != "expiring_soon" for e in diff.all_events())


def test_diff_insufficient_snapshots_returns_empty(tmp_store_path) -> None:
    store = SnapshotStore(tmp_store_path)
    t = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    _record(store, [_build_model("a")], t)
    diff = diff_snapshots(store, now=t + timedelta(hours=1))
    assert not diff.has_events


def test_diff_tz_naive_expiration_handled(tmp_store_path) -> None:
    """SQLite strips +00:00 from stored datetimes — diff must still compare correctly."""
    store = SnapshotStore(tmp_store_path)
    t = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    _record(store, [_build_model("a")], t)
    # Naive ISO timestamp (no tz suffix) — simulates SQLite round-trip
    expiration_iso = (t + timedelta(days=3, hours=1)).replace(tzinfo=None).isoformat()
    _record(store, [_build_model("a", expires=expiration_iso)], t + timedelta(hours=1))
    # Must not raise — used to fail with TypeError on offset-naive vs aware
    diff = diff_snapshots(
        store, expiration_horizon=timedelta(days=7), now=t + timedelta(hours=1)
    )
    assert any(e.kind == "expiring_soon" for e in diff.all_events())


def test_diff_critical_events_property(tmp_store_path) -> None:
    store = SnapshotStore(tmp_store_path)
    t = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    _record(store, [_build_model("a")], t)
    _record(store, [], t + timedelta(hours=1))  # a removed
    diff = diff_snapshots(store, now=t + timedelta(hours=1))
    assert len(diff.critical_events) == 1
    assert diff.critical_events[0].kind == "removed"
