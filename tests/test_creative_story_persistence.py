from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Iterator

import pytest

import cat_video_generator.infrastructure.db.aigc_canvas_repository as repository_module
from cat_video_generator.domain.aigc_canvas import (
    CreativeStoryCandidate,
    StoryboardPlanOutput,
    StoryCandidateOutput,
    StoryScorecard,
    StoryStrategy,
)
from cat_video_generator.infrastructure.db.aigc_canvas_repository import (
    SqlAlchemyAigcCanvasRepository,
    _story_json,
)
from cat_video_generator.infrastructure.db.models import (
    Scene,
    ShotBeat,
    StoryRevisionRecord,
    StoryScore,
)
from cat_video_generator.infrastructure.db.repositories import WorkflowConflictError


def _story(*, scenes: list[dict[str, object]] | None = None) -> StoryRevisionRecord:
    return StoryRevisionRecord(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        brief_id=uuid.uuid4(),
        source_event_candidate_id=None,
        revision=1,
        strategy="combined",
        status="candidate",
        title="雨前收画",
        logline="孩子和猫共同守住画作。",
        synopsis="风起时，小满和灰灰一起把晾晒的画收回廊下。",
        subject_ids_json=[str(uuid.uuid4()), str(uuid.uuid4())],
        scene_plan_json=[] if scenes is None else scenes,
        episode_rules_json={},
        candidate_prompt_id=uuid.uuid4(),
        critic_prompt_id=None,
    )


def test_creative_story_projection_restores_body_summary_and_prompt_diagnostics() -> None:
    row = _story()
    prompt = SimpleNamespace(
        structured_response_json={
            "batch": {
                "candidates": [
                    {"title": row.title, "body": row.synopsis, "summary": row.logline}
                ]
            },
            "diagnostics": [
                {
                    "code": "story_candidate_count",
                    "severity": "warning",
                    "message": "LLM 返回了 1 个故事候选，预期数量为 3。",
                }
            ],
        }
    )

    document = _story_json(row, None, prompt)

    assert document["body"] == row.synopsis
    assert document["summary"] == row.logline
    assert document["source"] == "ai"
    assert document["contractKind"] == "creative_text"
    assert document["warnings"] == prompt.structured_response_json["diagnostics"]
    assert document["legacyDetails"] is None
    assert document["scenes"] == []
    assert document["scorecard"] is None


def test_story_without_creative_prompt_evidence_has_unknown_provenance() -> None:
    row = _story()
    row.candidate_prompt_id = None

    document = _story_json(row, None)

    assert document["source"] == "unknown"
    assert document["contractKind"] == "creative_text"


def test_repository_reads_materialized_story_candidates_by_ids_in_attempt_order() -> None:
    project_id = uuid.uuid4()
    first = _story()
    second = _story()
    for row in (first, second):
        row.production_run_id = project_id
        row.candidate_prompt_id = None

    class CandidateReadSession:
        @staticmethod
        def scalars(statement: object) -> tuple[object, ...]:
            entity = statement.column_descriptions[0].get("entity")  # type: ignore[attr-defined]
            if entity is StoryRevisionRecord:
                return second, first
            if entity is StoryScore:
                return ()
            raise AssertionError(f"unexpected candidate read query: {entity}")

    repository = SqlAlchemyAigcCanvasRepository(
        _ReadSessions(CandidateReadSession())  # type: ignore[arg-type]
    )

    documents = repository.get_story_candidates(
        project_id=project_id,
        candidate_ids=(first.id, second.id),
    )

    assert [item["id"] for item in documents] == [str(first.id), str(second.id)]
    assert [item["body"] for item in documents] == [first.synopsis, second.synopsis]


