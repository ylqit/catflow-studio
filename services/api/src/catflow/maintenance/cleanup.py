from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from catflow.config import RuntimePaths
from catflow.infrastructure.models import (
    AssetRecord,
    Base,
    CanonProfileRecord,
    EditVersionRecord,
    EnvironmentPresetRecord,
    JobEventRecord,
    JobRecord,
    MediaPublicationRecord,
    ProjectRecord,
    ProjectSelectionRecord,
    ShotPlanVersionRecord,
    ValidationRunRecord,
    VideoRepairRecord,
)

ACTIVE_JOB_STATUSES = frozenset(
    {"queued", "submitting", "submitted", "polling", "storing", "cancel_requested"}
)
MANIFEST_FORMAT = "catflow-cleanup-manifest-v1"
QUARANTINE_HOURS = 7 * 24


@dataclass(frozen=True, slots=True)
class CleanupPolicy:
    delete_project_ids: frozenset[uuid.UUID]
    mixed_project_id: uuid.UUID
    restore_shot_plan_id: uuid.UUID
    delete_shot_plan_id: uuid.UUID
    restore_edit_version_id: uuid.UUID
    delete_edit_version_id: uuid.UUID
    delete_repair_ids: frozenset[uuid.UUID]
    delete_job_ids: frozenset[uuid.UUID]
    delete_canon_profile_ids: frozenset[uuid.UUID]

    @classmethod
    def reviewed(cls) -> CleanupPolicy:
        return cls(
            delete_project_ids=frozenset(
                {
                    uuid.UUID("4eaa29c6-e2ed-45bb-8776-40a6ac427f20"),
                    uuid.UUID("20b65033-76f6-49e6-b3f5-3f3412f46077"),
                    uuid.UUID("067067e7-a556-445d-8760-89e8ecb93250"),
                    uuid.UUID("c2d7ed18-e424-4787-b6d9-1c6ac2dad1f3"),
                    uuid.UUID("e1f7e598-1a61-4ec2-ba67-e9ae63e603cd"),
                }
            ),
            mixed_project_id=uuid.UUID("cf284238-0984-49fe-b88d-342fb20b1df5"),
            restore_shot_plan_id=uuid.UUID("01cde068-9fee-4513-a5fb-9ab1fa8f3d3d"),
            delete_shot_plan_id=uuid.UUID("c8e8d742-aad7-4984-be57-393960c20ae6"),
            restore_edit_version_id=uuid.UUID("026f1134-027c-4afe-a3c6-e2d3c61bdfd9"),
            delete_edit_version_id=uuid.UUID("4fb1d83b-74f9-43e7-9327-37c41a56d914"),
            delete_repair_ids=frozenset(
                {
                    uuid.UUID("5824483b-e8a9-48cc-81b9-196137a27495"),
                    uuid.UUID("686595c2-7265-496f-997a-a92832f0506f"),
                    uuid.UUID("6b2f0d0d-6b6d-4244-8e3d-89c8a1735c35"),
                    uuid.UUID("8f4ddc9c-172b-4716-b3bb-7d92d8dd7a76"),
                }
            ),
            delete_job_ids=frozenset(
                {
                    uuid.UUID("31dd7583-ea8c-4adc-9f89-65eba46a61fa"),
                    uuid.UUID("3d3e700b-f8ed-45a3-9d61-c370c3d313a3"),
                    uuid.UUID("edf93f55-9038-4bf4-b7b6-9cfd7888346b"),
                    uuid.UUID("fb10a0e2-8c62-4142-b4f2-fd3726d88013"),
                    uuid.UUID("c4c0dcec-ebac-4acb-8fa5-0b03e8af8301"),
                    uuid.UUID("3cade359-3a9f-4ece-ba12-8cc31706dccb"),
                }
            ),
            delete_canon_profile_ids=frozenset(
                {
                    uuid.UUID("71004437-7f1a-4110-a4ff-b5a8a663ee27"),
                    uuid.UUID("2e4160c1-1a31-4f74-8ccb-6baef298666a"),
                }
            ),
        )


