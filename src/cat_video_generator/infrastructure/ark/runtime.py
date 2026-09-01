"""Hot-reloadable, non-secret Ark production settings.

Deployment secrets and transport paths remain in :class:`RuntimeSettings`.
This module owns the lifecycle boundary for user-editable model choices: it
validates the server catalog, persists overrides atomically, and binds one
immutable configuration revision to an entire paid task.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from ...config import RuntimeSettings
from .gateway import ArkGateway


class RuntimeConfigurationConflictError(RuntimeError):
    """The UI confirmed a revision that is no longer current."""


class RuntimeConfigurationFileError(RuntimeError):
    """The non-secret override file cannot safely be used."""


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    id: str
    role: str
    display_name: str
    supported_resolutions: tuple[str, ...] = ()
    supported_input_modes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "displayName": self.display_name,
            "supportedResolutions": list(self.supported_resolutions),
            "supportedInputModes": list(self.supported_input_modes),
        }


MODEL_CATALOG: tuple[ModelCatalogEntry, ...] = (
    ModelCatalogEntry(
        id="doubao-seed-2-1-pro-260628",
        role="planning",
        display_name="Doubao Seed 2.1 Pro（剧情与分镜）",
    ),
    ModelCatalogEntry(
        id="doubao-seedream-5-0-260128",
        role="image",
        display_name="Doubao Seedream 5.0（图片）",
    ),
    ModelCatalogEntry(
        id="doubao-seedance-2-0-260128",
        role="video",
        display_name="Doubao Seedance 2.0",
        supported_resolutions=("480p", "720p"),
        supported_input_modes=("text_only", "reference_media", "first_frame"),
    ),
    ModelCatalogEntry(
        id="doubao-seedance-2-0-mini-260615",
        role="video",
        display_name="Doubao Seedance 2.0 Mini",
        supported_resolutions=("480p", "720p"),
        supported_input_modes=("text_only", "reference_media", "first_frame"),
    ),
    ModelCatalogEntry(
        id="doubao-seed-2-1-pro-260628",
        role="review",
        display_name="Doubao Seed 2.1 Pro（视频审稿）",
    ),
)


@dataclass(frozen=True, slots=True)
class ProductionRuntimeConfig:
    planning_model: str
    image_model: str
    video_model: str
    review_model: str
    video_resolution: str
    semantic_review_enabled: bool
    revision: int
    updated_at: datetime | None
    using_override: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "planningModel": self.planning_model,
            "imageModel": self.image_model,
            "videoModel": self.video_model,
            "reviewModel": self.review_model,
            "videoResolution": self.video_resolution,
            "semanticReviewEnabled": self.semantic_review_enabled,
            "revision": self.revision,
            "updatedAt": None if self.updated_at is None else self.updated_at.isoformat(),
            "usingOverride": self.using_override,
        }


@dataclass(frozen=True, slots=True)
class RuntimeExecution:
    config: ProductionRuntimeConfig
    settings: RuntimeSettings
    gateway: ArkGateway

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.config.to_dict(),
            "provider": self.settings.provider_profile,
            "arkBaseUrlProfile": (
                "standard"
                if self.settings.ark_base_url
                == "https://ark.cn-beijing.volces.com/api/v3"
                else "unknown"
            ),
        }


_BOUND_EXECUTION: ContextVar[RuntimeExecution | None] = ContextVar(
    "cvg_runtime_execution",
    default=None,
)


def current_execution_snapshot() -> dict[str, Any] | None:
    execution = _BOUND_EXECUTION.get()
    return None if execution is None else execution.snapshot()


T = TypeVar("T")


class RuntimeConfigurationManager:
    """Own the active global config and immutable paid-task executions."""

    def __init__(self, deployment: RuntimeSettings, state_path: Path) -> None:
        self._deployment = deployment
        self._state_path = state_path.expanduser().resolve()
        self._lock = threading.RLock()
        self._gateway_cache: dict[int, ArkGateway] = {}
        self._file_error: str | None = None
        self._config = self._load()

    @property
    def provider_profile(self) -> str:
        return self._deployment.provider_profile

    @property
    def current(self) -> ProductionRuntimeConfig:
        with self._lock:
            return self._config

    @property
    def video_resolution(self) -> str:
        execution = _BOUND_EXECUTION.get()
        return execution.config.video_resolution if execution else self.current.video_resolution

    @property
    def semantic_review_enabled(self) -> bool:
        execution = _BOUND_EXECUTION.get()
        return (
            execution.config.semantic_review_enabled
            if execution
            else self.current.semantic_review_enabled
        )

    def settings_for_current(self) -> RuntimeSettings:
        return self._settings_for(self.current)

    def capture(
        self,
        expected_revision: int | None,
    ) -> RuntimeExecution:
        with self._lock:
            config = self._config
            if expected_revision is None:
                raise RuntimeConfigurationConflictError(
                    "缺少 X-CVG-Runtime-Config-Revision，请重新打开费用确认框"
                )
            if expected_revision != config.revision:
                raise RuntimeConfigurationConflictError(
                    f"运行配置已从 revision {expected_revision} 更新为 {config.revision}，"
                    "请重新确认模型与费用"
                )
            if self._file_error:
                raise RuntimeConfigurationFileError(self._file_error)
            settings = self._settings_for(config)
            self._validate_config(config)
            settings.validate_for_ark_access()
            gateway = self._gateway_cache.get(config.revision)
            if gateway is None:
                gateway = ArkGateway(settings)
                self._gateway_cache[config.revision] = gateway
            return RuntimeExecution(config=config, settings=settings, gateway=gateway)

    @contextmanager
    def bind(self, execution: RuntimeExecution) -> Iterator[None]:
        token = _BOUND_EXECUTION.set(execution)
        try:
            yield
        finally:
            _BOUND_EXECUTION.reset(token)

    def run(self, execution: RuntimeExecution, fn: Callable[[], T]) -> T:
        with self.bind(execution):
            return fn()

    def save(self, expected_revision: int, values: dict[str, Any]) -> ProductionRuntimeConfig:
        with self._lock:
            if expected_revision != self._config.revision:
                raise RuntimeConfigurationConflictError(
                    f"运行配置已从 revision {expected_revision} 更新为 {self._config.revision}"
                )
            next_revision = self._config.revision + 1
            updated_at = datetime.now(timezone.utc)
            config = ProductionRuntimeConfig(
                planning_model=str(values["planningModel"]),
                image_model=str(values["imageModel"]),
                video_model=str(values["videoModel"]),
                review_model=str(values["reviewModel"]),
                video_resolution=str(values["videoResolution"]),
                semantic_review_enabled=bool(values["semanticReviewEnabled"]),
                revision=next_revision,
                updated_at=updated_at,
                using_override=True,
            )
            self._validate_config(config)
            self._write_state(
                revision=next_revision,
                updated_at=updated_at,
                override=self._override_dict(config),
            )
            self._config = config
            self._file_error = None
            return config

    def restore_defaults(self, expected_revision: int) -> ProductionRuntimeConfig:
        with self._lock:
            if expected_revision != self._config.revision:
                raise RuntimeConfigurationConflictError(
                    f"运行配置已从 revision {expected_revision} 更新为 {self._config.revision}"
                )
            next_revision = self._config.revision + 1
            updated_at = datetime.now(timezone.utc)
            config = self._deployment_config(
                revision=next_revision,
                updated_at=updated_at,
                using_override=False,
            )
            self._validate_config(config)
            self._write_state(revision=next_revision, updated_at=updated_at, override=None)
            self._config = config
            self._file_error = None
            return config

    def validate_for_video_generation(self, *, allow_paid_generation: bool) -> None:
        execution = self._bound_or_capture_current()
        execution.settings.validate_for_video_generation(
            allow_paid_generation=allow_paid_generation
        )

    def validate_for_range_edit(self, *, allow_paid_generation: bool) -> None:
        execution = self._bound_or_capture_current()
        execution.settings.validate_for_range_edit(allow_paid_generation=allow_paid_generation)

    def validate_for_local_composition(self) -> None:
        self._settings_for(self.current).validate_for_local_composition()

    def report(self) -> dict[str, Any]:
        config = self.current
        effective = self._settings_for(config)
        report = effective.preflight_report()
        issues = list(report.get("generationConfigurationIssues", []))
        try:
            self._validate_config(config)
        except ValueError as exc:
            issues.append(str(exc))
        if self._file_error:
            issues.append(self._file_error)
        report.update(
            {
                "runtimeConfigRevision": config.revision,
                "runtimeConfigUpdatedAt": (
                    None if config.updated_at is None else config.updated_at.isoformat()
                ),
                "runtimeConfigUsingOverride": config.using_override,
                "generationConfigurationIssues": issues,
                "generationConfigurationValid": not issues,
                "arkReady": not issues and bool(self._deployment.ark_api_key),
            }
        )
        return report

    def api_document(self) -> dict[str, Any]:
        config = self.current
        defaults = self._deployment_config(
            revision=0,
            updated_at=None,
            using_override=False,
        )
        report = self.report()
        return {
            "current": config.to_dict(),
            "deploymentDefaults": defaults.to_dict(),
            "modelCatalog": [entry.to_dict() for entry in MODEL_CATALOG],
            "arkApiKeyConfigured": bool(self._deployment.ark_api_key),
            "arkReady": bool(report["arkReady"]),
            "ffmpegAvailable": bool(report["ffmpegAvailable"]),
            "ffprobeAvailable": bool(report["ffprobeAvailable"]),
            "videoGenerationReady": bool(report["videoGenerationReady"]),
            "localCompositionReady": bool(report["localCompositionReady"]),
            "databaseManagedSeparately": True,
            "diagnostics": {
                "provider": self._deployment.provider_profile,
                "arkBaseUrlProfile": report["arkBaseUrlProfile"],
                "directorRequestTimeoutSeconds": (
                    self._deployment.ark_director_request_timeout_seconds
                ),
                "reviewRequestTimeoutSeconds": self._deployment.ark_review_request_timeout_seconds,
                "videoApiTimeoutSeconds": self._deployment.ark_video_api_timeout_seconds,
                "pollIntervalSeconds": self._deployment.ark_poll_interval_seconds,
                "taskTimeoutSeconds": self._deployment.ark_task_timeout_seconds,
                "imageRequestTimeoutSeconds": (
                    self._deployment.ark_image_request_timeout_seconds
                ),
                "workRoot": str(self._deployment.work_root),
                "assetRoot": str(self._deployment.asset_root),
                "configurationWarnings": list(self._deployment.configuration_warnings),
                "configurationIssues": report["generationConfigurationIssues"],
            },
        }

    def _bound_or_capture_current(self) -> RuntimeExecution:
        execution = _BOUND_EXECUTION.get()
        return execution or self.capture(self.current.revision)

    def _settings_for(self, config: ProductionRuntimeConfig) -> RuntimeSettings:
        return replace(
            self._deployment,
            ark_planning_model=config.planning_model,
            ark_image_model=config.image_model,
            ark_video_model=config.video_model,
            ark_review_model=config.review_model,
            ark_video_resolution=config.video_resolution,
            video_semantic_review_mode=(
                "diagnostic" if config.semantic_review_enabled else "off"
            ),
        )

    def _deployment_config(
        self,
        *,
        revision: int,
        updated_at: datetime | None,
        using_override: bool,
    ) -> ProductionRuntimeConfig:
        return ProductionRuntimeConfig(
            planning_model=self._deployment.ark_planning_model,
            image_model=self._deployment.ark_image_model,
            video_model=self._deployment.ark_video_model,
            review_model=self._deployment.ark_review_model,
            video_resolution=self._deployment.ark_video_resolution,
            semantic_review_enabled=(
                self._deployment.video_semantic_review_mode == "diagnostic"
            ),
            revision=revision,
            updated_at=updated_at,
            using_override=using_override,
        )

    def _load(self) -> ProductionRuntimeConfig:
        if not self._state_path.is_file():
            config = self._deployment_config(
                revision=0,
                updated_at=None,
                using_override=False,
            )
            try:
                self._validate_config(config)
            except ValueError as exc:
                self._file_error = str(exc)
            return config
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            if payload.get("schemaVersion") != 1:
                raise ValueError("unsupported schemaVersion")
            revision = int(payload["revision"])
            updated_at = datetime.fromisoformat(payload["updatedAt"])
            override = payload.get("override")
            if override is None:
                config = self._deployment_config(
                    revision=revision,
                    updated_at=updated_at,
                    using_override=False,
                )
            else:
                config = ProductionRuntimeConfig(
                    planning_model=str(override["planningModel"]),
                    image_model=str(override["imageModel"]),
                    video_model=str(override["videoModel"]),
                    review_model=str(override["reviewModel"]),
                    video_resolution=str(override["videoResolution"]),
                    semantic_review_enabled=bool(override["semanticReviewEnabled"]),
                    revision=revision,
                    updated_at=updated_at,
                    using_override=True,
                )
            self._validate_config(config)
            return config
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._file_error = (
                f"运行配置文件 {self._state_path} 已损坏：{exc}；"
                "付费能力已禁用，请在系统设置中恢复部署默认"
            )
            return self._deployment_config(
                revision=0,
                updated_at=None,
                using_override=False,
            )

    def _write_state(
        self,
        *,
        revision: int,
        updated_at: datetime,
        override: dict[str, Any] | None,
    ) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": 1,
            "revision": revision,
            "updatedAt": updated_at.isoformat(),
            "override": override,
        }
        temporary = self._state_path.with_name(
            f".{self._state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self._state_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise RuntimeConfigurationFileError(
                f"无法原子保存运行配置：{exc}"
            ) from exc

    @staticmethod
    def _override_dict(config: ProductionRuntimeConfig) -> dict[str, Any]:
        return {
            "planningModel": config.planning_model,
            "imageModel": config.image_model,
            "videoModel": config.video_model,
            "reviewModel": config.review_model,
            "videoResolution": config.video_resolution,
            "semanticReviewEnabled": config.semantic_review_enabled,
        }

    @staticmethod
    def _validate_config(config: ProductionRuntimeConfig) -> None:
        role_values = {
            "planning": config.planning_model,
            "image": config.image_model,
            "video": config.video_model,
            "review": config.review_model,
        }
        for role, model in role_values.items():
            if not any(entry.role == role and entry.id == model for entry in MODEL_CATALOG):
                raise ValueError(f"模型 {model!r} 不在 {role} 服务端白名单中")
        video_entry = next(
            entry
            for entry in MODEL_CATALOG
            if entry.role == "video" and entry.id == config.video_model
        )
        if config.video_resolution not in video_entry.supported_resolutions:
            raise ValueError(
                f"视频模型 {config.video_model} 不支持 {config.video_resolution}"
            )


class RuntimeArkGateway:
    """Delegate every Ark call to the execution bound to the current task."""

    def __init__(self, manager: RuntimeConfigurationManager) -> None:
        self._manager = manager

    @property
    def model(self) -> str:
        execution = _BOUND_EXECUTION.get()
        return (
            execution.config.planning_model
            if execution is not None
            else self._manager.current.planning_model
        )

    @property
    def analysis_model(self) -> str:
        return self.model

    @property
    def image_model(self) -> str:
        execution = _BOUND_EXECUTION.get()
        return (
            execution.config.image_model
            if execution is not None
            else self._manager.current.image_model
        )

    @property
    def video_model(self) -> str:
        execution = _BOUND_EXECUTION.get()
        return (
            execution.config.video_model
            if execution is not None
            else self._manager.current.video_model
        )

    @property
    def review_model(self) -> str:
        execution = _BOUND_EXECUTION.get()
        return (
            execution.config.review_model
            if execution is not None
            else self._manager.current.review_model
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._execution().gateway, name)

    def _execution(self) -> RuntimeExecution:
        execution = _BOUND_EXECUTION.get()
        if execution is not None:
            return execution
        return self._manager.capture(self._manager.current.revision)