def test_repository_rejects_missing_story_candidate_from_succeeded_attempt() -> None:
    project_id = uuid.uuid4()
    existing = _story()
    existing.production_run_id = project_id
    existing.candidate_prompt_id = None

    class CandidateReadSession:
        @staticmethod
        def scalars(statement: object) -> tuple[object, ...]:
            entity = statement.column_descriptions[0].get("entity")  # type: ignore[attr-defined]
            if entity is StoryRevisionRecord:
                return (existing,)
            if entity is StoryScore:
                return ()
            raise AssertionError(f"unexpected candidate read query: {entity}")

    repository = SqlAlchemyAigcCanvasRepository(
        _ReadSessions(CandidateReadSession())  # type: ignore[arg-type]
    )

    with pytest.raises(WorkflowConflictError, match="候选版本"):
        repository.get_story_candidates(
            project_id=project_id,
            candidate_ids=(existing.id, uuid.uuid4()),
        )


def test_legacy_import_without_prompt_evidence_has_manual_provenance() -> None:
    row = _story()
    row.candidate_prompt_id = None
    row.strategy = StoryStrategy.LEGACY_IMPORT.value

    document = _story_json(row, None)

    assert document["source"] == "manual"


class _CompletionSession:
    def __init__(self, prompt: SimpleNamespace, step: SimpleNamespace) -> None:
        self._rows = iter((prompt, step))

    def scalar(self, _statement: object) -> object:
        return next(self._rows)


class _Sessions:
    def __init__(self, session: _CompletionSession) -> None:
        self._session = session

    @contextmanager
    def begin(self) -> Iterator[_CompletionSession]:
        yield self._session


class _ReadSessions:
    def __init__(self, session: _CompletionSession) -> None:
        self._session = session

    @contextmanager
    def __call__(self) -> Iterator[_CompletionSession]:
        yield self._session


class _SaveSession:
    def __init__(self, *scalar_results: object) -> None:
        self._scalar_results = iter(scalar_results)
        self._scalars_results: Iterator[object] = iter(((),))
        self.added: list[object] = []

    def scalar(self, _statement: object) -> object:
        return next(self._scalar_results)

    def scalars(self, _statement: object) -> object:
        return next(self._scalars_results)

    def add(self, row: object) -> None:
        self.added.append(row)

    @staticmethod
    def flush() -> None:
        return None


class _SaveSessions:
    def __init__(self, session: _SaveSession) -> None:
        self._session = session

    @contextmanager
    def begin(self) -> Iterator[_SaveSession]:
        yield self._session


class _ApprovalSession(_SaveSession):
    def __init__(
        self,
        scalar_results: tuple[object, ...],
        scalar_lists: tuple[object, ...],
    ) -> None:
        super().__init__(*scalar_results)
        self._scalar_lists = iter(scalar_lists)

    def scalars(self, _statement: object) -> object:
        return next(self._scalar_lists)

    @staticmethod
    def execute(_statement: object) -> None:
        return None


def test_complete_prompt_run_preserves_raw_text_in_honest_json_envelope() -> None:
    prompt = SimpleNamespace(
        step_id=uuid.uuid4(),
        status="pending",
        completed_at=None,
        raw_response_json=None,
        structured_response_json=None,
        output_hash=None,
        error_json=None,
    )
    step = SimpleNamespace(status="pending", completed_at=None, error_json=None)
    repository = SqlAlchemyAigcCanvasRepository(
        _Sessions(_CompletionSession(prompt, step))  # type: ignore[arg-type]
    )

    repository.complete_prompt_run(
        uuid.uuid4(),
        status="succeeded",
        raw_response="完整的纯文本故事",
        structured_response={"batch": {"candidates": []}, "diagnostics": []},
        provider_response_id="response-text-1",
    )

    assert prompt.raw_response_json == {
        "text": "完整的纯文本故事",
        "_providerResponseId": "response-text-1",
    }
    assert prompt.structured_response_json == {
        "batch": {"candidates": []},
        "diagnostics": [],
    }


