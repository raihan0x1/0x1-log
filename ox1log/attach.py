"""Process-level capture: collector + stdlib handler + excepthook, no web app.

Framework-agnostic: this module must never import FastAPI/Starlette.
"""

from __future__ import annotations

import atexit
import logging

from typing import Any

from .collector import DEFAULT_DB_PATH, Collector
from .handler import ensure_handler, install_excepthook, resolve_level
from .memory import DEFAULT_MAX_EVENTS, DEFAULT_MAX_MESSAGE_CHARS
from .redis_storage import DEFAULT_KEY_PREFIX, DEFAULT_TTL_SECONDS


def attach(
    db_path: str = DEFAULT_DB_PATH,
    *,
    storage: Any | None = None,
    retention_days: int = 7,
    max_events: int = DEFAULT_MAX_EVENTS,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    key_prefix: str = DEFAULT_KEY_PREFIX,
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    level: int | str = logging.INFO,
    source: str | None = None,
    mode: str = "lite",
) -> Collector:
    """Capture this process's stdlib logs and uncaught exceptions.

    Point `db_path` at the same target as `observe()` — `memory://`, a
    `redis://` URL (`pip install '0x1-log[redis]'`), a SQLite file, or a
    `postgresql://` URL (`pip install '0x1-log[postgres]'`) — so a worker,
    cron job, or script shows up in the existing dashboard under `source`.
    """
    lvl = resolve_level(level)
    extra = {"source": source} if source else None
    collector = Collector(
        db_path,
        retention_days=retention_days,
        storage=storage,
        default_data=extra,
        mode=mode,
        max_events=max_events,
        ttl_seconds=ttl_seconds,
        key_prefix=key_prefix,
        max_message_chars=max_message_chars,
    ).start()
    root = logging.getLogger()
    # Root defaults to WARNING, which would drop INFO before our handler.
    if root.level == logging.NOTSET or root.level > lvl:
        root.setLevel(lvl)
    ensure_handler(collector, lvl)
    install_excepthook(collector)
    atexit.register(collector.stop)
    return collector
