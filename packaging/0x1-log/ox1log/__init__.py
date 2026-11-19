"""The 0x1LOG public API."""

from nahiarhdLOG import AISpan, Collector, PostgresStorage, Span, ai_span, attach, span

__all__ = [
    "AISpan",
    "Collector",
    "PostgresStorage",
    "Span",
    "ai_span",
    "attach",
    "observe",
    "span",
]


def __getattr__(name: str):
    if name == "observe":
        from nahiarhdLOG import observe

        return observe
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
