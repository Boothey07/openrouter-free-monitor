"""SQLite snapshot store for free-model monitoring.

Persists each ``FreeModel`` snapshot to a local SQLite DB. Provides query
helpers for the diff logic and a small schema that can be inspected from
the CLI.

Schema:
- ``snapshots``: one row per polling run (timestamp, source, model count)
- ``models``: one row per (snapshot_id, model_id) — pricing + metadata
- ``events``: append-only log of detected transitions (added/removed/changed/expired)

The store is intentionally tiny and JSON-friendly so future cron jobs can
introspect it without a custom ORM.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from openrouter_free_monitor.catalog import FreeModel

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at     TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'openrouter',
    model_count  INTEGER NOT NULL DEFAULT 0,
    free_count   INTEGER NOT NULL DEFAULT 0,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS models (
    snapshot_id    INTEGER NOT NULL,
    model_id       TEXT NOT NULL,
    name           TEXT,
    canonical_slug TEXT,
    context_length INTEGER,
    modalities     TEXT,
    top_provider   TEXT,
    created_at     TEXT,
    expiration_date TEXT,
    knowledge_cutoff TEXT,
    description    TEXT,
    raw_json       TEXT,
    PRIMARY KEY (snapshot_id, model_id),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_models_model_id ON models(model_id);
CREATE INDEX IF NOT EXISTS idx_models_expiration ON models(expiration_date)
    WHERE expiration_date IS NOT NULL;

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at  TEXT NOT NULL,
    snapshot_id  INTEGER,
    kind         TEXT NOT NULL,
    model_id     TEXT,
    detail_json  TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_model ON events(model_id);
"""


@dataclass
class SnapshotRecord:
    """One row from ``snapshots`` table."""

    id: int
    taken_at: datetime
    source: str
    model_count: int
    free_count: int
    notes: str | None


class SnapshotStore:
    """SQLite-backed snapshot store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA_SQL)

    def record_snapshot(
        self,
        free_models: list[FreeModel],
        *,
        source: str = "openrouter",
        notes: str | None = None,
        taken_at: datetime | None = None,
    ) -> int:
        """Insert a new snapshot row + one row per free model.

        Returns the ``snapshots.id`` of the inserted snapshot.
        """
        ts = (taken_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO snapshots (taken_at, source, model_count, free_count, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts.isoformat(), source, len(free_models), len(free_models), notes),
            )
            snap_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO models (snapshot_id, model_id, name, canonical_slug, "
                "context_length, modalities, top_provider, created_at, "
                "expiration_date, knowledge_cutoff, description, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        snap_id,
                        m.id,
                        m.name,
                        m.canonical_slug,
                        m.context_length,
                        json.dumps(list(m.modalities)),
                        m.top_provider,
                        m.created_at.isoformat() if m.created_at else None,
                        m.expiration_date.isoformat() if m.expiration_date else None,
                        m.knowledge_cutoff,
                        m.description,
                        json.dumps(m.raw),
                    )
                    for m in free_models
                ],
            )
        return snap_id

    def latest_snapshot(self) -> SnapshotRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM snapshots ORDER BY taken_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return _row_to_snapshot(row)

    def list_snapshots(self, limit: int = 20) -> list[SnapshotRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots ORDER BY taken_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_snapshot(r) for r in rows]

    def models_in_snapshot(self, snapshot_id: int) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM models WHERE snapshot_id = ? ORDER BY model_id",
                (snapshot_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_models_dict(self) -> dict[str, dict]:
        """Return {model_id: row_dict} for the most recent snapshot."""
        snap = self.latest_snapshot()
        if not snap:
            return {}
        rows = self.models_in_snapshot(snap.id)
        return {r["model_id"]: r for r in rows}

    def second_latest_models_dict(self) -> dict[str, dict] | None:
        """Return the previous snapshot's model map (for diff against latest)."""
        snaps = self.list_snapshots(limit=2)
        if len(snaps) < 2:
            return None
        rows = self.models_in_snapshot(snaps[1].id)
        return {r["model_id"]: r for r in rows}

    def log_event(
        self,
        kind: str,
        model_id: str | None = None,
        *,
        snapshot_id: int | None = None,
        detail: dict | None = None,
        detected_at: datetime | None = None,
    ) -> int:
        ts = (detected_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "INSERT INTO events (detected_at, snapshot_id, kind, model_id, detail_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    ts.isoformat(),
                    snapshot_id,
                    kind,
                    model_id,
                    json.dumps(detail or {}),
                ),
            )
            return int(cur.lastrowid)

    def list_events(
        self,
        *,
        kind: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses = []
        params: list = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if since:
            clauses.append("detected_at >= ?")
            params.append(since.astimezone(timezone.utc).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM events {where} ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def _row_to_snapshot(row: sqlite3.Row) -> SnapshotRecord:
    taken_at = row["taken_at"]
    if isinstance(taken_at, str):
        taken_at = datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
    return SnapshotRecord(
        id=row["id"],
        taken_at=taken_at,
        source=row["source"],
        model_count=row["model_count"],
        free_count=row["free_count"],
        notes=row["notes"],
    )
