from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from cat_video_generator.application.aigc_canvas import AigcCanvasService
from cat_video_generator.application.ports import CreativeDirectorResult, DirectorResult
from cat_video_generator.domain.aigc_canvas import StoryBrief, SubjectDraft
from cat_video_generator.infrastructure.db.repositories import WorkflowConflictError
from cat_video_generator.infrastructure.fake.gateway import FakeArkGateway
from cat_video_generator.interfaces.api_v2 import StoryStrategyRunRequest


@dataclass
class _StoredSubject:
    id: uuid.UUID
    revision_id: uuid.UUID
    draft: SubjectDraft


class _Repository:
    def __init__(self) -> None:
        self.project_id = uuid.uuid4()
        self.story_id = uuid.uuid4()
        self.brief_id = uuid.uuid4()
        self.brief = StoryBrief(
            theme="小孩与猫在雨前收回晾晒的画",
            audience="亲子观众",
            genre="治愈短剧",
            tone="温暖紧凑",
            aspectRatio="9:16",
            targetDurationSeconds=60,
        )
        self.subjects = (
            _StoredSubject(
                uuid.uuid4(),
                uuid.uuid4(),
                SubjectDraft(
                    name="小满",
                    kind="person",
                    role="protagonist",
                    identityAnchors=["六岁小孩"],
                    immutableTraits=["年龄不变"],
                ),
            ),
            _StoredSubject(
                uuid.uuid4(),
                uuid.uuid4(),
                SubjectDraft(
                    name="灰灰",
                    kind="animal",
                    role="co_protagonist",
                    identityAnchors=["灰白虎斑猫"],
                    immutableTraits=["纹路不变"],
                ),
            ),
        )
        self.events: list[tuple[str, object]] = []
        self.prompt_ids: list[uuid.UUID] = []
        self.prompt_results: dict[uuid.UUID, dict[str, Any]] = {}
        self.saved: list[dict[str, Any]] = []
        self.batch_save_calls: list[dict[str, object]] = []
        self.recovered_story_batch: dict[str, object] | None = None
        self.storyboard: dict[str, Any] | None = None

    def get_episode_visual_profile(self, project_id: uuid.UUID) -> dict[str, object]:
        assert project_id == self.project_id
        return {
            "sourceProfileId": "canon-v3-healing-child-cat-line-texture",
            "stylePositive": ["克制轮廓线", "湿润半透明高光", "柔和漫射光"],
            "styleNegative": ["摄影写实", "复制参考物体或构图"],
        }

    def save_brief(self, project_id: uuid.UUID, payload: StoryBrief) -> dict[str, Any]:
        assert project_id == self.project_id
        self.brief_id = uuid.uuid4()
        self.brief = payload
        return {
            "id": str(self.brief_id),
            "revision": 2,
            **payload.model_dump(mode="json", by_alias=True),
        }

    def get_current_brief(self, project_id: uuid.UUID) -> tuple[uuid.UUID, StoryBrief]:
        assert project_id == self.project_id
        return self.brief_id, self.brief

    def list_subjects(self, project_id: uuid.UUID) -> tuple[_StoredSubject, ...]:
        assert project_id == self.project_id
        return self.subjects

    def begin_generation_attempt(self, **values: object) -> tuple[dict[str, object], bool]:
        self.events.append(("attempt", values))
        return ({"id": str(uuid.uuid4()), "status": "pending"}, True)

    def begin_prompt_run(self, **values: object) -> tuple[uuid.UUID, uuid.UUID]:
        self.events.append(("prompt_started", values))
        prompt_id = uuid.uuid4()
        self.prompt_ids.append(prompt_id)
        return prompt_id, uuid.uuid4()

    def complete_prompt_run(self, prompt_id: uuid.UUID, **values: object) -> None:
        self.events.append(("prompt_completed", {"id": prompt_id, **values}))
        self.prompt_results[prompt_id] = dict(values)

    def save_story_candidate(self, **values: object) -> dict[str, object]:
        candidate = values["candidate"]
        prompt_id = values["candidate_prompt_id"]
        structured = self.prompt_results[prompt_id]["structured_response"]
        document = {
            **values,
            "id": str(uuid.uuid4()),
            "title": candidate.title,
            "body": candidate.body,
            "summary": candidate.summary or candidate.title,
            "warnings": structured["diagnostics"],
            "scorecard": values.get("scorecard"),
            "critic_prompt_id": values.get("critic_prompt_id"),
        }
        self.saved.append(document)
        self.events.append(("candidate_saved", document))
        return document

    def save_story_candidate_batch(self, **values: object) -> tuple[dict[str, object], ...]:
        self.batch_save_calls.append(dict(values))
        candidates = values.pop("candidates")
        return tuple(
            self.save_story_candidate(candidate=candidate, **values)
            for candidate in candidates
        )

    def get_succeeded_story_candidate_batch(self, **values: object) -> dict[str, object] | None:
        del values
        return self.recovered_story_batch

    def get_story_candidates(
        self,
        *,
        project_id: uuid.UUID,
        candidate_ids: tuple[uuid.UUID, ...],
    ) -> tuple[dict[str, object], ...]:
        assert project_id == self.project_id
        by_id = {uuid.UUID(str(item["id"])): item for item in self.saved}
        return tuple(by_id[candidate_id] for candidate_id in candidate_ids)

    def finish_generation_attempt(self, attempt_id: str, **values: object) -> None:
        self.events.append(("attempt_finished", {"id": attempt_id, **values}))

    def get_storyboard_context(
        self,
        project_id: uuid.UUID,
        *,
        source_story_revision_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        del source_story_revision_id
        assert project_id == self.project_id
        return {
            "projectId": str(project_id),
            "storyId": str(self.story_id),
            "brief": self.brief.model_dump(mode="json", by_alias=True),
            "story": {
                "id": str(self.story_id),
                "title": "雨前收画",
                "synopsis": "小满和灰灰一起保住画作。",
                "scenes": [
                    {"title": "风起", "synopsis": "发现暴雨。", "durationWeight": 1},
                    {"title": "收画", "synopsis": "合作收画。", "durationWeight": 2},
                ],
            },
            "subjects": [
                item.draft.model_dump(mode="json", by_alias=True) for item in self.subjects
            ],
            "existing": self.storyboard,
        }

    def save_storyboard_plan(self, project_id: uuid.UUID, **values: object) -> dict[str, Any]:
        assert project_id == self.project_id
        plan = values["plan"]
        durations = values["durations"]
        self.storyboard = {
            "projectId": str(project_id),
            "storyRevisionId": str(self.story_id),
            "status": "ready",
            "targetDurationSeconds": sum(durations),
            "beats": [
                {
                    "id": str(uuid.uuid4()),
                    "title": beat.title,
                    "durationSeconds": duration,
                    "promptId": str(values["prompt_id"]),
                }
                for beat, duration in zip(plan.beats, durations, strict=True)
            ],
        }
        return self.storyboard


class _Director:
    model = "fake-planning"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.prompts: list[str] = []
        self.creative_payload: dict[str, Any] | str = {
            "candidates": [
                {
                    "title": "一起收画",
                    "body": "风起时，小满和灰灰一起把晾晒的画收回廊下。",
                    "summary": "孩子和猫共同守住画作。",
                },
                {
                    "title": "猫爪天气预报",
                    "body": "灰灰先察觉雨意，小满读懂它的提醒，两者赶在雨前收好画。",
                    "summary": "猫先发现天气变化。",
                },
                {
                    "title": "最后一张画",
                    "body": "一张画被风吹走，小满追赶，灰灰把它拦在安全的花盆边。",
                    "summary": "一场意外变成温暖合作。",
                },
            ]
        }

    def generate_creative_text(
        self,
        *,
        prompt: str,
        output_name: str,
    ) -> CreativeDirectorResult:
        self.calls.append(output_name)
        self.prompts.append(prompt)
        return CreativeDirectorResult(
            payload=self.creative_payload,
            response_id=f"response-{len(self.calls)}",
            model=self.model,
            request_hash=f"hash-{len(self.calls)}",
        )

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
        image_paths: tuple[str, ...] = (),
    ) -> DirectorResult:
        del schema, image_paths
        self.calls.append(output_name)
        self.prompts.append(prompt)
        if output_name == "HealingCreativeBrief":
            payload = {
                "theme": "供应商返回主题",
                "audience": "喜欢治愈日常的短视频观众",
                "genre": "无对白治愈短片",
                "tone": "安静、温暖、克制",
                "aspectRatio": "9:16",
                "targetDurationSeconds": 15,
                "constraints": ["雨后小院", "自然猫四足姿态"],
            }
        elif output_name == "CanvasStoryCriticOutput":
            payload = {
                "openingHook": 8,
                "causalCompleteness": 8,
                "subjectNecessity": 9,
                "emotionalArc": 8,
                "visualizability": 9,
                "durationFit": 8,
                "continuityRisk": 7,
                "safety": 10,
                "rationale": "两个主体都不可替代。",
                "warnings": [],
            }
        else:
            payload = {
                "beats": [
                    {
                        "sceneOrder": 1 if index < 2 else 2,
                        "title": f"Beat {index + 1}",
                        "action": "一个连续且可拍摄的动作。",
                        "camera": "中景稳定推进。",
                        "dialogue": "",
                        "durationWeight": 1,
                    }
                    for index in range(5)
                ]
            }
        return DirectorResult(
            payload=payload,
            response_id=f"response-{len(self.calls)}",
            model=self.model,
            request_hash=f"hash-{len(self.calls)}",
        )


