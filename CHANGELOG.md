# Changelog

All notable changes to 0x1LOG will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.4.1] - 2026-11-24

### Added
- Console redesign and infinite scroll
- Page the log table by cursor

### Fixed
- Index the dashboard's hot queries and read on a separate connection

### Changed
- Add .gitignore and salt files

## [1.4.0] - 2026-11-21

### Added
- **Repeats in the log table.** Consecutive events with the same type, name, level and status fold into one row (`api/v1/vectors/search ×49 · 1.95 s to 2.03 s`) that opens in place to show each event; at most 1,000 events per Repeat. `GET /0x1log/api/logs?repeats=true` serves folded rows (`repeat` summary, `next_offset` cursor, `expand=<first_id,...>` for opened Repeats); without `repeats` the endpoint is unchanged.
- **One trace per background job.** `ox1log.integrations.rq` (`JobTraceMixin` for the Worker, `trace_meta(name=...)` for `enqueue(meta=...)`) and `ox1log.integrations.celery` (`trace_tasks()`) wrap each job in a `job` span that continues the trace of the request that queued it, so a job's spans and logs share one trace id. Other runners: `ox1log.integrations.job_span(name, traceparent)`.
- `job` spans ride their own line colour in the dashboard.

### Changed
- The log pager walks pages (Newer/Older) instead of numbering them out of a total, since a page now holds 50 rows rather than 50 events.

### Fixed
- **A span given an explicit `trace_id` now passes it to nested spans and logs.** They used to start their own traces.

## [1.3.2] - 2026-11-20

### Added
- Add automated release workflow

## [1.3.1] - 2026-11-20

### Fixed
- **Ring buffers have a byte bound.** Each event's `data` is capped as a whole at `MAX_EVENT_DATA_CHARS` (16,384), shrinking the largest values first into truncated JSON text, so a ring holds at most `max_events` x that. Spans carrying whole vector-search bodies had grown one 5,000-event Redis list to 581 MB.
- **AI spans store their completion once.** `set_completion()` also filled `output`; the event now keeps only `completion`.
- **Dashboard reads no longer scan whole rings on every poll.** Unfiltered counts use `LLEN`, and `/api/metrics` reads only sources that stored a request event (none in lite mode).
- **Dashboard polls never stack.** The 15 s health/status tick, metrics auto-refresh, and the 2 s live tail skip a tick while the previous call is still in flight; stacked full-ring scans from open tabs had taken one host to ~35 GB.

## [1.3.0] - 2026-11-20

### Added
- **`mode="lite"` for zero-DB ephemeral Service Logs.** Skips `LoggingMiddleware` (no HTTP request/body capture) and defaults to a bounded in-memory ring buffer (`memory://`, 5000 events per source) so logs never bloat Postgres or disk.
- **In-memory ring buffer (`MemoryStorage`, `db_path="memory://"`).** Zero-infrastructure default for `mode="lite"` with bounded per-source retention (`max_events=5000`) and message truncation (`max_message_chars=8192`).
- **Opt-in Redis ring buffer (`pip install '0x1-log[redis]'`, `RedisStorage`).** Pass `db_path="redis://..."` with `key_prefix`, `max_events` per source, and `ttl_seconds` (24h default) to merge multi-pod API and worker logs without touching Postgres.
- **`source` filtering, `after_id` cursor polling, and active `sources` discovery.** `GET /0x1log/api/logs` accepts `source` and `after_id` (ascending cursor pagination); `GET /0x1log/api/health` returns `mode`, `storage`, and active `sources`.
- **Dynamic level sync (`collector.set_level()`).** Updates bound handler thresholds at runtime (including Loguru sinks calling `emit()` directly) so captured logs track active terminal severity across dev/prod transitions.

### Changed
- Removed remaining legacy `nahiarhdlog` fallbacks (`/nahiarhdlog` prefix, `.nahiarhdlog/nahiarhdlog.db` fallback, `nahiarhdlog_token` cookie → `ox1log_token`, `nahiarhdlog_events` table → `ox1log_events`, and `ox1log.*` OpenTelemetry attributes). `LogHandler` remains available as an alias for `Ox1logHandler`.

### Fixed
- `truncate_message()` now respects small `max_chars` limits instead of expanding short messages with a negative tail budget.
- `SQLiteStorage` sets `PRAGMA synchronous=NORMAL` alongside WAL for lower disk fsync overhead, and `Collector.flush_pending()` wakes the writer thread immediately instead of sleeping for the flush interval.

## [1.2.0]

### Changed

