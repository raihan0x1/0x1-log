"""Seam 1A: MemoryStorage, RedisStorage, and source/after_id/sources query contract."""

from __future__ import annotations

import time
from typing import Any

import pytest

from ox1log.memory import MemoryStorage
from ox1log.query import LogFilter, count_logs, get_event, list_sources, search_logs
from ox1log.redis_storage import RedisStorage
from ox1log.storage import SQLiteStorage


class _FakePipeline:
    def __init__(self, store: _FakeRedis) -> None:
        self._store = store
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def lpush(self, key: str, *values: str) -> _FakePipeline:
        self._ops.append(("lpush", (key, *values)))
        return self

    def ltrim(self, key: str, start: int, end: int) -> _FakePipeline:
        self._ops.append(("ltrim", (key, start, end)))
        return self

    def sadd(self, key: str, *values: str) -> _FakePipeline:
        self._ops.append(("sadd", (key, *values)))
        return self

    def expire(self, key: str, seconds: int) -> _FakePipeline:
        self._ops.append(("expire", (key, seconds)))
        return self

    def lrange(self, key: str, start: int, end: int) -> _FakePipeline:
        self._ops.append(("lrange", (key, start, end)))
        return self

    def execute(self) -> list[Any]:
        results = []
        for name, args in self._ops:
            results.append(getattr(self._store, name)(*args))
        self._ops.clear()
        return results


class _FakeRedis:
    """In-memory fake implementing the Redis subset used by RedisStorage."""

    def __init__(self) -> None:
        self.kv: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttls: dict[str, int] = {}

    def incrby(self, key: str, amount: int = 1) -> int:
        self.kv[key] = self.kv.get(key, 0) + amount
        return self.kv[key]

    def lpush(self, key: str, *values: str) -> int:
        lst = self.lists.setdefault(key, [])
        for v in values:
            lst.insert(0, v)
        return len(lst)

    def ltrim(self, key: str, start: int, end: int) -> bool:
        lst = self.lists.get(key)
        if lst is None:
            return True
        if end < 0:
            end = len(lst) + end
        self.lists[key] = lst[start : end + 1]
        return True

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        lst = self.lists.get(key, [])
        if end < 0:
            end = len(lst) + end
        return list(lst[start : end + 1])

    def sadd(self, key: str, *values: str) -> int:
        s = self.sets.setdefault(key, set())
        before = len(s)
        s.update(values)
        return len(s) - before

    def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    def pipeline(self, transaction: bool = False) -> _FakePipeline:
        return _FakePipeline(self)

    def close(self) -> None:
        return None


def _event(msg: str, *, level: str = "INFO", source: str = "api", ts: float | None = None) -> dict[str, Any]:
    return {
        "ts": ts if ts is not None else time.time(),
        "type": "log",
        "level": level,
        "message": msg,
        "trace_id": None,
        "data": {"source": source},
    }


def test_memory_storage_ring_buffer_isolates_sources_and_evicts_oldest():
    storage = MemoryStorage(max_events=3)
    storage.insert_many([
        _event("api-1", source="api"),
        _event("api-2", source="api"),
        _event("worker-1", source="worker"),
        _event("worker-2", source="worker"),
        _event("worker-3", source="worker"),
        _event("worker-4", source="worker"),
        _event("worker-5", source="worker"),
    ])

    assert list_sources(storage) == ["api", "worker"]

    worker_rows = search_logs(storage, LogFilter(source="worker", limit=10))
    assert [r["message"] for r in worker_rows] == ["worker-5", "worker-4", "worker-3"]
    assert count_logs(storage, LogFilter(source="worker")) == 3

    # Noisy worker did not evict api logs.
    api_rows = search_logs(storage, LogFilter(source="api", limit=10))
    assert [r["message"] for r in api_rows] == ["api-2", "api-1"]


