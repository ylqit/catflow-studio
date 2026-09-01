"""Long-lived SSE framing with monotonic cursor replay and heartbeats."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from time import monotonic
from typing import Any, Protocol


class DisconnectAwareRequest(Protocol):
    async def is_disconnected(self) -> bool: ...


def parse_event_cursor(last_event_id: str | None, after_event_id: int | None) -> int:
    if after_event_id is not None:
        if after_event_id < 0:
            raise ValueError("afterEventId cannot be negative")
        return after_event_id
    if last_event_id is None or not last_event_id.strip():
        return 0
    cursor = int(last_event_id)
    if cursor < 0:
        raise ValueError("Last-Event-ID cannot be negative")
    return cursor


async def stream_events(
    request: DisconnectAwareRequest,
    *,
    loader: Callable[[int], Sequence[dict[str, Any]]],
    after_sequence: int,
    heartbeat_seconds: float = 15,
    idle_poll_seconds: float = 1,
) -> AsyncIterator[str]:
    cursor = after_sequence
    last_output = monotonic()
    yield "retry: 3000\n\n"
    while not await request.is_disconnected():
        events = await asyncio.to_thread(loader, cursor)
        for event in events:
            sequence = int(event["sequence"])
            cursor = max(cursor, sequence)
            yield _encode_event(event, sequence)
            last_output = monotonic()
        if len(events) >= 200:
            continue
        if monotonic() - last_output >= heartbeat_seconds:
            yield ": heartbeat\n\n"
            last_output = monotonic()
        await asyncio.sleep(idle_poll_seconds)


def _encode_event(event: dict[str, Any], sequence: int) -> str:
    return (
        f"id: {sequence}\n"
        f"event: {event.get('type', 'message')}\n"
        f"data: {json.dumps(event.get('data', {}), ensure_ascii=False)}\n\n"
    )