- `0x1-log` now ships the implementation in `ox1log` and has no dependency on the legacy PyPI distribution. Update any `nahiarhdLOG` imports, including submodules, to `ox1log`.
- New apps use `/0x1log` and `.0x1log/0x1log.db`. The legacy dashboard route is still available under the default configuration, and an existing legacy SQLite file is used when no database is specified.

## [0.9.0] - 2026-11-05

### Changed
- **Dashboard redesign.** The dashboard now draws the app's runtime as a transit network: every event kind is a line with its own colour (requests blue, LLM violet, tools orange, db teal, cache yellow, and so on), red is reserved for disruption, and the shell is a navy signage band with each tab underlined in its line colour. New mark and favicon. Type is Overpass and Overpass Mono, bundled with the package (SIL OFL 1.1, see `dashboard/static/fonts-OFL.txt`), so the dashboard still needs no network. Both themes follow the OS by default.
- Traces render as a route diagram: spans branch off their parent's line at 45 degrees, logs and errors sit as stops on the span they ran inside, durations have their own column, and the route draws itself once when a trace opens (skipped under reduced motion).
- Event details open in a side inspector instead of a modal, so the list stays visible: `J`/`K` move between events with the inspector following, `Esc` closes it, `/` focuses search.

### Added
- Service status in the header (Good service, Minor delays, Severe delays, No traffic), derived from the last five minutes of requests; its popover shows queue, dropped events, backend, storage, search mode, retention, and version. The Metrics tab states the same verdict for the selected window.
- `GET /api/logs/histogram` and `storage.histogram()` (SQLite and Postgres): event volume over time for the same filters as `/api/logs`, split into errors and warnings. The Logs tab draws it above the table; selecting a bar narrows the table to that slice.
- "Copy as cURL" for request events (redacted values stay redacted) and "Copy JSON" for every event.

### Fixed
- `@span(...)` used as a decorator now records each call in its own span. One instance was shared by every call: after a single failure every later call reported `error`, outputs leaked between calls, and overlapping async calls crashed with `ValueError: <Token ...> was created in a different Context`.
- Logging keeps working when the app lifespan runs more than once in a process (one `TestClient(app)` per test). The first shutdown used to stop the collector for good: later events were never written and dashboard reads failed on a closed database. `Collector.start()` / `Alerter.start()` can now be called again after `stop()`; storages gained `reopen()`.
- The first alert now fires right after a machine boots. Cooldown compared against `last_fired = 0.0` on `time.monotonic()`, which counts from boot, so for `cooldown_seconds` after boot nothing was ever sent.
- `Rule(event_type=...)` works for event types other than `"error"`; the collector only forwarded errors to the alerter.
- Events are timestamped when emitted, not when the background writer stores them (up to a flush interval later). Logs use `LogRecord.created`; failed requests use their start time like successful ones. This fixes trace ordering and timeline offsets.
- A non-ASCII dashboard token (`?token=é`) returns 403 instead of a 500 from `hmac.compare_digest`.
- `flush_pending()` no longer waits its full timeout on an idle collector when a batch finishes between its two counter reads.
- Postgres: NUL characters in a message or in `data` no longer fail the whole batch (Postgres rejects `\x00` in TEXT/JSONB); they are stored as U+FFFD.
- An event with a `None`/non-string message or a `None` ts no longer fails its whole batch.
- SQLite text search matches word prefixes (`timeo` finds `timeout`), punctuation-only queries (`->`) use a substring match instead of matching nothing, and the LIKE fallback treats `%`/`_` literally.
- Trace summaries cover work that outlives the response, so the waterfall no longer clips spans at the right edge.
- OpenTelemetry export maps `span` events to spans with their real name, duration, and error status (they were exported as 1 ms `log:` spans).
- Several processes opening the same new SQLite file at once (e.g. `uvicorn --workers N` on a fresh deploy) no longer crash with `sqlite3.OperationalError: database is locked`. Switching a fresh file to WAL needs an exclusive lock, and SQLite returns that error immediately there instead of waiting on `busy_timeout`; `SQLiteStorage` now retries the switch with backoff until its `timeout`.

### Security
- The dashboard sends its token in an `Authorization` header instead of `?token=` on every API call, so it no longer appears in server access logs.
- The login cookie is scoped to the dashboard prefix (and marked `Secure` over HTTPS) instead of `/`, so it is not sent with every request to the host app. Logout also clears cookies set on `/` by older versions.
- Request capture masks sensitive query parameters (`?api_key=`, `?token=`, ...) and fields in `application/x-www-form-urlencoded` bodies, and stores multipart bodies as a size placeholder instead of raw content. `x-csrf-token` / `x-xsrf-token` headers are masked.

