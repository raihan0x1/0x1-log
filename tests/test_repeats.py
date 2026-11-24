import time

import pytest

from ox1log import query
from ox1log.memory import MemoryStorage
from ox1log.query import LogFilter, fold_logs
from ox1log.storage import SQLiteStorage


def _span(name, ts, ms=2000.0, status="ok", trace="t", tokens=None):
    data = {"name": name, "span_type": "http", "duration_ms": ms, "status": status}
    if tokens:
        data["total_tokens"] = tokens
    return {"ts": ts, "type": "span", "level": "ERROR" if status == "error" else "INFO",
            "message": name, "trace_id": trace, "data": data}


@pytest.fixture(params=["memory", "sqlite"])
def storage(request, tmp_path):
    s = MemoryStorage() if request.param == "memory" else SQLiteStorage(str(tmp_path / "r.db"))
    yield s
    s.close()


def _names(page):
    return [(r["data"].get("name") or r["message"], (r.get("repeat") or {}).get("count", 1))
            for r in page["items"]]


def test_consecutive_same_key_folds_and_others_break_the_run(storage):
    now = time.time()
    # Oldest first: 3 searches, a log line, 2 searches, a failed search, 1 llm call.
    events = [_span("search", now - 10 + i, ms=1950 + i * 10, trace=f"t{i % 2}") for i in range(3)]
    events.append({"ts": now - 6, "type": "log", "level": "INFO", "message": "matching", "data": {}})
    events += [_span("search", now - 5 + i) for i in range(2)]
    events.append(_span("search", now - 2, status="error"))
    events.append(_span("agent", now - 1, tokens=500))
    storage.insert_many(events)

    page = fold_logs(storage, LogFilter(limit=50))

    assert _names(page) == [("agent", 1), ("search", 1), ("search", 2), ("matching", 1), ("search", 3)]
    assert page["next_offset"] is None
    first = page["items"][-1]["repeat"]
    assert first["offset"] == 5
    assert (first["duration_min_ms"], first["duration_max_ms"]) == (1950.0, 1970.0)
    assert first["traces"] == 2
    assert "events" not in page["items"][-1]


def test_expand_returns_the_events_of_that_repeat_only(storage):
    now = time.time()
    storage.insert_many([_span("search", now - 3 + i) for i in range(3)])
    plain = fold_logs(storage, LogFilter(limit=50))
    first_id = plain["items"][0]["repeat"]["first_id"]

    opened = fold_logs(storage, LogFilter(limit=50), frozenset({first_id}))

    assert [e["id"] for e in opened["items"][0]["events"]] == [first_id + 2, first_id + 1, first_id]


def test_page_limit_counts_rows_and_next_offset_counts_events(storage):
    now = time.time()
    storage.insert_many([_span("search", now - 20 + i) for i in range(5)]
                        + [_span(f"call{i}", now - 10 + i) for i in range(3)])

    page1 = fold_logs(storage, LogFilter(limit=3))
    page2 = fold_logs(storage, LogFilter(limit=3, offset=page1["next_offset"]))

    assert _names(page1) == [("call2", 1), ("call1", 1), ("call0", 1)]
    assert page1["next_offset"] == 3
    assert _names(page2) == [("search", 5)]
    assert page2["next_offset"] is None


def test_repeat_is_capped_and_runs_across_batches(storage, monkeypatch):
    monkeypatch.setattr(query, "REPEAT_MAX", 4)
    monkeypatch.setattr(query, "_FOLD_BATCH_MAX", 3)
    now = time.time()
    storage.insert_many([_span("search", now - 10 + i) for i in range(6)])

    page = fold_logs(storage, LogFilter(limit=50))

    assert _names(page) == [("search", 4), ("search", 2)]
    assert page["items"][1]["repeat"]["offset"] == 4


def test_dashboard_api_serves_repeats(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ox1log import observe

    app = FastAPI()
    collector = observe(app, db_path=str(tmp_path / "d.db"), dashboard_token="tok")
    now = time.time()
    collector.storage.insert_many([_span("search", now - 3 + i) for i in range(3)])
    client = TestClient(app)
    q = {"token": "tok", "type": "span", "repeats": "true"}

    res = client.get("/0x1log/api/logs", params=q).json()
    first_id = res["items"][0]["repeat"]["first_id"]
    opened = client.get("/0x1log/api/logs", params={**q, "expand": f"{first_id},junk"}).json()

    assert res["total"] == 3 and res["next_offset"] is None
    assert len(res["items"]) == 1 and "events" not in res["items"][0]
    assert len(opened["items"][0]["events"]) == 3


def _walk(storage, step):
    pages, filt = [], LogFilter(limit=3)
    while True:
        page = fold_logs(storage, filt)
        pages.append([r["id"] for r in page["items"]])
        if page["next_offset"] is None:
            assert page["next_cursor"] is None
            return pages
        filt = step(filt, page)


def test_cursor_pages_match_offset_pages_even_across_tied_timestamps(storage):
    now = time.time()
    # Alternating kinds so nothing folds; three events share one ts across a page edge.
    tied = now - 95  # between events 3 and 7, so pages split the tie
    events = []
    for i in range(12):
        ts = tied if i in (4, 5, 6) else now - 100 + i
        events.append(_span(f"s{i % 2}", ts))
    storage.insert_many(events)

    by_offset = _walk(storage, lambda f, p: LogFilter(limit=3, offset=p["next_offset"]))
    by_cursor = _walk(storage, lambda f, p: LogFilter(
        limit=3, until=p["next_cursor"]["until"], offset=p["next_cursor"]["skip"]))

    assert by_cursor == by_offset
    assert sum(len(p) for p in by_cursor) == 12
