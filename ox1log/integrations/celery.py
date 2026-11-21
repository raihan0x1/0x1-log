"""Celery: one trace per task, continuing the trace that sent it.

    from ox1log.integrations.celery import trace_tasks
    trace_tasks()  # once, in the worker and in the process that sends tasks
"""

from __future__ import annotations

from typing import Any

from ..span import Span
from ..tracecontext import current_traceparent
from . import TRACEPARENT_KEY, job_span

_running: dict[str, Span] = {}


def _publish(headers: dict[str, Any] | None = None, **_: Any) -> None:
    traceparent = current_traceparent()
    if headers is not None and traceparent:
        headers[TRACEPARENT_KEY] = traceparent


def _prerun(task_id: str, task: Any, **_: Any) -> None:
    # A worker exposes custom headers as request attributes; eager `apply` keeps them in `headers`.
    request = task.request
    traceparent = getattr(request, TRACEPARENT_KEY, None) or (getattr(request, "headers", None) or {}).get(TRACEPARENT_KEY)
    s = job_span(task.name, traceparent, task_id=task_id)
    s.__enter__()
    _running[task_id] = s


def _failure(task_id: str, exception: BaseException | None = None, **_: Any) -> None:
    s = _running.get(task_id)
    if s is not None:
        s.set_error(exception if isinstance(exception, Exception) else "task failed")


def _postrun(task_id: str, **_: Any) -> None:
    s = _running.pop(task_id, None)
    if s is not None:
        s.__exit__(None, None, None)


def trace_tasks() -> None:
    from celery import signals

    signals.before_task_publish.connect(_publish, weak=False)
    signals.task_prerun.connect(_prerun, weak=False)
    signals.task_failure.connect(_failure, weak=False)
    signals.task_postrun.connect(_postrun, weak=False)
