from __future__ import annotations

import pytest
from sqlalchemy import make_url

from catflow.infrastructure.database import DatabaseSettings, canon_v4_document, canon_v4_hash


def test_database_settings_require_postgresql_but_allow_configured_server() -> None:
    settings = DatabaseSettings(url="postgresql+psycopg://catflow:secret@127.0.0.1:55432/catflow")
    assert settings.url.startswith("postgresql+psycopg://")

    with pytest.raises(ValueError, match="PostgreSQL"):
        DatabaseSettings(url="sqlite:///catflow.db")

    configured_server = DatabaseSettings(
        url=("postgresql+psycopg://catflow:secret@192.168.1.20:5432/catflow_studio?sslmode=require")
    )
    assert configured_server.url.endswith("catflow_studio?sslmode=require")


def test_database_settings_can_be_built_from_discrete_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CATFLOW_DATABASE_URL", raising=False)
    monkeypatch.setenv("CATFLOW_DB_HOST", "db.internal")
    monkeypatch.setenv("CATFLOW_DB_PORT", "5432")
    monkeypatch.setenv("CATFLOW_DB_NAME", "catflow_studio")
    monkeypatch.setenv("CATFLOW_DB_USER", "catflow-user")
    monkeypatch.setenv("CATFLOW_DB_PASSWORD", "p@ss word")
    monkeypatch.setenv("CATFLOW_DB_SSLMODE", "disable")

    settings = DatabaseSettings.from_env()

    assert "catflow_studio" in settings.url
    assert "sslmode=disable" in settings.url
    assert make_url(settings.url).password == "p@ss word"


def test_canon_v4_seed_is_fixed_and_style_source_is_not_provider_eligible() -> None:
    document = canon_v4_document()

    assert document["profileId"] == "canon-v4-healing-child-cat-style-board"
    assert document["child"]["age"] == "8-9"
    assert "灰白虎斑" in document["cat"]["identity"]
    assert document["references"]["styleSource"]["providerEligible"] is False
    assert document["references"]["styleBoard"]["providerEligible"] is True
    assert canon_v4_hash() == canon_v4_hash()
    assert len(canon_v4_hash()) == 64