@pytest.mark.parametrize("next_status", ("failed", "submission_unknown", "pending"))
def test_finish_generation_attempt_rejects_succeeded_terminal_state_reversal(
    next_status: str,
) -> None:
    original_response = {"candidateIds": [str(uuid.uuid4())], "candidateCount": 1}
    row = SimpleNamespace(
        status="succeeded",
        response_json=original_response,
        error_json=None,
    )
    repository = SqlAlchemyAigcCanvasRepository(
        _SaveSessions(_SaveSession())  # type: ignore[arg-type]
    )
    repository._required = lambda *_args, **_kwargs: row  # type: ignore[method-assign]

    with pytest.raises(WorkflowConflictError, match="成功终态"):
        repository.finish_generation_attempt(
            str(uuid.uuid4()),
            status=next_status,
            error={"code": "late_failure"},
        )

    assert row.status == "succeeded"
    assert row.response_json is original_response
    assert row.error_json is None


def test_finish_generation_attempt_succeeded_retry_is_a_noop() -> None:
    original_response = {"candidateIds": [str(uuid.uuid4())], "candidateCount": 1}
    row = SimpleNamespace(
        status="succeeded",
        response_json=original_response,
        error_json=None,
    )
    repository = SqlAlchemyAigcCanvasRepository(
        _SaveSessions(_SaveSession())  # type: ignore[arg-type]
    )
    repository._required = lambda *_args, **_kwargs: row  # type: ignore[method-assign]

    repository.finish_generation_attempt(
        str(uuid.uuid4()),
        status="succeeded",
        response=None,
        error={"code": "late_error"},
    )

    assert row.status == "succeeded"
    assert row.response_json is original_response
    assert row.error_json is None


def test_succeeded_story_prompt_recovery_validates_batch_and_diagnostics() -> None:
    prompt = SimpleNamespace(
        id=uuid.uuid4(),
        structured_response_json={
            "batch": {
                "candidates": [
                    {"title": "雨前收画", "body": "孩子和猫一起把画收回廊下。"}
                ]
            },
            "diagnostics": [
                {
                    "code": "story_candidate_count",
                    "severity": "warning",
                    "message": "模型仅返回一个候选。",
                }
            ],
        },
    )
    repository = SqlAlchemyAigcCanvasRepository(
        _ReadSessions(_CompletionSession(prompt, SimpleNamespace()))  # type: ignore[arg-type]
    )

    recovered = repository.get_succeeded_story_candidate_batch(
        project_id=uuid.uuid4(),
        business_object_type="project_story_strategy",
        business_object_id=uuid.uuid4(),
        call_purpose="story_candidate_batch",
        input_hash="input-hash",
    )

    assert recovered is not None
    assert recovered["promptId"] == prompt.id
    assert recovered["batch"].candidates[0].body == "孩子和猫一起把画收回廊下。"  # type: ignore[union-attr]
    assert recovered["diagnostics"][0].code == "story_candidate_count"  # type: ignore[index,union-attr]


def test_succeeded_story_prompt_recovery_rejects_invalid_audited_batch() -> None:
    prompt = SimpleNamespace(
        id=uuid.uuid4(),
        structured_response_json={
            "batch": {
                "candidates": [
                    {"title": f"候选 {index}", "body": "正文"}
                    for index in range(6)
                ]
            },
            "diagnostics": [],
        },
    )
    repository = SqlAlchemyAigcCanvasRepository(
        _ReadSessions(_CompletionSession(prompt, SimpleNamespace()))  # type: ignore[arg-type]
    )

    with pytest.raises(WorkflowConflictError, match="恢复结果无效"):
        repository.get_succeeded_story_candidate_batch(
            project_id=uuid.uuid4(),
            business_object_type="project_story_strategy",
            business_object_id=uuid.uuid4(),
            call_purpose="story_candidate_batch",
            input_hash="input-hash",
        )


