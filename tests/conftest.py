from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.engine import URL

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_DATABASE_NAME = "catflow_studio"
_TEST_DATABASE_NAME_PATTERN = re.compile(r"(?:^|[_-])test(?:[_-]|$)", re.IGNORECASE)
_TEST_DATABASE_NAME_PATTERN_SAFE_SUFFIX = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class _PytestDatabaseState:
    generated_database_name: str | None
    original_database_url: str | None


def _require_test_database_name(database_name: str | None) -> str:
    if not database_name:
        raise pytest.UsageError("CATFLOW test database URL must include a database name")
    if database_name == _PRODUCTION_DATABASE_NAME:
        raise pytest.UsageError("CATFLOW test database must not be catflow_studio")
    if not _TEST_DATABASE_NAME_PATTERN.search(database_name):
        raise pytest.UsageError(
            "CATFLOW test database name must include a clear test marker such as _test_"
        )
    return database_name


def _drop_generated_database(database_url: URL, database_name: str) -> None:
    admin_url = database_url.set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with engine.connect() as connection:
            quoted_name = connection.dialect.identifier_preparer.quote(database_name)
            connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_name} WITH (FORCE)"))
    finally:
        engine.dispose()


def pytest_configure(config: pytest.Config) -> None:
    load_dotenv(_REPOSITORY_ROOT / ".env", override=False)

    original_database_url = os.environ.get("CATFLOW_DATABASE_URL")
    configured_test_url = os.environ.get("CATFLOW_TEST_DATABASE_URL")
    if configured_test_url:
        test_url = make_url(configured_test_url)
        _require_test_database_name(test_url.database)
        os.environ["CATFLOW_DATABASE_URL"] = test_url.render_as_string(hide_password=False)
        config._catflow_pytest_database_state = _PytestDatabaseState(
            generated_database_name=None,
            original_database_url=original_database_url,
        )
    else:
        source_url = make_url(original_database_url) if original_database_url else URL.create(
            drivername="postgresql+psycopg",
            username=os.environ.get("CATFLOW_DB_USER", "postgres"),
            password=os.environ.get("CATFLOW_DB_PASSWORD") or None,
            host=os.environ.get("CATFLOW_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("CATFLOW_DB_PORT", "5432")),
            database=_PRODUCTION_DATABASE_NAME,
            query={"sslmode": os.environ.get("CATFLOW_DB_SSLMODE", "prefer")},
        )
        safe_suffix = uuid.uuid4().hex
        database_name = f"catflow_studio_test_{safe_suffix}"
        if not _TEST_DATABASE_NAME_PATTERN_SAFE_SUFFIX.fullmatch(database_name):
            raise pytest.UsageError("Generated CATFLOW test database name is unsafe")
        test_url = source_url.set(database=database_name)
        _require_test_database_name(test_url.database)

        admin_url = source_url.set(database="postgres")
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
        try:
            with engine.connect() as connection:
                quoted_name = connection.dialect.identifier_preparer.quote(database_name)
                connection.execute(text(f"CREATE DATABASE {quoted_name}"))
        except BaseException:
            _drop_generated_database(test_url, database_name)
            raise
        finally:
            engine.dispose()

        os.environ["CATFLOW_DATABASE_URL"] = test_url.render_as_string(hide_password=False)
        config._catflow_pytest_database_state = _PytestDatabaseState(
            generated_database_name=database_name,
            original_database_url=original_database_url,
        )

    try:
        alembic_config = Config(str(_REPOSITORY_ROOT / "services" / "api" / "alembic.ini"))
        command.upgrade(alembic_config, "head")
    except BaseException:
        state = getattr(config, "_catflow_pytest_database_state", None)
        if state is not None and state.generated_database_name is not None:
            _drop_generated_database(
                make_url(os.environ["CATFLOW_DATABASE_URL"]), state.generated_database_name
            )
        if original_database_url is None:
            os.environ.pop("CATFLOW_DATABASE_URL", None)
        else:
            os.environ["CATFLOW_DATABASE_URL"] = original_database_url
        raise


def pytest_unconfigure(config: pytest.Config) -> None:
    state = getattr(config, "_catflow_pytest_database_state", None)
    if state is None:
        return
    try:
        if state.generated_database_name is not None:
            _drop_generated_database(
                make_url(os.environ["CATFLOW_DATABASE_URL"]), state.generated_database_name
            )
    finally:
        if state.original_database_url is None:
            os.environ.pop("CATFLOW_DATABASE_URL", None)
        else:
            os.environ["CATFLOW_DATABASE_URL"] = state.original_database_url
