"""Seam 1B: mode='lite', dynamic level sync, /0x1log/api/logs source+after_id, and /health sources."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ox1log import Collector, MemoryStorage, attach, observe
from ox1log.handler import Ox1logHandler


def _cleanup_handlers(collector) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, Ox1logHandler) and h.collector is collector:
            root.removeHandler(h)
            h.close()


def test_lite_mode_defaults_to_memory_skips_request_middleware_and_filters_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = FastAPI()

    @app.get("/ping")
    def ping():
        logging.getLogger("app.api").info("ping-handled")
        return {"ok": True}

    collector = observe(app, mode="lite", dashboard_token="secret-tok", level=logging.INFO)
    # Separate collector writing to the same shared storage simulates a worker process.
    worker_collector = Collector(
        storage=collector.storage, default_data={"source": "worker"}, mode="lite"
    ).start()
    try:
        # Zero files created on disk in lite memory mode.
        assert list(tmp_path.iterdir()) == []
        assert isinstance(collector.storage, MemoryStorage)

        with TestClient(app) as client:
            assert client.get("/ping").status_code == 200
            worker_collector.emit({"type": "log", "level": "WARNING", "message": "worker-job-slow"})
            assert collector.flush()
            assert worker_collector.flush()

            # Auth enforcement on /0x1log/api/*
            assert client.get("/0x1log/api/health").status_code == 401
            assert client.get("/0x1log/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 403

            headers = {"Authorization": "Bearer secret-tok"}
            health = client.get("/0x1log/api/health", headers=headers)
            assert health.status_code == 200
            hbody = health.json()
            assert hbody["mode"] == "lite"
            assert hbody["storage"] == "memory"
            assert hbody["sources"] == ["api", "worker"]

            # No HTTP request events captured in lite mode.
            req_resp = client.get("/0x1log/api/logs?type=request", headers=headers)
            assert req_resp.json()["items"] == []

            # Source filter isolates worker logs.
            worker_resp = client.get("/0x1log/api/logs?source=worker", headers=headers)
            worker_items = worker_resp.json()["items"]
            assert [i["message"] for i in worker_items] == ["worker-job-slow"]

            # Cursor polling with after_id fetches only new entries in ascending order.
            all_items = client.get("/0x1log/api/logs", headers=headers).json()["items"]
            last_id = max(i["id"] for i in all_items)
            logging.getLogger("app.api").info("after-1")
            logging.getLogger("app.api").error("after-2")
            assert collector.flush()

            delta = client.get(f"/0x1log/api/logs?source=api&after_id={last_id}", headers=headers)
            assert [i["message"] for i in delta.json()["items"]] == ["after-1", "after-2"]
    finally:
        _cleanup_handlers(collector)
        _cleanup_handlers(worker_collector)
        collector.stop()
        worker_collector.stop()


def test_dynamic_level_sync_follows_terminal_switches():
    app = FastAPI()
    collector = observe(app, mode="lite", dashboard_token="tok", level=logging.DEBUG)
    log = logging.getLogger("app.level.sync")
    log.setLevel(logging.DEBUG)
    try:
        log.debug("dev-debug-visible")
        assert collector.flush()
        assert collector.storage.count(text="dev-debug-visible") == 1

        # Simulate production switch to ERROR.
        collector.set_level("ERROR")
        log.info("prod-info-suppressed")
        log.error("prod-error-visible")
        assert collector.flush()

        assert collector.storage.count(text="prod-info-suppressed") == 0
        assert collector.storage.count(text="prod-error-visible") == 1
    finally:
        _cleanup_handlers(collector)
        collector.stop()


def test_attach_supports_memory_uri_without_disk_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    collector = attach("memory://", source="worker", level="INFO")
    try:
        logging.getLogger("app.worker.mem").info("ephemeral-worker-log")
        assert collector.flush()
        assert list(tmp_path.iterdir()) == []
        rows = collector.storage.search(source="worker")
        assert len(rows) == 1
        assert rows[0]["message"] == "ephemeral-worker-log"
    finally:
        _cleanup_handlers(collector)
        collector.stop()
