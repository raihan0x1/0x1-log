from types import SimpleNamespace

import pytest

from ox1log import span
from ox1log.collector import Collector
from ox1log.integrations import TRACEPARENT_KEY
from ox1log.integrations.rq import JobTraceMixin, trace_meta


@pytest.fixture()
def collector(tmp_path):
    c = Collector(str(tmp_path / "j.db")).start()
    yield c
    c.stop()


def _spans(collector):
    assert collector.flush()
    return {e["data"]["name"]: e for e in collector.storage.search(event_type="span")}


def test_explicit_trace_reaches_nested_spans(collector):
    with span("job", trace_id="a" * 32, collector=collector):
        with span("inner", collector=collector):
            pass
    rows = _spans(collector)
    assert rows["inner"]["trace_id"] == "a" * 32


class _FakeWorker:
    def perform_job(self, job, queue, *args):
        with span("vectors/search"):
            pass
        return job.ok


class _Worker(JobTraceMixin, _FakeWorker):
    pass


def _job(meta, ok=True):
    return SimpleNamespace(id="j1", func_name="tasks.top_issues", meta=meta, ok=ok)


def test_rq_job_continues_the_enqueuing_trace(collector):
    with span("POST /top-issues", span_type="http") as request:
        meta = trace_meta({"task": "x"})
    assert meta["task"] == "x" and TRACEPARENT_KEY in meta

    assert _Worker().perform_job(_job(meta), SimpleNamespace(name="default"), "execution") is True

    rows = _spans(collector)
    job, inner = rows["tasks.top_issues"], rows["vectors/search"]
    assert job["data"]["span_type"] == "job" and job["data"]["queue"] == "default"
    assert job["trace_id"] == inner["trace_id"] == request.trace_id
    assert job["data"]["parent_span_id"] == request.span_id
    assert inner["data"]["parent_span_id"] == job["data"]["span_id"]


def test_rq_job_without_trace_is_its_own_root_and_marks_failure(collector):
    assert trace_meta() == {}

    assert _Worker().perform_job(_job({}, ok=False), SimpleNamespace(name="default")) is False

    rows = _spans(collector)
    assert rows["tasks.top_issues"]["data"]["status"] == "error"
    assert rows["vectors/search"]["trace_id"] == rows["tasks.top_issues"]["trace_id"]


def test_rq_job_span_takes_the_name_from_meta(collector):
    meta = trace_meta(name="top_issues.analyze")

    _Worker().perform_job(_job(meta), SimpleNamespace(name="default"))

    assert "top_issues.analyze" in _spans(collector)


def test_celery_task_continues_the_sending_trace(collector):
    celery = pytest.importorskip("celery")
    from celery.contrib.testing.worker import start_worker

    from ox1log.integrations.celery import trace_tasks

    app = celery.Celery("t", broker="memory://", backend="cache+memory://")
    app.conf.task_create_missing_queues = True

    @app.task(name="tasks.summarize")
    def summarize():
        with span("llm/execute"):
            pass
        return "ok"

    @app.task(name="tasks.broken")
    def broken():
        raise ValueError("bad batch")

    trace_tasks()
    with start_worker(app, perform_ping_check=False, pool="solo"):
        with span("POST /summarize", span_type="http") as request:
            result = summarize.delay()
        assert result.get(timeout=10) == "ok"
        with pytest.raises(ValueError):
            broken.delay().get(timeout=10)

    rows = _spans(collector)
    task, inner = rows["tasks.summarize"], rows["llm/execute"]
    assert task["data"]["span_type"] == "job"
    assert task["trace_id"] == inner["trace_id"] == request.trace_id
    assert rows["tasks.broken"]["data"]["status"] == "error"
