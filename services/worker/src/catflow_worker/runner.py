from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session, sessionmaker

from catflow.application.gateways import ProviderGatewayError
from catflow.application.service import StudioService
from catflow.domain.billing import RateCardItem, calculate_usage_cost
from catflow.domain.models import (
    BlockingDesign,
    CompositionDesign,
    ContinuityDesign,
    DirectorMicroEvent,
    DirectorPlanPayload,
    DirectorStoryTreatment,
    EmotionalArc,
    LensDesign,
    LifeStoryProposalDraft,
    LightingDesign,
    PhysicalChangeDesign,
    PropStateChange,
    ShotSoundDesign,
    ShotSpec,
)
from catflow.infrastructure.models import JobEventRecord, JobRecord, VideoRepairRecord


class ProviderPoll(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["running", "succeeded", "failed"]
    result: dict[str, object] | None = None
    usage: dict[str, object] | None = None
    error: dict[str, object] | None = None


class ProviderSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str | None = Field(alias="taskId", default=None)
    result: dict[str, object] | None = None
    usage: dict[str, object] | None = None
    metadata: dict[str, object] | None = None


class ProviderTaskGateway(Protocol):
    def prepare_submission(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> None: ...

    def submit(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> ProviderSubmission: ...

    def poll(self, provider_task_id: str) -> ProviderPoll: ...

    def cancel(self, provider_task_id: str) -> bool: ...


class JobResultHandler(Protocol):
    def store_result(self, job_id: uuid.UUID) -> None: ...


class DurableJobWorker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        provider: ProviderTaskGateway,
        *,
        worker_id: str,
        provider_name: str | None = None,
        lease_seconds: int = 30,
        poll_backoff_seconds: float = 2.0,
        studio_service: StudioService | None = None,
        result_handler: JobResultHandler | None = None,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._worker_id = worker_id
        self._provider_name = provider_name
        self._lease_seconds = lease_seconds
        self._poll_backoff_seconds = poll_backoff_seconds
        self._studio_service = studio_service
        self._result_handler = result_handler

    def run_once(self) -> bool:
        claimed = self._claim()
        if claimed is None:
            return False
        (
            job_id,
            kind,
            provider,
            status,
            provider_task_id,
            submission_started_at,
            frozen_input,
        ) = claimed

        if status == "cancel_requested":
            self._cancel(job_id, provider_task_id)
        elif status == "storing":
            self._store_local_result(job_id)
        elif kind == "probe_segment_video_data_url" and status in {"submitted", "polling"}:
            self._finish_data_url_probe(job_id, provider_task_id)
        elif kind == "plan_story" and provider == "fake" and status == "submitting":
            self._complete_fake_planner_job(job_id, frozen_input)
        elif kind == "plan_shots" and provider == "fake" and status == "submitting":
            self._complete_fake_director_job(job_id, frozen_input)
        elif kind == "render_export" and status == "submitting":
            self._store_local_result(job_id)
        elif status == "submitting" and provider_task_id is not None:
            self._mark_submitted(job_id)
        elif status == "submitting" and submission_started_at is not None:
            self._mark_submission_unknown(
                job_id,
                ProviderGatewayError(
                    code="provider_submission_interrupted",
                    message="provider submission started before the worker restarted",
                    retryable=False,
                    submission_unknown=True,
                ),
            )
        elif status == "submitting":
            self._submit(job_id, frozen_input)
        elif status in {"submitted", "polling"}:
            self._poll(job_id, provider_task_id)
        return True

    def _claim(
        self,
    ) -> (
        tuple[
            uuid.UUID,
            str,
            str | None,
            str,
            str | None,
            datetime | None,
            dict[str, object],
        ]
        | None
    ):
        now = datetime.now(UTC)
        priority = case(
            (JobRecord.status.in_(("cancel_requested", "storing")), 0),
            (JobRecord.status.in_(("queued", "submitting")), 1),
            else_=2,
        )
        with self._sessions.begin() as session:
            job = session.scalar(
                select(JobRecord)
                .where(
                    JobRecord.status.in_(
                        (
                            "queued",
                            "submitting",
                            "submitted",
                            "polling",
                            "storing",
                            "cancel_requested",
                        )
                    ),
                    (
                        JobRecord.provider.in_((self._provider_name, "local_ffmpeg"))
                        if self._provider_name is not None
                        else True
                    ),
                    or_(JobRecord.leased_until.is_(None), JobRecord.leased_until < now),
                )
                .order_by(priority, JobRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.status == "queued":
                job.status = "submitting"
                job.updated_at = now
                self._add_event(session, job, "job.submitting")
            job.locked_by = self._worker_id
            job.leased_until = now + timedelta(seconds=self._lease_seconds)
            session.flush()
            return (
                job.id,
                job.kind,
                job.provider,
                job.status,
                job.provider_task_id,
                job.provider_submission_started_at,
                dict(job.frozen_input_json),
            )

    def _complete_fake_planner_job(
        self, job_id: uuid.UUID, frozen_input: dict[str, object]
    ) -> None:
        if self._studio_service is None:
            self._fail(job_id, "planner_service_unavailable", retryable=False)
            return
        theme = str(frozen_input.get("text") or "一人一猫的安静日常")
        target_duration = max(8, min(15, int(frozen_input.get("targetDurationSeconds") or 10)))
        self._studio_service.complete_planner_job(
            job_id,
            LifeStoryProposalDraft(
                title=theme[:80],
                summary=f"围绕“{theme}”展开一个安静、可见且温暖的生活微事件。",
                body=(
                    f"由“{theme}”触发，孩子注意到猫咪的需要并做出一个简单动作；"
                    "猫咪给出清楚回应，画面发生可见变化，最后两者安静地靠在一起。"
                ),
                trigger=theme,
                childAction="孩子注意到猫咪的需要并轻轻伸手帮忙",
                catResponse="猫咪停下来观察并安静配合",
                visibleChange="原本的小麻烦被整理好，画面恢复安稳",
                warmEnding="猫咪靠近孩子，柔和暖光落在一人一猫身上",
                targetDurationSeconds=target_duration,
                dialoguePolicy="none",
                environmentIntent="适合主题的日常室内环境，柔和漫射暖光",
            ),
        )

    def _complete_fake_director_job(
        self, job_id: uuid.UUID, frozen_input: dict[str, object]
    ) -> None:
        if self._studio_service is None:
            self._fail(job_id, "director_service_unavailable", retryable=False)
            return
        clip = frozen_input.get("clip")
        if not isinstance(clip, dict):
            self._fail(job_id, "director_clip_missing", retryable=False)
            return
        target_duration = int(clip.get("durationSeconds") or 12)
        theme = str(clip.get("microEvent") or "一人一猫生活微事件")
        base_duration = target_duration // 3
        durations = (base_duration, base_duration, target_duration - 2 * base_duration)
        shot_intents = (
            ("触发被看见", "注意到眼前变化", "停下观察", "事件状态清楚出现"),
            ("动作产生因果", "沿清楚路径完成帮助动作", "自然配合并转移重心", "目标状态逐步改变"),
            (
                "主动温暖收束",
                "收好道具并继续自然小动作",
                "向前迈步且尾巴轻摆",
                "变化完成且动作继续",
            ),
        )
        shots: list[ShotSpec] = []
        for index, (duration, intent) in enumerate(zip(durations, shot_intents, strict=True), 1):
            intent_name, child_action, cat_action, visible_change = intent
            shots.append(
                ShotSpec(
                    id=f"shot-{index}",
                    order=index,
                    durationSeconds=duration,
                    durationFrames=duration * 24,
                    framing=("中景" if index != 2 else "近景"),
                    cameraMovement=("固定观察" if index == 1 else "缓慢跟随"),
                    childAction=child_action,
                    catAction=cat_action,
                    environmentChange=visible_change,
                    transition=("continuous" if index != 2 else "soft_cut"),
                    lens=LensDesign(
                        focalLengthEquivalent="35mm",
                        cameraHeight="儿童腰部高度",
                        cameraAngle="轻微俯拍",
                        perspectiveIntent="同时保持儿童、猫咪和关键道具清楚可读",
                    ),
                    composition=CompositionDesign(
                        subjectPlacement="儿童与猫咪分处画面中线两侧",
                        foreground="当前生活道具",
                        middleGround="同一位儿童和同一只猫咪",
                        background="柔和数字插画生活环境",
                        screenDirection="始终由画面外侧向事件中心，再向室内",
                        eyeLine="儿童视线跟随猫咪和道具状态",
                    ),
                    childBlocking=BlockingDesign(
                        initialState=f"儿童准备进入镜头{index}的{intent_name}状态",
                        movementPath=child_action,
                        endState=f"儿童完成镜头{index}动作并为下一镜头留出连续姿态",
                        microMotions=["视线自然跟随", "衣角随动作轻摆"],
                    ),
                    catBlocking=BlockingDesign(
                        initialState=f"猫咪四足稳定处于镜头{index}起始位置",
                        movementPath=cat_action,
                        endState=f"猫咪四足稳定处于镜头{index}结束位置",
                        microMotions=["猫耳转向", "尾巴自然摆动"],
                    ),
                    physicalChange=PhysicalChangeDesign(
                        subject="当前事件中的道具或环境状态",
                        before=f"镜头{index}开始前尚未完成",
                        after=visible_change,
                    ),
                    continuity=ContinuityDesign(
                        incoming=("建立事件空间" if index == 1 else f"承接镜头{index - 1}结束姿态"),
                        outgoing=("进入核心动作" if index == 1 else "保持动作方向连续"),
                        sharedVisualElement="同一角色、道具、机位轴线与光线方向",
                        finalFrame=(
                            "儿童完成收纳动作，猫咪仍在主动向前迈步"
                            if index == 3
                            else f"镜头{index}动作状态清楚闭合"
                        ),
                    ),
                    lighting=LightingDesign(
                        direction="室内侧上方",
                        softness="柔和漫射",
                        colorIntent="自然暖灰色，不改变Canon画风",
                    ),
                    sound=ShotSoundDesign(
                        ambience=["安静生活环境声"],
                        objectEffects=["道具轻微接触声"],
                        movementEffects=["衣料与猫爪轻响"],
                        musicIntent="克制的温暖木琴点音",
                    ),
                    directorIntent=f"以{intent_name}推进唯一可见因果链",
                    generationRisks=[
                        {"code": "identity_drift", "message": "保持儿童和猫咪身份与比例"},
                        {"code": "motion_overload", "message": "每个角色不超过三个微动作"},
                    ],
                )
            )
        self._studio_service.complete_shot_plan_job(
            job_id,
            DirectorPlanPayload(
                targetDurationSeconds=target_duration,
                directorTreatment=DirectorStoryTreatment(
                    logline=f"围绕“{theme}”完成一个可见而温暖的生活动作",
                    theme="一人一猫的日常照顾",
                    emotionalTone=["安静", "温暖"],
                    visualMotif="关键道具或环境状态发生清楚变化",
                    spatialSetting=str(clip.get("environmentIntent") or "柔和室内生活空间"),
                    emotionalArc=EmotionalArc(
                        opening="发现一个日常小问题",
                        development="儿童与猫咪通过动作共同处理",
                        resolution="问题解决后仍保留一个主动温暖动作",
                    ),
                    microEvent=DirectorMicroEvent(
                        trigger=theme,
                        childIntent="照顾猫咪并解决眼前的小问题",
                        childAction=str(clip.get("childAction") or "完成一个简单帮助动作"),
                        catResponse=str(
                            clip.get("catActionOrObservation") or "猫咪自然观察并配合"
                        ),
                        visibleCauseAndEffect=str(
                            clip.get("visibleCauseAndEffect") or "生活状态明显改变"
                        ),
                        warmEnding=str(
                            clip.get("warmEnding") or "一人一猫继续一个自然小动作"
                        ),
                    ),
                    propStateChange=PropStateChange(
                        initialState="事件道具处于问题发生时的状态",
                        changedState="道具状态清楚变化并完成因果闭合",
                    ),
                    soundIntent="用环境声、物件声和动作声表达因果，不依赖对白",
                    endingImage="儿童完成收纳，猫咪继续主动迈步，不静止凝视",
                ),
                shots=shots,
            ),
        )
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "succeeded"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.succeeded")

    def _submit(self, job_id: uuid.UUID, frozen_input: dict[str, object]) -> None:
        kind = self._job_kind(job_id)
        prepare = getattr(self._provider, "prepare_submission", None)
        if callable(prepare):
            try:
                prepare(job_id=job_id, kind=kind, frozen_input=frozen_input)
            except ProviderGatewayError as exc:
                self._fail_with_document(job_id, exc.as_error_document())
                return
            except Exception as exc:
                self._fail_with_message(
                    job_id,
                    "provider_input_preparation_failed",
                    str(exc),
                    retryable=False,
                )
                return
        if not self._begin_submission(job_id):
            return
        try:
            submission = self._provider.submit(
                job_id=job_id,
                kind=kind,
                frozen_input=frozen_input,
            )
        except ProviderGatewayError as exc:
            if exc.submission_unknown:
                self._mark_submission_unknown(job_id, exc)
            else:
                self._fail_with_document(job_id, exc.as_error_document())
            return
        except Exception as exc:
            self._mark_submission_unknown(
                job_id,
                ProviderGatewayError(
                    code="provider_submission_exception",
                    message=str(exc),
                    retryable=False,
                    submission_unknown=True,
                ),
            )
            return
        if submission.result is not None:
            self._persist_immediate_result(job_id, submission)
            return
        if not submission.task_id:
            self._mark_submission_unknown(
                job_id,
                ProviderGatewayError(
                    code="missing_provider_task_id",
                    message="provider did not return a task id",
                    retryable=False,
                    submission_unknown=True,
                ),
            )
            return
        provider_task_id = submission.task_id
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            if job.provider_task_id is not None:
                self._release(job)
                return
            if job.status != "submitting":
                self._release(job)
                return
            job.provider_task_id = provider_task_id
            job.provider_result_json = submission.metadata
            job.provider_request_id = _provider_request_id(submission.metadata)
            job.status = "submitted"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.submitted")

    def _job_kind(self, job_id: uuid.UUID) -> str:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            return job.kind

    def _persist_immediate_result(self, job_id: uuid.UUID, submission: ProviderSubmission) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None or job.status != "submitting":
                return
            job.provider_result_json = submission.result
            _record_provider_usage(
                job,
                usage=submission.usage,
                provider_result=submission.result,
            )
            job.status = "storing" if self._result_handler is not None else "succeeded"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, f"job.{job.status}")
        if self._result_handler is not None:
            self._store_local_result(job_id)

    def _begin_submission(self, job_id: uuid.UUID) -> bool:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None or job.status != "submitting" or job.provider_task_id is not None:
                return False
            if job.provider_submission_started_at is not None:
                return False
            job.provider_submission_started_at = datetime.now(UTC)
            job.updated_at = job.provider_submission_started_at
            session.flush()
            return True

    def _mark_submission_unknown(self, job_id: uuid.UUID, error: ProviderGatewayError) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "submission_unknown"
            job.error_json = error.as_error_document()
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.submission_unknown")

    def _mark_submitted(self, job_id: uuid.UUID) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            if job.status == "submitting" and job.provider_task_id is not None:
                job.status = "submitted"
                job.updated_at = datetime.now(UTC)
                self._add_event(session, job, "job.submitted")
            self._release(job)

    def _poll(self, job_id: uuid.UUID, provider_task_id: str | None) -> None:
        if provider_task_id is None:
            self._fail(job_id, "missing_provider_task_id", retryable=False)
            return
        result = self._provider.poll(provider_task_id)
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            previous_status = job.status
            now = datetime.now(UTC)
            if result.status == "running":
                job.status = "polling"
                event_type = "job.polling" if previous_status != "polling" else None
                job.locked_by = None
                job.leased_until = now + timedelta(seconds=self._poll_backoff_seconds)
            elif result.status == "succeeded":
                job.provider_result_json = {
                    **dict(job.provider_result_json or {}),
                    **dict(result.result or {}),
                }
                _record_provider_usage(
                    job,
                    usage=result.usage,
                    provider_result=job.provider_result_json,
                )
                job.status = "storing" if self._result_handler is not None else "succeeded"
                event_type = f"job.{job.status}"
                self._release(job)
            else:
                job.status = "failed"
                job.error_json = result.error or {
                    "code": "provider_failed",
                    "message": "Provider task failed",
                    "retryable": False,
                }
                event_type = "job.failed"
                self._release(job)
            job.updated_at = now
            if event_type is not None:
                self._add_event(session, job, event_type)
        if result.status == "succeeded" and self._result_handler is not None:
            self._store_local_result(job_id)

    def _finish_data_url_probe(
        self, job_id: uuid.UUID, provider_task_id: str | None
    ) -> None:
        if provider_task_id is None:
            self._fail(job_id, "missing_provider_task_id", retryable=False)
            return
        cancel_error: str | None = None
        try:
            cancel_requested = self._provider.cancel(provider_task_id)
        except Exception as exc:
            cancel_requested = False
            cancel_error = str(exc)
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.provider_result_json = {
                **dict(job.provider_result_json or {}),
                "transport": "data_url_experimental",
                "transportAccepted": True,
                "cancelRequested": cancel_requested,
            }
            if cancel_error:
                job.provider_result_json["cancelError"] = cancel_error
            job.status = "succeeded"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.succeeded")

    def _store_local_result(self, job_id: uuid.UUID) -> None:
        if self._result_handler is None:
            self._fail(job_id, "result_handler_unavailable", retryable=False)
            return
        try:
            self._result_handler.store_result(job_id)
        except Exception as exc:
            self._fail_with_message(
                job_id,
                "result_storage_failed",
                str(exc),
                retryable=False,
            )
            return
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "succeeded"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.succeeded")

    def _cancel(self, job_id: uuid.UUID, provider_task_id: str | None) -> None:
        cancelled = provider_task_id is None or self._provider.cancel(provider_task_id)
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "cancelled" if cancelled else "cancel_requested"
            if cancelled and job.video_repair_id is not None:
                repair = session.get(VideoRepairRecord, job.video_repair_id)
                if repair is not None and repair.status == "generating":
                    repair.status = "cancelled"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, f"job.{job.status}")

    def _fail(self, job_id: uuid.UUID, code: str, *, retryable: bool) -> None:
        self._fail_with_message(job_id, code, code.replace("_", " "), retryable=retryable)

    def _fail_with_message(
        self, job_id: uuid.UUID, code: str, message: str, *, retryable: bool
    ) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "failed"
            job.error_json = {
                "code": code,
                "message": message,
                "retryable": retryable,
            }
            if job.video_repair_id is not None:
                repair = session.get(VideoRepairRecord, job.video_repair_id)
                if repair is not None and repair.status == "generating":
                    repair.status = "failed"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.failed")

    def _fail_with_document(self, job_id: uuid.UUID, error: dict[str, object]) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "failed"
            job.error_json = error
            if job.video_repair_id is not None:
                repair = session.get(VideoRepairRecord, job.video_repair_id)
                if repair is not None and repair.status == "generating":
                    repair.status = "failed"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.failed")

    @staticmethod
    def _release(job: JobRecord) -> None:
        job.locked_by = None
        job.leased_until = None

    @staticmethod
    def _add_event(session: Session, job: JobRecord, event_type: str) -> None:
        session.add(
            JobEventRecord(
                job_id=job.id,
                project_id=job.project_id,
                event_type=event_type,
                payload_json={"jobId": str(job.id), "status": job.status},
            )
        )


def _record_provider_usage(
    job: JobRecord,
    *,
    usage: dict[str, object] | None,
    provider_result: dict[str, object] | None,
) -> None:
    request_id = _provider_request_id(provider_result)
    if request_id is not None:
        job.provider_request_id = request_id
    if usage is None:
        return

    numeric_usage = {
        key: value
        for key, value in usage.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    job.actual_usage_json = numeric_usage

    provider_cost = None if provider_result is None else provider_result.get("actualCostMicros")
    if isinstance(provider_cost, int) and not isinstance(provider_cost, bool):
        job.actual_cost_micros = provider_cost
        job.billing_status = "provider_adjusted"
        return

    snapshot = job.pricing_snapshot_json
    rates_document = snapshot.get("rates") if isinstance(snapshot, dict) else None
    if not isinstance(rates_document, list):
        job.billing_status = "unpriced"
        return
    try:
        rates = tuple(RateCardItem.model_validate(item) for item in rates_document)
        calculated = calculate_usage_cost(numeric_usage, rates)
    except (TypeError, ValueError):
        job.billing_status = "unpriced"
        return
    job.actual_cost_micros = calculated.actual_cost_micros
    job.billing_status = calculated.status
    revision = snapshot.get("revision")
    if isinstance(revision, str):
        job.rate_card_revision = revision


def _provider_request_id(document: dict[str, object] | None) -> str | None:
    if document is None:
        return None
    for key in ("requestId", "responseId"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
