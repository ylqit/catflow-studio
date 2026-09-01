from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import (
    StudioConflictError,
    StudioService,
    ValidationRunCreateCommand,
)
from catflow.domain.validation import ValidationCallKind
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import ValidationRunRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository


def _runtime() -> ProviderRuntime:
    return ProviderRuntime(
        provider="ark",
        planning_model="doubao-seed-2-1-pro-260628",
        image_model="doubao-seedream-5-0-260128",
        video_model="doubao-seedance-2-0-260128",
        diagnostic_model="doubao-seed-2-1-pro-260628",
        capability_revision="ark-seedance-2.0-v1",
        paid_calls_enabled=True,
        maximum_video_references=5,
    )


def test_postgres_validation_run_serializes_concurrent_video_reservations() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(
        PostgresStudioRepository(sessions),
        provider_runtime=_runtime(),
    )
    preview = service.preview_validation_run()
    run = service.authorize_validation_run(
        ValidationRunCreateCommand(
            expectedManifestHash=preview.manifest_hash,
            paidCallAcknowledged=True,
        )
    )

    try:
        def reserve() -> bool:
            try:
                service.reserve_validation_call(run.id, ValidationCallKind.GENERATE_VIDEO)
            except StudioConflictError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=4) as executor:
            outcomes = list(executor.map(lambda _index: reserve(), range(4)))

        assert outcomes.count(True) == 3
        assert outcomes.count(False) == 1
        persisted = service.get_validation_run(run.id)
        assert persisted.usage[ValidationCallKind.GENERATE_VIDEO] == 3
    finally:
        with sessions.begin() as session:
            session.execute(delete(ValidationRunRecord).where(ValidationRunRecord.id == run.id))
        engine.dispose()
