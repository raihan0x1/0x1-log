"""RQ: one trace per job, continuing the trace that enqueued it.

    from ox1log.integrations.rq import JobTraceMixin, trace_meta

    class Worker(JobTraceMixin, rq.Worker): ...
    queue.enqueue(func, meta=trace_meta())
"""

from __future__ import annotations

from typing import Any

from ..tracecontext import current_traceparent
from . import TRACEPARENT_KEY, job_span

JOB_NAME_KEY = "ox1log_job"


def trace_meta(meta: dict[str, Any] | None = None, name: str | None = None) -> dict[str, Any]:
    """`meta` plus the active trace, for `Queue.enqueue(..., meta=...)`.

    `name` labels the job span when the enqueued function is a generic runner
    (an outbox dispatcher) rather than the work itself.
    """
    out = dict(meta or {})
    if name:
        out[JOB_NAME_KEY] = name
    traceparent = current_traceparent()
    if traceparent:
        out[TRACEPARENT_KEY] = traceparent
    return out


class JobTraceMixin:
    """Put before the RQ Worker class; works for fork and simple workers (RQ 1.16+)."""

    def perform_job(self, job: Any, queue: Any, *args: Any, **kwargs: Any) -> bool:
        meta = job.meta or {}
        name = meta.get(JOB_NAME_KEY) or job.func_name
        with job_span(name, meta.get(TRACEPARENT_KEY), job_id=job.id, queue=queue.name) as s:
            ok = super().perform_job(job, queue, *args, **kwargs)  # type: ignore[misc]
            if not ok:
                # RQ swallows the exception; its own error log already sits in this trace.
                s.set_error("job failed")
            return ok
