"""FastAPI adapter: the only module (with the dashboard) allowed to
import FastAPI/Starlette. One call wires middleware + handler + excepthook."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from ..alerter import Alerter, AlertSink, Rule
from ..collector import Collector
from ..handler import ensure_handler, install_excepthook
from ..middleware import LoggingMiddleware

logger = logging.getLogger(__name__)

DEFAULT_DASHBOARD_PREFIX = "/0x1log"
_LEGACY_DASHBOARD_PREFIX = "/nahiarhdlog"


def observe(
    app: Any,
    db_path: str | None = None,
    retention_days: int = 7,
    sample_rate: float = 1.0,
    level: int = logging.INFO,
    rules: list[Rule] | None = None,
    sinks: list[AlertSink] | None = None,
    dashboard_prefix: str | None = None,
    dashboard_token: str | None = None,
    skip_paths: list[str] | None = None,
    capture_body: bool = True,
    max_body_size: int = 65536,
) -> Collector:
    """Attach 0x1LOG to a FastAPI app. Returns the collector.

    The dashboard data is served only when `dashboard_token` is set: it
    must never be reachable without a key. Without a token the prefix
    serves a setup page telling the user how to enable the dashboard.
    The dashboard zone's own traffic is never logged, so live tail
    can't flood itself.
    """
    alerter = None
    if rules and sinks:
        alerter = Alerter(rules, sinks).start()
    elif rules or sinks:
        logger.warning(
            "0x1LOG alerting is disabled: pass both rules= and sinks= "
            "to observe() (got %s).",
            "rules only" if rules else "sinks only",
        )
    collector = Collector(
        db_path, retention_days=retention_days, alerter=alerter
    ).start()
    custom_prefix = (dashboard_prefix or "").rstrip("/")
    prefix = custom_prefix or DEFAULT_DASHBOARD_PREFIX
    prefixes = (prefix,) if custom_prefix else (prefix, _LEGACY_DASHBOARD_PREFIX)
    skips = tuple(dict.fromkeys([*(skip_paths or ()), *prefixes]))
    app.add_middleware(
        LoggingMiddleware,
        collector=collector,
        sample_rate=sample_rate,
        skip_prefixes=skips,
        capture_body=capture_body,
        max_body_size=max_body_size,
    )
    ensure_handler(collector, level)
    install_excepthook(collector)
    if dashboard_token:
        from ..dashboard.router import create_dashboard_router

        router = create_dashboard_router(collector, dashboard_token)
        for mount in prefixes:
            app.include_router(router, prefix=mount, include_in_schema=mount == prefix)
    else:
        from ..dashboard.router import create_setup_router

        logger.warning(
            "0x1LOG dashboard is disabled: dashboard_token is not set. "
            'Pass dashboard_token="..." to observe() to enable it at %s/.',
            prefix,
        )
        for mount in prefixes:
            app.include_router(create_setup_router(mount), prefix=mount)

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
    async def _log_lifespan(inner_app: Any):
        startup()
        try:
            async with prev(inner_app) as state:
                yield state
        finally:
            shutdown()

    router.lifespan_context = _log_lifespan
