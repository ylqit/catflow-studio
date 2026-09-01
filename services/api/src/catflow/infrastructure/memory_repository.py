from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from catflow.application.service import (
    AssetDto,
    EditDecisionListDto,
    EditVersionDto,
    JobDto,
    JobEventDto,
    LifeStoryProposalDto,
    PlannerMessageCommand,
    PlannerMessageDto,
    PlannerSnapshotDto,
    ProjectCreate,
    ProjectDto,
    ProjectPatch,
    ProjectSelectionDto,
    ShotPlanVersionDto,
    StoryCreateCommand,
    StoryVersionDto,
    StudioConflictError,
    StudioNotFoundError,
)
from catflow.domain.models import LifeStoryProposalDraft, ShotPlanDraft


class MemoryStudioRepository:
    """Deterministic test repository; production uses PostgreSQL."""

    def __init__(self) -> None:
        self._canon_profile_id = uuid.uuid4()
        self._projects: dict[uuid.UUID, ProjectDto] = {}
        self._planner_sessions: dict[uuid.UUID, tuple[uuid.UUID, int]] = {}
        self._messages: dict[uuid.UUID, list[PlannerMessageDto]] = {}
        self._proposals: dict[uuid.UUID, LifeStoryProposalDto] = {}
        self._stories: dict[uuid.UUID, list[StoryVersionDto]] = {}
        self._shot_plans: dict[uuid.UUID, list[ShotPlanVersionDto]] = {}
        self._assets: dict[uuid.UUID, AssetDto] = {}
        self._selections: dict[uuid.UUID, list[ProjectSelectionDto]] = {}
        self._jobs: dict[uuid.UUID, JobDto] = {}
        self._jobs_by_idempotency: dict[str, uuid.UUID] = {}
        self._job_events: list[JobEventDto] = []
        self._edits: dict[uuid.UUID, list[EditVersionDto]] = {}

    def active_canon_profile_id(self) -> uuid.UUID:
        return self._canon_profile_id

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
        session_id = uuid.uuid4()
        self._planner_sessions[project.id] = (session_id, 1)
        self._messages[session_id] = []
        return project

    def list_projects(self) -> list[ProjectDto]:
        return sorted(self._projects.values(), key=lambda project: project.created_at, reverse=True)

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
        return PlannerSnapshotDto(
            sessionId=session_id,
            projectId=project_id,
            contextRevision=context_revision,
            messages=list(self._messages[session_id]),
            proposals=sorted(
                [item for item in self._proposals.values() if item.project_id == project_id],
                key=lambda item: item.id.hex,
            ),
        )

    def enqueue_planner_message(
        self, project_id: uuid.UUID, command: PlannerMessageCommand, *, input_hash: str
    ) -> JobDto:
        existing = self._existing_job(command.idempotency_key, input_hash=input_hash)
        if existing is not None:
            return existing
        session_id, _ = self._planner_sessions[project_id]
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
        return self.create_job(
            JobDto(
                id=uuid.uuid4(),
                projectId=project_id,
                kind="plan_story",
                status="queued",
                inputHash=input_hash,
                idempotencyKey=command.idempotency_key,
                provider="fake",
                model="catflow-fake-planner-v1",
                expectedCostMicros=0,
                frozenInput={
                    "text": command.text,
                    "contextRevision": command.expected_context_revision,
                    "sessionId": str(session_id),
                    "targetDurationSeconds": self._projects[
                        project_id
                    ].target_duration_seconds,
                },
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

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
    ) -> AssetDto:
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
        asset = AssetDto(
            id=uuid.uuid4(),
            projectId=project_id,
            producingJobId=producing_job_id,
            role=role,
            mediaType=media_type,
            storageKey=storage_key,
            sha256=sha256,
            byteSize=byte_size,
            metadata={},
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
        asset = self._assets.get(asset_id)
        if asset is None or asset.project_id != project_id:
            raise StudioNotFoundError("asset not found")
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
        current: dict[str, AssetDto] = {}
        for selection in self._selections.get(project_id, []):
            if selection.decision in {"selected", "approved"}:
                current[selection.slot] = self._assets[selection.asset_id]
        return current

    def list_assets(self, project_id: uuid.UUID) -> list[AssetDto]:
        return sorted(
            [asset for asset in self._assets.values() if asset.project_id == project_id],
            key=lambda asset: asset.created_at,
            reverse=True,
        )

    def get_asset(self, asset_id: uuid.UUID) -> AssetDto | None:
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

    def create_edit(
        self,
        project_id: uuid.UUID,
        *,
        source_selection_hash: str,
        edl: EditDecisionListDto,
    ) -> EditVersionDto:
        project_edits = self._edits.setdefault(project_id, [])
        edit = EditVersionDto(
            id=uuid.uuid4(),
            projectId=project_id,
            revision=len(project_edits) + 1,
            sourceSelectionHash=source_selection_hash,
            edl=edl,
            status="draft",
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
