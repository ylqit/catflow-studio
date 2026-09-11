from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from catflow.application.series import (
    SERIES_NORMALIZATION_REVISION,
    SeriesPlanGenerationCommand,
    SeriesPlanMaterializeCommand,
    normalize_series_plan_result,
)
from catflow_worker.ark_results import ArkResultLandingService
from test_series_workflow import _plan, _series_command, _service


@pytest.mark.parametrize("name,changed", [("summer", False), ("rain", True)])
def test_paid_series_results_have_only_the_audited_metadata_change(name, changed):
    raw = json.loads(
        (Path(__file__).parent / "fixtures" / f"series-reception-{name}.json").read_text(
            encoding="utf-8-sig"
        )
    )
    original = copy.deepcopy(raw)
    settings = {
        "expected_episode_count": 3, "narrative_mode": "anthology",
        "source_unit_ordinals": {1, 2, 3}, "adaptation_policy": "condense_mainline",
        "expected_duration_seconds": 15,
    }
    old = normalize_series_plan_result(
        raw, must_keep=["保留来源文本中的核心事件"], **settings
    )
    assert old.disposition == "needs_input"
    assert "must_keep_unaccounted" in {issue.code for issue in old.issues}
    new = normalize_series_plan_result(
        raw, must_keep=[], normalization_revision=SERIES_NORMALIZATION_REVISION, **settings
    )
    assert new.disposition == "candidate_ready"
    assert new.raw_payload == original == raw
    reconstructed = copy.deepcopy(new.normalized_payload)
    changes = [issue for issue in new.issues if issue.code == "normalized_source_coverage"]
    assert len(changes) == int(changed)
    if changed:
        issue = changes[0]
        assert issue.path == "episodes.1.sourceCoverage.0.coverage"
        assert (issue.before_value, issue.after_value) == ("whole", "partial")
        assert issue.normalization_revision == SERIES_NORMALIZATION_REVISION
        reconstructed["episodes"][1]["sourceCoverage"][0]["coverage"] = issue.before_value
    assert reconstructed == original
    assert new.validation_document()["normalizationRevision"] == SERIES_NORMALIZATION_REVISION


def _generic_plan():
    payload = _plan().model_dump(mode="json", by_alias=True)
    payload["sourceTreatments"] = [{
        "sourceUnitOrdinal": 1, "treatment": "simplified", "episodeOrders": [1],
        "reason": "保留把相册递给另一人的结果，简化翻阅过程。",
    }]
    payload["episodes"][0]["sourceCoverage"] = [{
        "sourceUnitOrdinal": 1, "coverage": "whole", "coverageNote": "递交同一本相册。",
    }]
    return payload


def _normalize(payload, **overrides):
    settings = {
        "expected_episode_count": 3, "narrative_mode": "continuous", "source_unit_ordinals": {1},
        "adaptation_policy": "condense_mainline", "expected_duration_seconds": 12,
        "must_keep": [], "normalization_revision": SERIES_NORMALIZATION_REVISION,
    }
    return normalize_series_plan_result(payload, **(settings | overrides))


@pytest.mark.parametrize("treatment", ["simplified", "merged"])
def test_generic_metadata_normalization_is_conservative_and_idempotent(treatment):
    raw = _generic_plan()
    raw["sourceTreatments"][0]["treatment"] = treatment
    first = _normalize(raw)
    assert first.disposition == "candidate_ready"
    assert first.plan.episodes[0].source_coverage[0].coverage == "partial"
    assert raw["episodes"][0]["sourceCoverage"][0]["coverage"] == "whole"
    second = _normalize(first.normalized_payload)
    assert first.normalized_payload == second.normalized_payload
    assert not any(issue.code == "normalized_source_coverage" for issue in second.issues)


@pytest.mark.parametrize("case", [
    "duplicate_treatment", "unmatched_orders", "empty_reason", "empty_coverage_note",
    "duplicate_reference", "unknown_source", "duplicate_episode_order",
])
def test_ambiguous_metadata_is_never_silently_repaired(case):
    raw = _generic_plan()
    if case == "duplicate_treatment":
        raw["sourceTreatments"].append(copy.deepcopy(raw["sourceTreatments"][0]))
    elif case == "unmatched_orders":
        raw["sourceTreatments"][0]["episodeOrders"] = [2]
    elif case == "empty_reason":
        raw["sourceTreatments"][0]["reason"] = " "
    elif case == "empty_coverage_note":
        raw["episodes"][0]["sourceCoverage"][0]["coverageNote"] = " "
    elif case == "duplicate_reference":
        raw["episodes"][0]["sourceCoverage"] *= 2
    elif case == "unknown_source":
        raw["sourceTreatments"][0]["sourceUnitOrdinal"] = 99
        raw["episodes"][0]["sourceCoverage"][0]["sourceUnitOrdinal"] = 99
    else:
        raw["episodes"][1]["order"] = 1
    result = _normalize(raw)
    if case in {"empty_reason", "empty_coverage_note"}:
        assert result.disposition == "invalid"
        assert result.plan is None
    else:
        assert result.disposition == "needs_input"
        assert result.plan.episodes[0].source_coverage[0].coverage == "whole"
    assert result.normalized_payload["episodes"][0]["sourceCoverage"][0]["coverage"] == "whole"
    assert not any(issue.code == "normalized_source_coverage" for issue in result.issues)