def test_save_creative_story_maps_body_summary_and_does_not_create_score() -> None:
    prompt_id = uuid.uuid4()
    diagnostic = {
        "code": "story_candidate_count",
        "severity": "warning",
        "message": "LLM 返回了 1 个故事候选，预期数量为 3。",
    }
    prompt = SimpleNamespace(
        structured_response_json={"batch": {"candidates": []}, "diagnostics": [diagnostic]}
    )
    session = _SaveSession(0, prompt)
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    repository._require_project = lambda *_args, **_kwargs: SimpleNamespace()  # type: ignore[method-assign]

    document = repository.save_story_candidate(
        project_id=uuid.uuid4(),
        brief_id=uuid.uuid4(),
        strategy=StoryStrategy.COMBINED,
        candidate=CreativeStoryCandidate(
            title="雨前收画",
            body="风起时，小满和灰灰一起把晾晒的画收回廊下。",
            summary="孩子和猫共同守住画作。",
        ),
        subject_ids=(uuid.uuid4(), uuid.uuid4()),
        candidate_prompt_id=prompt_id,
    )

    story = next(row for row in session.added if isinstance(row, StoryRevisionRecord))
    assert story.logline == "孩子和猫共同守住画作。"
    assert story.synopsis == "风起时，小满和灰灰一起把晾晒的画收回廊下。"
    assert story.scene_plan_json == []
    assert story.critic_prompt_id is None
    assert not any(isinstance(row, StoryScore) for row in session.added)
    assert document["body"] == story.synopsis
    assert document["summary"] == story.logline
    assert document["warnings"] == [diagnostic]


def test_save_story_revision_edit_creates_manual_child_without_overwriting_source() -> None:
    source = _story(
        scenes=[
            {
                "sceneKey": "legacy-scene",
                "title": "旧场景",
                "synopsis": "旧结构化场景",
            }
        ]
    )
    source.status = "approved"
    source.revision = 7
    source.source_event_candidate_id = uuid.uuid4()
    source.episode_rules_json = {"environment": "outdoor"}
    original_title = source.title
    original_body = source.synopsis
    edited_body = "雨停以后，孩子和猫一起整理长廊。" * 1_500
    session = _SaveSession(None, source.revision)
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    repository._required = lambda *_args, **_kwargs: source  # type: ignore[method-assign]

    document = repository.save_story_revision_edit(
        revision_id=source.id,
        expected_revision=source.revision,
        idempotency_key="manual-story-edit-0001",
        title="雨后长廊",
        body=edited_body,
        summary="孩子和猫在雨后整理长廊。",
    )

    edited = next(row for row in session.added if isinstance(row, StoryRevisionRecord))
    assert edited.id != source.id
    assert edited.parent_revision_id == source.id
    assert edited.production_run_id == source.production_run_id
    assert edited.brief_id == source.brief_id
    assert edited.source_event_candidate_id == source.source_event_candidate_id
    assert edited.strategy == source.strategy
    assert edited.subject_ids_json == source.subject_ids_json
    assert edited.episode_rules_json == source.episode_rules_json
    assert edited.revision == 8
    assert edited.status == "candidate"
    assert edited.title == "雨后长廊"
    assert edited.synopsis == edited_body
    assert edited.logline == "孩子和猫在雨后整理长廊。"
    assert edited.scene_plan_json == []
    assert edited.candidate_prompt_id is None
    assert edited.critic_prompt_id is None
    assert document["source"] == "manual"
    assert document["body"] == edited_body
    assert document["legacyDetails"] is None
    assert not any(isinstance(row, StoryScore) for row in session.added)
    assert source.status == "approved"
    assert source.title == original_title
    assert source.synopsis == original_body


def test_save_story_revision_edit_rejects_stale_expected_revision() -> None:
    source = _story()
    source.revision = 4
    session = _SaveSession(None, 5)
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    repository._required = lambda *_args, **_kwargs: source  # type: ignore[method-assign]

    with pytest.raises(WorkflowConflictError, match="版本冲突"):
        repository.save_story_revision_edit(
            revision_id=source.id,
            expected_revision=3,
            idempotency_key="manual-story-edit-0002",
            title="并发修改",
            body="另一个编辑已经保存后，这次旧版本提交必须被拒绝。",
            summary=None,
        )

    assert session.added == []


