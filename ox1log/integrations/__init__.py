"""Optional job-runner integrations; each module imports its runner lazily."""

from __future__ import annotations

from typing import Any

from ..span import Span, span
from ..tracecontext import parse_traceparent

TRACEPARENT_KEY = "ox1log_traceparent"


def job_span(name: str, traceparent: str | None = None, **extra: Any) -> Span:
    """One `job` span per run; it continues the enqueuer's trace when `traceparent` is set."""
    parent = parse_traceparent(traceparent)
    return span(
        name,
        span_type="job",
        trace_id=parent.trace_id if parent else None,
        parent_span_id=parent.parent_id if parent else None,
        **extra,
    )
