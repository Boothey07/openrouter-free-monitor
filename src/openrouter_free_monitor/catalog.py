"""OpenRouter catalog fetcher and free-model classifier.

Provides:
- ``fetch_catalog``: HTTP GET against ``/api/v1/models`` with auth.
- ``parse_catalog``: filter free models and return structured ``FreeModel`` records.
- ``FreeModel``: dataclass with the metadata fields we monitor.

A model is considered "free" when both ``pricing.prompt`` and ``pricing.completion``
are zero (or absent). OpenRouter occasionally publishes models without pricing; we
treat those as paid unless explicitly zero.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
MODELS_ENDPOINT = "/models"
DEFAULT_TIMEOUT = 30


@dataclass(frozen=True)
class FreeModel:
    """A free-tier OpenRouter model snapshot."""

    id: str
    name: str
    canonical_slug: str
    context_length: int
    modalities: tuple[str, ...]
    top_provider: str
    created_at: datetime
    has_expiration: bool
    expiration_date: datetime | None
    knowledge_cutoff: str | None
    description: str
    snapshot_at: datetime
    raw: dict[str, Any] = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        if self.expiration_date:
            d["expiration_date"] = self.expiration_date.isoformat()
        d["snapshot_at"] = self.snapshot_at.isoformat()
        return d


def _is_free(pricing: dict[str, Any] | None) -> bool:
    """Return True when prompt and completion pricing are both zero.

    OpenRouter publishes free models with explicit "0" string values. Some
    legacy catalog entries may omit the field; treat missing as "unknown" rather
    than zero, so we don't misclassify paid models as free.
    """
    if not pricing:
        return False
    prompt = str(pricing.get("prompt", ""))
    completion = str(pricing.get("completion", ""))
    if not prompt or not completion:
        return False
    return prompt in ("0", "0.0", "0.000000") and completion in ("0", "0.0", "0.000000")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        # OpenRouter sometimes uses ISO-8601 strings, sometimes unix timestamps.
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if isinstance(value, str):
            # ISO-8601 like "2026-06-22T00:00:00Z" or just date "2026-06-22"
            cleaned = value.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError, OSError) as e:
        logger.warning("Could not parse datetime %r: %s", value, e)
    return None


def parse_catalog(payload: dict[str, Any], *, snapshot_at: datetime | None = None) -> list[FreeModel]:
    """Parse an OpenRouter /models payload into free ``FreeModel`` records.

    Args:
        payload: Decoded JSON response from ``GET /api/v1/models``.
        snapshot_at: Optional override for ``snapshot_at`` on every record
            (mostly useful for tests). Defaults to ``datetime.now(timezone.utc)``.

    Returns:
        List of free ``FreeModel`` records, sorted by id.
    """
    snap = snapshot_at or datetime.now(timezone.utc)
    out: list[FreeModel] = []
    for m in payload.get("data", []):
        if not _is_free(m.get("pricing")):
            continue
        created = _parse_dt(m.get("created")) or snap
        expires = _parse_dt(m.get("expiration_date"))
        modalities = tuple(m.get("architecture", {}).get("input_modalities") or ())
        out.append(
            FreeModel(
                id=m.get("id", ""),
                name=m.get("name", m.get("id", "?")),
                canonical_slug=m.get("canonical_slug", ""),
                context_length=int(m.get("context_length") or 0),
                modalities=modalities,
                top_provider=(m.get("top_provider") or {}).get("name", "?") or "?",
                created_at=created,
                has_expiration=expires is not None,
                expiration_date=expires,
                knowledge_cutoff=m.get("knowledge_cutoff"),
                description=(m.get("description") or "")[:200],
                snapshot_at=snap,
                raw=m,
            )
        )
    out.sort(key=lambda fm: fm.id)
    return out


def fetch_catalog(
    *,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Fetch the OpenRouter models catalog.

    Args:
        api_key: OpenRouter API key. Falls back to ``OPENROUTER_API_KEY`` env var.
        base_url: Override API base (mostly for testing).
        timeout: HTTP timeout in seconds.
        session: Optional pre-built ``requests.Session``.

    Returns:
        Decoded JSON dict with key ``data`` (list of model records).

    Raises:
        ``requests.HTTPError`` on non-2xx responses.
        ``ValueError`` when no API key is provided or resolved.
    """
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError(
            "OpenRouter API key required (pass api_key= or set OPENROUTER_API_KEY env var)"
        )
    sess = session or requests.Session()
    url = base_url.rstrip("/") + MODELS_ENDPOINT
    headers = {"Authorization": f"Bearer {key}"}
    logger.debug("Fetching %s", url)
    resp = sess.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_free_models(**kwargs: Any) -> list[FreeModel]:
    """Convenience: fetch catalog + parse free subset in one call."""
    payload = fetch_catalog(**kwargs)
    return parse_catalog(payload)


def to_summary_dict(models: Iterable[FreeModel]) -> list[dict[str, Any]]:
    """Convert ``FreeModel`` records to a JSON-safe summary (drops ``raw``)."""
    out = []
    for m in models:
        d = m.to_dict()
        d.pop("raw", None)
        out.append(d)
    return out
