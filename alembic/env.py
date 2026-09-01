from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import Connection, text

from alembic import context
from cat_video_generator.config import (
    DatabaseOperation,
    DatabaseSettings,
    load_local_env,
)
from cat_video_generator.infrastructure.db.models import Base
from cat_video_generator.infrastructure.db.session import create_database_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    raise RuntimeError(
        "Offline migration generation is disabled because database transport "
        "security cannot be verified."
    )


def _run_migrations(connection: Connection, schema: str) -> None:
    config.attributes["schema"] = schema

    def include_name(name: str | None, type_: str, _parent_names: dict[str, str | None]) -> bool:
        if type_ == "schema":
            return name == schema
        return True

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        compare_type=True,
        version_table_schema=schema,
        version_table="alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided_connection = config.attributes.get("connection")
    provided_schema = config.attributes.get("schema")
    if provided_connection is not None:
        if provided_schema is None:
            raise RuntimeError("A provided Alembic connection requires a schema.")
        _run_migrations(provided_connection, provided_schema)
        return

    # 独立执行``alembic upgrade``时也复用CLI的配置优先级：
    # PowerShell环境变量优先，缺失值才从被Git忽略的.env补齐。
    load_local_env()
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(settings, DatabaseOperation.MIGRATION)
    try:
        with engine.connect() as connection:
            quoted_schema = connection.dialect.identifier_preparer.quote_schema(settings.schema)
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}"))
            connection.commit()
            _run_migrations(connection, settings.schema)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
