from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, select

from catflow.application.service import ProjectCreate, StudioService
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import (
    AssetRecord,
    JobRecord,
    MediaPublicationRecord,
    ProjectRecord,
)
from catflow.infrastructure.object_storage import (
    ObjectPublisherError,
    ObjectStorageSettings,
    StoredObject,
)
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.segment_publisher import SegmentReferencePublisher


class RecordingObjectStore:
    def __init__(self) -> None:
        self.settings = ObjectStorageSettings(
            backend="s3",
            endpoint_url="https://tos-s3-cn-beijing.volces.com",
            public_endpoint_url="https://tos-s3-cn-beijing.volces.com",
            region="cn-beijing",
            bucket="test-vedio-ylq",
            access_key_id="access",
            secret_access_key="secret",
            addressing_style="virtual",
            prefix="catflow/segment-references",
            presign_ttl_seconds=7200,
            retention_days=7,
        )
        self.remote: dict[str, StoredObject] = {}
        self.uploads: list[str] = []
        self.deletes: list[str] = []
        self.delete_failures_remaining = 0

    def verify_object(
        self, object_key: str, *, expected_sha256: str, expected_byte_size: int
    ) -> StoredObject:
        stored = self.remote.get(object_key)
        if stored is None:
            raise ObjectPublisherError("object_head_failed", "object is absent")
        if stored.sha256 != expected_sha256 or stored.byte_size != expected_byte_size:
            raise ObjectPublisherError("remote_sha256_mismatch", "object differs")
        return stored

    def upload_verified(
        self, path: Path, *, object_key: str, expected_sha256: str
    ) -> StoredObject:
        self.uploads.append(object_key)
        stored = StoredObject(
            object_key=object_key,
            sha256=expected_sha256,
            byte_size=path.stat().st_size,
            etag="etag",
        )
        self.remote[object_key] = stored
        return stored

    def presign_get(self, object_key: str) -> str:
        assert object_key in self.remote
        return (
            "https://test-vedio-ylq.tos-s3-cn-beijing.volces.com/"
            f"{object_key}?X-Amz-Signature=secret"
        )

    def delete(self, object_key: str) -> None:
        self.deletes.append(object_key)
        if self.delete_failures_remaining:
            self.delete_failures_remaining -= 1
            raise ObjectPublisherError("object_delete_failed", "temporary delete failure")
        self.remote.pop(object_key, None)


