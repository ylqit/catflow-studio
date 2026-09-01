from __future__ import annotations

from typing import Any

from psycopg.errors import ConnectionTimeout
from sqlalchemy import event

from cat_video_generator.config import DatabaseOperation, DatabaseSettings
from cat_video_generator.infrastructure.db.session import (
    _connect_with_timeout_retry,
    create_database_engine,
)


class _RecoveringDialect:
    def __init__(self) -> None:
        self.attempts = 0
        self.connection = object()

    def connect(self, *_args: Any, **_kwargs: Any) -> object:
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionTimeout("connection timeout expired")
        return self.connection


def test_database_connection_timeout_is_retried_before_returning_connection(
    monkeypatch,
) -> None:
    dialect = _RecoveringDialect()
    delays: list[float] = []
    monkeypatch.setattr(
        "cat_video_generator.infrastructure.db.session.time.sleep",
        delays.append,
    )

    connection = _connect_with_timeout_retry(dialect, None, [], {})

    assert connection is dialect.connection
    assert dialect.attempts == 2
    assert delays == [0.5]


def test_database_engine_registers_connection_timeout_retry_on_returned_engine() -> None:
    settings = DatabaseSettings(
        host="127.0.0.1",
        port=5432,
        database="vedio-appdb",
        user="test-user",
        password="test-password",
        sslmode="disable",
        schema="cat_video",
        allow_insecure_runtime=True,
        allow_insecure_readonly_smoke=False,
    )

    engine = create_database_engine(settings, DatabaseOperation.RUNTIME)
    try:
        assert event.contains(engine, "do_connect", _connect_with_timeout_retry)
    finally:
        engine.dispose()