def test_creative_brief_uses_applied_canon_v3_visual_profile_without_watercolor_conflict(
) -> None:
    repository = _Repository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    result = service.complete_creative_brief(
        repository.project_id,
        theme="雨后小院里，孩子和猫咪守着发亮的叶子",
        target_duration_seconds=15,
    )

    prompt = director.prompts[-1]
    constraints = "\n".join(result["constraints"])
    assert result["theme"] == "雨后小院里，孩子和猫咪守着发亮的叶子"
    assert "canon-v3-healing-child-cat-line-texture" in constraints
    assert "细腻柔和的数字插画材质" in prompt
    assert "复制参考图中的叶片、露珠或微距构图" in prompt
    assert "同时混入旧室内或户外水彩参考" in prompt
    assert "二维水彩" not in prompt + constraints
    assert "原创柔和水彩画风" not in prompt + constraints


def test_story_strategy_run_uses_one_audited_creative_call_and_returns_batch_candidates(
) -> None:
    repository = _Repository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    result = service.run_story_strategies(
        repository.project_id,
        StoryStrategyRunRequest(idempotencyKey="strategy-run-001"),
    )

    assert result["status"] == "succeeded"
    assert len(result["candidates"]) == 3
    assert result["candidateCount"] == 3
    assert len(result["candidateIds"]) == 3
    assert result["diagnostics"] == []
    assert director.calls == ["StoryCandidateBatch"]
    assert len(repository.saved) == 3
    assert len(repository.batch_save_calls) == 1
    assert {item["candidate_prompt_id"] for item in repository.saved} == {
        repository.prompt_ids[0]
    }
    assert all(item["scorecard"] is None for item in repository.saved)
    assert all(item["critic_prompt_id"] is None for item in repository.saved)
    assert all(item["body"] and item["summary"] for item in result["candidates"])
    assert all(item["warnings"] == [] for item in result["candidates"])
    event_names = [name for name, _payload in repository.events]
    starts = [index for index, name in enumerate(event_names) if name == "prompt_started"]
    completions = [
        index for index, name in enumerate(event_names) if name == "prompt_completed"
    ]
    assert len(starts) == len(completions) == 1
    assert all(start < completion for start, completion in zip(starts, completions, strict=True))


