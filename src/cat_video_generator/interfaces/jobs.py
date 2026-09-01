"""本地HTTP接口的进程内后台任务登记。

视频、图片与镜头建议都是长任务，HTTP请求必须立即返回；真实状态由
PostgreSQL工作流表持有，这里只登记任务句柄用于去重、进度展示和错误呈现。
付费任务经过进程级串行门，避免并发提交造成重复扣费。进程重启后任务列表
清空，恢复语义仍由数据库中的provider task ID与resume用例兜底。
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..application.ports import GatewayError

logger = logging.getLogger(__name__)

PAID_KINDS = frozenset(
    {
        "story_diagnosis",
        "story_expansion",
        "story_rewrite",
        "shot_suggestions",
        "shot_assistance",
        "visual_asset_plan",
        "generate_anchor",
        "generate_reference_image",
        "generate_scene_look",
        "generate_video",
        "range_edit",
        "recipe_story",
        "recipe_creative_brief",
    "recipe_character_design",
    "recipe_group",
        "recipe_storyboard",
        "recipe_anchor",
        "recipe_video",
        "recipe_sequence",
        "story_strategy",
        "storyboard",
    }
)

_ACTIVE_STATUSES = frozenset({"queued", "running"})
_CONTEXT_KEYS = frozenset(
    {
        "projectId",
        "sceneId",
        "shotId",
        "stepId",
        "operationKey",
        "canvasNodeId",
        "canvasGroupId",
        "recipeInstanceId",
        "creationMode",
        "workflowStage",
        "phase",
    }
)


class JobConflictError(RuntimeError):
    """相同去重键的任务正在执行，禁止重复提交。"""

    def __init__(self, message: str, *, job_id: str) -> None:
        super().__init__(message)
        self.job_id = job_id


@dataclass(slots=True)
class JobRecord:
    """一次后台任务的可序列化状态。"""

    job_id: str
    kind: str
    dedup_key: str
    context: dict[str, str] = field(default_factory=dict)
    status: str = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: Any = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为HTTP响应使用的camelCase字典。"""

        return {
            "jobId": self.job_id,
            "kind": self.kind,
            "dedupKey": self.dedup_key,
            "context": self.context,
            "status": self.status,
            "createdAt": self.created_at.isoformat(),
            "startedAt": (None if self.started_at is None else self.started_at.isoformat()),
            "finishedAt": (None if self.finished_at is None else self.finished_at.isoformat()),
            "result": self.result,
            "error": self.error,
        }


class JobRegistry:
    """登记、去重并串行执行付费后台任务。"""

    def __init__(
        self,
        *,
        executor: ThreadPoolExecutor | None = None,
        inline: bool = False,
    ) -> None:
        self._executor = executor
        self._inline = inline
        self._records: dict[str, JobRecord] = {}
        self._records_lock = threading.Lock()
        self._paid_gate = threading.Lock()

    def submit(
        self,
        *,
        kind: str,
        dedup_key: str,
        fn: Callable[[], Any],
        context: dict[str, Any] | None = None,
    ) -> JobRecord:
        """登记并启动任务；相同去重键的活跃任务存在时拒绝。"""

        with self._records_lock:
            for record in self._records.values():
                if record.dedup_key != dedup_key:
                    continue
                if record.status in _ACTIVE_STATUSES:
                    raise JobConflictError(
                        "相同任务正在执行，请等待完成后再提交",
                        job_id=record.job_id,
                    )
                return record
            record = JobRecord(
                job_id=uuid.uuid4().hex,
                kind=kind,
                dedup_key=dedup_key,
                context={
                    key: str(value)
                    for key, value in (context or {}).items()
                    if key in _CONTEXT_KEYS and value is not None
                },
            )
            self._records[record.job_id] = record
        if self._inline:
            self._execute(record, fn)
            return record
        if self._executor is None:
            raise RuntimeError("非内联模式必须提供线程池执行器")
        self._executor.submit(self._execute, record, fn)
        return record

    def get(self, job_id: str) -> JobRecord:
        """按ID返回任务；未知ID抛出LookupError。"""

        try:
            return self._records[job_id]
        except KeyError as exc:
            raise LookupError(f"任务{job_id}不存在") from exc

    def list(self, *, limit: int = 50) -> list[JobRecord]:
        """按创建时间倒序返回最近任务。"""

        return sorted(
            self._records.values(),
            key=lambda record: record.created_at,
            reverse=True,
        )[:limit]

    def _execute(self, record: JobRecord, fn: Callable[[], Any]) -> None:
        try:
            if record.kind in PAID_KINDS:
                with self._paid_gate:
                    self._invoke(record, fn)
            else:
                self._invoke(record, fn)
        except Exception as exc:  # 任务错误必须落进记录而不是丢失
            # 先构造完整错误，再公开 failed 状态，避免轮询线程读到
            # ``failed + error=null`` 的短暂矛盾状态。错误分类本身也不得
            # 覆盖原始异常，否则 Web 将失去唯一可恢复线索。
            logger.exception("后台任务失败：kind=%s job_id=%s", record.kind, record.job_id)
            try:
                error = _classify_error(exc, context=record.context)
            except Exception as classify_exc:  # pragma: no cover - 最后的故障隔离层
                logger.exception("后台任务错误分类失败", exc_info=classify_exc)
                error = {
                    "code": "internal",
                    "message": str(exc) or exc.__class__.__name__,
                    **record.context,
                }
            record.error = error
            record.status = "failed"
        finally:
            record.finished_at = datetime.now(UTC)

    @staticmethod
    def _invoke(record: JobRecord, fn: Callable[[], Any]) -> None:
        record.status = "running"
        record.started_at = datetime.now(UTC)
        record.result = fn()
        record.status = "succeeded"


def _classify_error(
    exc: Exception,
    *,
    context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把异常分级为前端可展示的稳定错误码。"""

    if isinstance(exc, GatewayError):
        code = exc.code
    elif isinstance(exc, ValueError):
        code = "invalid_request"
    elif isinstance(exc, TimeoutError):
        code = "provider_timeout"
    else:
        code = "internal"
    payload: dict[str, Any] = {
        "code": code,
        "message": str(exc) or exc.__class__.__name__,
    }
    payload.update(context or {})
    errors = getattr(exc, "errors", None)
    if errors is not None:
        # Pydantic ValidationError 暴露的是 errors() 方法；业务规划异常则
        # 使用 tuple。两种形态都规范化为可序列化列表，不能让分类器再次失败。
        if callable(errors):
            try:
                errors = errors(include_context=False, include_url=False)
            except TypeError:
                errors = errors()
        payload["details"] = list(errors)
    return payload
