"""Command-line interface for openrouter-free-monitor.

Subcommands:
- ``init``      Initialize the local SQLite store
- ``poll``      Fetch the catalog, record a snapshot, run diff + alerts
- ``status``    Show the latest snapshot summary
- ``diff``      Show events between the latest two snapshots
- ``health``    Probe all free models in the latest snapshot
- ``events``    List logged events (kind, since)
- ``config``    Print resolved config and exit

All subcommands respect environment variables (see ``openrouter_free_monitor.config``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openrouter_free_monitor import __version__
from openrouter_free_monitor.alerts import (
    format_diff_alert,
    format_health_digest,
    send_alerts,
)
from openrouter_free_monitor.catalog import fetch_free_models
from openrouter_free_monitor.config import Config, load_config
from openrouter_free_monitor.diff import diff_snapshots
from openrouter_free_monitor.health import aggregate_status, probe_models
from openrouter_free_monitor.store import SnapshotStore

logger = logging.getLogger("openrouter_free_monitor")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="openrouter-fm",
        description="Monitor OpenRouter free models, catch expirations and removals.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase logging verbosity"
    )
    p.add_argument(
        "--db",
        default=os.environ.get(
            "OPENROUTER_FM_DB",
            str(Path.home() / ".local" / "share" / "openrouter-free-monitor" / "snapshots.db"),
        ),
        help="Path to the SQLite store (default: ~/.local/share/openrouter-free-monitor/snapshots.db)",
    )
    p.add_argument(
        "--no-alert", action="store_true", help="Don't send Telegram alerts (silent mode)"
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Initialize the local SQLite store")

    poll = sub.add_parser("poll", help="Fetch catalog, snapshot, diff, alert")
    poll.add_argument(
        "--expiration-horizon-days",
        type=int,
        default=7,
        help="Alert on models expiring within this many days (default 7)",
    )
    poll.add_argument(
        "--probe",
        action="store_true",
        help="Probe each free model with a 1-token health check after polling",
    )
    poll.add_argument(
        "--probe-delay",
        type=float,
        default=0.5,
        help="Seconds between probes (default 0.5)",
    )

    sub.add_parser("status", help="Show the latest snapshot summary")

    diff = sub.add_parser("diff", help="Show events between latest two snapshots")
    diff.add_argument(
        "--expiration-horizon-days",
        type=int,
        default=7,
        help="Horizon for 'expiring_soon' (default 7)",
    )

    health = sub.add_parser("health", help="Probe all free models in the latest snapshot")
    health.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between probes (default 0.5)"
    )

    events = sub.add_parser("events", help="List logged events")
    events.add_argument("--kind", help="Filter by event kind")
    events.add_argument("--since-hours", type=int, help="Only events from last N hours")
    events.add_argument("--limit", type=int, default=50)

    sub.add_parser("config", help="Print resolved config and exit")

    return p


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING - 10 * min(verbose, 2)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def cmd_init(args: argparse.Namespace, cfg: Config) -> int:
    SnapshotStore(args.db)  # creates the schema
    print(f"Initialized SQLite store at {args.db}")
    return 0


def cmd_poll(args: argparse.Namespace, cfg: Config) -> int:
    store = SnapshotStore(args.db)
    logger.info("Fetching free model catalog from %s", cfg.base_url)
    free_models = fetch_free_models(api_key=cfg.api_key, base_url=cfg.base_url)
    snap_id = store.record_snapshot(free_models, source="openrouter")
    print(
        f"Snapshot {snap_id}: recorded {len(free_models)} free models "
        f"({len({m.id for m in free_models})} unique ids)"
    )

    horizon = timedelta(days=args.expiration_horizon_days)
    diff = diff_snapshots(store, expiration_horizon=horizon)
    alerts = format_diff_alert(diff)

    if args.probe:
        model_ids = [m.id for m in free_models]
        reports = probe_models(
            model_ids,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            delay_seconds=args.probe_delay,
        )
        alerts.append(format_health_digest(reports))

    if not args.no_alert and alerts:
        sent = send_alerts(alerts, severity_threshold="info")
        print(f"Sent {sent}/{len(alerts)} alert(s)")
    else:
        for a in alerts:
            print(f"[{a.severity.upper()}] {a.title}\n{a.body}\n")

    return 0


def cmd_status(args: argparse.Namespace, cfg: Config) -> int:
    store = SnapshotStore(args.db)
    snap = store.latest_snapshot()
    if not snap:
        print("No snapshots recorded yet — run `openrouter-fm poll` first.")
        return 0
    models = store.models_in_snapshot(snap.id)
    expiring = [m for m in models if m.get("expiration_date")]
    print(f"Snapshot #{snap.id} — taken {snap.taken_at.isoformat()}")
    print(f"  source: {snap.source}")
    print(f"  free models: {len(models)}")
    print(f"  with explicit expiration_date: {len(expiring)}")
    print()
    print(f"{'MODEL ID':<55} {'CTX':>8} {'PROVIDER':<18} EXPIRES")
    print("-" * 100)
    for m in models:
        expires = m.get("expiration_date") or "-"
        ctx = m.get("context_length") or 0
        prov = (m.get("top_provider") or "?")[:18]
        print(f"{m['model_id']:<55} {ctx:>8} {prov:<18} {expires}")
    return 0


def cmd_diff(args: argparse.Namespace, cfg: Config) -> int:
    store = SnapshotStore(args.db)
    horizon = timedelta(days=args.expiration_horizon_days)
    diff = diff_snapshots(store, expiration_horizon=horizon)
    if not diff.has_events:
        print("No events between latest two snapshots.")
        return 0
    print(f"Added ({len(diff.added)}):")
    for evt in diff.added:
        print(f"  + {evt.model_id}")
    print(f"\nRemoved ({len(diff.removed)}):")
    for evt in diff.removed:
        print(f"  - {evt.model_id}")
    print(f"\nExpired ({len(diff.expired)}):")
    for evt in diff.expired:
        print(f"  ! {evt.model_id} (was: {evt.detail.get('expiration_date')})")
    print(f"\nExpiring soon ({len(diff.expiring_soon)}):")
    for evt in diff.expiring_soon:
        print(
            f"  ~ {evt.model_id} — days_left={evt.detail.get('days_left')}, "
            f"expires={evt.detail.get('expiration_date')}"
        )
    print(f"\nExpiration newly set ({len(diff.expiration_set)}):")
    for evt in diff.expiration_set:
        print(f"  ? {evt.model_id} — {evt.detail.get('expiration_date')}")
    return 0


def cmd_health(args: argparse.Namespace, cfg: Config) -> int:
    store = SnapshotStore(args.db)
    snap = store.latest_snapshot()
    if not snap:
        print("No snapshots — run `openrouter-fm poll` first.")
        return 1
    models = store.models_in_snapshot(snap.id)
    if not models:
        print("No free models recorded in latest snapshot.")
        return 0
    model_ids = [m["model_id"] for m in models]
    print(f"Probing {len(model_ids)} free models...")
    reports = probe_models(
        model_ids,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        delay_seconds=args.delay,
    )
    counts = aggregate_status(reports)
    print("\nAggregate:")
    for status, count in counts.items():
        if count:
            print(f"  {status:<20} {count}")
    print("\nPer-model:")
    for r in reports:
        marker = "✅" if r.status == "healthy" else "❌"
        print(f"  {marker} {r.model_id:<55} {r.status:<20} {r.latency_ms}ms")
    return 0


def cmd_events(args: argparse.Namespace, cfg: Config) -> int:
    store = SnapshotStore(args.db)
    since = None
    if args.since_hours:
        since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    events = store.list_events(kind=args.kind, since=since, limit=args.limit)
    if not events:
        print("No events found.")
        return 0
    for e in events:
        print(f"{e['detected_at']}  [{e['kind']:<15}]  {e['model_id'] or '-'}")
    return 0


def cmd_config(args: argparse.Namespace, cfg: Config) -> int:
    # Don't print secrets
    safe = {
        "api_key_set": bool(cfg.api_key),
        "api_key_prefix": (cfg.api_key[:7] + "...") if cfg.api_key else None,
        "base_url": cfg.base_url,
        "telegram_bot_token_set": bool(cfg.telegram_bot_token),
        "telegram_chat_id": cfg.telegram_chat_id,
        "db_path": args.db,
    }
    print(json.dumps(safe, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config()

    handlers = {
        "init": cmd_init,
        "poll": cmd_poll,
        "status": cmd_status,
        "diff": cmd_diff,
        "health": cmd_health,
        "events": cmd_events,
        "config": cmd_config,
    }
    handler = handlers.get(args.cmd)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