def test_story_strategy_rewrite_instruction_is_strict_and_part_of_audited_input() -> None:
    repository = _Repository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    request = StoryStrategyRunRequest(
        idempotencyKey="strategy-rewrite",
        rewriteInstruction="把情绪重心放在猫咪先发现雨滴。",
    )
    service.run_story_strategies(repository.project_id, request)

    attempt = next(payload for name, payload in repository.events if name == "attempt")
    assert attempt["request"]["creativeDirection"] == "把情绪重心放在猫咪先发现雨滴。"
    prompt = next(payload for name, payload in repository.events if name == "prompt_started")
    assert "把情绪重心放在猫咪先发现雨滴。" in prompt["draft"].final_prompt
    with pytest.raises(ValidationError, match="extra"):
        StoryStrategyRunRequest(
            idempotencyKey="strategy-extra",
            rewriteInstruction="保留温暖结尾",
            unexpectedField=True,
        )


@pytest.mark.parametrize("candidate_count", [1, 2, 4])
def test_story_strategy_run_accepts_non_preferred_batch_sizes_with_warning(
    candidate_count: int,
) -> None:
    repository = _Repository()
    director = _Director()
    director.creative_payload = {
        "candidates": [
            {
                "title": f"候选 {index}",
                "body": f"第 {index} 个完整故事。",
                "summary": f"摘要 {index}",
                "providerExtra": {"ignored": True},
            }
            for index in range(1, candidate_count + 1)
        ],
        "providerEnvelopeExtra": "ignored",
    }
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    result = service.run_story_strategies(
        repository.project_id,
        StoryStrategyRunRequest(idempotencyKey=f"strategy-run-{candidate_count}"),
    )

    assert result["candidateCount"] == candidate_count
    assert len(result["candidates"]) == candidate_count
    assert [item["code"] for item in result["diagnostics"]] == [
        "story_candidate_count"
    ]
    assert all(item["warnings"] == result["diagnostics"] for item in result["candidates"])
    assert director.calls == ["StoryCandidateBatch"]


