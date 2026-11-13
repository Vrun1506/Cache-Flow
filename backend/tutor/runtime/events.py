"""Run context and event emission (final-plan.md §15.2).

Every event carries ``run_id`` and a monotonic ``seq`` so the iPad can drop
stale events and replay after a reconnect.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class RunContext:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    sink: EventSink | None = None
    seq: int = 0

    async def emit(self, type: str, **fields: Any) -> None:
        self.seq += 1
        if self.sink is not None:
            await self.sink({"type": type, "run_id": self.run_id, "seq": self.seq, **fields})


class CollectingSink:
    """Test and debug sink that keeps every event."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def of_type(self, type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == type]

    def agent_states(self) -> list[tuple[str, str]]:
        return [(e["id"], e["state"]) for e in self.of_type("agent")]
