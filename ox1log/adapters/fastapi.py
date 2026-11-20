"""FastAPI adapter: the only module (with the dashboard) allowed to
import FastAPI/Starlette. One call wires middleware + handler + excepthook."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from ..alerter import Alerter, AlertSink, Rule
from ..collector import DEFAULT_DB_PATH, MEMORY_URI, Collector
from ..handler import ensure_handler, install_excepthook, resolve_level
from ..memory import DEFAULT_MAX_EVENTS, DEFAULT_MAX_MESSAGE_CHARS
from ..middleware import LoggingMiddleware
from ..redis_storage import DEFAULT_KEY_PREFIX, DEFAULT_TTL_SECONDS

logger = logging.getLogger(__name__)


def observe(
    app: Any,
    db_path: str = DEFAULT_DB_PATH,
    retention_days: int = 7,
    sample_rate: float = 1.0,
    level: int | str = logging.INFO,
    rules: list[Rule] | None = None,
    sinks: list[AlertSink] | None = None,
    dashboard_prefix: str = "/0x1log",
    dashboard_token: str | None = None,
    skip_paths: list[str] | None = None,
    capture_body: bool | None = None,
    max_body_size: int = 65536,
    *,
    mode: str = "full",
    storage: Any | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    key_prefix: str = DEFAULT_KEY_PREFIX,
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    source: str | None = None,
    capture_requests: bool | None = None,
) -> Collector:
    """Attach 0x1log to a FastAPI app. Returns the collector.

    `mode="lite"` is zero-overhead ephemeral logging: defaults to `memory://`
    (zero disk, zero DB), skips request/body middleware, and tags logs with
    `source="api"`. Pass `db_path="redis://..."` (`pip install '0x1-log[redis]'`)
    to merge API and worker pods in a bounded Redis ring buffer.

    `mode="full"` keeps persistent SQLite/Postgres history, request middleware,
    traces, and metrics.
    """
    if mode not in ("lite", "full"):
        raise ValueError(f"mode must be 'lite' or 'full', got {mode!r}")

    is_lite = mode == "lite"
    resolved_path = MEMORY_URI if (is_lite and db_path == DEFAULT_DB_PATH) else db_path
    resolved_capture_requests = (not is_lite) if capture_requests is None else capture_requests
    resolved_capture_body = (not is_lite) if capture_body is None else capture_body
    resolved_source = ("api" if is_lite else None) if source is None else source

    alerter = None
    if rules and sinks:
        alerter = Alerter(rules, sinks).start()
    elif rules or sinks:
        logger.warning(
            "0x1log alerting is disabled: pass both rules= and sinks= "
            "to observe() (got %s).",
            "rules only" if rules else "sinks only",
        )
    collector = Collector(
        resolved_path,
        retention_days=retention_days,
        alerter=alerter,
        storage=storage,
        default_data={"source": resolved_source} if resolved_source else None,
        mode=mode,
        max_events=max_events,
        ttl_seconds=ttl_seconds,
        key_prefix=key_prefix,
        max_message_chars=max_message_chars,
    ).start()
    prefix = (dashboard_prefix or "/0x1log").rstrip("/") or "/0x1log"
    if resolved_capture_requests:
        skips = list(skip_paths or [])
        if prefix not in skips:
            skips.append(prefix)
        app.add_middleware(
            LoggingMiddleware,
            collector=collector,
            sample_rate=sample_rate,
            skip_prefixes=tuple(skips),
            capture_body=resolved_capture_body,
            max_body_size=max_body_size,
        )
    if is_lite:
        lvl = resolve_level(level)
        root = logging.getLogger()
        if root.level == logging.NOTSET or root.level > lvl:
            root.setLevel(lvl)
    ensure_handler(collector, level)
    install_excepthook(collector)
    if dashboard_token:
        from ..dashboard.router import create_dashboard_router

        app.include_router(
            create_dashboard_router(collector, dashboard_token), prefix=prefix
        )
    else:
        from ..dashboard.router import create_setup_router

        logger.warning(
            "0x1log dashboard is disabled: dashboard_token is not set. "
            'Pass dashboard_token="..." to observe() to enable it at %s/.',
            prefix,
        )
        app.include_router(create_setup_router(prefix), prefix=prefix)

    def _startup() -> None:
        # No-op on the first run (observe() already started both). A later
        # lifespan run in the same process (one TestClient per test) must
        # restart them, or logging silently stops and the dashboard 500s.
        if alerter is not None:
            alerter.start()
        collector.start()

    def _shutdown() -> None:
        collector.stop()
        if alerter is not None:
            alerter.stop()

    _chain_lifespan(app, _startup, _shutdown)
    return collector


def _chain_lifespan(
    app: Any, startup: Callable[[], None], shutdown: Callable[[], None]
) -> None:
    """Run `startup`/`shutdown` around the app lifespan via composition.

    Wraps `router.lifespan_context` so a user-defined lifespan keeps
    running untouched and its state passes through to requests.
    `on_startup`/`on_shutdown` are deprecated upstream and removed in
    Starlette 1.x, so they remain only as a fallback for ancient versions.
    Sources:
    - https://starlette.dev/lifespan/
    - https://fastapi.tiangolo.com/advanced/events/#alternative-events-deprecated
    """
    router = getattr(app, "router", None)
    prev = getattr(router, "lifespan_context", None)
    if prev is None:
        legacy_up = getattr(router, "on_startup", None)
        if legacy_up is not None:
            legacy_up.append(startup)
        legacy = getattr(router, "on_shutdown", None)
        if legacy is not None:
            legacy.append(shutdown)
        return

    @asynccontextmanager
    async def _ox1log_lifespan(inner_app: Any):
        startup()
        try:
            async with prev(inner_app) as state:
                yield state
        finally:
            shutdown()

    router.lifespan_context = _ox1log_lifespan
