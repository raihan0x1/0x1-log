"""In-memory bounded ring-buffer storage: zero disk, zero database, zero infra.

Framework-agnostic: this module must never import FastAPI/Starlette.
Each log source keeps its own bounded deque so a noisy worker never evicts API
logs. Events live for the lifetime of the process.
"""

from __future__ import annotations

from collections import deque
import json
import threading
import time
from typing import Any, Iterable

from .grouping import truncate_message

_MAX_LIMIT = 1000
DEFAULT_MAX_EVENTS = 5000
DEFAULT_MAX_MESSAGE_CHARS = 8192
# Ring buffers keep whole events and dashboard reads parse every one, so an event's data
# is capped as a whole and a ring holds at most max_events x this. Uncapped, 1,090 spans
# with whole vector-search bodies (up to 632 KB) made one Redis list 581 MB (2026-11-20).
MAX_EVENT_DATA_CHARS = 16_384
_MIN_KEPT_CHARS = 512
_DEFAULT_SOURCE = "default"


def event_source(event: dict[str, Any]) -> str:
    data = event.get("data")
    if isinstance(data, dict):
        src = data.get("source")
        if isinstance(src, str) and src.strip():
            return src.strip()
    return _DEFAULT_SOURCE


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except ValueError:  # circular structure
        return str(value)


def _cap_data(data: dict[str, Any]) -> dict[str, Any]:
    """Shrink the largest values, as truncated JSON text, until data fits MAX_EVENT_DATA_CHARS."""
    texts = {
        key: _as_text(value)
        for key, value in data.items()
        if value is not None and not isinstance(value, (bool, int, float))
    }
    total = sum(len(text) for text in texts.values())
    for key in sorted(texts, key=lambda k: len(texts[k]), reverse=True):
        excess = total - MAX_EVENT_DATA_CHARS
        if excess <= 0:
            break
        keep = max(_MIN_KEPT_CHARS, len(texts[key]) - excess)
        if keep < len(texts[key]):
            data[key] = truncate_message(texts[key], keep)
            total -= len(texts[key]) - len(data[key])
    return data


def normalize_event(
    event: dict[str, Any], event_id: int, max_message_chars: int
) -> dict[str, Any]:
    raw_msg = event.get("message")
    text = "" if raw_msg is None else raw_msg if isinstance(raw_msg, str) else str(raw_msg)
    if max_message_chars > 0 and len(text) > max_message_chars:
        text = truncate_message(text, max_message_chars)
    raw_data = event.get("data")
    data = _cap_data(dict(raw_data)) if isinstance(raw_data, dict) else {}
    return {
        "id": event_id,
        "ts": float(event.get("ts") or time.time()),
        "type": str(event.get("type") or "log"),
        "level": event.get("level"),
        "message": text,
        "trace_id": event.get("trace_id"),
        "data": data,
    }


def matches_event(
    event: dict[str, Any],
    *,
    text: str | None = None,
    levels: set[str] | None = None,
    event_type: str | None = None,
    trace_id: str | None = None,
    signature: str | None = None,
    source: str | None = None,
    after_id: int | None = None,
    since: float | None = None,
    until: float | None = None,
) -> bool:
    if after_id is not None and int(event["id"]) <= after_id:
        return False
    if levels is not None and event.get("level") not in levels:
        return False
    if event_type and event.get("type") != event_type:
        return False
    if trace_id and event.get("trace_id") != trace_id:
        return False
    if source and event_source(event) != source:
        return False
    if signature and (event.get("data") or {}).get("signature") != signature:
        return False
    ts = float(event.get("ts") or 0.0)
    if since is not None and ts < since:
        return False
    if until is not None and ts > until:
        return False
    if text and text.lower() not in str(event.get("message") or "").lower():
        return False
    return True


def normalize_levels(level: str | list[str] | None) -> set[str] | None:
    if not level:
        return None
    if isinstance(level, str):
        return {level}
    return set(level)


def compute_histogram(
    events: list[dict[str, Any]],
    since: float | None,
    until: float | None,
    buckets: int,
) -> dict[str, Any]:
    buckets = max(1, min(int(buckets), 500))
    end = time.time() if until is None else float(until)
    if since is None:
        lo = min((float(e["ts"]) for e in events), default=None)
        start = lo if lo is not None else end - 3600.0
    else:
        start = float(since)
    width = max(end - start, 1.0) / buckets
    out = [
        {"ts": start + i * width, "total": 0, "errors": 0, "warnings": 0}
        for i in range(buckets)
    ]
    for e in events:
        ts = float(e["ts"])
        if ts < start or ts > end:
            continue
        i = max(0, min(buckets - 1, int((ts - start) / width)))
        out[i]["total"] += 1
        if e.get("type") == "error" or e.get("level") in ("ERROR", "CRITICAL"):
            out[i]["errors"] += 1
        if e.get("level") == "WARNING":
            out[i]["warnings"] += 1
    return {"since": start, "until": end, "width": width, "buckets": out}


