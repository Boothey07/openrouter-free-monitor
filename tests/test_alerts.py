"""Tests for alerts.py: formatting + Telegram delivery."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from openrouter_free_monitor.alerts import (
    format_diff_alert,
    format_health_digest,
    send_telegram,
)
from openrouter_free_monitor.diff import DiffEvent, DiffResult
from openrouter_free_monitor.health import (
    HealthReport,
    STATUS_HEALTHY,
    STATUS_NOT_FOUND,
    STATUS_RATE_LIMITED,
)


def _evt(kind: str, model_id: str, **detail) -> DiffEvent:
    return DiffEvent(kind=kind, model_id=model_id, detail=detail)


def test_format_diff_alert_no_events() -> None:
    alerts = format_diff_alert(DiffResult())
    assert alerts == []


def test_format_diff_alert_critical_only() -> None:
    diff = DiffResult(
        removed=[_evt("removed", "free/x")],
        expired=[_evt("expired", "free/y", expiration_date="2026-06-22")],
    )
    alerts = format_diff_alert(diff)
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    assert "2 critical event" in alerts[0].title


def test_format_diff_alert_separates_severities() -> None:
    diff = DiffResult(
        removed=[_evt("removed", "free/x")],
        expiring_soon=[
            _evt("expiring_soon", "free/y", days_left=3, expiration_date="2026-06-24")
        ],
        added=[_evt("added", "free/z", context_length=8192)],
    )
    alerts = format_diff_alert(diff)
    severities = [a.severity for a in alerts]
    assert "critical" in severities
    assert "warning" in severities
    assert "info" in severities


def test_format_health_digest_with_failures() -> None:
    reports = [
        HealthReport(model_id="free/healthy", status=STATUS_HEALTHY, latency_ms=200, http_status=200, error_message=None),
        HealthReport(model_id="free/gone", status=STATUS_NOT_FOUND, latency_ms=150, http_status=404, error_message="model not found"),
        HealthReport(model_id="free/rate", status=STATUS_RATE_LIMITED, latency_ms=100, http_status=429, error_message="too many"),
    ]
    msg = format_health_digest(reports)
    assert msg.severity == "warning"
    assert "Failed: 2" in msg.body


def test_format_health_digest_all_healthy() -> None:
    reports = [
        HealthReport(model_id="free/a", status=STATUS_HEALTHY, latency_ms=200, http_status=200, error_message=None),
        HealthReport(model_id="free/b", status=STATUS_HEALTHY, latency_ms=300, http_status=200, error_message=None),
    ]
    msg = format_health_digest(reports)
    assert msg.severity == "info"
    assert "Healthy: 2" in msg.body


def test_send_telegram_missing_credentials_returns_false(monkeypatch, caplog) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    msg = DiffResult(removed=[_evt("removed", "free/x")])  # ignored
    # We need an AlertMessage — format first
    alerts = format_diff_alert(DiffResult(removed=[_evt("removed", "free/x")]))
    assert send_telegram(alerts[0]) is False
