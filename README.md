# openrouter-free-monitor

Monitor OpenRouter free models, catch expirations and removals before they break your routing chain.

![Status: Alpha](https://img.shields.io/badge/status-alpha-yellow)
![Python: 3.10+](https://img.shields.io/badge/python-3.10+-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

## What it does

OpenRouter publishes ~25-30 free models at any time. They change frequently:
new ones appear, others get `expiration_date` set, some disappear (paid conversion
or deprecation), some rate-limit or 404 transiently.

`openrouter-free-monitor` keeps a local SQLite history of the free tier, diffs
snapshots, classifies events, and alerts via Telegram so you can swap routing
chains before breakage.

## Features

- **Hourly snapshot** of all free OpenRouter models into a local SQLite DB
- **Diff detection**: added, removed, expired, expiring soon, expiration newly set
- **Health probes**: 1-token chat-completion ping against each free model, with
  latency and HTTP status tracking
- **Telegram alerts**: critical events (removed/expired) sent immediately;
  warnings batched into a daily digest
- **CLI-first**: `openrouter-fm poll | status | diff | health | events | config`
- **Standalone**: zero OpenClaw coupling, runs anywhere Python 3.10+ runs
- **Idempotent**: safe to run repeatedly; deduplicates events via timestamps

## Why this matters

OpenRouter's free tier is dynamic. Models you route to today may 404 tomorrow.
This monitor:

1. Catches **withdrawals** before your agents hit 404 dead-ends
2. Detects **paid conversions** (e.g. deepseek-v4-flash going from `:free` to paid-only)
3. Flags **expiring_soon** so you can swap routes proactively
4. Tracks **rate-limit patterns** (429s) so you know which slots are flaky

## Install

```bash
# From PyPI (when published)
pip install openrouter-free-monitor

# From source (current)
git clone https://github.com/Boothey07/openrouter-free-monitor
cd openrouter-free-monitor
pip install -e .
```

## Configuration

Set these env vars (or put them in a systemd EnvironmentFile):

```bash
# Required
export OPENROUTER_API_KEY="sk-or-v1-..."

# Optional — Telegram alerts
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="7540800109"

# Optional — override defaults
export OPENROUTER_FM_DB="/var/lib/openrouter-fm/snapshots.db"
export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"  # default
```

## CLI Usage

### First-time setup

```bash
openrouter-fm init
```

### Take a snapshot + diff against previous + alert

```bash
openrouter-fm poll               # silent unless events
openrouter-fm poll --probe       # also health-check each free model
openrouter-fm poll --no-alert    # print alerts to stdout instead of Telegram
```

### Inspect state

```bash
openrouter-fm status             # latest snapshot, all free models
openrouter-fm diff               # events between latest two snapshots
openrouter-fm health             # live health probes
openrouter-fm events --kind expired --since-hours 24
```

### Inspect config (hides secrets)

```bash
openrouter-fm config
```

## Cron / systemd

Two recommended schedules:

| Schedule | Command | Purpose |
|---|---|---|
| Hourly | `openrouter-fm poll` | Snapshot catalog, detect transitions, alert |
| Daily 04:00 | `openrouter-fm poll --probe` | Health-check all free models, surface 429/404 patterns |

Example systemd timer at `systemd/openrouter-fm-poll.timer`:

```ini
[Unit]
Description=Hourly OpenRouter free-model poll

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

## Programmatic use

```python
from openrouter_free_monitor import fetch_free_models, SnapshotStore, diff_snapshots
from openrouter_free_monitor.alerts import format_diff_alert, send_alerts

# Fetch + record
models = fetch_free_models(api_key="sk-or-v1-...")
store = SnapshotStore("/tmp/snapshots.db")
store.record_snapshot(models)

# Diff
diff = diff_snapshots(store)
if diff.has_events:
    alerts = format_diff_alert(diff)
    send_alerts(alerts)  # uses TELEGRAM_BOT_TOKEN/CHAT_ID
```

## Event kinds

| Kind | Severity | When |
|---|---|---|
| `added` | info | Model appears in free tier for the first time |
| `removed` | critical | Free model disappears (paid conversion or removal) |
| `expired` | critical | Model hit its `expiration_date` |
| `expiring_soon` | warning | Expiration within horizon (default 7 days) |
| `expiration_set` | warning | Expiration newly assigned but outside horizon |
| `pricing_changed` | critical | Pricing went free → paid (or vice versa) |

## Roadmap

- **v0.2**: `--apply` flag to auto-patch `openclaw.json` fallback arrays
- **v0.3**: Web dashboard (lightweight FastAPI + HTML)
- **v0.4**: Cross-provider monitor (Groq, DeepInfra, Together — same pattern)

## License

MIT
