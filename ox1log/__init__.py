"""0x1LOG: embedded observability for FastAPI."""

from __future__ import annotations

from typing import Any

from .attach import attach
from .collector import Collector
from .memory import MemoryStorage
from .postgres import PostgresStorage
from .redis_storage import RedisStorage
from .span import AISpan, Span, ai_span, span

__version__ = "1.4.5"
__all__ = [
    "__version__",
    "AISpan",
    "Collector",
    "MemoryStorage",
    "PostgresStorage",
    "RedisStorage",
    "Span",
    "ai_span",
    "attach",
    "observe",
    "span",
]


def __getattr__(name: str) -> Any:
    # Lazy so `import ox1log` never requires FastAPI; only the
    # framework adapter does.
    if name == "observe":
        from .adapters.fastapi import observe

        return observe
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