def test_story_strategy_run_preserves_plain_text_as_one_editable_candidate() -> None:
    repository = _Repository()
    director = _Director()
    director.creative_payload = "  风起后，小满和灰灰一起把画收回了廊下。  "
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    result = service.run_story_strategies(
        repository.project_id,
        StoryStrategyRunRequest(idempotencyKey="strategy-run-text"),
    )

    assert result["candidateCount"] == 1
    assert result["candidates"][0]["body"] == "风起后，小满和灰灰一起把画收回了廊下。"
    assert {item["code"] for item in result["diagnostics"]} == {
        "story_candidate_unstructured",
        "story_candidate_count",
    }
    completion = next(
        payload for name, payload in repository.events if name == "prompt_completed"
    )
    assert completion["raw_response"].strip().startswith("风起后")
    assert completion["structured_response"]["batch"]["candidates"][0]["body"].startswith(
        "风起后"
    )


def test_story_save_failure_does_not_relabel_a_successful_model_prompt_as_failed() -> None:
    class FailingRepository(_Repository):
        def save_story_candidate_batch(
            self, **values: object
        ) -> tuple[dict[str, object], ...]:
            del values
            raise RuntimeError("persistence failed")

    repository = FailingRepository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    with pytest.raises(RuntimeError, match="persistence failed"):
        service.run_story_strategies(
            repository.project_id,
            StoryStrategyRunRequest(idempotencyKey="strategy-run-save-failure"),
        )

    prompt_completions = [
        payload for name, payload in repository.events if name == "prompt_completed"
    ]
    assert [item["status"] for item in prompt_completions] == ["succeeded"]


def test_story_prompt_audit_failure_marks_generation_attempt_failed() -> None:
    class FailingPromptRepository(_Repository):
        def begin_prompt_run(self, **values: object) -> tuple[uuid.UUID, uuid.UUID]:
            del values
            raise RuntimeError("prompt audit unavailable")

    repository = FailingPromptRepository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    with pytest.raises(RuntimeError, match="prompt audit unavailable"):
        service.run_story_strategies(
            repository.project_id,
            StoryStrategyRunRequest(idempotencyKey="strategy-run-prompt-failure"),
        )

    attempt_completion = next(
        payload for name, payload in repository.events if name == "attempt_finished"
    )
    assert attempt_completion["status"] == "failed"
    assert director.calls == []


def test_prompt_completion_failure_still_finishes_attempt_once() -> None:
    class FailingCompletionRepository(_Repository):
        def complete_prompt_run(self, prompt_id: uuid.UUID, **values: object) -> None:
            self.events.append(("prompt_completion_failed", {"id": prompt_id, **values}))
            raise RuntimeError("prompt audit write failed")

    repository = FailingCompletionRepository()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=_Director(),
        provider_name="fake",
    )

    with pytest.raises(RuntimeError, match="prompt audit write failed"):
        service.run_story_strategies(
            repository.project_id,
            StoryStrategyRunRequest(idempotencyKey="completion-failure"),
        )

    completions = [payload for name, payload in repository.events if name == "attempt_finished"]
    assert len(completions) == 1
    assert completions[0]["status"] == "failed"
    assert "prompt audit write failed" in str(completions[0]["error"])


