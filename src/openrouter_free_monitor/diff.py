"""Snapshot diff and event detection.

Compares the most recent two snapshots from a ``SnapshotStore`` and classifies
each transition as one of:

- ``added``: model appears for the first time
- ``removed``: model disappears from the free tier
- ``expired``: model explicitly hit ``expiration_date``
- ``expiring_soon``: expiration within the configured horizon
- ``pricing_changed``: free → paid (or paid → free)
- ``expiration_set``: previously had no expiration, now has one

Each detected event is logged via ``SnapshotStore.log_event`` so downstream
alerting can rely on the events table rather than re-running diffs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from openrouter_free_monitor.store import SnapshotStore

logger = logging.getLogger(__name__)


@dataclass
class DiffEvent:
    """A single detected transition."""

    kind: str
    model_id: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffResult:
    """Aggregate result of a diff run."""

    added: list[DiffEvent] = field(default_factory=list)
    removed: list[DiffEvent] = field(default_factory=list)
    expired: list[DiffEvent] = field(default_factory=list)
    expiring_soon: list[DiffEvent] = field(default_factory=list)
    pricing_changed: list[DiffEvent] = field(default_factory=list)
    expiration_set: list[DiffEvent] = field(default_factory=list)

    @property
    def has_events(self) -> bool:
        return bool(
            self.added
            or self.removed
            or self.expired
            or self.expiring_soon
            or self.pricing_changed
            or self.expiration_set
        )

    @property
    def critical_events(self) -> list[DiffEvent]:
        """Events that should trigger immediate alerts (removed, expired, pricing_changed)."""
        return self.removed + self.expired + self.pricing_changed

    def all_events(self) -> list[DiffEvent]:
        return (
            self.added
            + self.removed
            + self.expired
            + self.expiring_soon
            + self.pricing_changed
            + self.expiration_set
        )


def diff_snapshots(
    store: SnapshotStore,
    *,
    expiration_horizon: timedelta = timedelta(days=7),
    now: datetime | None = None,
) -> DiffResult:
    """Diff the latest two snapshots stored in ``store``.

    If only one snapshot exists, returns an empty ``DiffResult`` (no diff possible).
    """
    snaps = store.list_snapshots(limit=2)
    if len(snaps) < 2:
        logger.info("Not enough snapshots to diff (have %d, need 2)", len(snaps))
        return DiffResult()

    latest_id = snaps[0].id
    previous_id = snaps[1].id
    latest = store.models_in_snapshot(latest_id)
    previous = store.models_in_snapshot(previous_id)

    latest_by_id = {m["model_id"]: m for m in latest}
    previous_by_id = {m["model_id"]: m for m in previous}

    result = DiffResult()
    check_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    # ADDED — new free model appears
    for mid in sorted(latest_by_id.keys() - previous_by_id.keys()):
        evt = DiffEvent(kind="added", model_id=mid, detail={
            "name": latest_by_id[mid].get("name"),
            "context_length": latest_by_id[mid].get("context_length"),
        })
        result.added.append(evt)
        store.log_event("added", mid, snapshot_id=latest_id, detail=evt.detail)

    # REMOVED — free model disappears (could be: paid conversion, deprecation, removal)
    for mid in sorted(previous_by_id.keys() - latest_by_id.keys()):
        prev = previous_by_id[mid]
        evt = DiffEvent(
            kind="removed",
            model_id=mid,
            detail={
                "last_seen_name": prev.get("name"),
                "last_context_length": prev.get("context_length"),
            },
        )
        result.removed.append(evt)
        store.log_event("removed", mid, snapshot_id=latest_id, detail=evt.detail)

    # CHANGED — model exists in both, inspect for transitions
    for mid in sorted(latest_by_id.keys() & previous_by_id.keys()):
        prev = previous_by_id[mid]
        curr = latest_by_id[mid]

        prev_exp = _parse_iso(prev.get("expiration_date"))
        curr_exp = _parse_iso(curr.get("expiration_date"))

        # Expiration now set (was None, now has a date)
        if not prev_exp and curr_exp:
            evt = DiffEvent(
                kind="expiration_set",
                model_id=mid,
                detail={"expiration_date": curr_exp.isoformat()},
            )
            result.expiration_set.append(evt)
            store.log_event(
                "expiration_set", mid, snapshot_id=latest_id, detail=evt.detail
            )

        # Expiring soon (within horizon AND not already past)
        if curr_exp and check_time < curr_exp <= check_time + expiration_horizon:
            days_left = (curr_exp - check_time).days
            evt = DiffEvent(
                kind="expiring_soon",
                model_id=mid,
                detail={
                    "expiration_date": curr_exp.isoformat(),
                    "days_left": days_left,
                },
            )
            result.expiring_soon.append(evt)
            store.log_event(
                "expiring_soon", mid, snapshot_id=latest_id, detail=evt.detail
            )

        # Already expired
        if curr_exp and curr_exp <= check_time:
            evt = DiffEvent(
                kind="expired",
                model_id=mid,
                detail={"expiration_date": curr_exp.isoformat()},
            )
            result.expired.append(evt)
            store.log_event(
                "expired", mid, snapshot_id=latest_id, detail=evt.detail
            )

    return result


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Always return tz-aware UTC. SQLite strips "+00:00" on read, so naive
        # datetimes from DB need reattaching the timezone.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None
