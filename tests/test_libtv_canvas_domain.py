from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

import cat_video_generator.domain.aigc_canvas as canvas_domain
from cat_video_generator.domain.aigc_canvas import CanvasConnection, SubjectDraft


def _source_subject() -> SubjectDraft:
    return SubjectDraft(
        name="灰灰",
        kind="animal",
        role="co_protagonist",
        identityAnchors=["灰白虎斑猫"],
        immutableTraits=[],
        relationshipNotes="",
        dramaticFunction="",
        visualRisks=[],
    )


def test_subject_completion_contract_is_available() -> None:
    assert hasattr(canvas_domain, "SubjectCompletionProposal")
    assert hasattr(canvas_domain, "merge_subject_completion")
    assert hasattr(canvas_domain, "subject_completion_missing_fields")


def test_subject_completion_reports_missing_fields_without_mutating_source() -> None:
    source = _source_subject()

    assert canvas_domain.subject_completion_missing_fields(source) == (
        "immutableTraits",
        "relationshipNotes",
        "dramaticFunction",
        "visualRisks",
    )
    assert source.immutable_traits == []


def test_subject_completion_applies_only_explicitly_accepted_fields() -> None:
    source = _source_subject()
    proposal = canvas_domain.SubjectCompletionProposal(
        identityAnchors=["灰白虎斑猫", "左耳有浅色缺口"],
        immutableTraits=["额头 M 纹和尾巴环纹不变"],
        relationshipNotes="主动提醒小满收画",
        dramaticFunction="触发暴雨危机并帮助解决",
        visualRisks=["多镜头中容易改变尾巴环纹"],
        rationale={"immutableTraits": "建立跨镜头身份锚点"},
        warnings=[],
    )

    merged = canvas_domain.merge_subject_completion(
        source,
        proposal,
        accepted_fields=("immutableTraits", "visualRisks"),
    )

    assert merged.identity_anchors == source.identity_anchors
    assert merged.immutable_traits == proposal.immutable_traits
    assert merged.relationship_notes == ""
    assert merged.dramatic_function == ""
    assert merged.visual_risks == proposal.visual_risks
    assert source.immutable_traits == []


def test_subject_completion_rejects_unknown_accepted_field() -> None:
    source = _source_subject()
    proposal = canvas_domain.SubjectCompletionProposal(
        identityAnchors=["灰白虎斑猫"],
        immutableTraits=[],
        relationshipNotes="",
        dramaticFunction="",
        visualRisks=[],
        rationale={},
        warnings=[],
    )

    with pytest.raises(ValueError, match="不支持的主体补全字段"):
        canvas_domain.merge_subject_completion(source, proposal, accepted_fields=("name",))


def test_prompt_artifact_can_feed_a_video_generation_node() -> None:
    connection = CanvasConnection(
        sourceNodeId=uuid.uuid4(),
        sourceNodeType="PromptArtifactNode",
        sourcePort="prompt",
        targetNodeId=uuid.uuid4(),
        targetNodeType="VideoGenerationNode",
        targetPort="prompt",
    )

    assert connection.source_port.value == "prompt"


def test_completion_proposal_requires_at_least_one_identity_anchor() -> None:
    with pytest.raises(ValidationError):
        canvas_domain.SubjectCompletionProposal(
            identityAnchors=[],
            immutableTraits=[],
            relationshipNotes="",
            dramaticFunction="",
            visualRisks=[],
            rationale={},
            warnings=[],
        )


def test_generation_config_requires_an_omission_reason_for_dropped_references() -> None:
    with pytest.raises(ValidationError, match="omissionReason"):
        canvas_domain.NodeGenerationConfigDraft(
            provider="ark",
            model="seedance-2",
            mode="image_to_video",
            aspectRatio="9:16",
            resolution="720p",
            durationSeconds=5,
            audioEnabled=True,
            candidateCount=1,
            autoValidate=True,
            autoLink=True,
            actualReferences=[
                {
                    "assetId": str(uuid.uuid4()),
                    "semanticRole": "co_protagonist",
                    "providerIncluded": False,
                }
            ],
        )


def test_generation_config_exposes_exact_included_and_omitted_references() -> None:
    included_id = uuid.uuid4()
    omitted_id = uuid.uuid4()
    config = canvas_domain.NodeGenerationConfigDraft(
        provider="ark",
        model="seedance-2",
        mode="image_to_video",
        aspectRatio="9:16",
        resolution="720p",
        durationSeconds=5,
        audioEnabled=True,
        candidateCount=1,
        autoValidate=True,
        autoLink=True,
        actualReferences=[
            {
                "assetId": str(included_id),
                "semanticRole": "protagonist",
                "providerIncluded": True,
            },
            {
                "assetId": str(omitted_id),
                "semanticRole": "co_protagonist",
                "providerIncluded": False,
                "omissionReason": "供应商本模式最多接受一张主体参考图",
            },
        ],
    )

    assert config.actual_references[0].provider_included is True
    assert config.actual_references[1].omission_reason
