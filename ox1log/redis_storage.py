"""Redis bounded ring-buffer storage: shared ephemeral logs across pods/workers.

Each log source gets its own Redis LIST capped at `max_events` with `LTRIM` and
an expiring TTL, under a service-specific `key_prefix`. No per-event keys, no
Postgres writes, safe alongside Celery/RQ queues.

Requires the ``redis`` extra (``pip install 0x1-log[redis]``). The driver import
stays lazy so ``import ox1log`` never requires redis.

Framework-agnostic: this module must never import FastAPI/Starlette.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Iterable

from .memory import (
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_MESSAGE_CHARS,
    compute_histogram,
    compute_top_signatures,
    event_source,
    matches_event,
    normalize_event,
    normalize_levels,
)

try:
    import redis as _redis
except ImportError:  # pragma: no cover - exercised when extra not installed
    _redis = None  # type: ignore[assignment]

_MAX_LIMIT = 1000
DEFAULT_KEY_PREFIX = "ox1log"
DEFAULT_TTL_SECONDS = 86_400  # 24 hours
_CURSOR_CHUNK = 200


def _require_driver() -> Any:
    if _redis is None:
        raise ImportError(
            "RedisStorage needs the 'redis' extra: pip install '0x1-log[redis]'"
        )
    return _redis


def _decode(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


def _decode_str(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


class RedisStorage:
    """Shared bounded ring buffer backed by Redis LISTs per log source."""

    shared = True
    storage_kind = "redis"

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        *,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        max_events: int = DEFAULT_MAX_EVENTS,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
        socket_timeout: float = 3.0,
        client: Any | None = None,
    ) -> None:
        self.url = url
        self.key_prefix = (key_prefix or DEFAULT_KEY_PREFIX).rstrip(":")
        self.max_events = max(1, int(max_events))
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.max_message_chars = max(0, int(max_message_chars))
        self.socket_timeout = float(socket_timeout)
        self.retention_days = 0
        self._lock = threading.Lock()
        self._owns_client = client is None
        self._client = client if client is not None else self._connect()
        self._closed = False

    def _connect(self) -> Any:
        mod = _require_driver()
        return mod.Redis.from_url(
            self.url,
            decode_responses=True,
            socket_connect_timeout=self.socket_timeout,
            socket_timeout=self.socket_timeout,
        )

    @property
    def fts_available(self) -> bool:
        return False

    @property
    def closed(self) -> bool:
        return self._closed

    def reopen(self) -> None:
        with self._lock:
            if self._closed:
                if self._owns_client:
                    self._client = self._connect()
                self._closed = False

    def _seq_key(self) -> str:
        return f"{self.key_prefix}:seq"

    def _sources_key(self) -> str:
        return f"{self.key_prefix}:sources"

    def _list_key(self, source: str) -> str:
        return f"{self.key_prefix}:src:{source}"

    def insert_many(
        self, events: Iterable[dict[str, Any]], max_retries: int = 5
    ) -> int:
        _ = max_retries
        items = list(events)
        if not items:
            return 0
        # Reserve one contiguous block of IDs in a single round-trip.
        end_id = int(self._client.incrby(self._seq_key(), len(items)))
        start_id = end_id - len(items) + 1

        by_source: dict[str, list[str]] = {}
        for idx, raw in enumerate(items):
            ev = normalize_event(raw, start_id + idx, self.max_message_chars)
            src = event_source(ev)
            payload = json.dumps(ev, separators=(",", ":"), default=str)
            by_source.setdefault(src, []).append(payload)

        pipe = self._client.pipeline(transaction=False)
        sources_key = self._sources_key()
        for src, payloads in by_source.items():
            list_key = self._list_key(src)
            # LPUSH inserts left-to-right so the last (newest) item lands at index 0.
            pipe.lpush(list_key, *payloads)
            pipe.ltrim(list_key, 0, self.max_events - 1)
            pipe.sadd(sources_key, src)
            if self.ttl_seconds > 0:
                pipe.expire(list_key, self.ttl_seconds)
        if self.ttl_seconds > 0:
            pipe.expire(sources_key, self.ttl_seconds)
        pipe.execute()
        return len(items)

    def sources(self) -> list[str]:
        raw = self._client.smembers(self._sources_key()) or set()
        return sorted(_decode_str(s) for s in raw if s)

    def _read_source_events(
        self,
        src: str,
        *,
        max_read: int | None = None,
        after_id: int | None = None,
    ) -> list[dict[str, Any]]:
        key = self._list_key(src)
        if after_id is not None:
            # Scan newest-first in small chunks and stop once we pass after_id.
            collected: list[dict[str, Any]] = []
            start = 0
            while start < self.max_events:
                chunk = self._client.lrange(key, start, start + _CURSOR_CHUNK - 1)
                if not chunk:
                    break
                reached_cursor = False
                for raw in chunk:
                    ev = _decode(raw)
                    if ev is None:
                        continue
                    if int(ev["id"]) <= after_id:
                        reached_cursor = True
                        break
                    collected.append(ev)
                if reached_cursor or len(chunk) < _CURSOR_CHUNK:
                    break
                start += _CURSOR_CHUNK
            return collected

        end = (max_read - 1) if max_read is not None else (self.max_events - 1)
        raw_items = self._client.lrange(key, 0, end)
        out: list[dict[str, Any]] = []
        for raw in raw_items:
            ev = _decode(raw)
            if ev is not None:
                out.append(ev)
        return out

    def _read_events(
        self,
        source: str | None = None,
        *,
        max_read_per_source: int | None = None,
        after_id: int | None = None,
    ) -> list[dict[str, Any]]:
        target_sources = [source] if source else self.sources()
        if not target_sources:
            return []
        if after_id is not None or len(target_sources) == 1:
            out: list[dict[str, Any]] = []
            for src in target_sources:
                out.extend(
                    self._read_source_events(
                        src, max_read=max_read_per_source, after_id=after_id
                    )
                )
            return out

        end = (
            max_read_per_source - 1
            if max_read_per_source is not None
            else self.max_events - 1
        )
        pipe = self._client.pipeline(transaction=False)
        for src in target_sources:
            pipe.lrange(self._list_key(src), 0, end)
        batches = pipe.execute()
        out = []
        for raw_items in batches:
            for raw in raw_items:
                ev = _decode(raw)
                if ev is not None:
                    out.append(ev)
        return out

    def search(
        self,
        text: str | None = None,
        level: str | list[str] | None = None,
        event_type: str | None = None,
        trace_id: str | None = None,
        signature: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
        offset: int = 0,
        *,
        source: str | None = None,
        after_id: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), _MAX_LIMIT))
        offset = max(0, int(offset))
        levels = normalize_levels(level)
        unfiltered = (
            after_id is None
            and not text
            and levels is None
            and not event_type
            and not trace_id
            and not signature
            and since is None
            and until is None
        )
        max_read = (offset + limit) if unfiltered else None
        candidates = self._read_events(
            source, max_read_per_source=max_read, after_id=after_id
        )
        rows = [
            ev
            for ev in candidates
            if matches_event(
                ev,
                text=text,
                levels=levels,
                event_type=event_type,
                trace_id=trace_id,
                signature=signature,
                source=source,
                after_id=after_id,
                since=since,
                until=until,
            )
        ]
        if after_id is not None:
            rows.sort(key=lambda e: int(e["id"]))
        else:
            rows.sort(key=lambda e: (float(e["ts"]), int(e["id"])), reverse=True)
        return rows[offset : offset + limit]

    def count(
        self,
        text: str | None = None,
        level: str | list[str] | None = None,
        event_type: str | None = None,
        trace_id: str | None = None,
        signature: str | None = None,
        since: float | None = None,
        until: float | None = None,
        *,
        source: str | None = None,
        after_id: int | None = None,
    ) -> int:
        levels = normalize_levels(level)
        candidates = self._read_events(source, after_id=after_id)
        return sum(
            1
            for ev in candidates
            if matches_event(
                ev,
                text=text,
                levels=levels,
                event_type=event_type,
                trace_id=trace_id,
                signature=signature,
                source=source,
                after_id=after_id,
                since=since,
                until=until,
            )
        )

    def histogram(
        self,
        text: str | None = None,
        level: str | list[str] | None = None,
        event_type: str | None = None,
        trace_id: str | None = None,
        signature: str | None = None,
        since: float | None = None,
        until: float | None = None,
        buckets: int = 60,
        *,
        source: str | None = None,
    ) -> dict[str, Any]:
        levels = normalize_levels(level)
        matched = [
            ev
            for ev in self._read_events(source)
            if matches_event(
                ev,
                text=text,
                levels=levels,
                event_type=event_type,
                trace_id=trace_id,
                signature=signature,
                source=source,
                since=since,
                until=until,
            )
        ]
        return compute_histogram(matched, since, until, buckets)

    def top_signatures(
        self, since: float | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        return compute_top_signatures(self._read_events(), since, limit)

    def resolve_trace_id(self, prefix: str) -> str | None:
        if not prefix or len(prefix) < 8:
            return None
        found = {
            ev["trace_id"]
            for ev in self._read_events()
            if isinstance(ev.get("trace_id"), str) and ev["trace_id"].startswith(prefix)
        }
        return next(iter(found)) if len(found) == 1 else None

    def fetch_requests(
        self, since: float | None = None, until: float | None = None
    ) -> list[dict[str, Any]]:
        rows = []
        for ev in self._read_events():
            if ev.get("type") != "request":
                continue
            ts = float(ev["ts"])
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            data = ev.get("data") or {}
            rows.append(
                {
                    "ts": ts,
                    "status": data.get("status"),
                    "duration_ms": data.get("duration_ms"),
                }
            )
        rows.sort(key=lambda r: r["ts"])
        return rows

    def get(self, event_id: int) -> dict[str, Any] | None:
        target = int(event_id)
        for ev in self._read_events():
            if int(ev["id"]) == target:
                return ev
        return None

    def purge(self, max_retries: int = 3) -> int:
        _ = max_retries
        return 0

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                if self._owns_client:
                    try:
                        self._client.close()
                    except Exception:
                        pass
