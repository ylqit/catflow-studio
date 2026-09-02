from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from catflow.infrastructure.models import AssetRecord, JobRecord, MediaPublicationRecord
from catflow.infrastructure.object_storage import (
    ObjectPublisherError,
    ObjectStorageSettings,
    StoredObject,
)


class SegmentObjectStore(Protocol):
    settings: ObjectStorageSettings

    def verify_object(
        self, object_key: str, *, expected_sha256: str, expected_byte_size: int
    ) -> StoredObject: ...

    def upload_verified(
        self, path: Path, *, object_key: str, expected_sha256: str
    ) -> StoredObject: ...

    def presign_get(self, object_key: str) -> str: ...

    def delete(self, object_key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class PublishedSegmentReference:
    publication_id: uuid.UUID
    url: str


class SegmentReferencePublisher:
    """Own durable publication before the provider submission boundary."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        object_store: SegmentObjectStore,
    ) -> None:
        self._sessions = sessions
        self._store = object_store

    def publish(self, job_id: uuid.UUID, context_path: Path) -> PublishedSegmentReference:
        publication = self._ensure_record(job_id, context_path)
        try:
            stored = self._reuse_or_upload(publication, context_path)
            signed_url = self._store.presign_get(publication.object_key)
        except ObjectPublisherError as exc:
            self._mark_failed(publication.id, exc)
            raise

        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            record = session.scalar(
                select(MediaPublicationRecord)
                .where(MediaPublicationRecord.id == publication.id)
                .with_for_update()
            )
            if record is None:
                raise ObjectPublisherError(
                    "publication_missing", "durable segment publication record is missing"
                )
            record.state = "ready"
            record.etag = stored.etag
            record.signed_url_expires_at = now + timedelta(
                seconds=self._store.settings.presign_ttl_seconds
            )
            record.error_json = None
            record.updated_at = now
        return PublishedSegmentReference(publication_id=publication.id, url=signed_url)

    def delete_due(self, *, limit: int = 100) -> int:
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            records = list(
                session.scalars(
                    select(MediaPublicationRecord)
                    .where(
                        MediaPublicationRecord.state.in_(
                            ("ready", "failed", "delete_pending")
                        ),
                        MediaPublicationRecord.delete_after <= now,
                    )
                    .order_by(MediaPublicationRecord.delete_after)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                ).all()
            )
            claimed = [(record.id, record.object_key) for record in records]
            for record in records:
                record.state = "delete_pending"
                record.updated_at = now

        deleted = 0
        for publication_id, object_key in claimed:
            try:
                self._store.delete(object_key)
            except ObjectPublisherError as exc:
                self._record_cleanup_failure(publication_id, exc)
                continue
            with self._sessions.begin() as session:
                record = session.get(MediaPublicationRecord, publication_id)
                if record is None:
                    continue
                record.state = "deleted"
                record.deleted_at = datetime.now(UTC)
                record.updated_at = record.deleted_at
                record.error_json = None
                deleted += 1
        return deleted

    def _ensure_record(
        self, job_id: uuid.UUID, context_path: Path
    ) -> MediaPublicationRecord:
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(MediaPublicationRecord).where(MediaPublicationRecord.job_id == job_id)
            )
            if existing is not None:
                self._require_same_source(existing, context_path)
                return existing
            job = session.get(JobRecord, job_id)
            source = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.producing_job_id == job_id,
                    AssetRecord.role == "repair_context",
                )
            )
            if job is None or source is None or source.project_id != job.project_id:
                raise ObjectPublisherError(
                    "publication_source_missing",
                    "repair context asset does not match the provider job",
                )
            if (
                not context_path.is_file()
                or context_path.stat().st_size != source.byte_size
                or _sha256(context_path) != source.sha256
            ):
                raise ObjectPublisherError(
                    "publication_source_mismatch",
                    "repair context file does not match its durable asset",
                )
            object_key = (
                f"{self._store.settings.prefix}/{job.project_id}/{job_id}/{source.sha256}.mp4"
            )
            now = datetime.now(UTC)
            record = MediaPublicationRecord(
                job_id=job_id,
                source_asset_id=source.id,
                backend=self._store.settings.backend,
                bucket=self._store.settings.bucket,
                object_key=object_key,
                source_sha256=source.sha256,
                byte_size=source.byte_size,
                state="uploading",
                public_host=self._store.settings.public_object_host,
                delete_after=now + timedelta(days=self._store.settings.retention_days),
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            return record

    def _reuse_or_upload(
        self,
        publication: MediaPublicationRecord,
        context_path: Path,
    ) -> StoredObject:
        try:
            return self._store.verify_object(
                publication.object_key,
                expected_sha256=publication.source_sha256,
                expected_byte_size=publication.byte_size,
            )
        except ObjectPublisherError as exc:
            if exc.code not in {"object_head_failed", "object_not_found"}:
                raise
        return self._store.upload_verified(
            context_path,
            object_key=publication.object_key,
            expected_sha256=publication.source_sha256,
        )

    @staticmethod
    def _require_same_source(record: MediaPublicationRecord, context_path: Path) -> None:
        if record.state == "deleted":
            raise ObjectPublisherError(
                "publication_deleted", "segment publication was already deleted"
            )
        if (
            not context_path.is_file()
            or context_path.stat().st_size != record.byte_size
            or _sha256(context_path) != record.source_sha256
        ):
            raise ObjectPublisherError(
                "publication_source_mismatch",
                "repair context file changed after publication was recorded",
            )

    def _mark_failed(
        self, publication_id: uuid.UUID, error: ObjectPublisherError
    ) -> None:
        with self._sessions.begin() as session:
            record = session.get(MediaPublicationRecord, publication_id)
            if record is None:
                return
            record.state = "failed"
            record.error_json = {"code": error.code, "message": error.message}
            record.updated_at = datetime.now(UTC)
    def _record_cleanup_failure(
        self, publication_id: uuid.UUID, error: ObjectPublisherError
    ) -> None:
        with self._sessions.begin() as session:
            record = session.get(MediaPublicationRecord, publication_id)
            if record is None:
                return
            record.state = "delete_pending"
            record.error_json = {"code": error.code, "message": error.message}
            record.updated_at = datetime.now(UTC)


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
