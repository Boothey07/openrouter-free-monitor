# Changelog

All notable changes to `openrouter-free-monitor` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-06-21

### Added
- Initial release.
- `catalog` module: fetches `GET /api/v1/models`, parses free models (zero pricing),
  extracts `id`, `name`, `context_length`, `modalities`, `top_provider`,
  `created_at`, `expiration_date`, `knowledge_cutoff`.
- `store` module: SQLite-backed snapshot history with `snapshots`, `models`, `events`
  tables; WAL mode; FTS-ready schema.
- `diff` module: detects `added`, `removed`, `expired`, `expiring_soon`,
  `expiration_set` transitions between snapshots.
- `health` module: 1-token chat-completion probe per free model; classifies into
  `healthy`, `rate_limited`, `auth_error`, `payment_required`, `not_found`,
  `timeout`, `error`.
- `alerts` module: format diff/health output as Markdown, deliver via Telegram bot
  API; severity-aware filtering.
- `cli` module: `init | poll | status | diff | health | events | config` subcommands.
- 31 unit tests covering catalog parsing, store persistence, diff logic, alert
  formatting, CLI dispatch.
- Live end-to-end test: discovered `nex-agi/nex-n2-pro:free` expires 2026-06-22,
  surfaced via `expiring_soon` event.
- Bug fix: tz-naive datetimes from SQLite now coerced to UTC before comparison
  (was `TypeError: can't compare offset-naive and offset-aware datetimes`).
- systemd units: `openrouter-fm-poll.{service,timer}` (hourly),
  `openrouter-fm-health.{service,timer}` (daily 04:00).
- `scripts/install.sh`: full host bootstrap.