def validate_storage_key(media_root: Path, storage_key: str) -> Path:
    return _resolve_managed_path(media_root, storage_key)


def manifest_sha256(document: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in document.items() if key != "manifestSha256"}
    return hashlib.sha256(_canonical_json(unsigned).encode()).hexdigest()


class CleanupService:
    """Audit, quarantine, delete, and recover one reviewed production cleanup."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        paths: RuntimePaths,
        *,
        policy: CleanupPolicy | None = None,
    ) -> None:
        self._sessions = sessions
        self._paths = paths
        self._policy = policy or CleanupPolicy.reviewed()

    def audit(self, output: Path) -> dict[str, Any]:
        output = output.resolve()
        if not output.is_relative_to(self._paths.backup_root.resolve()):
            raise ValueError("cleanup audit must be written below the configured backup root")
        with self._sessions() as session:
            targets = self._collect_targets(session)
            database_fingerprint = _database_fingerprint(session)
            retained_ark_jobs = [
                {
                    "id": str(job.id),
                    "projectId": str(job.project_id),
                    "kind": job.kind,
                    "status": job.status,
                    "providerTaskId": job.provider_task_id,
                    "providerRequestId": job.provider_request_id,
                    "billingStatus": job.billing_status,
                }
                for job in session.scalars(
                    select(JobRecord)
                    .where(
                        JobRecord.provider == "ark",
                        JobRecord.project_id.not_in(targets["project_ids"]),
                    )
                    .order_by(JobRecord.created_at, JobRecord.id)
                ).all()
            ]
            referenced_assets = list(session.scalars(select(AssetRecord)).all())

        media_files, missing_references = self._media_plan(
            referenced_assets,
            targets["asset_ids"],
        )
        work_files = self._unreferenced_diagnosis_work()
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        document: dict[str, Any] = {
            "format": MANIFEST_FORMAT,
            "runId": run_id,
            "createdAt": datetime.now(UTC).isoformat(),
            "minimumQuarantineHours": QUARANTINE_HOURS,
            "git": self._git_state(),
            "databaseRevision": self._database_revision(),
            "databaseFingerprint": database_fingerprint,
            "targets": {
                name: sorted(str(item) for item in values)
                for name, values in targets.items()
            },
            "media": media_files,
            "workFiles": work_files,
            "missingReferencedMedia": missing_references,
            "retainedArkJobs": retained_ark_jobs,
            "postconditions": {
                "projectCount": 3,
                "mixedActiveShotPlanId": str(self._policy.restore_shot_plan_id),
                "mixedActiveEditVersionId": str(self._policy.restore_edit_version_id),
                "activeCanonProfileId": "7cd3f3e0-12fd-4e5f-9214-8f6a9b1d098b",
            },
        }
        document["manifestSha256"] = manifest_sha256(document)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return document

    def execute(self, manifest_path: Path, expected_sha256: str) -> Path:
        manifest_path = manifest_path.resolve()
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual_sha256 = manifest_sha256(document)
        if document.get("format") != MANIFEST_FORMAT:
            raise RuntimeError("unsupported cleanup manifest")
        if expected_sha256 != actual_sha256 or document.get("manifestSha256") != actual_sha256:
            raise RuntimeError("cleanup manifest SHA256 does not match")
        self._require_services_stopped()
        with self._sessions() as session:
            active = session.scalar(
                select(func.count())
                .select_from(JobRecord)
                .where(JobRecord.status.in_(ACTIVE_JOB_STATUSES))
            )
            if active:
                raise RuntimeError("cleanup cannot run while active jobs exist")
            if _database_fingerprint(session) != document["databaseFingerprint"]:
                raise RuntimeError("database changed after cleanup audit; create a new manifest")

        run_root = self._paths.backup_root / f"cleanup-{document['runId']}"
        run_root.mkdir(parents=True, exist_ok=False)
        shutil.copy2(manifest_path, run_root / "cleanup-plan.json")
        environment_file = self._paths.project_root / ".env"
        if environment_file.is_file():
            shutil.copy2(environment_file, run_root / "environment.backup")
        self._create_full_backup(run_root / "catflow-studio.zip")
        quarantine_root = self._paths.backup_root / "cleanup-quarantine" / document["runId"]
        copied = self._copy_to_quarantine(document, quarantine_root)
        try:
            with self._sessions.begin() as session:
                if _database_fingerprint(session) != document["databaseFingerprint"]:
                    raise RuntimeError("database changed while cleanup was preparing its backup")
                self._delete_database_targets(session, document)
            self._delete_originals(copied)
        except Exception:
            self._restore_originals(copied)
            raise

        completed = {
            "format": "catflow-cleanup-completion-v1",
            "runId": document["runId"],
            "manifestSha256": actual_sha256,
            "completedAt": datetime.now(UTC).isoformat(),
            "quarantineRoot": str(quarantine_root.relative_to(self._paths.project_root)),
            "backup": str((run_root / "catflow-studio.zip").relative_to(self._paths.project_root)),
        }
        (run_root / "completed.json").write_text(
            json.dumps(completed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return run_root

    def purge_quarantine(self, run_id: str) -> int:
        run_root = (self._paths.backup_root / f"cleanup-{run_id}").resolve()
        completion_path = run_root / "completed.json"
        manifest_path = run_root / "cleanup-plan.json"
        if not completion_path.is_file() or not manifest_path.is_file():
            raise RuntimeError("completed cleanup run not found")
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        completed_at = datetime.fromisoformat(completion["completedAt"])
        if datetime.now(UTC) - completed_at < timedelta(hours=QUARANTINE_HOURS):
            raise RuntimeError("quarantine must be retained for at least seven days")
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        with self._sessions() as session:
            referenced = set(session.scalars(select(AssetRecord.storage_key)).all())
        media_keys = {item["relativePath"] for item in document["media"]}
        reused = referenced.intersection(media_keys)
        if reused:
            raise RuntimeError("quarantined media is referenced again; purge was refused")
        quarantine_root = (self._paths.backup_root / "cleanup-quarantine" / run_id).resolve()
        if not quarantine_root.is_relative_to(self._paths.backup_root.resolve()):
            raise RuntimeError("unsafe quarantine root")
        removed = 0
        for path in sorted(quarantine_root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
                removed += 1
            elif path.is_dir():
                path.rmdir()
        quarantine_root.rmdir()
        return removed

    def restore(self, run_id: str) -> None:
        self._require_services_stopped()
        run_root = (self._paths.backup_root / f"cleanup-{run_id}").resolve()
        backup = run_root / "catflow-studio.zip"
        if not backup.is_file():
            raise RuntimeError("cleanup backup was not found")
        restore_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safety_backup = run_root / f"before-restore-{restore_stamp}.zip"
        self._create_full_backup(safety_backup)
        subprocess.run(
            [
                sys.executable,
                str(self._paths.project_root / "scripts" / "local_backup.py"),
                "restore",
                str(backup),
                "--replace",
            ],
            cwd=self._paths.project_root,
            check=True,
        )

    def _collect_targets(self, session: Session) -> dict[str, set[uuid.UUID]]:
        policy = self._policy
        project_ids = set(policy.delete_project_ids)
        jobs = list(session.scalars(select(JobRecord)).all())
        job_ids = set(policy.delete_job_ids)
        for job in jobs:
            if (
                job.project_id in project_ids
                or job.id in policy.delete_job_ids
            ):
                job_ids.add(job.id)

        repair_ids = set(policy.delete_repair_ids)
        for repair in session.scalars(select(VideoRepairRecord)).all():
            if repair.project_id in project_ids:
                repair_ids.add(repair.id)

        edit_ids = {policy.delete_edit_version_id}
        for edit in session.scalars(select(EditVersionRecord)).all():
            if edit.project_id in project_ids:
                edit_ids.add(edit.id)

        shot_plan_ids = {policy.delete_shot_plan_id}
        for shot_plan in session.scalars(select(ShotPlanVersionRecord)).all():
            if shot_plan.project_id in project_ids or shot_plan.id == policy.delete_shot_plan_id:
                shot_plan_ids.add(shot_plan.id)

        for job in jobs:
            edit_id = job.frozen_input_json.get("editVersionId")
            if edit_id and _uuid_or_none(edit_id) in edit_ids:
                job_ids.add(job.id)

        assets = list(session.scalars(select(AssetRecord)).all())
        asset_ids = {
            asset.id
            for asset in assets
            if asset.project_id in project_ids or asset.producing_job_id in job_ids
        }
        changed = True
        while changed:
            changed = False
            for asset in assets:
                source_id = _uuid_or_none(asset.metadata_json.get("sourceAssetId"))
                if source_id in asset_ids and asset.id not in asset_ids:
                    asset_ids.add(asset.id)
                    changed = True

        for edit in session.scalars(select(EditVersionRecord)).all():
            if edit.rendered_asset_id in asset_ids or _json_mentions_any(edit.edl_json, asset_ids):
                edit_ids.add(edit.id)
        for repair in session.scalars(select(VideoRepairRecord)).all():
            if (
                repair.base_video_asset_id in asset_ids
                or repair.candidate_asset_id in asset_ids
                or repair.approved_candidate_asset_id in asset_ids
            ):
                repair_ids.add(repair.id)
        for job in jobs:
            if job.video_repair_id in repair_ids:
                job_ids.add(job.id)

        return {
            "project_ids": project_ids,
            "job_ids": job_ids,
            "asset_ids": asset_ids,
            "repair_ids": repair_ids,
            "edit_version_ids": edit_ids,
            "shot_plan_ids": shot_plan_ids,
            "canon_profile_ids": set(policy.delete_canon_profile_ids),
        }

    def _media_plan(
        self,
        assets: list[AssetRecord],
        target_asset_ids: set[uuid.UUID],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        referenced = {asset.storage_key: asset for asset in assets}
        target_keys = {asset.storage_key for asset in assets if asset.id in target_asset_ids}
        files = {
            path.relative_to(self._paths.media_root).as_posix(): path
            for path in self._paths.media_root.rglob("*")
            if path.is_file() and not path.name.endswith(".partial")
        }
        keys = target_keys.union(set(files).difference(referenced))
        media = [
            _file_manifest_entry("media", key, files[key])
            for key in sorted(keys)
            if key in files
        ]
        missing = [
            {
                "assetId": str(asset.id),
                "projectId": str(asset.project_id) if asset.project_id else None,
                "storageKey": asset.storage_key,
                "expectedSha256": asset.sha256,
            }
            for asset in assets
            if asset.storage_key not in files
        ]
        return media, missing

    def _unreferenced_diagnosis_work(self) -> list[dict[str, Any]]:
        root = self._paths.work_root / "video-diagnosis"
        if not root.is_dir():
            return []
        return [
            _file_manifest_entry("work", path.relative_to(self._paths.work_root).as_posix(), path)
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]

    def _copy_to_quarantine(
        self, document: dict[str, Any], quarantine_root: Path
    ) -> list[tuple[Path, Path, str, int]]:
        copied: list[tuple[Path, Path, str, int]] = []
        for entry in [*document["media"], *document["workFiles"]]:
            root = self._paths.media_root if entry["root"] == "media" else self._paths.work_root
            source = _resolve_managed_path(root, entry["relativePath"])
            if not source.is_file():
                raise RuntimeError(f"cleanup source file disappeared: {entry['relativePath']}")
            digest, size = _file_digest(source)
            if digest != entry["sha256"] or size != entry["byteSize"]:
                raise RuntimeError(f"cleanup source changed: {entry['relativePath']}")
            destination = _resolve_managed_path(
                quarantine_root,
                f"{entry['root']}/{entry['relativePath']}",
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied_digest, copied_size = _file_digest(destination)
            if copied_digest != digest or copied_size != size:
                raise RuntimeError(f"quarantine copy verification failed: {entry['relativePath']}")
            copied.append((source, destination, digest, size))
        return copied

    def _delete_database_targets(self, session: Session, document: dict[str, Any]) -> None:
        targets = {
            name: {_uuid_or_raise(value) for value in values}
            for name, values in document["targets"].items()
        }
        projects = targets["project_ids"]
        jobs = targets["job_ids"]
        assets = targets["asset_ids"]
        repairs = targets["repair_ids"]
        edits = targets["edit_version_ids"]
        shots = targets["shot_plan_ids"]

        session.execute(
            update(ShotPlanVersionRecord)
            .where(ShotPlanVersionRecord.project_id == self._policy.mixed_project_id)
            .values(active=False)
        )
        restored_shot = session.get(ShotPlanVersionRecord, self._policy.restore_shot_plan_id)
        if restored_shot is None or restored_shot.project_id != self._policy.mixed_project_id:
            raise RuntimeError("reviewed Ark shot plan cannot be restored")
        restored_shot.active = True

        session.execute(
            update(EditVersionRecord)
            .where(EditVersionRecord.project_id == self._policy.mixed_project_id)
            .values(active=False)
        )
        restored_edit = session.get(EditVersionRecord, self._policy.restore_edit_version_id)
        if restored_edit is None or restored_edit.project_id != self._policy.mixed_project_id:
            raise RuntimeError("reviewed Ark edit version cannot be restored")
        restored_edit.active = True

        session.execute(
            delete(MediaPublicationRecord).where(
                or_(
                    MediaPublicationRecord.job_id.in_(jobs),
                    MediaPublicationRecord.source_asset_id.in_(assets),
                )
            )
        )
        session.execute(
            delete(EnvironmentPresetRecord).where(
                or_(
                    EnvironmentPresetRecord.source_project_id.in_(projects),
                    EnvironmentPresetRecord.asset_id.in_(assets),
                )
            )
        )
        session.execute(
            delete(ProjectSelectionRecord).where(
                or_(
                    ProjectSelectionRecord.project_id.in_(projects),
                    ProjectSelectionRecord.asset_id.in_(assets),
                )
            )
        )
        session.execute(
            update(JobRecord)
            .where(JobRecord.video_repair_id.in_(repairs))
            .values(video_repair_id=None)
        )
        session.execute(delete(VideoRepairRecord).where(VideoRepairRecord.id.in_(repairs)))
        session.execute(
            update(EditVersionRecord)
            .where(EditVersionRecord.parent_edit_version_id.in_(edits))
            .values(parent_edit_version_id=None)
        )
        session.execute(delete(EditVersionRecord).where(EditVersionRecord.id.in_(edits)))
        session.execute(delete(ShotPlanVersionRecord).where(ShotPlanVersionRecord.id.in_(shots)))
        session.execute(delete(JobEventRecord).where(JobEventRecord.job_id.in_(jobs)))
        session.execute(delete(AssetRecord).where(AssetRecord.id.in_(assets)))
        session.execute(
            update(JobRecord)
            .where(JobRecord.parent_job_id.in_(jobs))
            .values(parent_job_id=None)
        )
        session.execute(
            update(JobRecord)
            .where(JobRecord.supersedes_job_id.in_(jobs))
            .values(supersedes_job_id=None)
        )
        session.execute(delete(JobRecord).where(JobRecord.id.in_(jobs)))
        session.execute(delete(ProjectRecord).where(ProjectRecord.id.in_(projects)))
        session.flush()

        for canon_id in targets["canon_profile_ids"]:
            references = self._canon_references(session, canon_id)
            if references:
                raise RuntimeError(
                    f"canon profile {canon_id} is still referenced by {', '.join(references)}"
                )
            session.execute(delete(CanonProfileRecord).where(CanonProfileRecord.id == canon_id))

    def _canon_references(self, session: Session, canon_id: uuid.UUID) -> list[str]:
        value = str(canon_id)
        references: list[str] = []
        if session.scalar(
            select(func.count())
            .select_from(ProjectRecord)
            .where(ProjectRecord.canon_profile_id == canon_id)
        ):
            references.append("projects")
        if session.scalar(
            select(func.count())
            .select_from(AssetRecord)
            .where(AssetRecord.canon_profile_id == canon_id)
        ):
            references.append("assets")
        validation_documents = session.execute(
            select(ValidationRunRecord.canon_snapshot_json)
        ).scalars()
        if any(value in _canonical_json(item) for item in validation_documents if item):
            references.append("validation_runs")
        job_documents = session.execute(select(JobRecord.frozen_input_json)).scalars()
        if any(value in _canonical_json(item) for item in job_documents if item):
            references.append("jobs")
        return references

    def _delete_originals(self, copied: list[tuple[Path, Path, str, int]]) -> None:
        for source, _, digest, size in copied:
            current_digest, current_size = _file_digest(source)
            if current_digest != digest or current_size != size:
                raise RuntimeError(f"source changed before deletion: {source.name}")
        for source, _, _, _ in copied:
            source.unlink()

    @staticmethod
    def _restore_originals(copied: list[tuple[Path, Path, str, int]]) -> None:
        for source, quarantine, digest, size in copied:
            if source.is_file():
                current_digest, current_size = _file_digest(source)
                if current_digest == digest and current_size == size:
                    continue
                raise RuntimeError(f"cannot restore changed source file: {source}")
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(quarantine, source)

    def _create_full_backup(self, destination: Path) -> None:
        subprocess.run(
            [
                sys.executable,
                str(self._paths.project_root / "scripts" / "local_backup.py"),
                "backup",
                str(destination),
            ],
            cwd=self._paths.project_root,
            check=True,
        )

    def _require_services_stopped(self) -> None:
        process_file = self._paths.work_root / "local-processes.json"
        if process_file.is_file():
            raise RuntimeError("stop CatFlow API and Worker before cleanup")
        ready_file = self._paths.work_root / "worker-ready.json"
        if ready_file.is_file():
            raise RuntimeError("remove stale Worker readiness by running stop-local.ps1")

    def _git_state(self) -> dict[str, Any]:
        def run(*arguments: str) -> str:
            result = subprocess.run(
                ["git", *arguments],
                cwd=self._paths.project_root,
                text=True,
                capture_output=True,
                check=True,
            )
            return result.stdout.strip()

        return {
            "branch": run("branch", "--show-current"),
            "commit": run("rev-parse", "HEAD"),
            "workingTreeStatus": run("status", "--short").splitlines(),
        }

    def _database_revision(self) -> str | None:
        with self._sessions() as session:
            return session.execute(
                text("SELECT version_num FROM catflow.alembic_version")
            ).scalar_one_or_none()


def _database_fingerprint(session: Session) -> str:
    digest = hashlib.sha256()
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.fullname):
        primary_keys = list(table.primary_key.columns)
        statement = select(table)
        if primary_keys:
            statement = statement.order_by(*primary_keys)
        rows = session.execute(statement).mappings()
        digest.update(table.fullname.encode())
        for row in rows:
            digest.update(_canonical_json(dict(row)).encode())
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _resolve_managed_path(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve()
    relative = Path(relative_path)
    if not relative_path or relative.is_absolute():
        raise ValueError("storage key must stay inside managed media root")
    candidate = (resolved_root / relative).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError("storage key must stay inside managed media root")
    return candidate


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _file_manifest_entry(root: str, relative_path: str, path: Path) -> dict[str, Any]:
    digest, size = _file_digest(path)
    return {"root": root, "relativePath": relative_path, "byteSize": size, "sha256": digest}


def _uuid_or_none(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _uuid_or_raise(value: object) -> uuid.UUID:
    parsed = _uuid_or_none(value)
    if parsed is None:
        raise RuntimeError(f"cleanup manifest contains an invalid UUID: {value}")
    return parsed


def _json_mentions_any(document: object, identifiers: Iterable[uuid.UUID]) -> bool:
    text = _canonical_json(document)
    return any(str(identifier) in text for identifier in identifiers)