def test_legacy_contract_and_preserve_all_are_not_reinterpreted():
    raw = _generic_plan()
    legacy = _normalize(raw, normalization_revision=None)
    assert legacy.disposition == "needs_input"
    preserved = _normalize(raw, adaptation_policy="preserve_all")
    assert preserved.plan.episodes[0].source_coverage[0].coverage == "whole"
    for treatment, coverage in [("retained", "whole"), ("simplified", "continuation")]:
        raw["sourceTreatments"][0]["treatment"] = treatment
        raw["episodes"][0]["sourceCoverage"][0]["coverage"] = coverage
        result = _normalize(raw)
        assert result.plan.episodes[0].source_coverage[0].coverage == coverage
        assert not any(issue.code == "normalized_source_coverage" for issue in result.issues)


def test_real_requirements_and_blocking_risks_remain_blocking():
    raw = _generic_plan()
    raw["adaptationRisks"] = [{"message": "最后动作不能在指定时长内完成", "blocking": True}]
    result = _normalize(raw, must_keep=["保留来源文本中的核心事件"])
    assert result.disposition == "needs_input"
    assert {issue.code for issue in result.issues if issue.severity == "blocking"} == {
        "must_keep_unaccounted", "adaptation_risk",
    }


def test_saved_result_contract_forbids_client_story_rewrites():
    command = {
        "basePlanVersionId": str(uuid.uuid4()), "source": "saved_result",
        "expectedSettingsHash": "a" * 64, "idempotencyKey": "normalize-contract",
    }
    assert SeriesPlanMaterializeCommand.model_validate(command).plan is None
    for change in ({"plan": None}, {"plan": _generic_plan()}, {"expectedSettingsHash": None}):
        with pytest.raises(ValidationError):
            SeriesPlanMaterializeCommand.model_validate(command | change)
    with pytest.raises(ValidationError):
        SeriesPlanMaterializeCommand.model_validate(command | {"source": "edited"})


def test_planning_preview_separates_empty_user_requirements_and_freezes_revision():
    service = _service()
    series = service.create_story_series(_series_command().model_copy(update={
        "must_keep": [], "adaptation_policy": "condense_mainline",
    }))
    preview = service.preview_series_plan(series.id)
    assert "【用户必须保留要求】[]" in preview.prompt
    assert "【通用创作规则】" in preview.prompt
    assert "不要把故事全文" in preview.prompt
    assert len(preview.settings_input_hash) == 64
    job = service.create_series_plan_job(series.id, SeriesPlanGenerationCommand(
        expectedInputHash=preview.input_hash, idempotencyKey="freeze-series-normalization",
    ))
    assert job.frozen_input["normalizationRevision"] == SERIES_NORMALIZATION_REVISION
    assert job.frozen_input["mustKeep"] == []


@pytest.mark.parametrize("kind", ["plan_series", "plan_series_segment"])
@pytest.mark.parametrize("revision", [None, SERIES_NORMALIZATION_REVISION])
def test_worker_uses_the_frozen_normalization_contract(kind, revision):
    raw = _generic_plan()
    original = copy.deepcopy(raw)
    job_id = uuid.uuid4()
    frozen = {
        "plannedEpisodeCount": 3, "narrativeMode": "continuous",
        "adaptationPolicy": "condense_mainline", "defaultEpisodeDurationSeconds": 12,
        "mustKeep": [], "sourceBeats": [{"bindingOrder": 1}], "startEpisodeOrder": 1,
    }
    if revision:
        frozen["normalizationRevision"] = revision
    studio = Mock()
    studio.get_job.return_value = SimpleNamespace(
        id=job_id, series_id=uuid.uuid4(), kind=kind, frozen_input=frozen,
    )
    studio.get_story_series.return_value = SimpleNamespace(narrative_mode="continuous")
    landing = ArkResultLandingService(
        None, None, studio_service=studio, downloader=None, ffprobe_path=Path("unused"),
    )
    landing._provider_result = Mock(return_value={"payload": raw})
    landing._store_series_plan(job_id)
    validation = studio.record_series_plan_validation.call_args.args[1]
    assert validation["normalizationRevision"] == revision
    assert validation["disposition"] == ("candidate_ready" if revision else "needs_input")
    completion = (
        studio.complete_series_plan_segment_job if kind == "plan_series_segment"
        else studio.complete_series_plan_job
    )
    assert completion.call_count == 1
    assert completion.call_args.args[1].episodes[0].source_coverage[0].coverage == (
        "partial" if revision else "whole"
    )
    assert raw == original
