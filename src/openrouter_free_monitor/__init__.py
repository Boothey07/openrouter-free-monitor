"""OpenRouter Free Model Monitor.

A standalone tool that polls the OpenRouter catalog, snapshots free models,
detects expirations and removals, and alerts before routing chains break.
"""
from openrouter_free_monitor.catalog import FreeModel, fetch_catalog, parse_catalog
from openrouter_free_monitor.diff import DiffResult, diff_snapshots
from openrouter_free_monitor.store import SnapshotStore

__version__ = "0.1.0"

__all__ = [
    "FreeModel",
    "fetch_catalog",
    "parse_catalog",
    "DiffResult",
    "diff_snapshots",
    "SnapshotStore",
    "__version__",
]