def test_memory_storage_after_id_cursor_returns_ascending_without_gaps():
    storage = MemoryStorage(max_events=20)
    storage.insert_many([_event(f"m-{i}", source="api") for i in range(1, 7)])

    all_desc = search_logs(storage, LogFilter(limit=10))
    assert [r["message"] for r in all_desc] == ["m-6", "m-5", "m-4", "m-3", "m-2", "m-1"]

    first_id = all_desc[-1]["id"]
    page1 = search_logs(storage, LogFilter(after_id=first_id, limit=2))
    assert [r["message"] for r in page1] == ["m-2", "m-3"]

    page2 = search_logs(storage, LogFilter(after_id=page1[-1]["id"], limit=10))
    assert [r["message"] for r in page2] == ["m-4", "m-5", "m-6"]


def test_memory_storage_filters_level_text_and_truncates_large_messages():
    storage = MemoryStorage(max_events=20, max_message_chars=120)
    big = "x" * 400
    storage.insert_many([
        _event("healthy boot", level="INFO", source="api"),
        _event("redis timeout occurred", level="ERROR", source="worker"),
        _event(big, level="WARNING", source="api"),
    ])

    err = search_logs(storage, LogFilter(level=["ERROR"], text="timeout"))
    assert len(err) == 1
    assert err[0]["data"]["source"] == "worker"
    assert get_event(storage, err[0]["id"])["message"] == "redis timeout occurred"

    warn = search_logs(storage, LogFilter(level="WARNING"))
    assert len(warn[0]["message"]) <= 120


def test_redis_storage_ring_buffer_namespace_ttl_and_cursor():
    fake = _FakeRedis()
    agents = RedisStorage(
        "redis://unused",
        key_prefix="ox1log:agents",
        max_events=3,
        ttl_seconds=86400,
        max_message_chars=120,
        client=fake,
    )
    media = RedisStorage(
        "redis://unused",
        key_prefix="ox1log:media",
        max_events=3,
        ttl_seconds=86400,
        client=fake,
    )

    agents.insert_many([
        _event("agents-api-1", source="api"),
        _event("agents-worker-1", source="worker"),
        _event("agents-worker-2", source="worker"),
        _event("agents-worker-3", source="worker"),
        _event("agents-worker-4", source="worker", level="ERROR"),
    ])
    media.insert_many([_event("media-only", source="api")])

    # Namespaces stay isolated even on the same Redis client.
    assert list_sources(agents) == ["api", "worker"]
    assert list_sources(media) == ["api"]
    assert [r["message"] for r in search_logs(media, LogFilter(limit=10))] == ["media-only"]

    # Per-source cap keeps latest 3 worker events and leaves api untouched.
    worker_rows = search_logs(agents, LogFilter(source="worker", limit=10))
    assert [r["message"] for r in worker_rows] == [
        "agents-worker-4",
        "agents-worker-3",
        "agents-worker-2",
    ]
    assert fake.ttls["ox1log:agents:src:worker"] == 86400
    assert fake.ttls["ox1log:agents:sources"] == 86400

    # Cursor polling works across sources and returns ascending ids.
    oldest_worker_id = worker_rows[-1]["id"]
    newer = search_logs(agents, LogFilter(source="worker", after_id=oldest_worker_id, limit=10))
    assert [r["message"] for r in newer] == ["agents-worker-3", "agents-worker-4"]
    assert count_logs(agents, LogFilter(source="worker", level="ERROR")) == 1


def test_sqlite_storage_supports_source_after_id_and_list_sources(tmp_path):
    storage = SQLiteStorage(str(tmp_path / "logs.db"))
    try:
        storage.insert_many([
            _event("a1", source="api"),
            _event("w1", source="worker"),
            _event("a2", source="api"),
        ])
        assert list_sources(storage) == ["api", "worker"]
        api_rows = search_logs(storage, LogFilter(source="api", limit=10))
        assert [r["message"] for r in api_rows] == ["a2", "a1"]
        after = search_logs(storage, LogFilter(after_id=api_rows[-1]["id"], limit=10))
        assert [r["message"] for r in after] == ["w1", "a2"]
    finally:
        storage.close()