def test_save_creative_story_batch_assigns_contiguous_revisions_in_one_transaction() -> None:
    prompt = SimpleNamespace(structured_response_json={"diagnostics": []})
    session = _SaveSession(7, prompt)
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    repository._require_project = lambda *_args, **_kwargs: SimpleNamespace()  # type: ignore[method-assign]

    documents = repository.save_story_candidate_batch(
        project_id=uuid.uuid4(),
        brief_id=uuid.uuid4(),
        strategy=StoryStrategy.COMBINED,
        candidates=(
            CreativeStoryCandidate(title="候选一", body="故事一"),
            CreativeStoryCandidate(title="候选二", body="故事二"),
            CreativeStoryCandidate(title="候选三", body="故事三"),
        ),
        subject_ids=(uuid.uuid4(), uuid.uuid4()),
        candidate_prompt_id=uuid.uuid4(),
    )

    stories = [row for row in session.added if isinstance(row, StoryRevisionRecord)]
    assert [row.revision for row in stories] == [8, 9, 10]
    assert [document["revision"] for document in documents] == [8, 9, 10]
    assert not any(isinstance(row, StoryScore) for row in session.added)


def test_save_creative_story_batch_replays_complete_matching_materialization() -> None:
    project_id = uuid.uuid4()
    brief_id = uuid.uuid4()
    prompt_id = uuid.uuid4()
    subject_ids = (uuid.uuid4(), uuid.uuid4())
    prompt = SimpleNamespace(structured_response_json={"diagnostics": []})
    candidates = (
        CreativeStoryCandidate(title="候选一", body="故事一", summary="摘要一"),
        CreativeStoryCandidate(title="候选二", body="故事二"),
    )
    existing = []
    for revision, candidate in enumerate(candidates, 4):
        row = _story()
        row.production_run_id = project_id
        row.brief_id = brief_id
        row.revision = revision
        row.strategy = StoryStrategy.COMBINED.value
        row.title = candidate.title
        row.logline = candidate.summary or candidate.title
        row.synopsis = candidate.body
        row.subject_ids_json = [str(subject_id) for subject_id in subject_ids]
        row.candidate_prompt_id = prompt_id
        existing.append(row)
    session = _SaveSession(prompt)
    session._scalars_results = iter((tuple(existing),))
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    repository._require_project = lambda *_args, **_kwargs: SimpleNamespace()  # type: ignore[method-assign]

    documents = repository.save_story_candidate_batch(
        project_id=project_id,
        brief_id=brief_id,
        strategy=StoryStrategy.COMBINED,
        candidates=candidates,
        subject_ids=subject_ids,
        candidate_prompt_id=prompt_id,
    )

    assert [document["id"] for document in documents] == [str(row.id) for row in existing]
    assert session.added == []


