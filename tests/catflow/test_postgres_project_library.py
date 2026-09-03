from __future__ import annotations

import time
import uuid
from pathlib import Path
from statistics import median

from dotenv import load_dotenv
from sqlalchemy import delete, event

from catflow.application.project_library import (
    ProjectCollectionCreate,
    ProjectLibraryQuery,
    ProjectOrganizationCommand,
)
from catflow.application.service import ProjectCreate, StudioService
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import ProjectCollectionRecord, ProjectRecord
from catflow.infrastructure.postgres_project_library import PostgresProjectLibraryRepository
from catflow.infrastructure.postgres_repository import PostgresStudioRepository


def test_postgres_project_library_persists_organization_and_projects_real_activity() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    main_repository = PostgresStudioRepository(sessions)
    library_repository = PostgresProjectLibraryRepository(sessions)
    service = StudioService(main_repository, project_library_repository=library_repository)
    project = service.create_project(
        ProjectCreate(title="项目库 PostgreSQL", theme="雨天", targetDurationSeconds=12)
    )
    collection = service.create_project_collection(
        ProjectCollectionCreate(name="项目库测试", colorKey="sage")
    )

    try:
        organized = service.organize_project(
            project.id,
            ProjectOrganizationCommand(
                collectionId=collection.id,
                tags=("雨天", "室内"),
                pinned=True,
            ),
        )
        page = service.project_library(
            ProjectLibraryQuery(q="PostgreSQL", tags=("雨天", "室内"), limit=12)
        )

        assert organized.collection is not None
        assert organized.collection.id == collection.id
        assert organized.pinned is True
        assert [item.id for item in page.items] == [project.id]
        assert page.items[0].last_activity_at >= project.updated_at
        assert page.facets.system_views["pinned"] >= 1
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
            session.execute(
                delete(ProjectCollectionRecord).where(ProjectCollectionRecord.id == collection.id)
            )
        engine.dispose()


def test_postgres_project_library_pages_500_projects_without_card_by_card_queries() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    main_repository = PostgresStudioRepository(sessions)
    library_repository = PostgresProjectLibraryRepository(sessions)
    canon_profile_id = main_repository.active_canon_profile_id()
    marker = f"library-scale-{uuid.uuid4().hex}"
    project_ids = [uuid.uuid4() for _ in range(500)]
    with sessions.begin() as session:
        session.add_all(
            ProjectRecord(
                id=project_id,
                title=f"{marker}-{index:03d}",
                theme=f"{marker} 生活主题 {index:03d}",
                target_duration_seconds=12,
                aspect_ratio="9:16",
                canon_profile_id=canon_profile_id,
            )
            for index, project_id in enumerate(project_ids)
        )

    statement_count = 0

    def count_statement(*_args: object) -> None:
        nonlocal statement_count
        statement_count += 1

    try:
        query = ProjectLibraryQuery(q=marker, limit=36)
        library_repository.list_project_library(query)
        event.listen(engine, "before_cursor_execute", count_statement)
        samples: list[float] = []
        for _ in range(5):
            started_at = time.perf_counter()
            page = library_repository.list_project_library(query)
            samples.append(time.perf_counter() - started_at)
        event.remove(engine, "before_cursor_execute", count_statement)

        assert len(page.items) == 36
        assert page.total == 500
        assert page.next_cursor is not None
        assert statement_count == 5
        assert median(samples) < 0.3
        assert max(samples) < 0.75
        second_page = library_repository.list_project_library(
            query.model_copy(update={"cursor": page.next_cursor})
        )
        assert len(second_page.items) == 36
        assert {item.id for item in page.items}.isdisjoint(item.id for item in second_page.items)
    finally:
        if event.contains(engine, "before_cursor_execute", count_statement):
            event.remove(engine, "before_cursor_execute", count_statement)
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id.in_(project_ids)))
        engine.dispose()
