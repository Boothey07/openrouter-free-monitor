"""Alert formatting and Telegram delivery.

Critical events (model removed, expired, switched to paid) should be sent
immediately so Tom can swap routing chains. Informational events (new model
added, expiring soon) are batched into a daily digest.

Telegram delivery uses the plain ``sendMessage`` HTTP API — no SDK, no extra
dep. Configure via env vars:

- ``TELEGRAM_BOT_TOKEN``
- ``TELEGRAM_CHAT_ID``
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Iterable

import requests

from openrouter_free_monitor.diff import DiffEvent, DiffResult
from openrouter_free_monitor.health import HealthReport, STATUS_HEALTHY

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
TELEGRAM_API_BASE = "https://api.telegram.org"


@dataclass
class AlertMessage:
    """A formatted alert ready for delivery."""

    title: str
    body: str
    severity: str  # "critical" | "warning" | "info"
    events: list[DiffEvent]


def format_diff_alert(diff: DiffResult) -> list[AlertMessage]:
    """Convert a ``DiffResult`` into one or more ``AlertMessage`` records.

    Critical (immediate) — removed, expired, pricing_changed
    Warning (digest) — expiring_soon, expiration_set
    Info (digest) — added
    """
    alerts: list[AlertMessage] = []
    critical_events = diff.critical_events
    if critical_events:
        title = f"🚨 OpenRouter free-tier change: {len(critical_events)} critical event(s)"
        body_lines = []
        for evt in critical_events:
            body_lines.append(f"• *{evt.kind}* — `{evt.model_id}`")
            for k, v in evt.detail.items():
                body_lines.append(f"    {k}: `{v}`")
        alerts.append(
            AlertMessage(
                title=title,
                body="\n".join(body_lines),
                severity="critical",
                events=critical_events,
            )
        )

    warning_events = diff.expiring_soon + diff.expiration_set
    if warning_events:
        title = f"⚠️ OpenRouter free-tier: {len(warning_events)} upcoming change(s)"
        body_lines = []
        for evt in warning_events:
            days = evt.detail.get("days_left", "?")
            body_lines.append(
                f"• *{evt.kind}* — `{evt.model_id}` (days_left={days})"
            )
        alerts.append(
            AlertMessage(
                title=title,
                body="\n".join(body_lines),
                severity="warning",
                events=warning_events,
            )
        )

    if diff.added:
        body_lines = [
            f"• *added* — `{evt.model_id}` ({evt.detail.get('context_length', '?')} ctx)"
            for evt in diff.added
        ]
        alerts.append(
            AlertMessage(
                title=f"ℹ️ OpenRouter: {len(diff.added)} new free model(s)",
                body="\n".join(body_lines),
                severity="info",
                events=diff.added,
            )
        )
    return alerts


def format_health_digest(reports: list[HealthReport]) -> AlertMessage:
    """Aggregate per-model health probes into a single daily digest."""
    healthy = [r for r in reports if r.status == STATUS_HEALTHY]
    failed = [r for r in reports if r.status != STATUS_HEALTHY]
    body_lines = [
        f"✅ Healthy: {len(healthy)}",
        f"❌ Failed: {len(failed)}",
        "",
    ]
    if failed:
        body_lines.append("Failures:")
        for r in failed:
            err = (r.error_message or "").replace("\n", " ")[:80]
            body_lines.append(
                f"  • `{r.model_id}` → {r.status} (HTTP {r.http_status}, {err})"
            )
    body_lines.append("")
    if healthy:
        avg_ms = sum(r.latency_ms for r in healthy) // max(len(healthy), 1)
        body_lines.append(f"Avg healthy latency: {avg_ms}ms")

    return AlertMessage(
        title="📊 OpenRouter free-model health digest",
        body="\n".join(body_lines),
        severity="info" if not failed else "warning",
        events=[],
    )


def send_telegram(
    message: AlertMessage,
    *,
    bot_token: str | None = None,
    chat_id: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> bool:
    """Send an AlertMessage via Telegram bot API.

    Returns True on success, False on failure (logged).
    """
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        logger.warning(
            "Telegram credentials missing (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)"
        )
        return False

    text = f"*{message.title}*\n\n{message.body}"
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    sess = session or requests.Session()
    try:
        resp = sess.post(url, json=payload, timeout=timeout)
        if resp.status_code >= 300:
            logger.error(
                "Telegram send failed: HTTP %s, body=%s",
                resp.status_code,
                resp.text[:300],
            )
            return False
        return True
    except requests.RequestException as e:
        logger.error("Telegram send exception: %s", e)
        return False


def send_alerts(
    alerts: Iterable[AlertMessage],
    *,
    severity_threshold: str = "info",
) -> int:
    """Send multiple alerts, filtering by severity.

    Threshold levels (lowest → highest): info, warning, critical.
    Only alerts at or above the threshold are sent.
    """
    levels = {"info": 0, "warning": 1, "critical": 2}
    threshold = levels.get(severity_threshold, 0)
    sent = 0
    for alert in alerts:
        if levels.get(alert.severity, 0) >= threshold:
            if send_telegram(alert):
                sent += 1
    return sent