@pytest.mark.parametrize(
    "mismatch",
    (
        "brief",
        "strategy",
        "subjects",
        "source_event",
        "parent_revision",
        "status",
        "scene_plan",
        "scene_plan_null",
        "episode_rules",
        "critic_prompt",
        "approved_at",
    ),
)
def test_save_creative_story_batch_rejects_immutable_materialization_mismatch(
    mismatch: str,
) -> None:
    project_id = uuid.uuid4()
    brief_id = uuid.uuid4()
    prompt_id = uuid.uuid4()
    subject_ids = (uuid.uuid4(), uuid.uuid4())
    candidate = CreativeStoryCandidate(title="候选一", body="故事一", summary="摘要一")
    existing = _story()
    existing.production_run_id = project_id
    existing.brief_id = brief_id
    existing.strategy = StoryStrategy.COMBINED.value
    existing.title = candidate.title
    existing.logline = candidate.summary or candidate.title
    existing.synopsis = candidate.body
    existing.subject_ids_json = [str(subject_id) for subject_id in subject_ids]
    existing.candidate_prompt_id = prompt_id
    if mismatch == "brief":
        existing.brief_id = uuid.uuid4()
    elif mismatch == "strategy":
        existing.strategy = StoryStrategy.RELATIONSHIP.value
    elif mismatch == "subjects":
        existing.subject_ids_json = list(reversed(existing.subject_ids_json))
    elif mismatch == "source_event":
        existing.source_event_candidate_id = uuid.uuid4()
    elif mismatch == "parent_revision":
        existing.parent_revision_id = uuid.uuid4()
    elif mismatch == "status":
        existing.status = "approved"
    elif mismatch == "scene_plan":
        existing.scene_plan_json = [{"sceneKey": "legacy"}]
    elif mismatch == "scene_plan_null":
        existing.scene_plan_json = None  # type: ignore[assignment]
    elif mismatch == "episode_rules":
        existing.episode_rules_json = {"legacy": True}
    elif mismatch == "critic_prompt":
        existing.critic_prompt_id = uuid.uuid4()
    elif mismatch == "approved_at":
        existing.approved_at = repository_module.datetime.now(repository_module.UTC)
    session = _SaveSession()
    session._scalars_results = iter(((existing,),))
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    repository._require_project = lambda *_args, **_kwargs: SimpleNamespace()  # type: ignore[method-assign]

    with pytest.raises(WorkflowConflictError, match="不一致"):
        repository.save_story_candidate_batch(
            project_id=project_id,
            brief_id=brief_id,
            strategy=StoryStrategy.COMBINED,
            candidates=(candidate,),
            subject_ids=subject_ids,
            candidate_prompt_id=prompt_id,
        )


def test_save_creative_story_batch_rejects_partial_prompt_materialization() -> None:
    project_id = uuid.uuid4()
    prompt_id = uuid.uuid4()
    existing = _story()
    existing.production_run_id = project_id
    existing.title = "候选一"
    existing.logline = "候选一"
    existing.synopsis = "故事一"
    existing.candidate_prompt_id = prompt_id
    session = _SaveSession()
    session._scalars_results = iter(((existing,),))
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    repository._require_project = lambda *_args, **_kwargs: SimpleNamespace()  # type: ignore[method-assign]

    with pytest.raises(WorkflowConflictError, match="部分物化"):
        repository.save_story_candidate_batch(
            project_id=project_id,
            brief_id=uuid.uuid4(),
            strategy=StoryStrategy.COMBINED,
            candidates=(
                CreativeStoryCandidate(title="候选一", body="故事一"),
                CreativeStoryCandidate(title="候选二", body="故事二"),
            ),
            subject_ids=(uuid.uuid4(),),
            candidate_prompt_id=prompt_id,
        )

    assert session.added == []


def test_creative_story_batch_rolls_back_all_rows_when_one_insert_fails() -> None:
    class FailingBatchSession(_SaveSession):
        def __init__(self) -> None:
            super().__init__(0)
            self.story_add_count = 0

        def add(self, row: object) -> None:
            if isinstance(row, StoryRevisionRecord):
                self.story_add_count += 1
                if self.story_add_count == 2:
                    raise RuntimeError("second candidate insert failed")
            super().add(row)

    class AtomicSessions:
        def __init__(self) -> None:
            self.session = FailingBatchSession()
            self.committed: list[object] = []

        @contextmanager
        def begin(self) -> Iterator[FailingBatchSession]:
            try:
                yield self.session
            except Exception:
                self.session.added.clear()
                raise
            else:
                self.committed.extend(self.session.added)

    sessions = AtomicSessions()
    repository = SqlAlchemyAigcCanvasRepository(sessions)  # type: ignore[arg-type]
    repository._require_project = lambda *_args, **_kwargs: SimpleNamespace()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="second candidate insert failed"):
        repository.save_story_candidate_batch(
            project_id=uuid.uuid4(),
            brief_id=uuid.uuid4(),
            strategy=StoryStrategy.COMBINED,
            candidates=(
                CreativeStoryCandidate(title="候选一", body="故事一"),
                CreativeStoryCandidate(title="候选二", body="故事二"),
            ),
            subject_ids=(uuid.uuid4(),),
            candidate_prompt_id=uuid.uuid4(),
        )

    assert sessions.committed == []
    assert sessions.session.added == []


