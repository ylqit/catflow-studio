"""Shared PostgreSQL engine and migration gate."""

from __future__ import annotations

import time
from typing import Any

from psycopg.errors import ConnectionTimeout
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from ...config import DatabaseOperation, DatabaseSettings
from .models import SCHEMA_NAME

ALEMBIC_HEAD = "0032_creator_core"
_CONNECTION_TIMEOUT_RETRY_DELAYS = (0.5, 1.5)


def _connect_with_timeout_retry(
    dialect: Any,
    _connection_record: Any,
    connection_args: list[Any],
    connection_params: dict[str, Any],
) -> Any:
    """Retry transient PostgreSQL connection timeouts before a session exists."""

    for attempt in range(len(_CONNECTION_TIMEOUT_RETRY_DELAYS) + 1):
        try:
            return dialect.connect(*connection_args, **connection_params)
        except ConnectionTimeout:
            if attempt == len(_CONNECTION_TIMEOUT_RETRY_DELAYS):
                raise
            time.sleep(_CONNECTION_TIMEOUT_RETRY_DELAYS[attempt])

    raise RuntimeError("database connection retry loop exhausted unexpectedly")


def create_database_engine(
    settings: DatabaseSettings,
    operation: DatabaseOperation,
    *,
    pool_size: int = 3,
    max_overflow: int = 2,
) -> Engine:
    settings.validate_for(operation)
    engine = create_engine(
        settings.url,
        connect_args={
            "sslmode": settings.sslmode,
            "connect_timeout": 5,
            "application_name": "cat-video-generator",
            "options": (
                "-c statement_timeout=30000 "
                "-c lock_timeout=5000 "
                "-c idle_in_transaction_session_timeout=60000"
            ),
        },
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=900,
    )
    configured_engine = engine.execution_options(
        schema_translate_map={SCHEMA_NAME: settings.schema}
    )
    event.listen(configured_engine, "do_connect", _connect_with_timeout_retry, retval=True)
    return configured_engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def ensure_database_ready(engine: Engine, settings: DatabaseSettings) -> None:
    """Reject writes against the wrong database or an older schema."""

    with engine.connect() as connection:
        database, server_version = connection.execute(
            text("SELECT current_database(), current_setting('server_version_num')::int")
        ).one()
        if database != settings.database:
            raise RuntimeError(
                f"connected database {database!r} does not match {settings.database!r}"
            )
        if server_version < settings.minimum_server_version:
            raise RuntimeError("PostgreSQL 14 or newer is required")
        revision = connection.execute(
            text(f"SELECT version_num FROM {settings.schema}.alembic_version")
        ).scalar_one_or_none()
        if revision != ALEMBIC_HEAD:
            raise RuntimeError(f"database revision is {revision!r}; expected {ALEMBIC_HEAD!r}")