def test_failed_attempt_replays_succeeded_prompt_without_another_director_call() -> None:
    class RecoveryRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.attempt_id = str(uuid.uuid4())
            self.begin_count = 0
            self.fail_first_materialization = True

        def begin_generation_attempt(self, **values: object) -> tuple[dict[str, object], bool]:
            self.events.append(("attempt", values))
            self.begin_count += 1
            return (
                {"id": self.attempt_id, "status": "pending" if self.begin_count == 1 else "failed"},
                self.begin_count == 1,
            )

        def complete_prompt_run(self, prompt_id: uuid.UUID, **values: object) -> None:
            super().complete_prompt_run(prompt_id, **values)
            if values["status"] == "succeeded":
                structured = values["structured_response"]
                self.recovered_story_batch = {
                    "promptId": prompt_id,
                    "batch": structured["batch"],
                    "diagnostics": structured["diagnostics"],
                }

        def save_story_candidate_batch(
            self, **values: object
        ) -> tuple[dict[str, object], ...]:
            if self.fail_first_materialization:
                self.fail_first_materialization = False
                raise RuntimeError("batch persistence interrupted")
            if self.saved:
                return tuple(self.saved)
            return super().save_story_candidate_batch(**values)

    repository = RecoveryRepository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )
    request = StoryStrategyRunRequest(idempotencyKey="recover-paid-prompt")

    with pytest.raises(RuntimeError, match="batch persistence interrupted"):
        service.run_story_strategies(repository.project_id, request)
    assert director.calls == ["StoryCandidateBatch"]

    recovered = service.run_story_strategies(repository.project_id, request)
    repeated = service.run_story_strategies(repository.project_id, request)

    assert recovered["status"] == "succeeded"
    assert recovered["candidateCount"] == 3
    assert repeated["candidateIds"] == recovered["candidateIds"]
    assert director.calls == ["StoryCandidateBatch"]
    assert len(repository.saved) == 3


def test_existing_succeeded_story_attempt_returns_before_recovery_lookup() -> None:
    class ExistingSucceededRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.lookup_calls = 0
            candidate = {
                "id": str(uuid.uuid4()),
                "title": "雨后亮叶",
                "body": "孩子和猫一起观察雨后发亮的叶子。",
                "summary": "雨后的小发现",
                "warnings": [],
            }
            self.saved.append(candidate)
            self.attempt = {
                "id": str(uuid.uuid4()),
                "status": "succeeded",
                "response": {
                    "candidateIds": [candidate["id"]],
                    "candidateCount": 1,
                    "diagnostics": [],
                },
            }

        def begin_generation_attempt(self, **values: object) -> tuple[dict[str, object], bool]:
            self.events.append(("attempt", values))
            return self.attempt, False

        def get_succeeded_story_candidate_batch(
            self, **values: object
        ) -> dict[str, object] | None:
            del values
            self.lookup_calls += 1
            raise RuntimeError("recovery lookup must not run for succeeded attempt")

    repository = ExistingSucceededRepository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    result = service.run_story_strategies(
        repository.project_id,
        StoryStrategyRunRequest(idempotencyKey="existing-succeeded-attempt"),
    )

    assert result["id"] == repository.attempt["id"]
    assert result["candidateIds"] == repository.attempt["response"]["candidateIds"]
    assert result["candidates"] == repository.saved
    assert repository.lookup_calls == 0
    assert director.calls == []
    assert not any(name == "attempt_finished" for name, _payload in repository.events)


def test_existing_succeeded_story_attempt_candidate_read_failure_never_reverses_terminal_state(
) -> None:
    class UnavailableCandidateRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.candidate_id = uuid.uuid4()
            self.attempt = {
                "id": str(uuid.uuid4()),
                "status": "succeeded",
                "response": {
                    "candidateIds": [str(self.candidate_id)],
                    "candidateCount": 1,
                    "diagnostics": [],
                },
            }

        def begin_generation_attempt(self, **values: object) -> tuple[dict[str, object], bool]:
            self.events.append(("attempt", values))
            return self.attempt, False

        def get_story_candidates(
            self,
            *,
            project_id: uuid.UUID,
            candidate_ids: tuple[uuid.UUID, ...],
        ) -> tuple[dict[str, object], ...]:
            assert project_id == self.project_id
            assert candidate_ids == (self.candidate_id,)
            raise RuntimeError("candidate read temporarily unavailable")

    repository = UnavailableCandidateRepository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        service.run_story_strategies(
            repository.project_id,
            StoryStrategyRunRequest(idempotencyKey="succeeded-candidate-read-failure"),
        )

    assert director.calls == []
    assert not any(name == "attempt_finished" for name, _payload in repository.events)


