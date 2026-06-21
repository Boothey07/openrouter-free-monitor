"""Configuration loader for openrouter-free-monitor.

All settings come from environment variables. Optional, with safe defaults.

Required:
- ``OPENROUTER_API_KEY`` — your OpenRouter key (free tier works)

Optional:
- ``OPENROUTER_BASE_URL`` — override API base (default: https://openrouter.ai/api/v1)
- ``TELEGRAM_BOT_TOKEN`` — Telegram bot token for alerts
- ``TELEGRAM_CHAT_ID`` — Telegram chat ID for alert delivery
- ``OPENROUTER_FM_DB`` — path to SQLite store (CLI default also reads this)

Example::

    export OPENROUTER_API_KEY="sk-or-v1-..."
    export TELEGRAM_BOT_TOKEN="..."
    export TELEGRAM_CHAT_ID="7540800109"
    openrouter-fm poll --probe
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_key: str | None
    base_url: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None


def load_config() -> Config:
    return Config(
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
    )