def test_publication_is_durable_idempotent_and_never_persists_the_signed_url(tmp_path) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    repository = PostgresStudioRepository(sessions)
    service = StudioService(repository)
    project = service.create_project(
        ProjectCreate(title="发布恢复", theme="雨天擦爪", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    context = tmp_path / "context.mp4"
    context.write_bytes(b"durable-segment-context")
    digest = hashlib.sha256(context.read_bytes()).hexdigest()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="regenerate_video_segment",
                    status="submitting",
                    input_hash="f" * 64,
                    idempotency_key=f"publication-{job_id}",
                    provider="ark",
                    model="video-model",
                    frozen_input_json={"prompt": "repair"},
                )
            )
            session.flush()
            session.add(
                AssetRecord(
                    project_id=project.id,
                    producing_job_id=job_id,
                    role="repair_context",
                    media_type="video",
                    storage_key=f"generated/{project.id}/video-repairs/{job_id}/context.mp4",
                    sha256=digest,
                    byte_size=context.stat().st_size,
                    metadata_json={"durationFrames": 96},
                )
            )

        store = RecordingObjectStore()
        publisher = SegmentReferencePublisher(sessions, store)

        first = publisher.publish(job_id, context)
        recovered = SegmentReferencePublisher(sessions, store).publish(job_id, context)

        assert first.publication_id == recovered.publication_id
        assert first.url.startswith("https://test-vedio-ylq.tos-s3-cn-beijing.volces.com/")
        assert len(store.uploads) == 1
        public_job = service.get_job(job_id)
        assert public_job.publication is not None
        assert public_job.publication.id == first.publication_id
        assert public_job.publication.state == "ready"
        assert public_job.publication.public_host == (
            "test-vedio-ylq.tos-s3-cn-beijing.volces.com"
        )
        assert public_job.publication.signed_url_expires_at is not None
        assert public_job.publication.delete_after > public_job.publication.signed_url_expires_at
        publication_document = public_job.publication.model_dump(mode="json", by_alias=True)
        assert {"bucket", "objectKey", "etag", "signedUrl"}.isdisjoint(publication_document)
        with sessions() as session:
            record = session.scalar(
                select(MediaPublicationRecord).where(MediaPublicationRecord.job_id == job_id)
            )
            assert record is not None
            assert record.state == "ready"
            assert record.object_key == (
                f"catflow/segment-references/{project.id}/{job_id}/{digest}.mp4"
            )
            assert record.signed_url_expires_at is not None
            assert record.delete_after - record.created_at >= timedelta(days=6, hours=23)
            assert "Signature" not in str(record.error_json)
            assert not hasattr(record, "signed_url")
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_cleanup_deletes_only_due_exact_object_keys_and_keeps_audit_rows(tmp_path) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    repository = PostgresStudioRepository(sessions)
    project = StudioService(repository).create_project(
        ProjectCreate(title="发布清理", theme="雨天擦爪", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    context = tmp_path / "context.mp4"
    context.write_bytes(b"cleanup-segment-context")
    digest = hashlib.sha256(context.read_bytes()).hexdigest()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="regenerate_video_segment",
                    status="failed",
                    input_hash="e" * 64,
                    idempotency_key=f"cleanup-publication-{job_id}",
                    provider="ark",
                    model="video-model",
                    frozen_input_json={"prompt": "repair"},
                )
            )
            session.flush()
            session.add(
                AssetRecord(
                    project_id=project.id,
                    producing_job_id=job_id,
                    role="repair_context",
                    media_type="video",
                    storage_key=f"generated/{project.id}/video-repairs/{job_id}/context.mp4",
                    sha256=digest,
                    byte_size=context.stat().st_size,
                    metadata_json={"durationFrames": 96},
                )
            )
        store = RecordingObjectStore()
        publisher = SegmentReferencePublisher(sessions, store)
        published = publisher.publish(job_id, context)
        with sessions.begin() as session:
            record = session.get(MediaPublicationRecord, published.publication_id)
            assert record is not None
            record.delete_after = datetime.now(UTC) - timedelta(seconds=1)

        assert publisher.delete_due(limit=10) == 1

        with sessions() as session:
            record = session.get(MediaPublicationRecord, published.publication_id)
            assert record is not None
            assert record.state == "deleted"
            assert record.deleted_at is not None
            assert store.deletes == [record.object_key]
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_existing_publication_rejects_same_size_changed_source(tmp_path) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    repository = PostgresStudioRepository(sessions)
    project = StudioService(repository).create_project(
        ProjectCreate(title="发布源校验", theme="雨天擦爪", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    context = tmp_path / "context.mp4"
    context.write_bytes(b"original-context")
    digest = hashlib.sha256(context.read_bytes()).hexdigest()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="regenerate_video_segment",
                    status="submitting",
                    input_hash="d" * 64,
                    idempotency_key=f"changed-source-{job_id}",
                    provider="ark",
                    model="video-model",
                    frozen_input_json={"prompt": "repair"},
                )
            )
            session.flush()
            session.add(
                AssetRecord(
                    project_id=project.id,
                    producing_job_id=job_id,
                    role="repair_context",
                    media_type="video",
                    storage_key=f"generated/{project.id}/video-repairs/{job_id}/context.mp4",
                    sha256=digest,
                    byte_size=context.stat().st_size,
                    metadata_json={"durationFrames": 96},
                )
            )
        publisher = SegmentReferencePublisher(sessions, RecordingObjectStore())
        publisher.publish(job_id, context)
        context.write_bytes(b"modified-context")

        with pytest.raises(ObjectPublisherError, match="changed after publication") as captured:
            publisher.publish(job_id, context)

        assert captured.value.code == "publication_source_mismatch"
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_cleanup_failure_remains_pending_and_retries_the_exact_key(tmp_path) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    repository = PostgresStudioRepository(sessions)
    project = StudioService(repository).create_project(
        ProjectCreate(title="发布清理重试", theme="雨天擦爪", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    context = tmp_path / "context.mp4"
    context.write_bytes(b"retry-cleanup-context")
    digest = hashlib.sha256(context.read_bytes()).hexdigest()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="regenerate_video_segment",
                    status="failed",
                    input_hash="c" * 64,
                    idempotency_key=f"retry-cleanup-{job_id}",
                    provider="ark",
                    model="video-model",
                    frozen_input_json={"prompt": "repair"},
                )
            )
            session.flush()
            session.add(
                AssetRecord(
                    project_id=project.id,
                    producing_job_id=job_id,
                    role="repair_context",
                    media_type="video",
                    storage_key=f"generated/{project.id}/video-repairs/{job_id}/context.mp4",
                    sha256=digest,
                    byte_size=context.stat().st_size,
                    metadata_json={"durationFrames": 96},
                )
            )
        store = RecordingObjectStore()
        publisher = SegmentReferencePublisher(sessions, store)
        published = publisher.publish(job_id, context)
        with sessions.begin() as session:
            record = session.get(MediaPublicationRecord, published.publication_id)
            assert record is not None
            record.delete_after = datetime.now(UTC) - timedelta(seconds=1)
        store.delete_failures_remaining = 1

        assert publisher.delete_due(limit=10) == 0
        with sessions() as session:
            record = session.get(MediaPublicationRecord, published.publication_id)
            assert record is not None
            assert record.state == "delete_pending"
            assert record.error_json == {
                "code": "object_delete_failed",
                "message": "temporary delete failure",
            }

        assert publisher.delete_due(limit=10) == 1
        assert store.deletes == [record.object_key, record.object_key]
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()
