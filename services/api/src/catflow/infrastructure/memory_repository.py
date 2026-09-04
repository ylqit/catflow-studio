from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from catflow.application.continuity import (
    EpisodeContinuityConfirmCommand,
    EpisodeContinuityResetCommand,
    EpisodeContinuitySnapshotDto,
    SeriesAssetBindingDto,
    SeriesAssetBindingsPatchCommand,
    planned_continuity_state,
)
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
from catflow.application.series import (
    SeriesCreateCommand,
    SeriesEpisodeDto,
    SeriesPatchCommand,
    SeriesPlanDraft,
    SeriesPlanVersionDto,
    SeriesValidationIssueDto,
    StorySeriesDto,
    validate_series_plan,
)
from catflow.application.service import (
    FIXED_CANON_ROLES,
    AssetDto,
    CanonProfileDto,
    CanonRevisionCreateCommand,
    EditDecisionListDto,
    EditDecisionListV2,
    EditVersionDto,
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
    StudioIdempotencyInputConflictError,
    StudioNotFoundError,
    ValidationRunDto,
    VideoRepairDto,
    VideoRepairStatus,
)
from catflow.application.story_imports import (
    StoryImportAnalysisDraft,
    StoryImportConfirmCommand,
    StoryImportCreateCommand,
    StoryImportMaterializationDto,
    StoryImportProjectDto,
    StorySourceDocumentDto,
    StorySourceRelationSuggestionDto,
    StorySourceUnitDto,
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
        self._story_series: dict[uuid.UUID, StorySeriesDto] = {}
        self._series_plans: dict[uuid.UUID, list[SeriesPlanVersionDto]] = {}
        self._series_episodes: dict[uuid.UUID, list[SeriesEpisodeDto]] = {}
        self._series_activation_idempotency: dict[str, uuid.UUID] = {}
        self._series_plan_materialization_idempotency: dict[str, uuid.UUID] = {}
        self._series_materialization_idempotency: dict[str, uuid.UUID] = {}
        self._episode_continuity: dict[
            uuid.UUID, list[EpisodeContinuitySnapshotDto]
        ] = {}
        self._continuity_idempotency: dict[str, uuid.UUID] = {}
        self._series_asset_bindings: dict[uuid.UUID, list[SeriesAssetBindingDto]] = {}
        self._episode_reference_manifests: dict[uuid.UUID, list[dict[str, Any]]] = {}
        self._story_source_documents: dict[uuid.UUID, StorySourceDocumentDto] = {}
        self._story_source_materializations: dict[str, StoryImportMaterializationDto] = {}
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
        series_context = next(
            (
                {
                    "seriesId": str(episode.series_id),
                    "seriesTitle": self._story_series[episode.series_id].title,
                    "episodeId": str(episode.id),
                    "episodeOrder": episode.order,
                }
                for episodes in self._series_episodes.values()
                for episode in episodes
                if episode.project_id == project.id
            ),
            None,
        )
        return ProjectLibraryItemDto(
            id=project.id,
            title=project.title,
            themeSummary=project.theme[:80],
            targetDurationSeconds=project.target_duration_seconds,
            aspectRatio=project.aspect_ratio,
            coverAssetId=cover.id if cover is not None and cover.media_type == "image" else None,
            series=series_context,
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

    def create_story_series(
        self, command: SeriesCreateCommand, *, canon_profile_id: uuid.UUID
    ) -> StorySeriesDto:
        now = datetime.now(UTC)
        series = StorySeriesDto(
            id=uuid.uuid4(),
            canonProfileId=canon_profile_id,
            activePlanVersionId=None,
            plannedCount=0,
            materializedCount=0,
            completedCount=0,
            createdAt=now,
            updatedAt=now,
            **command.model_dump(mode="python", by_alias=True),
        )
        self._story_series[series.id] = series
        self._series_plans[series.id] = []
        self._series_episodes[series.id] = []
        return series

    def list_story_series(self) -> list[StorySeriesDto]:
        return sorted(
            self._story_series.values(),
            key=lambda item: (item.updated_at, item.id.hex),
            reverse=True,
        )

    def get_story_series(self, series_id: uuid.UUID) -> StorySeriesDto | None:
        return self._story_series.get(series_id)

    def update_story_series(
        self, series_id: uuid.UUID, command: SeriesPatchCommand
    ) -> StorySeriesDto:
        series = self._story_series.get(series_id)
        if series is None:
            raise StudioNotFoundError("story series not found")
        changes = {
            name: value
            for name, value in command.model_dump(mode="python").items()
            if name in command.model_fields_set
        }
        changes["updated_at"] = datetime.now(UTC)
        updated = series.model_copy(update=changes)
        self._story_series[series_id] = updated
        return updated

    def create_series_plan_version(
        self,
        series_id: uuid.UUID,
        *,
        plan: SeriesPlanDraft,
        input_hash: str,
        prompt_revision: str,
        producing_job_id: uuid.UUID,
        validation_issues: list[SeriesValidationIssueDto] | None = None,
    ) -> SeriesPlanVersionDto:
        series = self._story_series.get(series_id)
        if series is None:
            raise StudioNotFoundError("story series not found")
        plans = self._series_plans.setdefault(series_id, [])
        existing = next((item for item in plans if item.producing_job_id == producing_job_id), None)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        for item in plans:
            if item.status == "candidate":
                item.status = "superseded"
                item.decided_at = now
        if validation_issues is None:
            disposition, issues = validate_series_plan(
                plan,
                expected_episode_count=series.planned_episode_count,
                narrative_mode=series.narrative_mode,
            )
        else:
            issues = validation_issues
            disposition = (
                "needs_input"
                if any(issue.severity == "blocking" for issue in issues)
                else "candidate_ready"
            )
        version = SeriesPlanVersionDto(
            id=uuid.uuid4(),
            seriesId=series_id,
            revision=len(plans) + 1,
            status="candidate",
            active=False,
            disposition=disposition,
            plan=plan,
            inputHash=input_hash,
            promptRevision=prompt_revision,
            producingJobId=producing_job_id,
            issues=issues,
            createdAt=now,
        )
        plans.append(version)
        job = self._jobs.get(producing_job_id)
        if job is not None:
            completed = job.model_copy(update={"status": "succeeded", "updated_at": now})
            self._jobs[producing_job_id] = completed
            self._record_event(
                completed,
                "series.plan.candidate_created",
                {"seriesPlanVersionId": str(version.id)},
            )
        return version

    def list_series_plan_versions(self, series_id: uuid.UUID) -> list[SeriesPlanVersionDto]:
        return list(reversed(self._series_plans.get(series_id, [])))

    def materialize_series_plan_version(
        self,
        series_id: uuid.UUID,
        *,
        base_plan_version_id: uuid.UUID,
        plan: SeriesPlanDraft,
        idempotency_key: str,
    ) -> SeriesPlanVersionDto:
        plans = self._series_plans.get(series_id, [])
        prior_id = self._series_plan_materialization_idempotency.get(idempotency_key)
        if prior_id is not None:
            prior = next((item for item in plans if item.id == prior_id), None)
            if prior is None or prior.base_plan_version_id != base_plan_version_id:
                raise StudioIdempotencyInputConflictError(
                    "idempotency key already belongs to different input"
                )
            return prior
        series = self._story_series.get(series_id)
        base = next((item for item in plans if item.id == base_plan_version_id), None)
        if series is None or base is None:
            raise StudioNotFoundError("series plan version not found")
        if base.active or base.status != "candidate":
            raise StudioConflictError("only a pending series plan can be completed")
        now = datetime.now(UTC)
        for item in plans:
            if item.status == "candidate":
                item.status = "superseded"
                item.decided_at = now
        disposition, issues = validate_series_plan(
            plan,
            expected_episode_count=series.planned_episode_count,
            narrative_mode=series.narrative_mode,
        )
        input_hash = hashlib.sha256(
            json.dumps(
                {
                    "basePlanVersionId": str(base_plan_version_id),
                    "plan": plan.model_dump(mode="json", by_alias=True),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        version = SeriesPlanVersionDto(
            id=uuid.uuid4(),
            seriesId=series_id,
            revision=len(plans) + 1,
            status="candidate",
            active=False,
            disposition=disposition,
            plan=plan,
            inputHash=input_hash,
            promptRevision="manual-series-plan-v1",
            producingJobId=None,
            basePlanVersionId=base_plan_version_id,
            issues=issues,
            createdAt=now,
        )
        plans.append(version)
        self._series_plan_materialization_idempotency[idempotency_key] = version.id
        return version

    def activate_series_plan_version(
        self,
        series_id: uuid.UUID,
        plan_version_id: uuid.UUID,
        *,
        expected_active_plan_version_id: uuid.UUID | None,
        idempotency_key: str,
    ) -> SeriesPlanVersionDto:
        existing_id = self._series_activation_idempotency.get(idempotency_key)
        if existing_id is not None:
            existing = next(
                (item for item in self._series_plans.get(series_id, []) if item.id == existing_id),
                None,
            )
            if existing is None or existing.id != plan_version_id:
                raise StudioIdempotencyInputConflictError(
                    "idempotency key already belongs to different input"
                )
            return existing
        series = self._story_series.get(series_id)
        plans = self._series_plans.get(series_id, [])
        selected = next((item for item in plans if item.id == plan_version_id), None)
        if series is None or selected is None:
            raise StudioNotFoundError("series plan version not found")
        if selected.status != "candidate" or selected.disposition != "candidate_ready":
            raise StudioConflictError("series plan requires completion before adoption")
        if series.active_plan_version_id != expected_active_plan_version_id:
            raise StudioConflictError("active series plan changed")
        now = datetime.now(UTC)
        for item in plans:
            item.active = item.id == selected.id
        selected.status = "accepted"
        selected.decided_at = now
        existing_by_order = {
            item.order: item for item in self._series_episodes.get(series_id, [])
        }
        episodes: list[SeriesEpisodeDto] = []
        for outline in selected.plan.episodes:
            existing_episode = existing_by_order.get(outline.order)
            if existing_episode is None:
                episodes.append(
                    SeriesEpisodeDto(
                        id=uuid.uuid4(),
                        seriesId=series_id,
                        order=outline.order,
                        title=outline.title,
                        targetDurationSeconds=outline.target_duration_seconds,
                        status="outline",
                        projectId=None,
                        activeOutlineVersionId=uuid.uuid4(),
                        outline=outline,
                        createdAt=now,
                        updatedAt=now,
                    )
                )
                continue
            episodes.append(
                existing_episode.model_copy(
                    update={
                        "title": outline.title,
                        "target_duration_seconds": outline.target_duration_seconds,
                        "active_outline_version_id": uuid.uuid4(),
                        "outline": outline,
                        "updated_at": now,
                    }
                )
            )
        self._series_episodes[series_id] = episodes
        for index, episode in enumerate(episodes):
            history = self._episode_continuity.setdefault(episode.id, [])
            history[:] = [
                item.model_copy(update={"active": False}) if item.active else item
                for item in history
            ]
            previous_outline = episodes[index - 1].outline if index > 0 else None
            for direction in ("incoming", "outgoing"):
                history.append(
                    EpisodeContinuitySnapshotDto(
                        id=uuid.uuid4(),
                        episodeId=episode.id,
                        direction=direction,
                        source="planned",
                        state=planned_continuity_state(
                            bible=selected.plan.series_bible,
                            episode=episode.outline,
                            direction=direction,
                            previous_episode=previous_outline,
                        ),
                        decisions={},
                        confirmed=False,
                        active=True,
                        createdAt=now,
                    )
                )
        self._story_series[series_id] = series.model_copy(
            update={
                "active_plan_version_id": selected.id,
                "planned_count": len(episodes),
                "updated_at": now,
            }
        )
        self._series_activation_idempotency[idempotency_key] = selected.id
        return selected

    def reject_series_plan_version(
        self, series_id: uuid.UUID, plan_version_id: uuid.UUID
    ) -> SeriesPlanVersionDto:
        selected = next(
            (item for item in self._series_plans.get(series_id, []) if item.id == plan_version_id),
            None,
        )
        if selected is None:
            raise StudioNotFoundError("series plan version not found")
        if selected.active or selected.status != "candidate":
            raise StudioConflictError("only a pending series plan can be rejected")
        selected.status = "rejected"
        selected.decided_at = datetime.now(UTC)
        return selected

    def list_series_episodes(self, series_id: uuid.UUID) -> list[SeriesEpisodeDto]:
        return sorted(self._series_episodes.get(series_id, []), key=lambda item: item.order)

    def list_series_jobs(self, series_id: uuid.UUID) -> list[JobDto]:
        episode_project_ids = {
            episode.project_id
            for episode in self._series_episodes.get(series_id, [])
            if episode.project_id is not None
        }
        return sorted(
            [
                item
                for item in self._jobs.values()
                if item.series_id == series_id or item.project_id in episode_project_ids
            ],
            key=lambda item: (item.created_at, item.id.hex),
            reverse=True,
        )

    def materialize_series_episode(
        self,
        series_id: uuid.UUID,
        episode_id: uuid.UUID,
        *,
        idempotency_key: str,
    ) -> ProjectDto:
        prior_project_id = self._series_materialization_idempotency.get(idempotency_key)
        if prior_project_id is not None:
            prior = self._projects.get(prior_project_id)
            if prior is None:
                raise StudioConflictError("materialized project is missing")
            return prior
        series = self._story_series.get(series_id)
        episode = next(
            (item for item in self._series_episodes.get(series_id, []) if item.id == episode_id),
            None,
        )
        if series is None or episode is None:
            raise StudioNotFoundError("series episode not found")
        if episode.project_id is not None:
            project = self._projects.get(episode.project_id)
            if project is None:
                raise StudioConflictError("materialized project is missing")
            self._series_materialization_idempotency[idempotency_key] = project.id
            return project
        project = self.create_project(
            ProjectCreate(
                title=f"第{episode.order}集 · {episode.title}",
                theme=episode.outline.premise,
                targetDurationSeconds=episode.target_duration_seconds,
            ),
            canon_profile_id=series.canon_profile_id,
        )
        now = datetime.now(UTC)
        episode.project_id = project.id
        episode.status = "story_review"
        episode.updated_at = now
        current = self._story_series[series_id]
        self._story_series[series_id] = current.model_copy(
            update={
                "materialized_count": current.materialized_count + 1,
                "updated_at": now,
            }
        )
        self._series_materialization_idempotency[idempotency_key] = project.id
        return project

    def list_episode_continuity(
        self, episode_id: uuid.UUID
    ) -> list[EpisodeContinuitySnapshotDto]:
        return sorted(
            self._episode_continuity.get(episode_id, []),
            key=lambda item: (item.created_at, item.id.hex),
            reverse=True,
        )

    def confirm_episode_continuity(
        self, episode_id: uuid.UUID, command: EpisodeContinuityConfirmCommand
    ) -> EpisodeContinuitySnapshotDto:
        prior_id = self._continuity_idempotency.get(command.idempotency_key)
        history = self._episode_continuity.get(episode_id, [])
        if prior_id is not None:
            prior = next((item for item in history if item.id == prior_id), None)
            if prior is None or prior.direction != command.direction:
                raise StudioIdempotencyInputConflictError(
                    "idempotency key already belongs to different input"
                )
            return prior
        active = next(
            (
                item
                for item in history
                if item.direction == command.direction and item.active
            ),
            None,
        )
        if active is None:
            raise StudioNotFoundError("episode continuity snapshot not found")
        if command.expected_snapshot_id is not None and active.id != command.expected_snapshot_id:
            raise StudioConflictError("episode continuity changed")
        active.active = False
        created = EpisodeContinuitySnapshotDto(
            id=uuid.uuid4(),
            episodeId=episode_id,
            direction=command.direction,
            source="confirmed",
            state=command.state,
            decisions=command.decisions,
            confirmed=True,
            active=True,
            createdAt=datetime.now(UTC),
        )
        history.append(created)
        self._continuity_idempotency[command.idempotency_key] = created.id
        return created

    def reset_episode_continuity(
        self, episode_id: uuid.UUID, command: EpisodeContinuityResetCommand
    ) -> EpisodeContinuitySnapshotDto:
        history = self._episode_continuity.get(episode_id, [])
        active = next(
            (
                item
                for item in history
                if item.direction == command.direction and item.active
            ),
            None,
        )
        if active is None or active.id != command.expected_snapshot_id:
            raise StudioConflictError("episode continuity changed")
        planned = next(
            (
                item
                for item in history
                if item.direction == command.direction and item.source == "planned"
            ),
            None,
        )
        if planned is None:
            raise StudioNotFoundError("planned episode continuity not found")
        active.active = False
        restored = planned.model_copy(
            update={"id": uuid.uuid4(), "active": True, "created_at": datetime.now(UTC)}
        )
        history.append(restored)
        return restored

    def series_episode_for_project(self, project_id: uuid.UUID) -> SeriesEpisodeDto | None:
        return next(
            (
                episode
                for episodes in self._series_episodes.values()
                for episode in episodes
                if episode.project_id == project_id
            ),
            None,
        )

    def list_series_asset_bindings(
        self, series_id: uuid.UUID
    ) -> list[SeriesAssetBindingDto]:
        return [
            item
            for item in self._series_asset_bindings.get(series_id, [])
            if item.active
        ]

    def replace_series_asset_bindings(
        self, series_id: uuid.UUID, command: SeriesAssetBindingsPatchCommand
    ) -> list[SeriesAssetBindingDto]:
        if series_id not in self._story_series:
            raise StudioNotFoundError("story series not found")
        current = self.list_series_asset_bindings(series_id)
        desired = {
            item.binding_key: (item.role, item.asset_id) for item in command.bindings
        }
        existing = {
            item.binding_key: (item.role, item.asset_id) for item in current
        }
        if desired == existing:
            return current
        for binding in command.bindings:
            if binding.asset_id not in self._assets:
                raise StudioNotFoundError("series asset not found")
        history = self._series_asset_bindings.setdefault(series_id, [])
        history[:] = [
            item.model_copy(update={"active": False}) if item.active else item
            for item in history
        ]
        now = datetime.now(UTC)
        for binding in command.bindings:
            asset = self._assets[binding.asset_id]
            history.append(
                SeriesAssetBindingDto(
                    id=uuid.uuid4(),
                    seriesId=series_id,
                    bindingKey=binding.binding_key,
                    role=binding.role,
                    assetId=asset.id,
                    assetSha256=asset.sha256,
                    active=True,
                    createdAt=now,
                )
            )
        return self.list_series_asset_bindings(series_id)

    def find_story_source_document(self, *, content_hash: str) -> StorySourceDocumentDto | None:
        return next(
            (
                item
                for item in self._story_source_documents.values()
                if item.content_hash == content_hash
            ),
            None,
        )

    def list_story_source_documents(self) -> list[StorySourceDocumentDto]:
        return sorted(
            (
                self._story_source_document_with_job_status(item)
                for item in self._story_source_documents.values()
            ),
            key=lambda item: (item.created_at, item.id.hex),
            reverse=True,
        )

    def get_story_source_document(self, document_id: uuid.UUID) -> StorySourceDocumentDto | None:
        document = self._story_source_documents.get(document_id)
        return (
            self._story_source_document_with_job_status(document)
            if document is not None
            else None
        )

    def create_story_source_document(
        self,
        command: StoryImportCreateCommand,
        *,
        document_id: uuid.UUID,
        content_hash: str,
        job: JobDto,
    ) -> StorySourceDocumentDto:
        duplicate = self.find_story_source_document(content_hash=content_hash)
        if duplicate is not None:
            return duplicate
        now = datetime.now(UTC)
        document = StorySourceDocumentDto(
            id=document_id,
            contentHash=content_hash,
            sourceFormat=command.source_format,
            fileName=command.file_name,
            rawText=command.raw_text.replace("\r\n", "\n").replace("\r", "\n").strip(),
            status="analyzing",
            analysisJobId=job.id,
            units=[],
            relationSuggestions=[],
            createdAt=now,
            updatedAt=now,
        )
        self._story_source_documents[document.id] = document
        self.create_job(job)
        return document

    def restart_story_source_analysis(
        self, document_id: uuid.UUID, job: JobDto
    ) -> JobDto:
        existing = self._existing_job(job.idempotency_key, input_hash=job.input_hash)
        if existing is not None:
            if existing.story_source_document_id != document_id:
                raise StudioIdempotencyInputConflictError(
                    "idempotency key already belongs to different input"
                )
            return existing
        document = self._story_source_documents.get(document_id)
        if document is None:
            raise StudioNotFoundError("story source document not found")
        materialized_suggestion_ids = {
            item.suggestion_id for item in self._story_source_materializations.values()
        }
        if document.status == "confirmed" or any(
            item.id in materialized_suggestion_ids for item in document.relation_suggestions
        ):
            raise StudioConflictError("confirmed story relationships cannot be reanalyzed")
        previous = (
            self._jobs.get(document.analysis_job_id)
            if document.analysis_job_id is not None
            else None
        )
        if previous is not None and previous.status == "submission_unknown":
            raise StudioConflictError("story source analysis submission is unresolved")
        if previous is not None and previous.status not in {"failed", "cancelled", "succeeded"}:
            raise StudioConflictError("story source analysis is still running")
        persisted = self.create_job(job)
        self._story_source_documents[document_id] = document.model_copy(
            update={
                "status": "analyzing",
                "analysis_job_id": persisted.id,
                "updated_at": datetime.now(UTC),
            }
        )
        return persisted

    def _story_source_document_with_job_status(
        self, document: StorySourceDocumentDto
    ) -> StorySourceDocumentDto:
        if document.status != "analyzing" or document.analysis_job_id is None:
            return document
        job = self._jobs.get(document.analysis_job_id)
        if job is not None and job.status in {"failed", "cancelled"}:
            return document.model_copy(update={"status": "failed"})
        return document

    def complete_story_source_analysis(
        self, job_id: uuid.UUID, analysis: StoryImportAnalysisDraft
    ) -> StorySourceDocumentDto:
        job = self._jobs.get(job_id)
        if job is None or job.story_source_document_id is None:
            raise StudioNotFoundError("story source analysis job not found")
        document = self._story_source_documents.get(job.story_source_document_id)
        if document is None:
            raise StudioNotFoundError("story source document not found")
        if document.analysis_job_id != job_id:
            raise StudioConflictError("story source analysis is no longer current")
        if document.status == "analyzed" and document.units:
            return document
        materialized_suggestion_ids = {
            item.suggestion_id for item in self._story_source_materializations.values()
        }
        if any(item.id in materialized_suggestion_ids for item in document.relation_suggestions):
            raise StudioConflictError("confirmed story relationships cannot be replaced")
        now = datetime.now(UTC)
        units = [
            StorySourceUnitDto(
                id=uuid.uuid4(),
                documentId=document.id,
                createdAt=now,
                **item.model_dump(mode="python", by_alias=True),
            )
            for item in analysis.units
        ]
        units_by_ordinal = {item.ordinal: item.id for item in units}
        suggestions = [
            StorySourceRelationSuggestionDto(
                id=uuid.uuid4(),
                documentId=document.id,
                relationType=item.relation_type,
                unitIds=[units_by_ordinal[ordinal] for ordinal in item.unit_ordinals],
                title=item.title,
                narrativeMode=item.narrative_mode,
                suggestedSeriesId=item.suggested_series_id,
                confidence=item.confidence,
                rationale=item.rationale,
                status="suggested",
                createdAt=now,
            )
            for item in analysis.relation_suggestions
        ]
        updated = document.model_copy(
            update={
                "status": "analyzed",
                "units": units,
                "relation_suggestions": suggestions,
                "updated_at": now,
            }
        )
        self._story_source_documents[document.id] = updated
        completed = job.model_copy(update={"status": "succeeded", "updated_at": now})
        self._jobs[job.id] = completed
        self._record_event(completed, "story_source.analysis.completed")
        return updated

    def confirm_story_source(
        self, document_id: uuid.UUID, command: StoryImportConfirmCommand
    ) -> StoryImportMaterializationDto:
        existing = self._story_source_materializations.get(command.idempotency_key)
        if existing is not None:
            if (
                existing.suggestion_id != command.suggestion_id
                or existing.target != command.target
                or existing.target_series_id != command.target_series_id
                or existing.target_project_id != command.target_project_id
            ):
                raise StudioIdempotencyInputConflictError(
                    "idempotency key already belongs to different input"
                )
            return existing
        document = self._story_source_documents.get(document_id)
        if document is None:
            raise StudioNotFoundError("story source document not found")
        suggestion = next(
            (item for item in document.relation_suggestions if item.id == command.suggestion_id),
            None,
        )
        if suggestion is None:
            raise StudioNotFoundError("story source relation suggestion not found")
        unit_by_id = {item.id: item for item in document.units}
        units = [unit_by_id[item] for item in suggestion.unit_ids]
        series: StorySeriesDto | None = None
        projects: list[ProjectDto] = []
        if command.target == "new_series":
            if len(units) < 2:
                raise StudioConflictError("a series requires at least two source units")
            series = self.create_story_series(
                SeriesCreateCommand(
                    title=suggestion.title,
                    premise="\n".join(item.raw_text for item in units),
                    narrativeMode=suggestion.narrative_mode or "continuous",
                    plannedEpisodeCount=len(units),
                    defaultEpisodeDurationSeconds=12,
                    worldSetting="由已导入原文中的地点、时间和环境归纳",
                    emotionalDirection="保持原文的情绪变化",
                    recurringElements=[],
                    mustKeep=["保留来源文本中的核心事件"],
                    mustAvoid=["不得无依据改写来源事实"],
                    additionalNotes=f"来源文档 {document.id}",
                ),
                canon_profile_id=self._canon_profile_id,
            )
        elif command.target == "append_series":
            if command.target_series_id is None:
                raise StudioConflictError("append_series requires a target series")
            series = self._story_series.get(command.target_series_id)
            if series is None:
                raise StudioNotFoundError("target story series not found")
        elif command.target == "independent":
            projects = [
                self.create_project(
                    ProjectCreate(
                        title=item.title,
                        theme=item.raw_text,
                        targetDurationSeconds=12,
                    ),
                    canon_profile_id=self._canon_profile_id,
                )
                for item in units
            ]
        elif command.target_series_id is not None:
            series = self._story_series.get(command.target_series_id)
            if series is None:
                raise StudioNotFoundError("target story series not found")
        elif command.target_project_id is not None:
            if command.target_project_id not in self._projects:
                raise StudioNotFoundError("target project not found")
        else:
            raise StudioConflictError("story relationship target is missing")
        now = datetime.now(UTC)
        suggestion.status = "accepted"
        materialization = StoryImportMaterializationDto(
            id=uuid.uuid4(),
            suggestionId=suggestion.id,
            target=command.target,
            targetSeriesId=command.target_series_id,
            targetProjectId=command.target_project_id,
            series=series,
            projects=[
                StoryImportProjectDto(
                    id=item.id,
                    title=item.title,
                    theme=item.theme,
                    targetDurationSeconds=item.target_duration_seconds,
                )
                for item in projects
            ],
            createdAt=now,
        )
        self._story_source_materializations[command.idempotency_key] = materialization
        if all(item.status != "suggested" for item in document.relation_suggestions):
            document.status = "confirmed"
            document.updated_at = now
        return materialization

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
                if job.project_id == project_id
                and job.kind in {"plan_story", "plan_series_episode"}
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
        if job.kind not in {"plan_story", "plan_series_episode"}:
            raise StudioConflictError("job is not a planner job")
        if job.project_id is None:
            raise StudioConflictError("planner job has no project")
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

    def create_shot_plan(
        self,
        project_id: uuid.UUID,
        draft: ShotPlanDraft,
        *,
        active: bool = True,
        review_status: str = "accepted",
        producing_job_id: uuid.UUID | None = None,
        base_shot_plan_version_id: uuid.UUID | None = None,
    ) -> ShotPlanVersionDto:
        plans = self._shot_plans.setdefault(project_id, [])
        now = datetime.now(UTC)
        if active:
            for plan in plans:
                plan.active = False
        if review_status == "candidate":
            for plan in plans:
                if plan.review_status == "candidate":
                    plan.review_status = "superseded"
                    plan.decided_at = now
        if active and base_shot_plan_version_id is not None:
            for plan in plans:
                if plan.id == base_shot_plan_version_id and plan.review_status == "candidate":
                    plan.review_status = "superseded"
                    plan.decided_at = now
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
            reviewStatus=review_status,
            producingJobId=producing_job_id,
            baseShotPlanVersionId=base_shot_plan_version_id,
            decidedAt=now if review_status != "candidate" else None,
            active=active,
            outdated=False,
            createdAt=now,
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
        self,
        project_id: uuid.UUID,
        shot_plan_id: uuid.UUID,
        *,
        expected_active_shot_plan_version_id: uuid.UUID | None,
    ) -> ShotPlanVersionDto:
        plans = self._shot_plans.get(project_id, [])
        selected = next((plan for plan in plans if plan.id == shot_plan_id), None)
        if selected is None:
            raise StudioNotFoundError("shot plan version not found")
        if selected.active and selected.review_status == "accepted":
            return selected
        if selected.review_status in {"rejected", "superseded"}:
            raise StudioConflictError("shot plan version cannot be activated")
        current = next((plan for plan in plans if plan.active), None)
        if expected_active_shot_plan_version_id is not None and (
            current is None or current.id != expected_active_shot_plan_version_id
        ):
            raise StudioConflictError("active shot plan version changed")
        for plan in plans:
            plan.active = plan.id == shot_plan_id
        selected.review_status = "accepted"
        selected.decided_at = datetime.now(UTC)
        return selected

    def reject_shot_plan(
        self, project_id: uuid.UUID, shot_plan_id: uuid.UUID
    ) -> ShotPlanVersionDto:
        plans = self._shot_plans.get(project_id, [])
        selected = next((plan for plan in plans if plan.id == shot_plan_id), None)
        if selected is None:
            raise StudioNotFoundError("shot plan version not found")
        if selected.review_status == "rejected":
            return selected
        if selected.review_status != "candidate" or selected.active:
            raise StudioConflictError("only a pending shot plan candidate can be rejected")
        selected.review_status = "rejected"
        selected.decided_at = datetime.now(UTC)
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
            raise StudioConflictError("project environment must be an environment image")
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
            else:
                current.pop(selection.slot, None)
        return current

    def replace_continuity_keyframes(
        self, project_id: uuid.UUID, asset_ids: list[uuid.UUID]
    ) -> list[AssetDto]:
        current = self.current_selections(project_id)
        selected: list[AssetDto] = []
        for index, slot in enumerate(("continuity_keyframe_1", "continuity_keyframe_2")):
            if index < len(asset_ids):
                asset = self._assets.get(asset_ids[index])
                if asset is None or asset.project_id != project_id or asset.media_type != "image":
                    raise StudioNotFoundError("continuity keyframe not found")
                self.select_asset(project_id, slot=slot, asset_id=asset.id)
                selected.append(asset)
            elif slot in current:
                self.select_asset(
                    project_id,
                    slot=slot,
                    asset_id=current[slot].id,
                    decision="rejected",
                )
        return selected

    def save_episode_reference_manifest(
        self,
        episode_id: uuid.UUID,
        job_id: uuid.UUID,
        continuity_snapshot_id: uuid.UUID | None,
        references: list[dict[str, Any]],
    ) -> None:
        existing = self._episode_reference_manifests.get(job_id)
        if existing is not None and existing != references:
            raise StudioConflictError("episode reference manifest changed")
        self._episode_reference_manifests[job_id] = references

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

    def record_director_validation(
        self, job_id: uuid.UUID, validation: dict[str, object]
    ) -> JobDto:
        job = self._jobs.get(job_id)
        if job is None:
            raise StudioNotFoundError("job not found")
        provider_result = dict(job.provider_result or {})
        provider_result["validation"] = validation
        updated = job.model_copy(update={"provider_result": provider_result})
        self._jobs[job_id] = updated
        return updated

    def record_series_plan_validation(
        self, job_id: uuid.UUID, validation: dict[str, object]
    ) -> JobDto:
        job = self._jobs.get(job_id)
        if job is None:
            raise StudioNotFoundError("job not found")
        provider_result = dict(job.provider_result or {})
        provider_result["validation"] = validation
        updated = job.model_copy(update={"provider_result": provider_result})
        self._jobs[job_id] = updated
        return updated

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
            raise StudioIdempotencyInputConflictError(
                "idempotency key already belongs to different input"
            )
        return existing

    def _record_event(
        self, job: JobDto, event_type: str, payload: dict[str, str] | None = None
    ) -> None:
        self._job_events.append(
            JobEventDto(
                id=len(self._job_events) + 1,
                jobId=job.id,
                projectId=job.project_id,
                seriesId=job.series_id,
                storySourceDocumentId=job.story_source_document_id,
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
