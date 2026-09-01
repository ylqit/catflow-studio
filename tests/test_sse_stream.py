from __future__ import annotations

import asyncio

import pytest

from cat_video_generator.interfaces.sse import parse_event_cursor, stream_events


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


def test_event_cursor_prefers_query_and_rejects_negative_headers() -> None:
    assert parse_event_cursor("12", None) == 12
    assert parse_event_cursor("12", 19) == 19
    with pytest.raises(ValueError, match="negative"):
        parse_event_cursor("-1", None)


def test_sse_replay_uses_sequence_as_event_id() -> None:
    async def read_two_frames() -> tuple[str, str]:
        stream = stream_events(
            _ConnectedRequest(),
            loader=lambda cursor: (
                ({"sequence": 8, "type": "task_running", "data": {"stepId": "step-1"}},)
                if cursor < 8
                else ()
            ),
            after_sequence=7,
            idle_poll_seconds=60,
        )
        return await anext(stream), await anext(stream)

    retry, event = asyncio.run(read_two_frames())

    assert retry == "retry: 3000\n\n"
    assert "id: 8\n" in event
    assert "event: task_running\n" in event
    assert '"stepId": "step-1"' in event


def test_sse_sends_heartbeat_without_closing_the_stream() -> None:
    async def read_heartbeat() -> tuple[str, str]:
        stream = stream_events(
            _ConnectedRequest(),
            loader=lambda _cursor: (),
            after_sequence=0,
            heartbeat_seconds=0,
            idle_poll_seconds=0,
        )
        return await anext(stream), await anext(stream)

    retry, heartbeat = asyncio.run(read_heartbeat())

    assert retry == "retry: 3000\n\n"
    assert heartbeat == ": heartbeat\n\n"
