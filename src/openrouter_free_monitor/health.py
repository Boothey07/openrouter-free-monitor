"""Live health probes for free models.

Sends a 1-token chat-completions call to each free model and classifies the
result into one of:

- ``healthy`` — got a valid reply
- ``rate_limited`` — 429
- ``auth_error`` — 401 / 403
- ``payment_required`` — 402 (paid tier, not actually free)
- ``not_found`` — 404 (model withdrawn)
- ``timeout`` — request exceeded timeout
- ``error`` — other non-2xx
- ``unknown_error`` — exception

The result is a ``HealthReport`` per model. Health probes are intentionally
cheap (max_tokens=4) so we can run them across all free models every poll
without blowing the OpenRouter daily free quota (50 req/day).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
CHAT_ENDPOINT = "/chat/completions"
DEFAULT_TIMEOUT = 25

# Status categories — keep stable; alert rules may match by string.
STATUS_HEALTHY = "healthy"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_AUTH_ERROR = "auth_error"
STATUS_PAYMENT_REQUIRED = "payment_required"
STATUS_NOT_FOUND = "not_found"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
STATUS_UNKNOWN_ERROR = "unknown_error"

ALL_STATUSES = {
    STATUS_HEALTHY,
    STATUS_RATE_LIMITED,
    STATUS_AUTH_ERROR,
    STATUS_PAYMENT_REQUIRED,
    STATUS_NOT_FOUND,
    STATUS_TIMEOUT,
    STATUS_ERROR,
    STATUS_UNKNOWN_ERROR,
}


@dataclass
class HealthReport:
    """Health probe result for a single model."""

    model_id: str
    status: str
    latency_ms: int
    http_status: int | None
    error_message: str | None
    detail: dict[str, Any] = field(default_factory=dict)
    probed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "error_message": self.error_message,
            "detail": self.detail,
            "probed_at": self.probed_at.isoformat(),
        }


def probe_model(
    model_id: str,
    *,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> HealthReport:
    """Probe a single free model with a 1-token chat completion."""
    from openrouter_free_monitor.catalog import fetch_catalog  # late import

    key = api_key or _read_api_key()
    sess = session or requests.Session()
    url = base_url.rstrip("/") + CHAT_ENDPOINT
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 4,
    }

    started = time.monotonic()
    try:
        resp = sess.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.Timeout:
        return HealthReport(
            model_id=model_id,
            status=STATUS_TIMEOUT,
            latency_ms=int((time.monotonic() - started) * 1000),
            http_status=None,
            error_message=f"timeout after {timeout}s",
        )
    except requests.RequestException as e:
        return HealthReport(
            model_id=model_id,
            status=STATUS_UNKNOWN_ERROR,
            latency_ms=int((time.monotonic() - started) * 1000),
            http_status=None,
            error_message=str(e)[:300],
        )

    latency = int((time.monotonic() - started) * 1000)
    http_status = resp.status_code

    if 200 <= http_status < 300:
        return HealthReport(
            model_id=model_id,
            status=STATUS_HEALTHY,
            latency_ms=latency,
            http_status=http_status,
            error_message=None,
        )

    # Try to extract error code from body
    error_code = None
    error_msg = None
    try:
        body = resp.json()
        if isinstance(body, dict):
            err = body.get("error") or {}
            error_code = err.get("code")
            error_msg = err.get("message")
    except (json.JSONDecodeError, ValueError):
        error_msg = resp.text[:200]

    if http_status == 401 or http_status == 403:
        status = STATUS_AUTH_ERROR
    elif http_status == 402:
        status = STATUS_PAYMENT_REQUIRED
    elif http_status == 404:
        status = STATUS_NOT_FOUND
    elif http_status == 429:
        status = STATUS_RATE_LIMITED
    else:
        status = STATUS_ERROR

    return HealthReport(
        model_id=model_id,
        status=status,
        latency_ms=latency,
        http_status=http_status,
        error_message=error_msg,
        detail={"error_code": error_code, "raw": resp.text[:500]},
    )


def probe_models(
    model_ids: list[str],
    *,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
    delay_seconds: float = 0.0,
    session: requests.Session | None = None,
) -> list[HealthReport]:
    """Probe multiple models sequentially. Optional delay between calls.

    Use a small delay (e.g. 0.5s) when probing many models to avoid tripping
    OpenRouter's per-minute rate limits on free tier.
    """
    sess = session or requests.Session()
    reports = []
    for i, mid in enumerate(model_ids):
        if i > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        rep = probe_model(
            mid,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            session=sess,
        )
        reports.append(rep)
        logger.info(
            "Health probe: %s → %s (%dms, HTTP %s)",
            mid,
            rep.status,
            rep.latency_ms,
            rep.http_status,
        )
    return reports


def aggregate_status(reports: list[HealthReport]) -> dict[str, int]:
    """Count occurrences of each status across a list of reports."""
    counts: dict[str, int] = {s: 0 for s in ALL_STATUSES}
    for r in reports:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def _read_api_key() -> str:
    import os

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError("OpenRouter API key required (set OPENROUTER_API_KEY env var)")
    return key
