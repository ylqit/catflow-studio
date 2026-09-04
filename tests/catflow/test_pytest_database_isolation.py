from __future__ import annotations

from sqlalchemy import make_url

from catflow.infrastructure.database import DatabaseSettings


def test_pytest_database_is_never_the_production_database() -> None:
    database_name = make_url(DatabaseSettings.from_env().url).database

    assert database_name is not None
    assert database_name != "catflow_studio"
    assert database_name.startswith("catflow_studio_test_")
