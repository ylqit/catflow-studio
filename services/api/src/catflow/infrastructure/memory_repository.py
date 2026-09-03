from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from catflow.application.project_library import (
    ProjectCollectionCreate,
    ProjectCollectionDto,
    ProjectCollectionPatch,
    ProjectLibraryBatchActionCommand,
    ProjectLibraryBatchResultDto,
    ProjectLibraryItemDto,
    ProjectLibraryPageDto,
    ProjectLibraryQuery,
    ProjectOrganizationCommand,
    ProjectTagDto,
    normalize_organization_name,
    normalize_tags,
    project_library_page,
    suggested_theme_tags,
)
from catflow.application.service import (
    FIXED_CANON_ROLES,
    AssetDto,
    CanonProfileDto,
    CanonRevisionCreateCommand,
    EditDecisionListDto,
    EditDecisionListV2,
    EditVersionDto,
    EnvironmentPresetDto,
    FixedCanonRole,
    JobDto,
    JobEventDto,
    LifeStoryProposalDto,
    PlannerJobDto,
    PlannerMessageCommand,
    PlannerMessageDto,
    PlannerSnapshotDto,
    ProjectCreate,
    ProjectDto,
    ProjectPatch,
    ProjectSelectionDto,
    RateCardRevisionCreateCommand,
    RateCardRevisionDto,
    ShotPlanVersionDto,
    StoredAssetDto,
    StoryCreateCommand,
    StoryVersionDto,
    StudioConflictError,
    StudioNotFoundError,
    ValidationRunDto,
    VideoRepairDto,
    VideoRepairStatus,
)
from catflow.domain.billing import rate_card_revision_signature
from catflow.domain.models import LifeStoryProposalDraft, ShotPlanDraft
from catflow.domain.video_repairs import FrameRange