def test_save_legacy_story_candidate_still_persists_scene_plan_and_scorecard() -> None:
    prompt = SimpleNamespace(structured_response_json={})
    session = _SaveSession(0, prompt)
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    repository._require_project = lambda *_args, **_kwargs: SimpleNamespace()  # type: ignore[method-assign]
    candidate = StoryCandidateOutput.model_validate(
        {
            "title": "旧版候选",
            "logline": "孩子和猫一起收画。",
            "synopsis": "风起后，他们合作保住画作。",
            "scenes": [
                {
                    "sceneKey": "courtyard",
                    "title": "小院",
                    "purpose": "完成收画",
                    "synopsis": "两者合作收画。",
                    "durationWeight": 1,
                    "continuity": {
                        "location": "住宅小院",
                        "environment": "outdoor",
                        "timeWeather": "午后，阵雨将至",
                        "decorations": ["晾画绳"],
                        "props": ["画作"],
                        "transitionReason": "",
                    },
                }
            ],
        }
    )
    scorecard = StoryScorecard(
        openingHook=8,
        causalCompleteness=8,
        subjectNecessity=9,
        emotionalArc=8,
        visualizability=9,
        durationFit=8,
        continuityRisk=7,
        safety=10,
        rationale="主体共同推动行动。",
    )

    document = repository.save_story_candidate(
        project_id=uuid.uuid4(),
        brief_id=uuid.uuid4(),
        strategy=StoryStrategy.RELATIONSHIP,
        candidate=candidate,
        scorecard=scorecard,
        subject_ids=(uuid.uuid4(), uuid.uuid4()),
        candidate_prompt_id=uuid.uuid4(),
        critic_prompt_id=uuid.uuid4(),
    )

    story = next(row for row in session.added if isinstance(row, StoryRevisionRecord))
    assert len(story.scene_plan_json) == 1
    assert any(isinstance(row, StoryScore) for row in session.added)
    assert document["source"] == "ai"
    assert document["contractKind"] == "legacy_structured"
    assert document["legacyDetails"]["scenes"]
    assert document["legacyDetails"]["scorecard"]


def test_approve_creative_story_without_score_does_not_materialize_empty_scene_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _story()
    subject_ids = tuple(uuid.UUID(value) for value in row.subject_ids_json)
    prompt = SimpleNamespace(structured_response_json={"diagnostics": []})
    session = _ApprovalSession(
        scalar_results=(row, None, prompt),
        scalar_lists=(subject_ids, ()),
    )
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    materialized: list[uuid.UUID] = []
    monkeypatch.setattr(
        repository_module,
        "materialize_approved_story_scenes",
        lambda _session, story: materialized.append(story.id),
    )

    document = repository.approve_story_revision(row.id)

    assert document["status"] == "approved"
    assert document["scorecard"] is None
    assert document["scenes"] == []
    assert materialized == []


def test_approving_edited_story_invalidates_previous_story_downstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _story()
    previous.status = "approved"
    edited = _story()
    edited.parent_revision_id = previous.id
    edited.revision = previous.revision + 1
    subject_ids = tuple(uuid.UUID(value) for value in edited.subject_ids_json)
    previous.subject_ids_json = list(edited.subject_ids_json)
    prompt = SimpleNamespace(structured_response_json={"diagnostics": []})
    session = _ApprovalSession(
        scalar_results=(edited, None, prompt),
        scalar_lists=(subject_ids, (previous,)),
    )
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    invalidated: list[tuple[uuid.UUID, tuple[uuid.UUID, ...]]] = []
    monkeypatch.setattr(
        repository_module,
        "invalidate_story_production_lineage",
        lambda _session, *, project_id, story_ids, reason: invalidated.append(
            (project_id, story_ids)
        ),
    )

    document = repository.approve_story_revision(edited.id)

    assert document["status"] == "approved"
    assert previous.status == "superseded"
    assert invalidated == [(edited.production_run_id, (previous.id,))]