### Added
- Requests record their `span_id`; spans, logs, and errors record the span they ran inside (`parent_span_id`). The trace waterfall builds a real tree from these links: spans nest under the request, logs under their span, and services sharing one store nest under the upstream request.
- `GET /api/metrics` returns summary and series from one scan of the window (the dashboard made two). `metrics.overview()` exposes the same in Python.
- Dashboard: time-range filter and `span` type on Logs (filters apply on change, `/` focuses search); span rows show kind, name, model, duration, and failures; window filter on Errors; auto-refresh, throughput, and gap-aware charts with rounded axes on Metrics; waterfall time ticks and markers for zero-duration events; "Copy JSON" and nested attributes in the event dialog; recent traces labelled by their root request; no light-theme flash in dark mode; layout fixes for phone widths.
- `observe()` warns when only one of `rules=` / `sinks=` is given (alerting stays off).

## [0.8.1] - 2026-09-23

### Fixed
- The "reached a different backend" banner no longer fires on Postgres-backed deployments, where every replica reads the same events. `/api/health` reports `shared_storage` from the storage's new `shared` flag (`SQLiteStorage.shared = False`, `PostgresStorage.shared = True`; custom storages without it are treated as per-process).

## [0.8.0] - 2026-09-21

### Added
- **Shared Postgres storage (`nahiarhdLOG.PostgresStorage`, `pip install nahiarhdlog[postgres]`):** point `db_path` at a `postgresql://` URL and every replica reads and writes one `nahiarhdlog_events` table — the dashboard stops flip-flopping between per-pod SQLite files and traces resolve on the first click. SQLite stays the zero-infrastructure default for plain paths.
- `Collector` reconnects once and retries after a dropped Postgres connection instead of failing permanently; use-after-`close()` raises immediately.
- CI runs the Postgres backend suite against a `postgres:16` service.

## [0.7.1] - 2026-09-21

### Fixed
- Dashboard reads wait for the queued backlog (`Collector.flush_pending`) so just-emitted errors appear on the first click instead of after repeated clicks.
- Dashboard API responses send `Cache-Control: no-store` — a cached `[]` is indistinguishable from "no data".
- Error-card clicks no longer fire duplicate log requests; stale out-of-order responses are dropped via request generations in `app.js`.
- Text-search FTS fallback preserves all other filters (type/level/signature/...) instead of returning a wrong unfiltered superset; transient lock errors now surface instead of silently degrading into different results.

### Added
- `/api/health` reports the backend `instance` (`host`/`pid`), shown in the dashboard footer with a warning banner when consecutive calls reach different backends (multi-replica round-robin).

## [0.7.0] - 2026-09-21

### Added
- **Interactive Trace Waterfall UI:** Visual timeline waterfall rendering parent-child span execution, relative timing offsets, duration bars, and aggregate trace metrics.
- **Hierarchical Trace Tree View:** Visual branch guide lines (`└─`) and depth nesting for HTTP requests, AI agents, LLM synthesis, tool calls, vector embeddings, databases, and caches.
- **HTTP Request & Response Body Inspection:** Non-destructive ASGI stream interception capturing request payloads and response bodies with configurable size limits (`max_body_size`).
- **AI Agent Tracing API (`nahiarhdLOG.span`, `nahiarhdLOG.ai_span`):** Context managers and decorators capturing LLM parameters (model, prompt, completion, system prompt, tokens, tool calls) and embedding steps.
- **Dedicated Payload Viewers:** Formatted JSON viewer with copy buttons for requests, responses, model outputs, and tool call arguments.
- **Color-Coded Operation Badges:** Visual badges and timeline bar colors for `[REQ]`, `[AGENT]`, `[LLM]`, `[TOOL]`, `[EMBED]`, `[DB]`, `[CACHE]`, `[HTTP]`, `[AUTH]`, and `[ERROR]`.
- **Sensitive Credential Redaction:** Automatic redaction of passwords, bearer tokens, API keys, and authorization secrets in request/response bodies and headers.

### Changed
- `observe(app)` accepts `capture_body: bool = True` and `max_body_size: int = 65536` parameters.
- `/api/traces/{trace_id}` endpoint returns computed trace summary metrics alongside events.
- Root HTTP requests are timestamped from execution start for accurate chronological timeline alignment.

### Fixed
- Fixed premature database shutdown in demo app by replacing TestClient lifespan runner with background daemon seeding.

---

## [0.6.3] - 2026-09-18
- Initial release with embedded SQLite logging, error tracking, alerts, and metrics.