def compute_top_signatures(
    events: Iterable[dict[str, Any]], since: float | None, limit: int
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), _MAX_LIMIT))
    groups: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("type") != "error":
            continue
        ts = float(e.get("ts") or 0.0)
        if since is not None and ts < since:
            continue
        sig = (e.get("data") or {}).get("signature") or "(unknown)"
        cur = groups.get(sig)
        if cur is None:
            groups[sig] = {
                "signature": sig,
                "count": 1,
                "last_ts": ts,
                "last_id": int(e["id"]),
                "last_message": e.get("message") or "",
            }
        else:
            cur["count"] += 1
            if int(e["id"]) >= cur["last_id"]:
                cur["last_ts"] = max(cur["last_ts"], ts)
                cur["last_id"] = int(e["id"])
                cur["last_message"] = e.get("message") or ""
    ranked = sorted(
        groups.values(),
        key=lambda g: (-g["count"], -g["last_ts"]),
    )[:limit]
    return [
        {
            "signature": g["signature"],
            "count": g["count"],
            "last_ts": g["last_ts"],
            "last_message": g["last_message"],
        }
        for g in ranked
    ]


class MemoryStorage:
    """Thread-safe in-memory ring buffer partitioned by log source."""

    shared = False
    storage_kind = "memory"

    def __init__(
        self,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    ) -> None:
        self.max_events = max(1, int(max_events))
        self.max_message_chars = max(0, int(max_message_chars))
        self.retention_days = 0
        self._lock = threading.Lock()
        self._next_id = 1
        self._buckets: dict[str, deque[dict[str, Any]]] = {}
        self._by_id: dict[int, dict[str, Any]] = {}
        self._closed = False

    @property
    def fts_available(self) -> bool:
        return False

    @property
    def closed(self) -> bool:
        return self._closed

    def reopen(self) -> None:
        with self._lock:
            self._closed = False

    def insert_many(
        self, events: Iterable[dict[str, Any]], max_retries: int = 5
    ) -> int:
        _ = max_retries
        items = list(events)
        if not items:
            return 0
        with self._lock:
            for raw in items:
                eid = self._next_id
                self._next_id += 1
                ev = normalize_event(raw, eid, self.max_message_chars)
                src = event_source(ev)
                dq = self._buckets.get(src)
                if dq is None:
                    dq = deque(maxlen=self.max_events)
                    self._buckets[src] = dq
                if len(dq) == dq.maxlen:
                    evicted = dq[0]
                    self._by_id.pop(int(evicted["id"]), None)
                dq.append(ev)
                self._by_id[eid] = ev
        return len(items)

    def _snapshot(self, source: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if source:
                dq = self._buckets.get(source)
                return list(dq) if dq else []
            out: list[dict[str, Any]] = []
            for dq in self._buckets.values():
                out.extend(dq)
            return out

    def sources(self) -> list[str]:
        with self._lock:
            return sorted(
                src for src, dq in self._buckets.items() if len(dq) > 0
            )

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
        rows = [
            ev
            for ev in self._snapshot(source)
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
        return sum(
            1
            for ev in self._snapshot(source)
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
            for ev in self._snapshot(source)
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
        return compute_top_signatures(self._snapshot(), since, limit)

    def resolve_trace_id(self, prefix: str) -> str | None:
        if not prefix or len(prefix) < 8:
            return None
        found = {
            ev["trace_id"]
            for ev in self._snapshot()
            if isinstance(ev.get("trace_id"), str) and ev["trace_id"].startswith(prefix)
        }
        return next(iter(found)) if len(found) == 1 else None

    def fetch_requests(
        self, since: float | None = None, until: float | None = None
    ) -> list[dict[str, Any]]:
        rows = []
        for ev in self._snapshot():
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
        with self._lock:
            return self._by_id.get(int(event_id))

    def purge(self, max_retries: int = 3) -> int:
        _ = max_retries
        return 0

    def close(self) -> None:
        with self._lock:
            self._closed = True