def test_approve_legacy_structured_story_without_score_is_rejected() -> None:
    row = _story(
        scenes=[
            {
                "sceneKey": "legacy-scene",
                "title": "旧场景",
                "purpose": "推进旧故事",
                "synopsis": "旧版结构化场景。",
                "durationWeight": 1,
                "continuity": {},
            }
        ]
    )
    subject_ids = tuple(uuid.UUID(value) for value in row.subject_ids_json)
    session = _ApprovalSession(
        scalar_results=(row, None),
        scalar_lists=(subject_ids,),
    )
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="评审评分"):
        repository.approve_story_revision(row.id)


def test_save_storyboard_plan_derives_neutral_scenes_from_creative_beats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid.uuid4()
    story = _story()
    story.production_run_id = project_id
    story.status = "approved"
    prompt_id = uuid.uuid4()

    class StoryboardSession:
        def __init__(self) -> None:
            self.scalar_results = iter((SimpleNamespace(id=project_id), story, 0))
            self.scalars_results = iter(((), ()))
            self.added: list[object] = []

        def scalar(self, _statement: object) -> object:
            return next(self.scalar_results)

        def scalars(self, _statement: object) -> object:
            return next(self.scalars_results)

        @staticmethod
        def get(_model: object, _identifier: object) -> SimpleNamespace:
            return SimpleNamespace(step_id=uuid.uuid4())

        def add(self, row: object) -> None:
            self.added.append(row)

        def flush(self) -> None:
            for row in self.added:
                if isinstance(row, Scene):
                    if row.look_draft_json is None:
                        row.look_draft_json = {}
                    if row.look_draft_revision is None:
                        row.look_draft_revision = 0

    session = StoryboardSession()
    repository = SqlAlchemyAigcCanvasRepository(_SaveSessions(session))  # type: ignore[arg-type]
    monkeypatch.setattr(
        repository_module,
        "_create_generation_plan_for_beats",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4(), input_hash="plan-hash"),
    )
    plan = StoryboardPlanOutput.model_validate(
        {
            "beats": [
                {
                    "sceneOrder": 1,
                    "sceneLabel": "院中发现",
                    "title": "发现雨滴",
                    "action": "孩子和猫共同完成故事动作。",
                    "camera": "中景",
                    "dialogue": "",
                    "durationWeight": 1,
                },
                {
                    "sceneOrder": 2,
                    "sceneLabel": "廊下收尾",
                    "title": "安放画纸",
                    "action": "他们把画纸放好。",
                    "camera": "近景",
                    "dialogue": "",
                    "durationWeight": 1,
                },
            ]
        }
    )

    document = repository.save_storyboard_plan(
        project_id,
        story_id=story.id,
        plan=plan,
        durations=(15, 15),
        prompt_id=prompt_id,
        input_bindings=[],
    )

    scenes = [row for row in session.added if isinstance(row, Scene)]
    beats = [row for row in session.added if isinstance(row, ShotBeat)]
    assert [scene.scene_key for scene in scenes] == ["storyboard-scene-01", "storyboard-scene-02"]
    assert [scene.title for scene in scenes] == ["院中发现", "廊下收尾"]
    assert all(scene.look_plan_json == {} for scene in scenes)
    for scene in scenes:
        continuity = __import__("json").loads(scene.context_note)["continuity"]
        assert continuity["location"] == ""
        assert continuity["environment"] == ""
        assert continuity["timeWeather"] == ""
    assert len(beats) == 2
    assert {beat.scene_id for beat in beats} == {scene.id for scene in scenes}
    assert [beat.action for beat in beats] == [
        "孩子和猫共同完成故事动作。",
        "他们把画纸放好。",
    ]
    assert all(beat.child_action == "" for beat in beats)
    assert all(beat.cat_action == "" for beat in beats)
    assert all(beat.spatial_relation == "" for beat in beats)
    assert [beat["direction"] for beat in document["beats"]] == [
        "孩子和猫共同完成故事动作。",
        "他们把画纸放好。",
    ]