@pytest.mark.parametrize(
    ("failure_stage", "failure"),
    (
        (
            "lookup",
            WorkflowConflictError("成功故事 Prompt 的恢复结果无效"),
        ),
        (
            "materialization",
            WorkflowConflictError("故事候选批次已部分物化"),
        ),
        ("materialization", RuntimeError("recovery database unavailable")),
    ),
)
def test_story_prompt_recovery_failure_finishes_attempt_without_director_call(
    failure_stage: str,
    failure: Exception,
) -> None:
    class FailedRecoveryRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.attempt_id = str(uuid.uuid4())
            self.recovered_story_batch = {
                "promptId": uuid.uuid4(),
                "batch": {
                    "candidates": [
                        {
                            "title": "雨前收画",
                            "body": "孩子和猫一起把画收回廊下。",
                        }
                    ]
                },
                "diagnostics": [],
            }

        def begin_generation_attempt(self, **values: object) -> tuple[dict[str, object], bool]:
            self.events.append(("attempt", values))
            return ({"id": self.attempt_id, "status": "pending"}, True)

        def get_succeeded_story_candidate_batch(
            self, **values: object
        ) -> dict[str, object] | None:
            if failure_stage == "lookup":
                raise failure
            return super().get_succeeded_story_candidate_batch(**values)

        def save_story_candidate_batch(
            self, **values: object
        ) -> tuple[dict[str, object], ...]:
            raise failure

    repository = FailedRecoveryRepository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    with pytest.raises(type(failure), match=str(failure)):
        service.run_story_strategies(
            repository.project_id,
            StoryStrategyRunRequest(idempotencyKey=f"failed-recovery-{failure_stage}"),
        )

    completions = [payload for name, payload in repository.events if name == "attempt_finished"]
    assert completions == [
        {
            "id": repository.attempt_id,
            "status": "failed",
            "error": {
                "code": "story_candidate_recovery_failed",
                "message": str(failure),
                "exceptionType": type(failure).__name__,
            },
        }
    ]
    assert director.calls == []


def test_creative_story_without_legacy_scenes_can_naturally_generate_two_scenes() -> None:
    class EmptySceneRepository(_Repository):
        def get_storyboard_context(
            self,
            project_id: uuid.UUID,
            *,
            source_story_revision_id: uuid.UUID | None = None,
        ) -> dict[str, Any]:
            context = super().get_storyboard_context(
                project_id,
                source_story_revision_id=source_story_revision_id,
            )
            context["story"]["scenes"] = []
            context["story"]["body"] = context["story"]["synopsis"]
            return context

    class OneSceneDirector(_Director):
        def generate_structured(self, **values: Any) -> DirectorResult:
            if values["output_name"] != "CanvasStoryboardPlanOutput":
                return super().generate_structured(**values)
            self.calls.append(values["output_name"])
            self.prompts.append(values["prompt"])
            return DirectorResult(
                payload={
                    "beats": [
                            {
                                "sceneOrder": 1,
                                "sceneLabel": "院中发现",
                                "title": "发现雨滴",
                                "action": "孩子和猫一起收回画纸。",
                            "camera": "中景稳定推进。",
                            "dialogue": "",
                            "durationWeight": 1,
                            },
                            {
                                "sceneOrder": 2,
                                "sceneLabel": "廊下收尾",
                                "title": "安放画纸",
                                "action": "他们在廊下放好画纸。",
                                "camera": "近景停留。",
                                "dialogue": "",
                                "durationWeight": 1,
                            },
                    ]
                },
                response_id="storyboard-one-scene",
                model=self.model,
                request_hash="storyboard-one-scene-hash",
            )

    repository = EmptySceneRepository()
    repository.brief = repository.brief.model_copy(
        update={"target_duration_seconds": 30}
    )
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=OneSceneDirector(),
        provider_name="fake",
    )

    result = service.create_storyboard(repository.project_id)

    assert len(result["beats"]) == 2
    assert [beat["durationSeconds"] for beat in result["beats"]] == [15, 15]


