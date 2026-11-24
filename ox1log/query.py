"""Read API over storage: validated filters + pagination.

Framework-agnostic: this module must never import FastAPI/Starlette.
This is the API the dashboard (and user code) reads through.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .memory import MemoryStorage
from .postgres import PostgresStorage
from .redis_storage import RedisStorage
from .storage import SQLiteStorage

StorageLike = SQLiteStorage | PostgresStorage | MemoryStorage | RedisStorage

_MAX_LIMIT = 1000


@dataclass(frozen=True)
class LogFilter:
    text: str | None = None
    level: str | list[str] | None = None
    event_type: str | None = None
    trace_id: str | None = None
    signature: str | None = None
    source: str | None = None
    after_id: int | None = None
    since: float | None = None
    until: float | None = None
    limit: int = 100
    offset: int = 0

    def normalized(self) -> LogFilter:
        return LogFilter(
            text=self.text or None,
            level=self.level,
            event_type=self.event_type,
            trace_id=self.trace_id,
            signature=self.signature or None,
            source=self.source or None,
            after_id=int(self.after_id) if self.after_id is not None else None,
            since=self.since,
            until=self.until,
            limit=max(1, min(int(self.limit), _MAX_LIMIT)),
            offset=max(0, int(self.offset)),
        )


def search_logs(storage: StorageLike, filt: LogFilter) -> list[dict[str, Any]]:
    f = filt.normalized()
    extra: dict[str, Any] = {}
    if f.source is not None:
        extra["source"] = f.source
    if f.after_id is not None:
        extra["after_id"] = f.after_id
    return storage.search(
        text=f.text,
        level=f.level,
        event_type=f.event_type,
        trace_id=f.trace_id,
        signature=f.signature,
        since=f.since,
        until=f.until,
        limit=f.limit,
        offset=f.offset,
        **extra,
    )


def count_logs(storage: StorageLike, filt: LogFilter) -> int:
    f = filt.normalized()
    extra: dict[str, Any] = {}
    if f.source is not None:
        extra["source"] = f.source
    if f.after_id is not None:
        extra["after_id"] = f.after_id
    return storage.count(
        text=f.text,
        level=f.level,
        event_type=f.event_type,
        trace_id=f.trace_id,
        signature=f.signature,
        since=f.since,
        until=f.until,
        **extra,
    )


REPEAT_MAX = 1000
_FOLD_BATCH_MAX = 1000


def repeat_key(event: dict[str, Any]) -> tuple[Any, ...]:
    """What makes consecutive events one Repeat: type, name, level, status."""
    d = event.get("data") or {}
    kind = event.get("type")
    if kind == "span":
        name = d.get("name")
    elif kind == "request":
        name = f"{d.get('method')} {d.get('path')}"
    else:
        name = event.get("message")
    return (kind, name, event.get("level"), d.get("status"))


def _repeat_summary(members: list[dict[str, Any]], offset: int) -> dict[str, Any]:
    durations = [
        float(m["data"]["duration_ms"])
        for m in members
        if (m.get("data") or {}).get("duration_ms") is not None
    ]
    tokens = sum(int((m.get("data") or {}).get("total_tokens") or 0) for m in members)
    return {
        "count": len(members),
        "offset": offset,
        "first_id": members[-1]["id"],
        "first_ts": members[-1]["ts"],
        "last_ts": members[0]["ts"],
        "duration_min_ms": min(durations) if durations else None,
        "duration_max_ms": max(durations) if durations else None,
        "total_tokens": tokens or None,
        "traces": len({m["trace_id"] for m in members if m.get("trace_id")}),
    }


def fold_logs(
    storage: StorageLike, filt: LogFilter, expand: frozenset[int] = frozenset()
) -> dict[str, Any]:
    """One page of `limit` rows, newest first, where consecutive events with the
    same `repeat_key` fold into one Repeat (at most REPEAT_MAX events each).

    `offset` counts events, not rows; `next_offset` is where the next page starts
    (None on the last page). `next_cursor` ({until, skip}) reaches the same event
    as `until` plus `offset`, so the next page reads from the ts index instead
    of skipping every event before it. A Repeat row is its newest event plus a `repeat`
    summary; its events ride along as `events` only when its `first_id` is in
    `expand`.
    """
    f = filt.normalized()
    runs: list[tuple[int, list[dict[str, Any]]]] = []
    pos = f.offset
    next_offset: int | None = None
    next_cursor: dict[str, Any] | None = None
    # Consumed events sharing the newest-unread ts: the cursor's skip.
    tail_ts: float | None = None
    tail_same = 0
    # Most pages fold little, so read about two pages first and grow only for long Repeats.
    size = min(f.limit * 2, _FOLD_BATCH_MAX)
    while next_offset is None:
        batch = search_logs(storage, replace(f, limit=size, offset=pos, after_id=None))
        for event in batch:
            last = runs[-1][1] if runs else None
            if last and len(last) < REPEAT_MAX and repeat_key(last[0]) == repeat_key(event):
                last.append(event)
            elif len(runs) == f.limit:
                next_offset = pos
                skip = tail_same if tail_ts == event["ts"] else 0
                next_cursor = {"until": event["ts"], "skip": skip}
                break
            else:
                runs.append((pos, [event]))
            pos += 1
            if event["ts"] == tail_ts:
                tail_same += 1
            else:
                tail_ts, tail_same = event["ts"], 1
        if len(batch) < size:
            break
        size = min(size * 2, _FOLD_BATCH_MAX)
    items = []
    for start, members in runs:
        row = dict(members[0])
        if len(members) > 1:
            row["repeat"] = _repeat_summary(members, start)
            if row["repeat"]["first_id"] in expand:
                row["events"] = members
        items.append(row)
    return {"items": items, "offset": f.offset, "next_offset": next_offset, "next_cursor": next_cursor}


def list_sources(storage: Any) -> list[str]:
    fn = getattr(storage, "sources", None)
    if fn is None:
        return []
    return list(fn())


def log_histogram(
    storage: StorageLike, filt: LogFilter, buckets: int = 60
) -> dict[str, Any] | None:
    """Event volume over time for a filter, or None when the storage
    (a custom backend) does not implement `histogram`."""
    fn = getattr(storage, "histogram", None)
    if fn is None:
        return None
    f = filt.normalized()
    return fn(
        text=f.text,
        level=f.level,
        event_type=f.event_type,
        trace_id=f.trace_id,
        signature=f.signature,
        since=f.since,
        until=f.until,
        buckets=buckets,
    )


def get_event(storage: StorageLike, event_id: int) -> dict[str, Any] | None:
    return storage.get(int(event_id))


def top_errors(
    storage: StorageLike, since: float | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    return storage.top_signatures(since=since, limit=limit)


def get_trace(
    storage: StorageLike, trace_id: str, limit: int = 500
) -> list[dict[str, Any]]:
    """All events of one trace, oldest first (v1: flat, single-service).

    A unique prefix (≥8 chars) resolves to the full id so a truncated
    copy from the table still works.
    """
    rows = search_logs(storage, LogFilter(trace_id=trace_id, limit=limit))
    if not rows:
        resolved = storage.resolve_trace_id(trace_id)
        if resolved and resolved != trace_id:
            rows = search_logs(storage, LogFilter(trace_id=resolved, limit=limit))
    return list(reversed(rows))


def summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate execution and AI metrics across all events in a trace."""
    root_event = None
    min_ts = None
    max_end_ts = None
    span_count = 0
    error_count = 0
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    models_used: list[str] = []
    tool_calls_count = 0

    for e in events:
        t = e.get("type")
        data = e.get("data") or {}
        ts = float(e.get("ts", 0))
        dur_ms = float(data.get("duration_ms") or 0)
        end_ts = ts + (dur_ms / 1000.0)

        if min_ts is None or ts < min_ts:
            min_ts = ts
        if max_end_ts is None or end_ts > max_end_ts:
            max_end_ts = end_ts

        if t == "request" and root_event is None:
            root_event = e
        elif t == "span":
            span_count += 1

        if (
            t == "error"
            or e.get("level") == "CRITICAL"
            or data.get("status") == "error"
        ):
            error_count += 1

        # AI statistics
        if "total_tokens" in data and data["total_tokens"] is not None:
            total_tokens += int(data["total_tokens"])
        if "prompt_tokens" in data and data["prompt_tokens"] is not None:
            prompt_tokens += int(data["prompt_tokens"])
        if "completion_tokens" in data and data["completion_tokens"] is not None:
            completion_tokens += int(data["completion_tokens"])
        if "model" in data and data["model"] and data["model"] not in models_used:
            models_used.append(data["model"])
        if "tool_calls" in data and isinstance(data["tool_calls"], list):
            tool_calls_count += len(data["tool_calls"])

    extent_ms = (
        round((max_end_ts - min_ts) * 1000.0, 3)
        if min_ts is not None and max_end_ts is not None
        else 0.0
    )
    root_data = (root_event or {}).get("data") or {}
    if root_data.get("duration_ms") is not None:
        # Background work can outlive the response; the timeline must fit
        # every event or the waterfall clips them at the right edge.
        total_duration_ms = max(float(root_data["duration_ms"]), extent_ms)
    else:
        total_duration_ms = extent_ms

    return {
        "total_duration_ms": total_duration_ms,
        "event_count": len(events),
        "span_count": span_count,
        "error_count": error_count,
        "start_ts": min_ts,
        "has_ai": bool(
            total_tokens
            or models_used
            or tool_calls_count
            or any(
                e.get("data", {}).get("span_type")
                in ("llm", "tool", "embedding", "agent")
                for e in events
            )
        ),
        "ai_summary": {
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "models": models_used,
            "tool_calls_count": tool_calls_count,
        },
    }