class MemoryStudioRepository:
    """Deterministic test repository; production uses PostgreSQL."""

    def __init__(self) -> None:
        self._canon_profile_id = uuid.uuid4()
        now = datetime.now(UTC)
        self._assets: dict[uuid.UUID, StoredAssetDto] = {}
        self._rate_cards: list[RateCardRevisionDto] = []
        fixed_assets: dict[FixedCanonRole, StoredAssetDto] = {}
        for index, role in enumerate(FIXED_CANON_ROLES, start=1):
            asset = StoredAssetDto(
                id=uuid.uuid4(),
                projectId=None,
                role=role,
                mediaType="image",
                storageKey=f"canon/test/{role}.png",
                sha256=f"{index:x}" * 64,
                byteSize=100,
                createdAt=now,
            )
            self._assets[asset.id] = asset
            fixed_assets[role] = asset
        profile_document = {
            "profileId": "canon-v4-healing-child-cat-style-board",
            "child": {
                "age": "6-7",
                "heightCm": 120,
                "heightRangeCm": [115, 125],
                "bodyProportion": "约4.5至5头身的柔和儿童插画比例",
            },
            "fixedAssets": {
                role: {"assetId": str(asset.id), "sha256": asset.sha256}
                for role, asset in fixed_assets.items()
            },
        }
        self._canon_profiles: list[CanonProfileDto] = [
            CanonProfileDto(
                id=self._canon_profile_id,
                version=4,
                specVersion=4,
                active=True,
                profileHash=hashlib.sha256(
                    json.dumps(
                        profile_document,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                profile=profile_document,
                fixedAssets=fixed_assets,
                createdAt=now,
            )
        ]
        self._projects: dict[uuid.UUID, ProjectDto] = {}
        self._project_collections: dict[uuid.UUID, ProjectCollectionDto] = {}
        self._project_collection_ids: dict[uuid.UUID, uuid.UUID | None] = {}
        self._project_tags: dict[uuid.UUID, tuple[ProjectTagDto, ...]] = {}
        self._project_pinned_at: dict[uuid.UUID, datetime | None] = {}
        self._project_archived_at: dict[uuid.UUID, datetime | None] = {}
        self._planner_sessions: dict[uuid.UUID, tuple[uuid.UUID, int]] = {}
        self._messages: dict[uuid.UUID, list[PlannerMessageDto]] = {}
        self._proposals: dict[uuid.UUID, LifeStoryProposalDto] = {}
        self._stories: dict[uuid.UUID, list[StoryVersionDto]] = {}
        self._shot_plans: dict[uuid.UUID, list[ShotPlanVersionDto]] = {}
        self._selections: dict[uuid.UUID, list[ProjectSelectionDto]] = {}
        self._jobs: dict[uuid.UUID, JobDto] = {}
        self._jobs_by_idempotency: dict[str, uuid.UUID] = {}
        self._job_events: list[JobEventDto] = []
        self._edits: dict[uuid.UUID, list[EditVersionDto]] = {}
        self._video_repairs: dict[uuid.UUID, VideoRepairDto] = {}
        self._repair_approvals_by_idempotency: dict[str, uuid.UUID] = {}
        self._validation_runs: dict[uuid.UUID, ValidationRunDto] = {}
        self._environment_presets: list[EnvironmentPresetDto] = []

    def publish_rate_card(self, command: RateCardRevisionCreateCommand) -> RateCardRevisionDto:
        existing = next(
            (
                item
                for item in self._rate_cards
                if item.provider == command.provider
                and item.model == command.model
                and item.revision == command.revision
            ),
            None,
        )
        if existing is not None:
            expected = rate_card_revision_signature(
                provider=command.provider,
                model=command.model,
                revision=command.revision,
                source_url=command.source_url,
                effective_from=command.effective_from,
                rates=command.rates,
            )
            actual = rate_card_revision_signature(
                provider=existing.provider,
                model=existing.model,
                revision=existing.revision,
                source_url=existing.source_url,
                effective_from=existing.effective_from,
                rates=existing.rates,
            )
            if actual != expected:
                raise StudioConflictError("rate-card revision already exists with different rates")
            return existing
        self._rate_cards = [
            item.model_copy(update={"active": False})
            if item.provider == command.provider and item.model == command.model
            else item
            for item in self._rate_cards
        ]
        created = RateCardRevisionDto(
            **command.model_dump(mode="python", by_alias=True),
            active=True,
            createdAt=datetime.now(UTC),
        )
        self._rate_cards.append(created)
        return created

    def list_rate_cards(self) -> list[RateCardRevisionDto]:
        return sorted(self._rate_cards, key=lambda item: item.created_at, reverse=True)

    def active_canon_profile_id(self) -> uuid.UUID:
        return self._canon_profile_id

    def current_canon_profile(self) -> CanonProfileDto:
        return next(profile for profile in self._canon_profiles if profile.active)

    def register_canon_asset(
        self,
        *,
        role: FixedCanonRole,
        sha256: str,
        storage_key: str,
        byte_size: int,
    ) -> StoredAssetDto:
        existing = next(
            (
                asset
                for asset in self._assets.values()
                if asset.project_id is None and asset.role == role and asset.sha256 == sha256
            ),
            None,
        )
        if existing is not None:
            return existing
        asset = StoredAssetDto(
            id=uuid.uuid4(),
            projectId=None,
            role=role,
            mediaType="image",
            storageKey=storage_key,
            sha256=sha256,
            byteSize=byte_size,
            createdAt=datetime.now(UTC),
        )
        self._assets[asset.id] = asset
        return asset

    def publish_canon_revision(self, command: CanonRevisionCreateCommand) -> CanonProfileDto:
        fixed: dict[FixedCanonRole, AssetDto] = {}
        for role in FIXED_CANON_ROLES:
            asset = self._assets.get(command.fixed_assets[role])
            if asset is None or asset.project_id is not None or asset.role != role:
                raise StudioConflictError("fixed asset must be a matching global Canon candidate")
            fixed[role] = asset
        document = {
            "profileId": "canon-v4-healing-child-cat-style-board",
            "child": {
                "age": "6-7",
                "heightCm": 120,
                "heightRangeCm": [115, 125],
                "bodyProportion": "约4.5至5头身的柔和儿童插画比例",
            },
            "fixedAssets": {
                role: {"assetId": str(asset.id), "sha256": asset.sha256}
                for role, asset in fixed.items()
            },
        }
        digest = hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._canon_profiles = [
            profile.model_copy(update={"active": False}) for profile in self._canon_profiles
        ]
        profile = CanonProfileDto(
            id=uuid.uuid4(),
            version=max(item.version for item in self._canon_profiles) + 1,
            specVersion=4,
            active=True,
            profileHash=digest,
            profile=document,
            fixedAssets=fixed,
            createdAt=datetime.now(UTC),
        )
        self._canon_profiles.append(profile)
        self._canon_profile_id = profile.id
        return profile

    def get_validation_run(self, run_id: uuid.UUID) -> ValidationRunDto | None:
        return self._validation_runs.get(run_id)

    def latest_validation_run(self) -> ValidationRunDto | None:
        return next(reversed(self._validation_runs.values()), None)

    def create_project(self, draft: ProjectCreate, *, canon_profile_id: uuid.UUID) -> ProjectDto:
        now = datetime.now(UTC)
        project = ProjectDto(
            id=uuid.uuid4(),
            title=draft.title,
            theme=draft.theme,
            targetDurationSeconds=draft.target_duration_seconds,
            aspectRatio="9:16",
            canonProfileId=canon_profile_id,
            createdAt=now,
            updatedAt=now,
        )
        self._projects[project.id] = project
        self._project_collection_ids[project.id] = None
        self._project_pinned_at[project.id] = None
        self._project_archived_at[project.id] = None
        self._project_tags[project.id] = suggested_theme_tags(draft.theme)
        session_id = uuid.uuid4()
        self._planner_sessions[project.id] = (session_id, 1)
        self._messages[session_id] = []
        return project

    def list_projects(self) -> list[ProjectDto]:
        return sorted(self._projects.values(), key=lambda project: project.created_at, reverse=True)

    def list_project_library(self, query: ProjectLibraryQuery) -> ProjectLibraryPageDto:
        return project_library_page(
            [self._project_library_item(project_id) for project_id in self._projects],
            query,
        )

    def list_project_collections(
        self, *, include_archived: bool = False
    ) -> list[ProjectCollectionDto]:
        return sorted(
            [
                item
                for item in self._project_collections.values()
                if include_archived or not item.archived
            ],
            key=lambda item: (item.sort_order, item.name.casefold()),
        )

    def create_project_collection(self, command: ProjectCollectionCreate) -> ProjectCollectionDto:
        name, normalized = normalize_organization_name(command.name, maximum_length=40)
        if any(
            normalize_organization_name(item.name, maximum_length=40)[1] == normalized
            and not item.archived
            for item in self._project_collections.values()
        ):
            raise StudioConflictError("collection name already exists")
        now = datetime.now(UTC)
        collection = ProjectCollectionDto(
            id=uuid.uuid4(),
            name=name,
            colorKey=command.color_key,
            sortOrder=len(self._project_collections),
            archived=False,
            createdAt=now,
            updatedAt=now,
        )
        self._project_collections[collection.id] = collection
        return collection

    def update_project_collection(
        self, collection_id: uuid.UUID, command: ProjectCollectionPatch
    ) -> ProjectCollectionDto:
        current = self._project_collections.get(collection_id)
        if current is None:
            raise StudioNotFoundError("project collection not found")
        name = current.name
        if command.name is not None:
            name, normalized = normalize_organization_name(command.name, maximum_length=40)
            if any(
                item.id != collection_id
                and normalize_organization_name(item.name, maximum_length=40)[1] == normalized
                and not item.archived
                for item in self._project_collections.values()
            ):
                raise StudioConflictError("collection name already exists")
        updated = current.model_copy(
            update={
                "name": name,
                "color_key": command.color_key or current.color_key,
                "sort_order": (
                    command.sort_order if command.sort_order is not None else current.sort_order
                ),
                "updated_at": datetime.now(UTC),
            }
        )
        self._project_collections[collection_id] = updated
        return updated

    def set_project_collection_archived(
        self, collection_id: uuid.UUID, *, archived: bool
    ) -> ProjectCollectionDto:
        current = self._project_collections.get(collection_id)
        if current is None:
            raise StudioNotFoundError("project collection not found")
        if not archived:
            normalized = normalize_organization_name(current.name, maximum_length=40)[1]
            if any(
                item.id != collection_id
                and not item.archived
                and normalize_organization_name(item.name, maximum_length=40)[1] == normalized
                for item in self._project_collections.values()
            ):
                raise StudioConflictError("collection name already exists")
        updated = current.model_copy(update={"archived": archived, "updated_at": datetime.now(UTC)})
        self._project_collections[collection_id] = updated
        if archived:
            now = datetime.now(UTC)
            for project_id, assigned_id in self._project_collection_ids.items():
                if assigned_id == collection_id:
                    self._project_collection_ids[project_id] = None
                    self._projects[project_id] = self._projects[project_id].model_copy(
                        update={"updated_at": now}
                    )
        return updated

    def list_project_tags(self, *, query: str | None = None) -> list[dict[str, object]]:
        counts: dict[str, tuple[str, int]] = {}
        needle = (query or "").strip().casefold()
        for tags in self._project_tags.values():
            for tag in tags:
                if needle and needle not in tag.normalized_name:
                    continue
                display, count = counts.get(tag.normalized_name, (tag.name, 0))
                counts[tag.normalized_name] = (display, count + 1)
        return [
            {"name": name, "count": count}
            for _, (name, count) in sorted(counts.items(), key=lambda item: (-item[1][1], item[0]))
        ]

    def organize_project(
        self, project_id: uuid.UUID, command: ProjectOrganizationCommand
    ) -> ProjectLibraryItemDto:
        if project_id not in self._projects:
            raise StudioNotFoundError("project not found")
        if "collection_id" in command.model_fields_set:
            self._require_active_collection(command.collection_id)
        if command.archived:
            self._require_projects_not_running((project_id,))
        now = datetime.now(UTC)
        if "collection_id" in command.model_fields_set:
            self._project_collection_ids[project_id] = command.collection_id
        if command.tags is not None:
            self._project_tags[project_id] = normalize_tags(list(command.tags))
        if command.pinned is not None:
            self._project_pinned_at[project_id] = now if command.pinned else None
        if command.archived is not None:
            self._project_archived_at[project_id] = now if command.archived else None
        self._projects[project_id] = self._projects[project_id].model_copy(
            update={"updated_at": now}
        )
        return self._project_library_item(project_id)

    def apply_project_library_action(
        self, command: ProjectLibraryBatchActionCommand
    ) -> ProjectLibraryBatchResultDto:
        missing = [
            project_id for project_id in command.project_ids if project_id not in self._projects
        ]
        if missing:
            raise StudioNotFoundError("project not found")
        if command.action == "move_collection":
            self._require_active_collection(command.collection_id)
        if command.action == "archive":
            self._require_projects_not_running(command.project_ids)
        normalized_tags = normalize_tags(list(command.tags)) if command.tags else ()
        added_tags: dict[uuid.UUID, tuple[ProjectTagDto, ...]] = {}
        if command.action == "add_tags":
            for project_id in command.project_ids:
                current = self._project_tags.get(project_id, ())
                added_tags[project_id] = normalize_tags(
                    [*(tag.name for tag in current), *(tag.name for tag in normalized_tags)]
                )
        now = datetime.now(UTC)
        for project_id in command.project_ids:
            if command.action == "move_collection":
                self._project_collection_ids[project_id] = command.collection_id
            elif command.action == "add_tags":
                self._project_tags[project_id] = added_tags[project_id]
            elif command.action == "remove_tags":
                removed = {tag.normalized_name for tag in normalized_tags}
                self._project_tags[project_id] = tuple(
                    tag
                    for tag in self._project_tags.get(project_id, ())
                    if tag.normalized_name not in removed
                )
            elif command.action == "pin":
                self._project_pinned_at[project_id] = now
            elif command.action == "unpin":
                self._project_pinned_at[project_id] = None
            elif command.action == "archive":
                self._project_archived_at[project_id] = now
            elif command.action == "restore":
                self._project_archived_at[project_id] = None
            self._projects[project_id] = self._projects[project_id].model_copy(
                update={"updated_at": now}
            )
        return ProjectLibraryBatchResultDto(updatedCount=len(command.project_ids))

    def _require_active_collection(self, collection_id: uuid.UUID | None) -> None:
        if collection_id is None:
            return
        collection = self._project_collections.get(collection_id)
        if collection is None or collection.archived:
            raise StudioNotFoundError("project collection not found")

    def _require_projects_not_running(self, project_ids: tuple[uuid.UUID, ...]) -> None:
        active_statuses = {
            "queued",
            "submitting",
            "submitted",
            "polling",
            "storing",
            "cancel_requested",
        }
        if any(
            job.project_id in project_ids and job.status in active_statuses
            for job in self._jobs.values()
        ):
            raise StudioConflictError("running projects cannot be archived")

    def _project_library_item(self, project_id: uuid.UUID) -> ProjectLibraryItemDto:
        project = self._projects[project_id]
        stories = self._stories.get(project_id, [])
        active_story = next((item for item in reversed(stories) if item.active), None)
        plans = self._shot_plans.get(project_id, [])
        active_plan = next((item for item in reversed(plans) if item.active), None)
        selections = self.current_selections(project_id)
        production_slots = {
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        }
        current_selection_hash = _library_selection_hash(selections)
        plan_outdated = active_plan is not None and (
            active_story is None
            or active_plan.source_story_version_id != active_story.id
            or active_plan.source_selection_hash != current_selection_hash
        )
        if active_story is None:
            stage = "story"
        elif not production_slots <= selections.keys():
            stage = "assets"
        elif active_plan is None or plan_outdated:
            stage = "storyboard"
        elif "video" not in selections:
            stage = "generation"
        elif "final" not in selections:
            stage = "editing"
        else:
            stage = "completed"

        project_jobs = [job for job in self._jobs.values() if job.project_id == project_id]
        latest_by_kind: dict[str, JobDto] = {}
        for job in sorted(project_jobs, key=lambda item: item.updated_at):
            latest_by_kind[job.kind] = job
        active_statuses = {
            "queued",
            "submitting",
            "submitted",
            "polling",
            "storing",
            "cancel_requested",
        }
        reasons: list[str] = []
        if any(job.status == "submission_unknown" for job in latest_by_kind.values()):
            reasons.append("submission_unknown")
        if any(job.status == "failed" for job in latest_by_kind.values()):
            reasons.append("generation_failed")
        if plan_outdated:
            reasons.append("storyboard_outdated")
        if any(
            repair.project_id == project_id and repair.status == "candidate_ready"
            for repair in self._video_repairs.values()
        ):
            reasons.append("edit_candidate_ready")
        if "video" not in selections and any(
            job.kind == "generate_video" and job.status == "succeeded" and job.result_asset_ids
            for job in latest_by_kind.values()
        ):
            reasons.append("video_candidate_ready")
        if reasons:
            attention = "needs_attention"
        elif any(job.status in active_statuses for job in latest_by_kind.values()):
            attention = "running"
        else:
            attention = "normal"

        activity_times = [project.updated_at, project.created_at]
        activity_times.extend(item.created_at for item in stories)
        activity_times.extend(item.created_at for item in plans)
        activity_times.extend(item.created_at for item in self._selections.get(project_id, []))
        activity_times.extend(item.updated_at for item in project_jobs)
        activity_times.extend(item.created_at for item in self._edits.get(project_id, []))
        collection_id = self._project_collection_ids.get(project_id)
        collection = self._project_collections.get(collection_id) if collection_id else None
        project_assets = [
            asset for asset in self._assets.values() if asset.project_id == project_id
        ]
        preferred_cover_source = selections.get("final") or selections.get("video")
        poster = next(
            (
                asset
                for asset in reversed(project_assets)
                if asset.role == "project_poster"
                and preferred_cover_source is not None
                and asset.metadata.get("sourceAssetId") == str(preferred_cover_source.id)
            ),
            None,
        )
        cover = poster or selections.get("environment")
        return ProjectLibraryItemDto(
            id=project.id,
            title=project.title,
            themeSummary=project.theme[:80],
            targetDurationSeconds=project.target_duration_seconds,
            aspectRatio=project.aspect_ratio,
            coverAssetId=cover.id if cover is not None and cover.media_type == "image" else None,
            collection=collection,
            tags=self._project_tags.get(project_id, ()),
            stage=stage,
            attention=attention,
            attentionReasons=tuple(reasons),
            pinned=self._project_pinned_at.get(project_id) is not None,
            archived=self._project_archived_at.get(project_id) is not None,
            lastActivityAt=max(activity_times),
            createdAt=project.created_at,
            search_text=project.theme,
        )

    def get_project(self, project_id: uuid.UUID) -> ProjectDto | None:
        return self._projects.get(project_id)

    def update_project(self, project_id: uuid.UUID, patch: ProjectPatch) -> ProjectDto:
        project = self._projects.get(project_id)
        if project is None:
            raise StudioNotFoundError("project not found")
        updated = project.model_copy(
            update={
                "title": patch.title if patch.title is not None else project.title,
                "theme": patch.theme if patch.theme is not None else project.theme,
                "target_duration_seconds": (
                    patch.target_duration_seconds
                    if patch.target_duration_seconds is not None
                    else project.target_duration_seconds
                ),
                "updated_at": datetime.now(UTC),
            }
        )
        self._projects[project_id] = updated
        session_id, context_revision = self._planner_sessions[project_id]
        self._planner_sessions[project_id] = (session_id, context_revision + 1)
        for proposal in self._proposals.values():
            if proposal.project_id == project_id and proposal.status == "draft":
                proposal.status = "outdated"
        return updated

    def planner_snapshot(self, project_id: uuid.UUID) -> PlannerSnapshotDto:
        try:
            session_id, context_revision = self._planner_sessions[project_id]
        except KeyError as exc:
            raise StudioNotFoundError("planner session not found") from exc
        latest_job = max(
            (
                job
                for job in self._jobs.values()
                if job.project_id == project_id and job.kind == "plan_story"
            ),
            key=lambda job: (job.created_at, job.id.hex),
            default=None,
        )
        return PlannerSnapshotDto(
            sessionId=session_id,
            projectId=project_id,
            contextRevision=context_revision,
            messages=list(self._messages[session_id]),
            proposals=sorted(
                [item for item in self._proposals.values() if item.project_id == project_id],
                key=lambda item: item.id.hex,
            ),
            latestJob=(
                PlannerJobDto(
                    id=latest_job.id,
                    status=latest_job.status,
                    provider=latest_job.provider,
                    model=latest_job.model,
                    providerTaskId=latest_job.provider_task_id,
                    actualUsage=latest_job.actual_usage,
                    actualCostMicros=latest_job.actual_cost_micros,
                    currency=latest_job.currency,
                    billingStatus=latest_job.billing_status,
                    rateCardRevision=latest_job.rate_card_revision,
                    error=latest_job.error,
                    createdAt=latest_job.created_at,
                    updatedAt=latest_job.updated_at,
                )
                if latest_job is not None
                else None
            ),
        )

    def enqueue_planner_message(
        self, project_id: uuid.UUID, command: PlannerMessageCommand, *, job: JobDto
    ) -> JobDto:
        existing = self._existing_job(command.idempotency_key, input_hash=job.input_hash)
        if existing is not None:
            return existing
        session_id, _ = self._planner_sessions[project_id]
        created = self.create_job(job)
        messages = self._messages[session_id]
        now = datetime.now(UTC)
        messages.append(
            PlannerMessageDto(
                id=uuid.uuid4(),
                role="user",
                content=command.text,
                ordinal=len(messages) + 1,
                createdAt=now,
            )
        )
        return created

    def complete_planner_job(
        self, job_id: uuid.UUID, proposal: LifeStoryProposalDraft
    ) -> LifeStoryProposalDto:
        try:
            job = self._jobs[job_id]
        except KeyError as exc:
            raise StudioNotFoundError("job not found") from exc
        if job.kind != "plan_story":
            raise StudioConflictError("job is not a planner job")
        existing = next(
            (
                item
                for item in self._proposals.values()
                if item.project_id == job.project_id and item.context_hash == job.input_hash
            ),
            None,
        )
        if existing is not None:
            return existing
        session_id, _ = self._planner_sessions[job.project_id]
        now = datetime.now(UTC)
        dto = LifeStoryProposalDto(
            id=uuid.uuid4(),
            projectId=job.project_id,
            status="draft",
            title=proposal.title,
            summary=proposal.summary,
            body=proposal.body,
            microEvent=proposal.micro_event,
            targetDurationSeconds=proposal.target_duration_seconds,
            dialoguePolicy=proposal.dialogue_policy,
            environmentIntent=proposal.environment_intent,
            propIntent=proposal.prop_intent,
            contextHash=job.input_hash,
            warnings=[],
        )
        self._proposals[dto.id] = dto
        self._messages[session_id].append(
            PlannerMessageDto(
                id=uuid.uuid4(),
                role="assistant",
                content=proposal.summary,
                ordinal=len(self._messages[session_id]) + 1,
                createdAt=now,
            )
        )
        self._jobs[job.id] = job.model_copy(update={"status": "succeeded", "updated_at": now})
        self._record_event(job, "planner.proposal.created", {"proposalId": str(dto.id)})
        return dto

    def adopt_proposal(self, project_id: uuid.UUID, proposal_id: uuid.UUID) -> StoryVersionDto:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.project_id != project_id:
            raise StudioNotFoundError("proposal not found")
        stories = self._stories.setdefault(project_id, [])
        existing = next((item for item in stories if item.source_proposal_id == proposal_id), None)
        if existing is not None:
            return existing
        for story in stories:
            story.active = False
        story = StoryVersionDto(
            id=uuid.uuid4(),
            projectId=project_id,
            revision=len(stories) + 1,
            sourceProposalId=proposal.id,
            title=proposal.title,
            body=proposal.body,
            microEvent=proposal.micro_event,
            targetDurationSeconds=proposal.target_duration_seconds,
            dialoguePolicy=proposal.dialogue_policy,
            environmentIntent=proposal.environment_intent,
            active=True,
            createdAt=datetime.now(UTC),
        )
        stories.append(story)
        proposal.status = "adopted"
        return story

    def active_story(self, project_id: uuid.UUID) -> StoryVersionDto | None:
        return next(
            (story for story in reversed(self._stories.get(project_id, [])) if story.active), None
        )

    def list_stories(self, project_id: uuid.UUID) -> list[StoryVersionDto]:
        return list(reversed(self._stories.get(project_id, [])))

    def create_story(self, project_id: uuid.UUID, command: StoryCreateCommand) -> StoryVersionDto:
        stories = self._stories.setdefault(project_id, [])
        for story in stories:
            story.active = False
        story = StoryVersionDto(
            id=uuid.uuid4(),
            projectId=project_id,
            revision=len(stories) + 1,
            title=command.title,
            body=command.body,
            microEvent=command.micro_event,
            targetDurationSeconds=command.target_duration_seconds,
            dialoguePolicy=command.dialogue_policy,
            environmentIntent=command.environment_intent,
            active=True,
            createdAt=datetime.now(UTC),
        )
        stories.append(story)
        return story

    def activate_story(self, project_id: uuid.UUID, story_id: uuid.UUID) -> StoryVersionDto:
        stories = self._stories.get(project_id, [])
        selected = next((story for story in stories if story.id == story_id), None)
        if selected is None:
            raise StudioNotFoundError("story version not found")
        for story in stories:
            story.active = story.id == story_id
        return selected

    def create_shot_plan(self, project_id: uuid.UUID, draft: ShotPlanDraft) -> ShotPlanVersionDto:
        plans = self._shot_plans.setdefault(project_id, [])
        for plan in plans:
            plan.active = False
        plan = ShotPlanVersionDto(
            id=uuid.uuid4(),
            projectId=project_id,
            revision=len(plans) + 1,
            sourceStoryVersionId=draft.source_story_version_id,
            sourceSelectionHash=draft.source_selection_hash,
            clip=draft.clip.model_dump(mode="json", by_alias=True),
            shots=draft.shots,
            totalDurationSeconds=draft.total_duration_seconds,
            directorTreatment=draft.director_treatment,
            directorPromptRevision=draft.director_prompt_revision,
            directorModel=draft.director_model,
            directorInputHash=draft.director_input_hash,
            active=True,
            outdated=False,
            createdAt=datetime.now(UTC),
        )
        plans.append(plan)
        return plan

    def active_shot_plan(self, project_id: uuid.UUID) -> ShotPlanVersionDto | None:
        return next(
            (plan for plan in reversed(self._shot_plans.get(project_id, [])) if plan.active), None
        )

    def list_shot_plans(self, project_id: uuid.UUID) -> list[ShotPlanVersionDto]:
        return list(reversed(self._shot_plans.get(project_id, [])))

    def activate_shot_plan(
        self, project_id: uuid.UUID, shot_plan_id: uuid.UUID
    ) -> ShotPlanVersionDto:
        plans = self._shot_plans.get(project_id, [])
        selected = next((plan for plan in plans if plan.id == shot_plan_id), None)
        if selected is None:
            raise StudioNotFoundError("shot plan version not found")
        for plan in plans:
            plan.active = plan.id == shot_plan_id
        return selected

    def register_asset(
        self,
        project_id: uuid.UUID,
        *,
        role: str,
        sha256: str,
        media_type: str,
        storage_key: str,
        byte_size: int,
        producing_job_id: uuid.UUID | None,
        metadata: dict[str, Any] | None = None,
    ) -> StoredAssetDto:
        existing = next(
            (
                asset
                for asset in self._assets.values()
                if asset.sha256 == sha256 and asset.role == role
            ),
            None,
        )
        if existing is not None:
            return existing
        asset = StoredAssetDto(
            id=uuid.uuid4(),
            projectId=project_id,
            producingJobId=producing_job_id,
            role=role,
            mediaType=media_type,
            storageKey=storage_key,
            sha256=sha256,
            byteSize=byte_size,
            metadata=metadata or {},
            createdAt=datetime.now(UTC),
        )
        self._assets[asset.id] = asset
        return asset

    def select_asset(
        self,
        project_id: uuid.UUID,
        *,
        slot: str,
        asset_id: uuid.UUID,
        decision: str = "selected",
    ) -> ProjectSelectionDto:
        if slot in self.current_canon_profile().fixed_assets:
            raise StudioConflictError("global Canon slots cannot be overridden by a project")
        asset = self._assets.get(asset_id)
        if asset is None or asset.project_id != project_id:
            raise StudioNotFoundError("asset not found")
        if slot == "environment" and (asset.role != "environment" or asset.media_type != "image"):
            raise StudioConflictError("shared environment must be an environment image")
        selection = ProjectSelectionDto(
            id=uuid.uuid4(),
            projectId=project_id,
            assetId=asset_id,
            slot=slot,
            decision=decision,
            sourceHash=_selection_source_hash(project_id, slot, asset),
            createdAt=datetime.now(UTC),
        )
        self._selections.setdefault(project_id, []).append(selection)
        if slot == "environment":
            self._environment_presets = [
                preset.model_copy(update={"active": False}) for preset in self._environment_presets
            ]
            self._environment_presets.insert(
                0,
                EnvironmentPresetDto(
                    id=uuid.uuid4(),
                    sourceProjectId=project_id,
                    asset=asset,
                    active=True,
                    createdAt=datetime.now(UTC),
                ),
            )
        return selection

    def current_selections(self, project_id: uuid.UUID) -> dict[str, AssetDto]:
        project = self._projects.get(project_id)
        if project is None:
            raise StudioNotFoundError("project not found")
        profile = next(item for item in self._canon_profiles if item.id == project.canon_profile_id)
        current: dict[str, AssetDto] = dict(profile.fixed_assets)
        for selection in self._selections.get(project_id, []):
            if selection.decision in {"selected", "approved"}:
                current[selection.slot] = self._assets[selection.asset_id]
        active_environment = next(
            (preset.asset for preset in self._environment_presets if preset.active),
            None,
        )
        if active_environment is not None:
            current["environment"] = active_environment
        return current

    def environment_presets(self) -> list[EnvironmentPresetDto]:
        return list(self._environment_presets)

    def list_assets(self, project_id: uuid.UUID) -> list[AssetDto]:
        return sorted(
            [asset for asset in self._assets.values() if asset.project_id == project_id],
            key=lambda asset: asset.created_at,
            reverse=True,
        )

    def get_asset(self, asset_id: uuid.UUID) -> StoredAssetDto | None:
        return self._assets.get(asset_id)

    def create_job(self, job: JobDto) -> JobDto:
        existing = self._existing_job(job.idempotency_key, input_hash=job.input_hash)
        if existing is not None:
            return existing
        self._jobs[job.id] = job
        self._jobs_by_idempotency[job.idempotency_key] = job.id
        self._record_event(job, "job.queued")
        return job

    def get_job(self, job_id: uuid.UUID) -> JobDto | None:
        return self._jobs.get(job_id)

    def list_project_jobs(self, project_id: uuid.UUID) -> list[JobDto]:
        return sorted(
            (job for job in self._jobs.values() if job.project_id == project_id),
            key=lambda job: (job.created_at, job.id.hex),
            reverse=True,
        )

    def latest_job(self, project_id: uuid.UUID, *, kind: str) -> JobDto | None:
        return max(
            (
                job
                for job in self._jobs.values()
                if job.project_id == project_id and job.kind == kind
            ),
            key=lambda job: (job.created_at, job.id.hex),
            default=None,
        )

    def resume_job_storage(self, job_id: uuid.UUID) -> JobDto:
        job = self._jobs.get(job_id)
        if job is None:
            raise StudioNotFoundError("job not found")
        if (
            job.status != "failed"
            or not isinstance(job.error, dict)
            or job.error.get("code") != "result_storage_failed"
            or job.kind not in {"generate_image", "generate_video"}
            or not isinstance(job.provider_result, dict)
            or not (job.provider_result.get("url") or job.provider_result.get("videoUrl"))
            or (job.kind == "generate_video" and not job.provider_task_id)
        ):
            raise StudioConflictError("job is not eligible for result storage recovery")
        updated = job.model_copy(
            update={"status": "storing", "error": None, "updated_at": datetime.now(UTC)}
        )
        self._jobs[job_id] = updated
        self._record_event(updated, "job.storing")
        return updated

    def cancel_job(self, job_id: uuid.UUID) -> JobDto:
        job = self._jobs.get(job_id)
        if job is None:
            raise StudioNotFoundError("job not found")
        if job.status in {"succeeded", "failed", "cancelled"}:
            return job
        now = datetime.now(UTC)
        status = "cancelled" if job.status == "queued" else "cancel_requested"
        updated = job.model_copy(update={"status": status, "updated_at": now})
        self._jobs[job_id] = updated
        self._record_event(updated, f"job.{status}")
        return updated

    def list_job_events(self, *, after_event_id: int, limit: int = 100) -> list[JobEventDto]:
        return [event for event in self._job_events if event.id > after_event_id][:limit]

    def latest_job_event_id(self) -> int:
        return self._job_events[-1].id if self._job_events else 0

    def create_edit(
        self,
        project_id: uuid.UUID,
        *,
        source_selection_hash: str,
        edl: EditDecisionListDto,
    ) -> EditVersionDto:
        project_edits = self._edits.setdefault(project_id, [])
        for index, existing in enumerate(project_edits):
            if existing.active:
                project_edits[index] = existing.model_copy(update={"active": False})
        timeline_hash = hashlib.sha256(
            json.dumps(
                edl.model_dump(mode="json", by_alias=True),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        edit = EditVersionDto(
            id=uuid.uuid4(),
            projectId=project_id,
            revision=len(project_edits) + 1,
            sourceSelectionHash=source_selection_hash,
            edl=edl,
            status="draft",
            formatVersion=1,
            active=True,
            timelineHash=timeline_hash,
            createdAt=datetime.now(UTC),
        )
        project_edits.append(edit)
        return edit

    def list_edits(self, project_id: uuid.UUID) -> list[EditVersionDto]:
        return list(reversed(self._edits.get(project_id, [])))

    def get_edit(self, edit_id: uuid.UUID) -> EditVersionDto | None:
        return next(
            (
                edit
                for project_edits in self._edits.values()
                for edit in project_edits
                if edit.id == edit_id
            ),
            None,
        )

    def active_edit(self, project_id: uuid.UUID) -> EditVersionDto | None:
        return next(
            (edit for edit in reversed(self._edits.get(project_id, [])) if edit.active),
            None,
        )

    def create_video_repair(self, repair: VideoRepairDto) -> VideoRepairDto:
        self._video_repairs[repair.id] = repair
        return repair

    def create_video_repair_job(self, repair: VideoRepairDto, job: JobDto) -> JobDto:
        existing = self._existing_job(job.idempotency_key, input_hash=job.input_hash)
        if existing is not None:
            return existing
        self._video_repairs[repair.id] = repair
        try:
            return self.create_job(job)
        except Exception:
            self._video_repairs.pop(repair.id, None)
            raise

    def get_video_repair(self, repair_id: uuid.UUID) -> VideoRepairDto | None:
        return self._video_repairs.get(repair_id)

    def list_video_repairs(self, project_id: uuid.UUID) -> list[VideoRepairDto]:
        return list(
            reversed(
                [
                    repair
                    for repair in self._video_repairs.values()
                    if repair.project_id == project_id
                ]
            )
        )

    def set_video_repair_status(
        self,
        repair_id: uuid.UUID,
        *,
        status: VideoRepairStatus,
        candidate_asset_id: uuid.UUID | None = None,
    ) -> VideoRepairDto:
        repair = self._video_repairs.get(repair_id)
        if repair is None:
            raise StudioNotFoundError("video repair not found")
        changes: dict[str, object] = {"status": status}
        if candidate_asset_id is not None:
            changes["candidate_asset_id"] = candidate_asset_id
        updated = repair.model_copy(update=changes)
        self._video_repairs[repair_id] = updated
        return updated

    def approve_video_repair(
        self,
        repair_id: uuid.UUID,
        *,
        edl: EditDecisionListV2,
        source_selection_hash: str,
        parent_edit_version_id: uuid.UUID | None,
        candidate_asset_id: uuid.UUID,
        candidate_source_range: FrameRange,
        idempotency_key: str,
    ) -> EditVersionDto:
        existing_edit_id = self._repair_approvals_by_idempotency.get(idempotency_key)
        if existing_edit_id is not None:
            existing = self.get_edit(existing_edit_id)
            if existing is None:
                raise StudioConflictError("repair approval idempotency record is invalid")
            repair = self._video_repairs.get(repair_id)
            if repair is None or repair.approved_edit_version_id != existing.id:
                raise StudioConflictError(
                    "repair approval idempotency key belongs to different input"
                )
            return existing
        repair = self._video_repairs.get(repair_id)
        if repair is None:
            raise StudioNotFoundError("video repair not found")
        project_edits = self._edits.setdefault(repair.project_id, [])
        for index, existing in enumerate(project_edits):
            if existing.active:
                project_edits[index] = existing.model_copy(update={"active": False})
        timeline_hash = hashlib.sha256(
            json.dumps(
                edl.model_dump(mode="json", by_alias=True),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        edit = EditVersionDto(
            id=uuid.uuid4(),
            projectId=repair.project_id,
            revision=len(project_edits) + 1,
            sourceSelectionHash=source_selection_hash,
            edl=edl,
            status="draft",
            parentEditVersionId=parent_edit_version_id,
            formatVersion=2,
            active=True,
            timelineHash=timeline_hash,
            createdAt=datetime.now(UTC),
        )
        project_edits.append(edit)
        approved = repair.model_copy(
            update={
                "status": "approved",
                "candidate_core_range": candidate_source_range,
                "approved_candidate_asset_id": candidate_asset_id,
                "approved_edit_version_id": edit.id,
                "approval_idempotency_key": idempotency_key,
                "approved_at": datetime.now(UTC),
            }
        )
        self._video_repairs[repair_id] = approved
        self._repair_approvals_by_idempotency[idempotency_key] = edit.id
        return edit

    def _existing_job(self, idempotency_key: str, *, input_hash: str) -> JobDto | None:
        existing_id = self._jobs_by_idempotency.get(idempotency_key)
        if existing_id is None:
            return None
        existing = self._jobs[existing_id]
        if existing.input_hash != input_hash:
            raise StudioConflictError("idempotency key already belongs to different input")
        return existing

    def _record_event(
        self, job: JobDto, event_type: str, payload: dict[str, str] | None = None
    ) -> None:
        self._job_events.append(
            JobEventDto(
                id=len(self._job_events) + 1,
                jobId=job.id,
                projectId=job.project_id,
                eventType=event_type,
                payload=payload or {"jobId": str(job.id), "status": job.status},
                createdAt=datetime.now(UTC),
            )
        )


def _selection_source_hash(project_id: uuid.UUID, slot: str, asset: AssetDto) -> str:
    document = {"projectId": str(project_id), "slot": slot, "sha256": asset.sha256}
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _library_selection_hash(selections: dict[str, AssetDto]) -> str:
    production_slots = {
        "episode_child",
        "episode_cat",
        "pair_scale",
        "environment",
        "style_board",
    }
    document = {
        slot: {"assetId": str(asset.id), "sha256": asset.sha256}
        for slot, asset in sorted(selections.items())
        if slot in production_slots
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