def test_storyboard_director_warns_when_a_legacy_scene_is_not_used() -> None:
    class SingleSceneDirector(_Director):
        def generate_structured(self, **values: Any) -> DirectorResult:
            if values["output_name"] != "CanvasStoryboardPlanOutput":
                return super().generate_structured(**values)
            return DirectorResult(
                payload={
                    "shots": [
                        {
                            "order": 1,
                            "sceneOrder": 1,
                            "title": "院中收好画纸",
                            "direction": "孩子把画纸收好，猫蹲在旁边看着。",
                            "durationSeconds": 15,
                        }
                    ]
                },
                response_id="single-scene-storyboard",
                model=self.model,
                request_hash="single-scene-storyboard-hash",
            )

    repository = _Repository()
    repository.brief = repository.brief.model_copy(
        update={"target_duration_seconds": 15}
    )
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=SingleSceneDirector(),
        provider_name="fake",
    )

    result = service.create_storyboard(repository.project_id)

    assert result["status"] == "ready"
    assert [item["code"] for item in result["diagnostics"]] == [
        "storyboard_scene_uncovered"
    ]
    assert result["diagnostics"][0]["severity"] == "warning"


@pytest.mark.parametrize(
    "invalid_payload",
    [
        "   ",
        {
            "candidates": [
                {"title": f"候选 {index}", "body": f"故事 {index}"}
                for index in range(6)
            ]
        },
    ],
)
def test_invalid_returned_creative_payload_remains_fully_auditable(
    invalid_payload: dict[str, Any] | str,
) -> None:
    repository = _Repository()
    director = _Director()
    director.creative_payload = invalid_payload
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    with pytest.raises(ValueError):
        service.run_story_strategies(
            repository.project_id,
            StoryStrategyRunRequest(idempotencyKey="invalid-returned-payload"),
        )

    completion = next(
        payload for name, payload in repository.events if name == "prompt_completed"
    )
    assert completion["status"] == "failed"
    assert completion["raw_response"] == invalid_payload
    assert completion["provider_response_id"] == "response-1"
    assert completion["output_hash"]
    assert completion["error"]["message"]


def test_provider_failure_before_a_response_does_not_invent_raw_audit_evidence() -> None:
    class FailingDirector(_Director):
        def generate_creative_text(
            self,
            *,
            prompt: str,
            output_name: str,
        ) -> CreativeDirectorResult:
            del prompt, output_name
            raise RuntimeError("provider returned no result")

    repository = _Repository()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=FailingDirector(),
        provider_name="fake",
    )

    with pytest.raises(RuntimeError, match="provider returned no result"):
        service.run_story_strategies(
            repository.project_id,
            StoryStrategyRunRequest(idempotencyKey="provider-no-result"),
        )

    completion = next(
        payload for name, payload in repository.events if name == "prompt_completed"
    )
    assert completion["status"] == "failed"
    assert "raw_response" not in completion
    assert "provider_response_id" not in completion
    assert "output_hash" not in completion


def test_story_strategy_service_uses_the_real_fake_creative_contract() -> None:
    repository = _Repository()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=FakeArkGateway(),
        provider_name="fake",
    )

    result = service.run_story_strategies(
        repository.project_id,
        StoryStrategyRunRequest(idempotencyKey="real-fake-contract"),
    )

    assert result["candidateCount"] == 1
    assert result["candidates"][0]["title"] == "雨前收画"


def test_storyboard_director_creates_audited_provider_bounded_beats() -> None:
    repository = _Repository()
    director = _Director()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    result = service.create_storyboard(repository.project_id)

    assert result["targetDurationSeconds"] == 60
    assert len(result["beats"]) == 5
    assert all(8 <= beat["durationSeconds"] <= 15 for beat in result["beats"])
    assert director.calls == ["CanvasStoryboardPlanOutput"]
    assert all(beat["promptId"] for beat in result["beats"])
    assert [name for name, _payload in repository.events].count("prompt_started") == 1


