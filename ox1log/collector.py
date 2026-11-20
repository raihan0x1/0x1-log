"""Central hub: bounded queue + batch writer thread + storage.

Framework-agnostic: this module must never import FastAPI/Starlette.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING, Any

from .memory import DEFAULT_MAX_EVENTS, DEFAULT_MAX_MESSAGE_CHARS, MemoryStorage
from .postgres import PostgresStorage
from .redis_storage import DEFAULT_KEY_PREFIX, DEFAULT_TTL_SECONDS, RedisStorage
from .storage import SQLiteStorage

if TYPE_CHECKING:
    from .alerter import Alerter

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 0.2  # seconds
_IDLE_POLL = 0.05  # seconds: lets flush_pending wake promptly without busy-spinning
_BATCH_SIZE = 500
_PURGE_INTERVAL = 3600.0  # seconds

# Default on-disk location: a dot-directory keeps project roots clean.
DEFAULT_DB_PATH = ".0x1log/0x1log.db"
MEMORY_URI = "memory://"

_ERROR_ONLY = frozenset({"error"})

_active_collector: Collector | None = None


def get_active_collector() -> Collector | None:
    """Return the most recently started Collector, or None."""
    return _active_collector


def _build_storage(
    db_path: str,
    *,
    retention_days: int,
    max_events: int,
    ttl_seconds: int,
    key_prefix: str,
    max_message_chars: int,
) -> SQLiteStorage | PostgresStorage | MemoryStorage | RedisStorage:
    if db_path == MEMORY_URI:
        return MemoryStorage(
            max_events=max_events,
            max_message_chars=max_message_chars,
        )
    if db_path.startswith(("redis://", "rediss://", "unix://")):
        return RedisStorage(
            db_path,
            key_prefix=key_prefix,
            max_events=max_events,
            ttl_seconds=ttl_seconds,
            max_message_chars=max_message_chars,
        )
    if db_path.startswith(("postgresql://", "postgres://")):
        return PostgresStorage(db_path, retention_days=retention_days)
    return SQLiteStorage(db_path, retention_days=retention_days)


class Collector:
    """Receives events without blocking callers and persists them in batches."""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        retention_days: int = 7,
        queue_size: int = 10_000,
        alerter: Alerter | None = None,
        storage: Any | None = None,
        default_data: dict[str, Any] | None = None,
        *,
        mode: str = "full",
        max_events: int = DEFAULT_MAX_EVENTS,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    ) -> None:
        # Custom backends plug in via `storage`; anything duck-typed like
        # SQLiteStorage works. Otherwise URI scheme selects memory, redis,
        # postgres, or local SQLite.
        if storage is not None:
            self.storage = storage
        else:
            self.storage = _build_storage(
                db_path,
                retention_days=retention_days,
                max_events=max_events,
                ttl_seconds=ttl_seconds,
                key_prefix=key_prefix,
                max_message_chars=max_message_chars,
            )
        self.mode = mode
        self.alerter = alerter
        self._default_data = dict(default_data) if default_data else {}
        self._bound_handlers: list[logging.Handler] = []
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_size)
        self._dropped = 0
        self._completed = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._flush_now = threading.Event()
        self._started = False
        self._last_purge = time.monotonic()
        self._thread = self._new_thread()

    @property
    def bound_handlers(self) -> tuple[logging.Handler, ...]:
        with self._lock:
            return tuple(self._bound_handlers)

    def bind_handler(self, handler: logging.Handler) -> logging.Handler:
        with self._lock:
            if handler not in self._bound_handlers:
                self._bound_handlers.append(handler)
        return handler

    def set_level(self, level: int | str) -> int:
        from .handler import set_handler_level

        return set_handler_level(self, level)

    def _new_thread(self) -> threading.Thread:
        # Each writer owns its stop event: a writer that outlived stop()'s
        # join timeout must keep seeing "stop", even after a restart.
        return threading.Thread(
            target=self._run,
            args=(self._stop,),
            name="ox1log-writer",
            daemon=True,
        )

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    @property
    def running(self) -> bool:
        return self._started

    def start(self) -> Collector:
        """Start the writer. Safe to call again after `stop()`.

        Restarting matters for apps whose lifespan runs more than once in
        one process, e.g. a test suite opening `TestClient(app)` per test.
        """
        if self._started:
            return self
        if self._thread.ident is not None:
            # Threads are single-use and stop() closed the storage.
            reopen = getattr(self.storage, "reopen", None)
            if reopen is not None:
                reopen()
            self._stop = threading.Event()
            self._thread = self._new_thread()
        global _active_collector
        _active_collector = self
        self._started = True
        self._thread.start()
        return self

    def emit(self, event: dict[str, Any]) -> bool:
        """Queue one event. Never blocks; returns False when dropped."""
        if self._default_data:
            event = {
                **event,
                "data": {**self._default_data, **(event.get("data") or {})},
            }
        if not event.get("ts"):
            # Stamp when it happened, not when the writer gets to it: the
            # batch can land hundreds of ms later and reorder the trace.
            event = {**event, "ts": time.time()}
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False
        if self.alerter is not None and event.get("type") in getattr(
            self.alerter, "event_types", _ERROR_ONLY
        ):
            try:
                self.alerter.notify(event)
            except Exception:
                pass
        return True

    def flush(self, timeout: float = 10.0) -> bool:
        """Block until queued events are persisted. Returns False on timeout."""
        self._flush_now.set()
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True

    def flush_pending(self, timeout: float = 2.0) -> bool:
        """Block until events queued *at call time* are persisted.

        Unlike `flush()`, arrivals after the call don't extend the wait, so
        dashboard reads under continuous traffic stay fast (idle: instant).
        Returns False on timeout; callers proceed with whatever is visible.
        """
        # Read both counters atomically (the writer updates them together
        # under the lock); otherwise a batch finishing between the two reads
        # is counted as still owed and an idle dashboard waits the full timeout.
        with self._lock:
            owed = self._queue.unfinished_tasks
            done_at_entry = self._completed
        if owed <= 0:
            return True
        self._flush_now.set()
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                done = self._completed - done_at_entry
            if done >= owed:
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)

    def stop(self, timeout: float = 10.0) -> None:
        if not self._started:
            return
        global _active_collector
        if _active_collector is self:
            _active_collector = None
        self._started = False
        self._stop.set()
        self._thread.join(timeout=timeout)
        self.storage.close()

    def _insert(self, batch: list[dict[str, Any]]) -> None:
        try:
            self.storage.insert_many(batch)
        finally:
            with self._lock:
                for _ in batch:
                    self._queue.task_done()
                self._completed += len(batch)

    def _run(self, stop: threading.Event) -> None:
        batch: list[dict[str, Any]] = []
        last_flush = time.monotonic()
        while not stop.is_set():
            try:
                batch.append(self._queue.get(timeout=_IDLE_POLL))
            except queue.Empty:
                pass
            now = time.monotonic()
            flush_requested = self._flush_now.is_set()
            if batch and (
                flush_requested
                or len(batch) >= _BATCH_SIZE
                or now - last_flush >= _FLUSH_INTERVAL
            ):
                if flush_requested:
                    self._flush_now.clear()
                try:
                    self._insert(batch)
                except Exception as exc:
                    with self._lock:
                        self._dropped += len(batch)
                    logger.warning(
                        "0x1log: failed to persist batch of %d events: %s",
                        len(batch),
                        exc,
                    )
                batch = []
                last_flush = now
            elif flush_requested:
                self._flush_now.clear()
            if now - self._last_purge >= _PURGE_INTERVAL:
                self._last_purge = now
                try:
                    self.storage.purge()
                except Exception:
                    pass
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            try:
                self._insert(batch)
            except Exception as exc:
                with self._lock:
                    self._dropped += len(batch)
                logger.warning(
                    "0x1log: failed to persist shutdown batch of %d events: %s",
                    len(batch),
                    exc,
                )