def test_storyboard_director_preserves_unstructured_text_without_saving_beats() -> None:
    raw_text = "镜头一：孩子发现雨云。\n镜头二：孩子和猫把画纸搬回屋内。"

    class RawStoryboardDirector(_Director):
        def generate_storyboard_text(self, **values: Any) -> CreativeDirectorResult:
            self.calls.append(values["output_name"])
            self.prompts.append(values["prompt"])
            return CreativeDirectorResult(
                payload=raw_text,
                response_id="raw-storyboard-response",
                model=self.model,
                request_hash="raw-storyboard-hash",
            )

    repository = _Repository()
    director = RawStoryboardDirector()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    result = service.create_storyboard(repository.project_id)

    assert result["status"] == "needs_structuring"
    assert result["rawText"] == raw_text
    assert result["diagnostics"] == [
        {
            "code": "storyboard_needs_structuring",
            "severity": "blocker",
            "message": "分镜原文需要整理为至少一个包含标题、镜头描述和有效时长的镜头",
            "targetId": None,
        }
    ]
    assert repository.storyboard is None
    completion = next(
        payload for name, payload in repository.events if name == "prompt_completed"
    )
    assert completion["status"] == "succeeded"
    assert completion["raw_response"] == raw_text
    assert completion["provider_response_id"] == "raw-storyboard-response"
    attempt = next(
        payload for name, payload in repository.events if name == "attempt_finished"
    )
    assert attempt["status"] == "succeeded"
    assert attempt["response"]["status"] == "needs_structuring"
    assert attempt["response"]["promptId"] == result["promptId"]


def test_unstructured_storyboard_idempotency_does_not_repeat_provider_call() -> None:
    class IdempotentRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.attempt_id = uuid.uuid4()
            self.persisted_attempt: dict[str, object] | None = None

        def begin_generation_attempt(self, **values: object) -> tuple[dict[str, object], bool]:
            self.events.append(("attempt", values))
            if self.persisted_attempt is not None:
                return self.persisted_attempt, False
            return {"id": str(self.attempt_id), "status": "pending"}, True

        def finish_generation_attempt(self, attempt_id: str, **values: object) -> None:
            assert attempt_id == str(self.attempt_id)
            self.persisted_attempt = {"id": attempt_id, **values}
            super().finish_generation_attempt(attempt_id, **values)

    class RawStoryboardDirector(_Director):
        def generate_storyboard_text(self, **values: Any) -> CreativeDirectorResult:
            self.calls.append(values["output_name"])
            return CreativeDirectorResult(
                payload="镜头一：孩子和猫把画纸收回屋内。",
                response_id="raw-storyboard-response",
                model=self.model,
                request_hash="raw-storyboard-hash",
            )

    repository = IdempotentRepository()
    director = RawStoryboardDirector()
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=director,
        provider_name="fake",
    )

    first = service.create_storyboard(
        repository.project_id,
        idempotency_key="same-storyboard-request",
    )
    second = service.create_storyboard(
        repository.project_id,
        idempotency_key="same-storyboard-request",
    )

    assert first["status"] == "needs_structuring"
    assert second["status"] == "succeeded"
    assert second["response"]["status"] == "needs_structuring"
    assert director.calls == ["CanvasStoryboardPlanOutput"]


def test_storyboard_director_accepts_minimal_shots_and_warns_about_dialogue() -> None:
    class MinimalStoryboardDirector(_Director):
        def generate_storyboard_text(self, **values: Any) -> CreativeDirectorResult:
            self.calls.append(values["output_name"])
            self.prompts.append(values["prompt"])
            return CreativeDirectorResult(
                payload={
                    "shots": [
                        {
                            "order": 1,
                            "title": "发现雨云",
                            "direction": "孩子发现雨云，猫咪停在画纸旁。",
                            "durationSeconds": 10,
                            "dialogue": "要下雨了。",
                            "extraModelField": "ignored",
                        }
                    ]
                },
                response_id="minimal-storyboard-response",
                model=self.model,
                request_hash="minimal-storyboard-hash",
            )

    repository = _Repository()
    repository.brief = repository.brief.model_copy(
        update={"target_duration_seconds": 10}
    )
    repository.get_storyboard_context = lambda *args, **kwargs: {  # type: ignore[method-assign]
        **_Repository.get_storyboard_context(repository, repository.project_id),
        "story": {
            **_Repository.get_storyboard_context(repository, repository.project_id)["story"],
            "scenes": [],
        },
    }
    service = AigcCanvasService(
        repository=repository,  # type: ignore[arg-type]
        director=MinimalStoryboardDirector(),
        provider_name="fake",
    )

    result = service.create_storyboard(repository.project_id, healing_recipe=True)

    assert result["beats"][0]["durationSeconds"] == 10
    assert result["diagnostics"][0]["code"] == "storyboard_dialogue_present"
    assert result["diagnostics"][0]["severity"] == "warning"
