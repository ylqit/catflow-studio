from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cat_video_generator.domain.aigc_canvas import (
    CanvasNodeType,
    StoryboardPlanOutput,
    SubjectDraft,
)
from cat_video_generator.domain.contracts import (
    AcceptedVisualAssetPlan,
    SceneLookDraft,
    SceneLookPlan,
    VisualAssetAction,
    VisualAssetPlanSelection,
)
from cat_video_generator.domain.production_recipes import (
    CHARACTER_DESIGN_SEMANTIC_ROLE_BY_SLOT,
    SEEDANCE_2_0_CAPABILITY,
    CharacterDesignSlot,
    DirectorWorkflowAdoptionRequest,
    GenerationPlanRevisionDraft,
    HumanReviewDecision,
    HumanReviewDraft,
    ProductionRecipeInstanceDraft,
)
from cat_video_generator.infrastructure.db.aigc_canvas_repository import (
    SqlAlchemyAigcCanvasRepository,
)
from cat_video_generator.infrastructure.db.models import (
    Asset,
    Base,
    CanvasGraphNode,
    CharacterDesignAsset,
    CharacterDesignRevision,
    GenerationClipShot,
    GenerationPlan,
    HumanReviewDecisionRecord,
    ProductionRecipeInstance,
    ProductionRun,
    PromptRecord,
    Scene,
    ShotBeat,
    ShotCard,
    StoryboardRevision,
    StoryBriefRecord,
    StoryRevisionRecord,
    Subject,
    SubjectReference,
    SubjectRevision,
    VideoSequence,
    VisualProfileRevision,
    WorkflowStep,
)
from cat_video_generator.infrastructure.db.production_recipe_repository import (
    SqlAlchemyProductionRecipeRepository,
)
from cat_video_generator.infrastructure.db.repositories import (
    SqlAlchemyWorkflowRepository,
    WorkflowConflictError,
)
from cat_video_generator.infrastructure.db.storyboard_hashing import storyboard_structure_hash
from cat_video_generator.infrastructure.db.visual_preset_profiles import (
    CANON_V3_REQUIRED_KEYS,
    CANON_V4_PROFILE_ID,
    CANON_V4_REQUIRED_KEYS,
    CANON_V4_STYLE_BOARD_KEY,
)
from cat_video_generator.interfaces.api import create_app
from cat_video_generator.interfaces.api_v2 import (
    ManualStoryboardDraftRequest,
    ShotBeatReferenceBindingsRequest,
)
from cat_video_generator.interfaces.jobs import JobRegistry


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type: JSONB, _compiler: object, **_kwargs: object) -> str:
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_for_sqlite(_type: UUID, _compiler: object, **_kwargs: object) -> str:
    return "CHAR(32)"


@pytest.fixture
def storyboard_sessions() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _prepare_sqlite(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.create_function(  # type: ignore[attr-defined]
            "BTRIM",
            1,
            lambda value: None if value is None else value.strip(),
        )
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS cat_video")  # type: ignore[attr-defined]

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()


def test_child_cat_project_creation_is_atomic_unpaid_and_directly_readable(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    asset_ids: dict[str, uuid.UUID] = {}
    with storyboard_sessions.begin() as session:
        for index, semantic_key in enumerate(CANON_V4_REQUIRED_KEYS, 1):
            asset_id = uuid.uuid4()
            asset_ids[semantic_key] = asset_id
            session.add(
                Asset(
                    id=asset_id,
                    production_run_id=None,
                    role="canon_reference",
                    semantic_key=semantic_key,
                    scope="canon",
                    status="approved",
                    media_type="image",
                    storage_key=f"canon-v4/{asset_id}.png",
                    sha256=f"{index:064x}",
                    byte_size=1,
                    metadata_json={},
                )
            )

    repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    payload = SimpleNamespace(
        title="  纸星星的一人一猫短片  ",
        content_date=date(2026, 8, 29),
        child_canon_profile_id=CANON_V4_PROFILE_ID,
        cat_canon_profile_id=CANON_V4_PROFILE_ID,
        style_board_asset_id=asset_ids[CANON_V4_STYLE_BOARD_KEY],
        brief=SimpleNamespace(
            body="  清晨窗边，孩子和猫咪把纸星星贴回玻璃。  ",
            duration_seconds=8,
            aspect_ratio="9:16",
            quality_tier="quick",
        ),
    )

    created = repository.create_child_cat_project(payload)
    project_id = uuid.UUID(created["projectId"])
    shell = repository.get_workspace_shell(project_id)
    flow = repository.get_production_flow(project_id)

    assert created["providerCallCount"] == 0
    assert shell["project"]["title"] == "纸星星的一人一猫短片"
    assert [module["id"] for module in shell["modules"]] == [
        "script",
        "assets",
        "production",
    ]
    assert [node["kind"] for node in flow["nodes"]] == [
        "script",
        "director_plan",
        "assets",
        "storyboard_table",
        "storyboard",
        "workbench",
    ]
    with storyboard_sessions() as session:
        project = session.get(ProductionRun, project_id)
        assert project is not None
        assert project.universal_canvas_enabled is False
        assert (
            session.scalar(select(WorkflowStep).where(WorkflowStep.production_run_id == project_id))
            is None
        )
        assert (
            len(
                list(
                    session.scalars(select(Subject).where(Subject.production_run_id == project_id))
                )
            )
            == 2
        )


def test_child_cat_project_rejects_blank_content_without_partial_rows(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    payload = SimpleNamespace(
        title="   ",
        content_date=None,
        child_canon_profile_id=CANON_V4_PROFILE_ID,
        cat_canon_profile_id=CANON_V4_PROFILE_ID,
        style_board_asset_id=uuid.uuid4(),
        brief=SimpleNamespace(
            body="   ",
            duration_seconds=8,
            aspect_ratio="9:16",
            quality_tier="quick",
        ),
    )

    with pytest.raises(ValueError, match="项目名称不能为空"):
        repository.create_child_cat_project(payload)
    with storyboard_sessions() as session:
        assert session.scalar(select(ProductionRun)) is None


def test_production_flow_exposes_only_six_stable_product_artifacts(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    batch_node_id = uuid.uuid4()
    child_asset_id = uuid.uuid4()
    cat_asset_id = uuid.uuid4()
    product_asset_id = uuid.uuid4()

    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="主体 Canon 参考投影",
                content_date=date.today(),
                status="active",
                canvas_v2_enabled=True,
                universal_canvas_enabled=True,
            )
        )
        session.add(
            CanvasGraphNode(
                id=batch_node_id,
                production_run_id=project_id,
                node_type=CanvasNodeType.GENERATION_BATCH.value,
                object_type="media_generation_batch",
                object_id=None,
                status="ready",
                data_json={"title": "生成批次"},
            )
        )
        session.add_all(
            Asset(
                id=asset_id,
                production_run_id=None,
                role="canon_reference",
                semantic_key=semantic_key,
                scope="canon",
                status="approved",
                media_type="image",
                storage_key=f"canon/{asset_id}.png",
                sha256=f"{index:064x}",
                byte_size=1,
                metadata_json={},
            )
            for index, (asset_id, semantic_key) in enumerate(
                (
                    (child_asset_id, "canon:child:front"),
                    (cat_asset_id, "canon:cat:turnaround"),
                    (product_asset_id, "canon:product:front"),
                ),
                1,
            )
        )

    repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    for draft in (
        SubjectDraft.model_validate(
            {
                "name": "固定儿童",
                "kind": "person",
                "role": "protagonist",
                "identityAnchors": ["固定脸型"],
                "references": [{"assetId": child_asset_id, "semanticRole": "front"}],
            }
        ),
        SubjectDraft.model_validate(
            {
                "name": "固定猫咪",
                "kind": "animal",
                "role": "co_protagonist",
                "identityAnchors": ["固定虎斑纹"],
                "references": [{"assetId": cat_asset_id, "semanticRole": "turnaround"}],
            }
        ),
        SubjectDraft.model_validate(
            {
                "name": "无关商品",
                "kind": "product",
                "role": "hero_product",
                "identityAnchors": ["固定包装"],
                "references": [{"assetId": product_asset_id, "semanticRole": "front"}],
            }
        ),
    ):
        repository.create_subject(project_id, draft)

    flow = repository.get_production_flow(project_id)

    assert [node["kind"] for node in flow["nodes"]] == [
        "script",
        "director_plan",
        "assets",
        "storyboard_table",
        "storyboard",
        "workbench",
    ]
    assert len(flow["edges"]) == 5
    assert all(
        node["id"].startswith(str(uuid.uuid5(project_id, f"production-flow:{node['kind']}")))
        for node in flow["nodes"]
    )
    serialized = str(flow)
    assert str(child_asset_id) not in serialized
    assert str(cat_asset_id) not in serialized
    assert str(product_asset_id) not in serialized


def test_script_workspace_reads_latest_story_candidates_and_current_brief(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    recipe_id = uuid.uuid4()
    approved_story_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add_all(
            [
                ProductionRun(
                    id=project_id,
                    title="导演剧本文档投影",
                    content_date=date.today(),
                    status="active",
                ),
                ProductionRecipeInstance(
                    id=recipe_id,
                    production_run_id=project_id,
                    recipe_key="healing_child_cat_v1",
                    theme="纸星星",
                    target_duration_seconds=8,
                    quality_tier="quick",
                    canon_profile_id="canon-v4-healing-child-cat-style-board",
                ),
                StoryBriefRecord(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    revision=1,
                    theme="旧创作要求",
                    audience="家庭观众",
                    genre="治愈日常",
                    tone="温暖",
                    aspect_ratio="9:16",
                    target_duration_seconds=8,
                    constraints_json=["旧要求"],
                ),
                StoryBriefRecord(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    revision=2,
                    theme="纸星星回到窗边",
                    audience="家庭观众",
                    genre="治愈日常",
                    tone="安静温暖",
                    aspect_ratio="9:16",
                    target_duration_seconds=8,
                    constraints_json=["无对白", "单场景"],
                ),
                CanvasGraphNode(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    node_type=CanvasNodeType.BRIEF.value,
                    object_type="story_brief",
                    object_id=uuid.uuid4(),
                    status="ready",
                    data_json={"title": "历史图节点中的旧创作要求", "revision": 0},
                ),
            ]
        )
        for revision in range(1, 7):
            session.add(
                StoryRevisionRecord(
                    id=approved_story_id if revision == 1 else uuid.uuid4(),
                    production_run_id=project_id,
                    revision=revision,
                    strategy="combined",
                    status=(
                        "approved"
                        if revision == 1
                        else "superseded"
                        if revision == 2
                        else "candidate"
                    ),
                    title=f"完整候选 {revision}",
                    logline=f"摘要 {revision}",
                    synopsis=f"这是完整长文本正文 {revision}。",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={},
                )
            )

    workspace = SqlAlchemyAigcCanvasRepository(storyboard_sessions).get_script_workspace(project_id)

    assert workspace["brief"]["revision"] == 2
    assert workspace["brief"]["theme"] == "纸星星回到窗边"
    assert workspace["recipeInstanceId"] == str(recipe_id)
    assert workspace["currentStoryId"] == str(approved_story_id)
    assert len(workspace["documents"]) == 5
    assert {document["revision"] for document in workspace["documents"]} == {1, 3, 4, 5, 6}
    assert {document["body"] for document in workspace["documents"]} == {
        "这是完整长文本正文 1。",
        "这是完整长文本正文 3。",
        "这是完整长文本正文 4。",
        "这是完整长文本正文 5。",
        "这是完整长文本正文 6。",
    }


def test_script_workspace_api_serializes_persisted_story_documents(
    storyboard_sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add_all(
            [
                ProductionRun(
                    id=project_id,
                    title="导演 API 真实剧情读取",
                    content_date=date.today(),
                    status="active",
                ),
                StoryBriefRecord(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    revision=1,
                    theme="窗边纸星星",
                    audience="家庭观众",
                    genre="治愈日常",
                    tone="安静",
                    aspect_ratio="9:16",
                    target_duration_seconds=8,
                    constraints_json=["无对白"],
                ),
                StoryRevisionRecord(
                    id=story_id,
                    production_run_id=project_id,
                    revision=1,
                    strategy="combined",
                    status="candidate",
                    title="纸星星候选",
                    logline="猫咪把纸星星推回来。",
                    synopsis="孩子发现纸星星，猫咪把它轻轻推回，两者一起贴在窗上。",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={},
                ),
            ]
        )
    repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    app = create_app(
        SimpleNamespace(
            repository=object(),
            editing=object(),
            canvas_v2=repository,
            runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
        ),  # type: ignore[arg-type]
        job_registry=JobRegistry(inline=True),
    )

    response = TestClient(app).get(f"/api/v2/projects/{project_id}/script-workspace")

    assert response.status_code == 200
    document = response.json()
    assert document["brief"]["theme"] == "窗边纸星星"
    story = next(item for item in document["documents"] if item["id"] == str(story_id))
    assert story["body"].startswith("孩子发现纸星星")
    assert story["status"] == "candidate"


def test_asset_read_exposes_current_character_design_selection_and_review_route(
    storyboard_sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    project_id = uuid.uuid4()
    recipe_id = uuid.uuid4()
    story_id = uuid.uuid4()
    old_revision_id = uuid.uuid4()
    current_revision_id = uuid.uuid4()
    old_asset_id = uuid.uuid4()
    selected_asset_id = uuid.uuid4()
    unselected_asset_id = uuid.uuid4()
    validation_asset_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add_all(
            [
                ProductionRun(
                    id=project_id,
                    title="角色设计读取语义",
                    content_date=date.today(),
                    status="active",
                ),
                ProductionRecipeInstance(
                    id=recipe_id,
                    production_run_id=project_id,
                    recipe_key="healing_child_cat_v1",
                    theme="纸星星",
                    target_duration_seconds=8,
                    quality_tier="quick",
                    canon_profile_id="canon-v4-healing-child-cat-style-board",
                ),
                StoryRevisionRecord(
                    id=story_id,
                    production_run_id=project_id,
                    revision=1,
                    strategy="combined",
                    status="approved",
                    title="纸星星",
                    logline="孩子和猫一起贴纸星星。",
                    synopsis="完整剧情正文。",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={},
                ),
                CharacterDesignRevision(
                    id=old_revision_id,
                    production_recipe_instance_id=recipe_id,
                    production_run_id=project_id,
                    source_story_revision_id=story_id,
                    revision=1,
                    idempotency_key="character-old",
                    status="approved",
                ),
                CharacterDesignRevision(
                    id=current_revision_id,
                    production_recipe_instance_id=recipe_id,
                    production_run_id=project_id,
                    source_story_revision_id=story_id,
                    revision=2,
                    idempotency_key="character-current",
                    status="awaiting_review",
                ),
            ]
        )
        for asset_id, status, role, digest in (
            (old_asset_id, "approved", "character_design_child", "1" * 64),
            (selected_asset_id, "approved", "character_design_child", "2" * 64),
            (unselected_asset_id, "ready", "character_design_child", "3" * 64),
            (validation_asset_id, "ready", "character_design_child", "4" * 64),
        ):
            session.add(
                Asset(
                    id=asset_id,
                    production_run_id=project_id,
                    role=role,
                    scope="project",
                    status=status,
                    media_type="image",
                    storage_key=f"character/{asset_id}.png",
                    sha256=digest,
                    metadata_json={
                        "title": str(asset_id),
                        **(
                            {"characterDesign": {"validationOnly": True}}
                            if asset_id == validation_asset_id
                            else {}
                        ),
                    },
                )
            )
        session.add_all(
            [
                CharacterDesignAsset(
                    character_design_revision_id=old_revision_id,
                    asset_id=old_asset_id,
                    slot="child",
                    candidate_index=1,
                    semantic_role=CHARACTER_DESIGN_SEMANTIC_ROLE_BY_SLOT[CharacterDesignSlot.CHILD],
                    selected=True,
                ),
                CharacterDesignAsset(
                    character_design_revision_id=current_revision_id,
                    asset_id=selected_asset_id,
                    slot="child",
                    candidate_index=1,
                    semantic_role=CHARACTER_DESIGN_SEMANTIC_ROLE_BY_SLOT[CharacterDesignSlot.CHILD],
                    selected=True,
                ),
                CharacterDesignAsset(
                    character_design_revision_id=current_revision_id,
                    asset_id=unselected_asset_id,
                    slot="child",
                    candidate_index=2,
                    semantic_role=CHARACTER_DESIGN_SEMANTIC_ROLE_BY_SLOT[CharacterDesignSlot.CHILD],
                    selected=False,
                ),
                CharacterDesignAsset(
                    character_design_revision_id=current_revision_id,
                    asset_id=validation_asset_id,
                    slot="child",
                    candidate_index=3,
                    semantic_role=CHARACTER_DESIGN_SEMANTIC_ROLE_BY_SLOT[CharacterDesignSlot.CHILD],
                    selected=False,
                ),
            ]
        )

    assets = SqlAlchemyAigcCanvasRepository(storyboard_sessions).list_project_assets(
        project_id, media_kind="image"
    )
    by_id = {item["id"]: item for item in assets}

    assert by_id[str(old_asset_id)]["characterDesign"] == {
        "recipeInstanceId": str(recipe_id),
        "revisionId": str(old_revision_id),
        "revision": 1,
        "revisionStatus": "approved",
        "isCurrentRevision": False,
        "slot": "child",
        "candidateIndex": 1,
        "semanticRole": "appearance",
        "selected": True,
    }
    assert by_id[str(old_asset_id)]["reviewAction"]["executable"] is False
    assert by_id[str(selected_asset_id)]["characterDesign"]["isCurrentRevision"] is True
    assert by_id[str(selected_asset_id)]["reviewAction"] == {
        "executable": True,
        "route": "recipe_character_design",
        "recipeInstanceId": str(recipe_id),
        "targetType": "character_design",
        "targetId": str(selected_asset_id),
        "targetHash": "2" * 64,
    }
    assert by_id[str(unselected_asset_id)]["characterDesign"]["selected"] is False
    assert by_id[str(unselected_asset_id)]["reviewAction"]["executable"] is True
    assert by_id[str(validation_asset_id)]["reviewAction"] == {
        "executable": False,
        "route": "readonly",
        "recipeInstanceId": str(recipe_id),
        "targetType": "character_design",
        "targetId": str(validation_asset_id),
        "targetHash": "4" * 64,
        "disabledReason": "引用顺序验证候选只用于审计，不能审核或替换生产版本",
    }

    with pytest.raises(WorkflowConflictError, match="引用顺序验证候选"):
        SqlAlchemyProductionRecipeRepository(storyboard_sessions).record_review(
            recipe_id,
            HumanReviewDraft(
                targetType="character_design",
                targetId=validation_asset_id,
                targetHash="4" * 64,
                decision=HumanReviewDecision.APPROVE,
            ),
        )

    app = create_app(
        SimpleNamespace(
            repository=object(),
            editing=object(),
            canvas_v2=SqlAlchemyAigcCanvasRepository(storyboard_sessions),
            runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
        ),  # type: ignore[arg-type]
        job_registry=JobRegistry(inline=True),
    )
    response = TestClient(app).get(f"/api/v2/projects/{project_id}/assets?kind=image")
    assert response.status_code == 200
    api_assets = {item["id"]: item for item in response.json()}
    assert api_assets[str(selected_asset_id)]["characterDesign"]["revisionId"] == str(
        current_revision_id
    )
    assert api_assets[str(old_asset_id)]["reviewAction"]["route"] == "readonly"


def test_visual_profile_read_exposes_current_subject_revision_authority(
    storyboard_sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    project_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    child_subject_id = uuid.uuid4()
    child_revision_id = uuid.uuid4()
    child_asset_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add_all(
            [
                ProductionRun(
                    id=project_id,
                    title="视觉档案主体权威读取",
                    content_date=date.today(),
                    status="active",
                    current_visual_profile_revision_id=profile_id,
                ),
                Asset(
                    id=child_asset_id,
                    production_run_id=None,
                    role="canon_reference",
                    semantic_key="person:headshot",
                    scope="canon",
                    status="approved",
                    media_type="image",
                    storage_key="canon/child.png",
                    sha256="4" * 64,
                    metadata_json={"title": "儿童面部"},
                ),
                Subject(
                    id=child_subject_id,
                    production_run_id=project_id,
                    kind="person",
                    role="protagonist",
                    status="ready",
                    current_revision_id=child_revision_id,
                ),
                SubjectRevision(
                    id=child_revision_id,
                    subject_id=child_subject_id,
                    revision=3,
                    name="固定儿童",
                    identity_anchors_json=["固定脸型"],
                    immutable_traits_json=["齐下颌短发"],
                    relationship_notes="与猫咪陪伴",
                    dramatic_function="完成小行动",
                    visual_risks_json=[],
                    revision_hash="5" * 64,
                    approval_status="approved",
                ),
                SubjectReference(
                    id=uuid.uuid4(),
                    subject_revision_id=child_revision_id,
                    asset_id=child_asset_id,
                    semantic_role="front",
                    sort_order=1,
                    instruction="锁定儿童脸部",
                ),
                VisualProfileRevision(
                    id=profile_id,
                    production_run_id=project_id,
                    revision=4,
                    profile_hash="6" * 64,
                    source_profile_id="canon-v4-healing-child-cat-style-board",
                    person_identity="固定儿童",
                    person_hair="齐下颌短发",
                    person_body="儿童比例",
                    cat_identity="固定猫咪",
                    style_positive_json=["二维插画"],
                    style_negative_json=["摄影写实"],
                    reference_bindings_json=[
                        {
                            "assetId": str(child_asset_id),
                            "purpose": "person_identity",
                            "instruction": "锁定儿童脸部",
                            "authority": None,
                        }
                    ],
                    reference_snapshot_json=[
                        {
                            "assetId": str(child_asset_id),
                            "semanticKey": "person:headshot",
                            "title": "儿童面部",
                            "contentUrl": f"/api/v1/assets/{child_asset_id}/content",
                            "thumbnailUrl": f"/api/v1/assets/{child_asset_id}/content",
                            "approvalStatus": "approved",
                            "sha256": "4" * 64,
                            "required": True,
                            "authority": {
                                "role": "identity",
                                "providerEligible": True,
                                "priority": 100,
                                "lockedTraits": ["脸型"],
                                "mutableTraits": ["表情"],
                                "forbiddenTransfer": ["背景"],
                            },
                        }
                    ],
                ),
            ]
        )

    profile = SqlAlchemyAigcCanvasRepository(storyboard_sessions).get_episode_visual_profile(
        project_id
    )
    reference = profile["references"][0]

    assert reference["subjectId"] == str(child_subject_id)
    assert reference["subjectRevisionId"] == str(child_revision_id)
    assert reference["subjectRevision"] == 3
    assert reference["subjectKind"] == "person"
    assert reference["subjectRole"] == "protagonist"
    assert reference["authorityOrigin"] == "subject_revision"
    assert reference["currentAuthority"] is True
    assert reference["visualProfileRevisionId"] == str(profile_id)

    app = create_app(
        SimpleNamespace(
            repository=object(),
            editing=object(),
            canvas_v2=SqlAlchemyAigcCanvasRepository(storyboard_sessions),
            runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
        ),  # type: ignore[arg-type]
        job_registry=JobRegistry(inline=True),
    )
    response = TestClient(app).get(f"/api/v2/projects/{project_id}/visual-profile")
    assert response.status_code == 200
    api_reference = response.json()["references"][0]
    assert api_reference["subjectId"] == str(child_subject_id)
    assert api_reference["subjectRevisionId"] == str(child_revision_id)
    assert api_reference["authorityOrigin"] == "subject_revision"
    assert api_reference["currentAuthority"] is True

    with storyboard_sessions.begin() as session:
        rejected_asset = session.get(Asset, child_asset_id)
        assert rejected_asset is not None
        rejected_asset.status = "rejected"
    rejected_reference = SqlAlchemyAigcCanvasRepository(
        storyboard_sessions
    ).get_episode_visual_profile(project_id)["references"][0]
    assert rejected_reference["subjectRevisionId"] == str(child_revision_id)
    assert rejected_reference["authorityOrigin"] == "subject_revision"
    assert rejected_reference["currentAuthority"] is False


def _add_raw_storyboard_prompt(
    session: Session,
    *,
    project_id: uuid.UUID,
    story_id: uuid.UUID,
    raw_text: str,
) -> None:
    step_id = uuid.uuid4()
    session.add(
        WorkflowStep(
            id=step_id,
            production_run_id=project_id,
            kind="director",
            status="succeeded",
            attempt=1,
            operation_key="director:storyboard_director",
            idempotency_key=uuid.uuid4().hex * 2,
            provider="fake",
            model="fake-storyboard",
            input_hash="1" * 64,
            request_hash="2" * 64,
            input_snapshot_json={},
            completed_at=datetime.now(UTC),
        )
    )
    session.add(
        PromptRecord(
            id=uuid.uuid4(),
            step_id=step_id,
            purpose="director",
            model="fake-storyboard",
            prompt_text="把当前剧情拆成分镜",
            sha256="3" * 64,
            call_purpose="storyboard_director",
            business_object_type="story_revision",
            business_object_id=story_id,
            raw_response_json={"text": raw_text},
            structured_response_json={
                "status": "needs_structuring",
                "rawText": raw_text,
                "diagnostics": [
                    {
                        "code": "storyboard_needs_structuring",
                        "severity": "blocker",
                        "message": "分镜原文需要整理为镜头",
                        "targetId": None,
                    }
                ],
            },
            status="succeeded",
            completed_at=datetime.now(UTC),
        )
    )


def test_visual_asset_plan_binding_refreshes_scene_to_current_visual_profile(
    storyboard_sessions: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    project_id = uuid.uuid4()
    old_profile_id = uuid.uuid4()
    current_profile_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    sha256 = "a" * 64
    storage_key = f"imported/sha256/{sha256}"
    asset_path = tmp_path / storage_key
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"approved environment reference")

    def profile(profile_id: uuid.UUID, revision: int) -> VisualProfileRevision:
        return VisualProfileRevision(
            id=profile_id,
            production_run_id=project_id,
            revision=revision,
            profile_hash=str(revision) * 64,
            source_profile_id=f"canon-v{revision}",
            person_identity="固定儿童身份",
            person_hair="齐下颌短发",
            person_body="儿童身体比例",
            cat_identity="固定灰白虎斑猫",
            style_positive_json=["原创二维柔和数字插画"],
            style_negative_json=["摄影写实"],
            reference_bindings_json=[],
            reference_snapshot_json=[],
        )

    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="视觉档案升级场景",
                content_date=date.today(),
                status="active",
                current_visual_profile_revision_id=current_profile_id,
            )
        )
        session.add_all(
            [
                profile(old_profile_id, 3),
                profile(current_profile_id, 4),
                Scene(
                    id=scene_id,
                    production_run_id=project_id,
                    sort_order=1,
                    title="窗边",
                    source_text="清晨窗边的纸星星",
                    look_plan_json=SceneLookPlan().model_dump(mode="json", by_alias=True),
                    look_draft_json=SceneLookDraft(
                        visualProfileRevisionId=old_profile_id,
                        lookPlan=SceneLookPlan(),
                    ).model_dump(mode="json", by_alias=True),
                    look_draft_revision=1,
                    status="draft",
                ),
                Asset(
                    id=asset_id,
                    production_run_id=project_id,
                    scene_id=scene_id,
                    role="generated_reference",
                    semantic_key="scene:window:environment",
                    scope="scene",
                    status="approved",
                    media_type="image",
                    storage_key=storage_key,
                    sha256=sha256,
                    byte_size=30,
                    metadata_json={"referencePurpose": "environment"},
                ),
            ]
        )

    accepted = AcceptedVisualAssetPlan(
        selections=[
            VisualAssetPlanSelection(
                suggestionKey="window-environment",
                action=VisualAssetAction.EXISTING,
                displayName="清晨窗边空场景",
                purpose="environment",
                targetScope="scene",
                prompt="使用当前已批准的清晨窗边空场景",
                existingAssetId=asset_id,
            )
        ]
    )
    repository = SqlAlchemyWorkflowRepository(storyboard_sessions, asset_root=tmp_path)
    with storyboard_sessions.begin() as session:
        scene = session.get(Scene, scene_id)
        assert scene is not None
        changed = repository._apply_visual_asset_plan_bindings(
            session,
            scene=scene,
            accepted_output=accepted,
        )

    assert changed is True
    with storyboard_sessions() as session:
        scene = session.get(Scene, scene_id)
        assert scene is not None
        draft = SceneLookDraft.model_validate(scene.look_draft_json)
        assert draft.visual_profile_revision_id == current_profile_id
        assert [binding.asset_id for binding in draft.reference_bindings] == [asset_id]


def test_recipe_can_start_after_web_template_inputs_and_canon_are_applied(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    draft_child_id = uuid.uuid4()
    draft_cat_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="Web 组合包初始化",
                content_date=date.today(),
                status="active",
                canvas_v2_enabled=True,
                universal_canvas_enabled=True,
            )
        )
        session.add(
            StoryBriefRecord(
                id=uuid.uuid4(),
                production_run_id=project_id,
                revision=1,
                theme="清晨窗边的纸星星",
                audience="竖屏短视频观众",
                genre="原创治愈短片",
                tone="柔和",
                aspect_ratio="9:16",
                target_duration_seconds=8,
                constraints_json=["无对白"],
            )
        )
        session.add_all(
            [
                Subject(
                    id=draft_child_id,
                    production_run_id=project_id,
                    kind="person",
                    role="protagonist",
                    status="draft",
                ),
                Subject(
                    id=draft_cat_id,
                    production_run_id=project_id,
                    kind="animal",
                    role="co_protagonist",
                    status="draft",
                ),
            ]
        )
        session.add_all(
            CanvasGraphNode(
                id=node_id,
                production_run_id=project_id,
                node_type=node_type,
                object_type=object_type,
                object_id=object_id,
                status="ready",
                data_json={"title": title},
            )
            for node_id, node_type, object_type, object_id, title in (
                (
                    uuid.uuid5(project_id, "creative-brief"),
                    "BriefNode",
                    "story_brief",
                    None,
                    "创作要求",
                ),
                (draft_child_id, "SubjectNode", "subject", draft_child_id, "固定儿童"),
                (draft_cat_id, "SubjectNode", "subject", draft_cat_id, "固定猫咪"),
                (
                    uuid.uuid5(project_id, "style-preset:line-texture"),
                    "StylePresetNode",
                    "visual_preset",
                    None,
                    "线条材质",
                ),
            )
        )
        session.add_all(
            Asset(
                id=uuid.uuid4(),
                production_run_id=None,
                role="canon_reference",
                scope="canon",
                status="approved",
                media_type="image",
                storage_key=f"canon/{semantic_key.replace(':', '-')}.png",
                sha256=f"{index + 1:064x}",
                metadata_json={},
                semantic_key=semantic_key,
            )
            for index, semantic_key in enumerate(CANON_V4_REQUIRED_KEYS)
        )

    created = SqlAlchemyProductionRecipeRepository(storyboard_sessions).create_instance(
        project_id,
        ProductionRecipeInstanceDraft(
            theme="清晨窗边的纸星星",
            targetDurationSeconds=8,
            qualityTier="quick",
        ),
    )

    assert created["targetDurationSeconds"] == 8
    assert created["qualityTier"] == "quick"
    with storyboard_sessions() as session:
        briefs = list(
            session.scalars(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision)
            )
        )
        subjects = list(
            session.scalars(
                select(Subject).where(
                    Subject.production_run_id == project_id,
                )
            )
        )
    assert briefs[-1].target_duration_seconds == 8
    assert len(subjects) == 2
    assert {subject.status for subject in subjects} == {"ready"}


def test_legacy_board_adoption_reuses_shots_and_materializes_director_facts(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    shot_ids = (uuid.uuid4(), uuid.uuid4())
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="旧版镜头生产看板",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            Scene(
                id=scene_id,
                production_run_id=project_id,
                sort_order=1,
                title="雨后窗边",
                source_text="孩子与猫咪在窗边找回纸星星。",
                story_mode="multi",
                target_shot_count=2,
                status="ready",
            )
        )
        session.add_all(
            [
                ShotCard(
                    id=shot_ids[0],
                    scene_id=scene_id,
                    sort_order=1,
                    title="发现纸星星",
                    direction="孩子看见纸星星落在窗台，猫咪靠近。",
                    duration_seconds=4,
                    anchor_mode="text_only",
                    inherit_project_references=True,
                    use_scene_look=False,
                    scene_look_usage="off",
                    status="ready",
                ),
                ShotCard(
                    id=shot_ids[1],
                    scene_id=scene_id,
                    sort_order=2,
                    title="一起贴回玻璃",
                    direction="猫咪用鼻尖推回纸星星，孩子把它贴上玻璃。",
                    duration_seconds=4,
                    anchor_mode="text_only",
                    inherit_project_references=True,
                    use_scene_look=False,
                    scene_look_usage="off",
                    status="ready",
                ),
            ]
        )
        session.add_all(
            Asset(
                id=uuid.uuid4(),
                production_run_id=None,
                role="canon_reference",
                scope="canon",
                status="approved",
                media_type="image",
                storage_key=f"canon/{semantic_key.replace(':', '-')}.png",
                sha256=f"{index + 100:064x}",
                metadata_json={},
                semantic_key=semantic_key,
            )
            for index, semantic_key in enumerate(CANON_V3_REQUIRED_KEYS)
        )

    repository = SqlAlchemyProductionRecipeRepository(storyboard_sessions)
    preview = repository.preview_director_workflow_adoption(project_id)
    with storyboard_sessions.begin() as session:
        shot = session.scalar(
            select(ShotCard)
            .join(Scene, ShotCard.scene_id == Scene.id)
            .where(Scene.production_run_id == project_id)
        )
        assert shot is not None
        shot.direction = "孩子与猫咪一起把纸星星贴回玻璃，阳光落在纸面。"
    with pytest.raises(WorkflowConflictError, match="重新预览"):
        repository.adopt_director_workflow(
            project_id,
            DirectorWorkflowAdoptionRequest(
                expectedSourceHash=preview["sourceHash"],
                recipeKey="healing_child_cat_v1",
                targetDurationSeconds=8,
                qualityTier="quick",
            ),
            idempotency_key="legacy-missing-canon-stale-preview",
        )
    preview = repository.preview_director_workflow_adoption(project_id)
    adopted = repository.adopt_director_workflow(
        project_id,
        DirectorWorkflowAdoptionRequest(
            expectedSourceHash=preview["sourceHash"],
            recipeKey="healing_child_cat_v1",
            targetDurationSeconds=8,
            qualityTier="quick",
        ),
        idempotency_key="legacy-board-adoption-001",
    )
    repeated = repository.adopt_director_workflow(
        project_id,
        DirectorWorkflowAdoptionRequest(
            expectedSourceHash=preview["sourceHash"],
            recipeKey="healing_child_cat_v1",
            targetDurationSeconds=8,
            qualityTier="quick",
        ),
        idempotency_key="legacy-board-adoption-001",
    )

    assert preview["eligible"] is True
    assert preview["summary"] == {
        "sceneCount": 1,
        "shotCount": 2,
        "selectedVideoCount": 0,
    }
    assert preview["providerCallCount"] == 0
    assert adopted["adopted"] is True
    assert adopted["providerCallCount"] == 0
    assert repeated["recipeInstanceId"] == adopted["recipeInstanceId"]

    with storyboard_sessions() as session:
        instance = session.scalar(
            select(ProductionRecipeInstance).where(
                ProductionRecipeInstance.production_run_id == project_id
            )
        )
        story = session.scalar(
            select(StoryRevisionRecord).where(
                StoryRevisionRecord.production_run_id == project_id,
                StoryRevisionRecord.status == "approved",
            )
        )
        storyboard = session.scalar(
            select(StoryboardRevision).where(StoryboardRevision.production_run_id == project_id)
        )
        beats = list(
            session.scalars(
                select(ShotBeat)
                .where(ShotBeat.storyboard_revision_id == storyboard.id)
                .order_by(ShotBeat.sort_order)
            )
        )
        shots = list(
            session.scalars(
                select(ShotCard).where(ShotCard.scene_id == scene_id).order_by(ShotCard.sort_order)
            )
        )

    assert instance is not None
    assert story is not None
    assert storyboard is not None
    assert [shot.id for shot in shots] == list(shot_ids)
    assert [beat.shot_card_id for beat in beats] == list(shot_ids)
    assert all(shot.generation_plan_id is not None for shot in shots)


def test_legacy_board_can_be_adopted_before_canon_is_complete(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="待补 Canon 的旧版看板",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            Scene(
                id=scene_id,
                production_run_id=project_id,
                sort_order=1,
                title="窗边",
                source_text="孩子与猫咪一起看纸星星。",
                story_mode="single",
                target_shot_count=1,
                status="ready",
            )
        )
        session.add(
            ShotCard(
                id=uuid.uuid4(),
                scene_id=scene_id,
                sort_order=1,
                title="纸星星",
                direction="孩子与猫咪一起把纸星星贴回玻璃。",
                duration_seconds=8,
                anchor_mode="text_only",
                inherit_project_references=True,
                use_scene_look=False,
                scene_look_usage="off",
                status="ready",
            )
        )

    repository = SqlAlchemyProductionRecipeRepository(storyboard_sessions)
    preview = repository.preview_director_workflow_adoption(project_id)
    adopted = repository.adopt_director_workflow(
        project_id,
        DirectorWorkflowAdoptionRequest(
            expectedSourceHash=preview["sourceHash"],
            recipeKey="healing_child_cat_v1",
            targetDurationSeconds=8,
            qualityTier="quick",
        ),
        idempotency_key="legacy-missing-canon-001",
    )

    assert preview["eligible"] is True
    assert preview["warnings"] == ["人物与猫咪 Canon 尚未完整，可在采用后继续补齐"]
    assert adopted["adopted"] is True
    with storyboard_sessions() as session:
        subjects = list(
            session.scalars(select(Subject).where(Subject.production_run_id == project_id))
        )
    assert {(subject.role, subject.status) for subject in subjects} == {
        ("protagonist", "draft"),
        ("co_protagonist", "draft"),
    }


def test_story_inputs_prefer_approved_canon_subject_over_legacy_duplicate(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    subject_node_ids: list[uuid.UUID] = []
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="重复主体兼容",
                content_date=date.today(),
                status="active",
            )
        )
        for name, kind, role in (
            ("固定儿童", "person", "protagonist"),
            ("固定猫咪", "animal", "co_protagonist"),
        ):
            for status, approval_status in (("draft", "draft"), ("ready", "approved")):
                subject = Subject(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    kind=kind,
                    role=role,
                    status=status,
                )
                session.add(subject)
                session.flush()
                revision = SubjectRevision(
                    id=uuid.uuid4(),
                    subject_id=subject.id,
                    revision=1,
                    name=name,
                    identity_anchors_json=["固定身份"],
                    immutable_traits_json=[],
                    relationship_notes="",
                    dramatic_function="",
                    visual_risks_json=[],
                    revision_hash=uuid.uuid4().hex * 2,
                    approval_status=approval_status,
                )
                session.add(revision)
                session.flush()
                subject.current_revision_id = revision.id
                subject_node_ids.append(subject.id)
                session.add(
                    CanvasGraphNode(
                        id=subject.id,
                        production_run_id=project_id,
                        node_type="SubjectNode",
                        object_type="subject",
                        object_id=subject.id,
                        status=status,
                        data_json={"title": name, "role": role},
                    )
                )

    repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    subjects = repository.list_subjects(project_id)
    canvas = repository.get_canvas(project_id)

    assert {(item.draft.name, item.status) for item in subjects} == {
        ("固定儿童", "ready"),
        ("固定猫咪", "ready"),
    }
    visible_subject_nodes = [node for node in canvas["nodes"] if node["type"] == "SubjectNode"]
    assert len(visible_subject_nodes) == 2
    assert {uuid.UUID(node["id"]) for node in visible_subject_nodes}.issubset(set(subject_node_ids))


def test_editing_non_latest_story_candidates_allocates_new_project_revisions(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_ids = [uuid.uuid4() for _ in range(3)]
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="批量候选独立编辑",
                content_date=date.today(),
                status="active",
            )
        )
        session.add_all(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=revision,
                strategy="combined",
                status="candidate",
                title=f"候选 {revision}",
                logline=f"候选 {revision} 摘要",
                synopsis=f"候选 {revision} 正文",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
            for story_id, revision in zip(story_ids, (8, 9, 10), strict=True)
        )

    repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    edited_eight = repository.save_story_revision_edit(
        revision_id=story_ids[0],
        expected_revision=8,
        idempotency_key="edit-batch-candidate-eight",
        title="候选八人工版",
        body="候选八的新正文。",
        summary=None,
    )
    edited_nine = repository.save_story_revision_edit(
        revision_id=story_ids[1],
        expected_revision=9,
        idempotency_key="edit-batch-candidate-nine",
        title="候选九人工版",
        body="候选九的新正文。",
        summary=None,
    )

    assert [edited_eight["revision"], edited_nine["revision"]] == [11, 12]
    with storyboard_sessions() as session:
        revisions = list(
            session.scalars(
                select(StoryRevisionRecord.revision)
                .where(StoryRevisionRecord.production_run_id == project_id)
                .order_by(StoryRevisionRecord.revision)
            )
        )
    assert revisions == [8, 9, 10, 11, 12]


def test_concurrent_edits_of_different_candidates_never_allocate_duplicate_revisions(
    tmp_path: Path,
) -> None:
    main_path = tmp_path / "story-edit-main.sqlite"
    schema_path = tmp_path / "story-edit-schema.sqlite"
    engine = create_engine(
        f"sqlite+pysqlite:///{main_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _prepare_concurrent_sqlite(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.create_function(  # type: ignore[attr-defined]
            "BTRIM",
            1,
            lambda value: None if value is None else value.strip(),
        )
        dbapi_connection.execute(  # type: ignore[attr-defined]
            f"ATTACH DATABASE '{schema_path.as_posix()}' AS cat_video"
        )

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    project_id = uuid.uuid4()
    story_ids = (uuid.uuid4(), uuid.uuid4())
    with sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="并发编辑不同候选",
                content_date=date.today(),
                status="active",
            )
        )
        session.add_all(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=revision,
                strategy="combined",
                status="candidate",
                title=f"候选 {revision}",
                logline=f"摘要 {revision}",
                synopsis=f"正文 {revision}",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
            for story_id, revision in zip(story_ids, (1, 2), strict=True)
        )
    max_read_barrier = Barrier(2)
    max_read_lock = Lock()
    synchronized_reads_remaining = 2

    @event.listens_for(engine, "before_cursor_execute")
    def _synchronize_revision_reads(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal synchronized_reads_remaining
        if "max(" not in statement.lower() or "story_revisions" not in statement.lower():
            return
        with max_read_lock:
            if synchronized_reads_remaining == 0:
                return
            synchronized_reads_remaining -= 1
        max_read_barrier.wait(timeout=5)

    repository = SqlAlchemyAigcCanvasRepository(sessions)

    def edit(index: int) -> dict[str, object]:
        return repository.save_story_revision_edit(
            revision_id=story_ids[index],
            expected_revision=index + 1,
            idempotency_key=f"concurrent-story-edit-{index + 1}",
            title=f"并发人工稿 {index + 1}",
            body=f"并发编辑后的正文 {index + 1}。",
            summary=None,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(edit, 0), executor.submit(edit, 1)]
            documents = [future.result() for future in futures]
        assert sorted(int(item["revision"]) for item in documents) == [3, 4]
        with sessions() as session:
            revisions = list(
                session.scalars(
                    select(StoryRevisionRecord.revision)
                    .where(StoryRevisionRecord.production_run_id == project_id)
                    .order_by(StoryRevisionRecord.revision)
                )
            )
        assert revisions == [1, 2, 3, 4]
    finally:
        engine.dispose()


def test_story_edit_idempotency_replay_rejects_a_different_expected_revision(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="剧情编辑幂等请求同一性",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="candidate",
                title="来源故事",
                logline="来源摘要",
                synopsis="来源正文",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )
    values = {
        "revision_id": story_id,
        "idempotency_key": "same-story-edit-request",
        "title": "人工版本",
        "body": "人工编辑后的正文。",
        "summary": "人工摘要",
    }
    repository.save_story_revision_edit(expected_revision=1, **values)

    with pytest.raises(WorkflowConflictError, match="版本冲突"):
        repository.save_story_revision_edit(expected_revision=999, **values)


def test_manual_child_of_legacy_story_uses_creative_contract_for_direct_approval(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    legacy_id = uuid.uuid4()
    edited_id = uuid.uuid4()
    source_event_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="旧结构剧情人工改稿",
                content_date=date.today(),
                status="active",
            )
        )
        session.add_all(
            [
                StoryRevisionRecord(
                    id=legacy_id,
                    production_run_id=project_id,
                    source_event_candidate_id=source_event_id,
                    revision=1,
                    strategy="combined",
                    status="approved",
                    title="旧结构原稿",
                    logline="旧摘要",
                    synopsis="旧正文",
                    subject_ids_json=[],
                    scene_plan_json=[{"sceneKey": "legacy", "title": "旧场景"}],
                    episode_rules_json={"environment": "indoor"},
                ),
                StoryRevisionRecord(
                    id=edited_id,
                    production_run_id=project_id,
                    parent_revision_id=legacy_id,
                    source_event_candidate_id=source_event_id,
                    revision=2,
                    strategy="combined",
                    status="candidate",
                    title="人工完整文本",
                    logline="人工摘要",
                    synopsis="人工重写后的完整正文。",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={"environment": "indoor"},
                ),
            ]
        )

    document = SqlAlchemyAigcCanvasRepository(storyboard_sessions).approve_story_revision(edited_id)

    assert document["status"] == "approved"
    assert document["contractKind"] == "creative_text"
    assert document["sourceEventCandidateId"] == str(source_event_id)
    assert document["scorecard"] is None


def test_recipe_approves_manual_child_of_legacy_story_without_score_or_episode_rules(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    legacy_id = uuid.uuid4()
    edited_id = uuid.uuid4()
    source_event_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add_all(
            [
                ProductionRun(
                    id=project_id,
                    title="配方旧结构剧情人工改稿",
                    content_date=date.today(),
                    status="active",
                ),
                ProductionRecipeInstance(
                    id=instance_id,
                    production_run_id=project_id,
                    recipe_key="healing_child_cat_v1",
                    theme="雨后长廊",
                    target_duration_seconds=15,
                    quality_tier="balanced",
                    canon_profile_id="canon-v3",
                ),
                StoryRevisionRecord(
                    id=legacy_id,
                    production_run_id=project_id,
                    source_event_candidate_id=source_event_id,
                    revision=1,
                    strategy="combined",
                    status="approved",
                    title="旧结构原稿",
                    logline="旧摘要",
                    synopsis="旧正文",
                    subject_ids_json=[],
                    scene_plan_json=[{"sceneKey": "legacy", "title": "旧场景"}],
                    episode_rules_json={"environment": "indoor"},
                ),
                StoryRevisionRecord(
                    id=edited_id,
                    production_run_id=project_id,
                    parent_revision_id=legacy_id,
                    source_event_candidate_id=source_event_id,
                    revision=2,
                    strategy="combined",
                    status="candidate",
                    title="人工完整文本",
                    logline="人工摘要",
                    synopsis="人工重写后的完整正文。",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={},
                ),
            ]
        )

    review = SqlAlchemyProductionRecipeRepository(storyboard_sessions).record_review(
        instance_id,
        HumanReviewDraft(
            targetType="story_revision",
            targetId=edited_id,
            targetRevision=2,
            decision=HumanReviewDecision.APPROVE,
        ),
    )

    assert review["decision"] == "approve"
    with storyboard_sessions() as session:
        assert session.get(StoryRevisionRecord, edited_id).status == "approved"
        assert session.get(StoryRevisionRecord, legacy_id).status == "superseded"


def test_save_minimal_explicit_duration_storyboard_in_real_transaction(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="真实事务最小分镜",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            ProductionRecipeInstance(
                id=uuid.uuid4(),
                production_run_id=project_id,
                recipe_key="healing_child_cat_v1",
                theme="两地寻找纸飞机",
                target_duration_seconds=30,
                quality_tier="balanced",
                canon_profile_id="canon-v3",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="approved",
                title="两地寻找纸飞机",
                logline="孩子和猫在院子与廊下寻找纸飞机。",
                synopsis="他们先在院中寻找，随后到廊下发现纸飞机。",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )
    repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    plan = StoryboardPlanOutput.model_validate(
        {
            "shots": [
                {
                    "order": 1,
                    "sceneLabel": "院中",
                    "title": "开始寻找",
                    "direction": "孩子和猫在院中寻找纸飞机。",
                    "dialogue": "纸飞机会在哪里呢？",
                    "durationSeconds": 15,
                },
                {
                    "order": 2,
                    "sceneOrder": 2,
                    "sceneLabel": "廊下",
                    "title": "找到纸飞机",
                    "direction": "他们在廊下的长椅旁找到纸飞机。",
                    "durationSeconds": 15,
                },
            ]
        }
    )

    document = repository.save_storyboard_plan(
        project_id,
        story_id=story_id,
        plan=plan,
        durations=(15, 15),
        prompt_id=uuid.uuid4(),
        input_bindings=[],
    )

    with storyboard_sessions() as session:
        scenes = list(session.scalars(select(Scene).order_by(Scene.sort_order)))
        beats = list(session.scalars(select(ShotBeat).order_by(ShotBeat.sort_order)))
        clips = list(session.scalars(select(ShotCard).order_by(ShotCard.plan_sort_order)))
        beats[0].reference_bindings_json = [
            {
                "assetId": str(uuid.uuid4()),
                "sha256": "c" * 64,
                "semanticRole": "composition",
                "purpose": "shot_composition",
                "instruction": "保持人物和猫咪相对位置",
                "ordinal": 1,
                "sourceType": "editorial_shot_reference",
                "locked": False,
            }
        ]
        beats[0].reference_binding_revision = 2
        session.commit()
    with storyboard_sessions() as session:
        recipe_instance_id = session.scalar(select(ProductionRecipeInstance.id))
    assert recipe_instance_id is not None
    recipe = SqlAlchemyProductionRecipeRepository(storyboard_sessions).get_instance(
        recipe_instance_id
    )
    assert document["generationPlanId"]
    assert [scene.title for scene in scenes] == ["院中", "廊下"]
    assert [beat.duration_seconds for beat in beats] == [15, 15]
    assert [beat.action for beat in beats] == [
        "孩子和猫在院中寻找纸飞机。",
        "他们在廊下的长椅旁找到纸飞机。",
    ]
    assert [clip.direction for clip in clips] == [
        "分镜1：孩子和猫在院中寻找纸飞机。",
        "分镜1：他们在廊下的长椅旁找到纸飞机。",
    ]
    first_editorial = recipe["editorialShots"][0]
    assert first_editorial["direction"] == "孩子和猫在院中寻找纸飞机。"
    assert first_editorial["action"] == "孩子和猫在院中寻找纸飞机。"
    assert first_editorial["dialogue"] == "纸飞机会在哪里呢？"
    assert first_editorial["referenceBindingRevision"] == 2
    assert first_editorial["referenceBindings"][0]["semanticRole"] == "composition"


def test_canvas_does_not_project_raw_storyboard_from_a_superseded_story(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_a_id = uuid.uuid4()
    story_b_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="切换当前剧情",
                content_date=date.today(),
                status="active",
            )
        )
        session.add_all(
            [
                StoryRevisionRecord(
                    id=story_a_id,
                    production_run_id=project_id,
                    revision=1,
                    strategy="combined",
                    status="superseded",
                    title="剧情 A",
                    logline="旧剧情",
                    synopsis="旧剧情产生过一段未结构化分镜。",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={},
                ),
                StoryRevisionRecord(
                    id=story_b_id,
                    production_run_id=project_id,
                    revision=2,
                    strategy="combined",
                    status="approved",
                    title="剧情 B",
                    logline="当前剧情",
                    synopsis="用户已切换到新剧情。",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={},
                ),
            ]
        )
        _add_raw_storyboard_prompt(
            session,
            project_id=project_id,
            story_id=story_a_id,
            raw_text="这是剧情 A 的原始分镜文本。",
        )

    canvas = SqlAlchemyAigcCanvasRepository(storyboard_sessions).get_canvas(project_id)
    director = next(node for node in canvas["nodes"] if node["type"] == "StoryboardDirectorNode")

    assert "storyboardDraftStatus" not in director["data"]
    assert "rawStoryboardText" not in director["data"]


def test_canvas_stops_projecting_raw_storyboard_after_manual_structuring(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="原文整理后刷新",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="approved",
                title="收好画纸",
                logline="孩子和猫收好画纸。",
                synopsis="一段需要手工整理的分镜原文。",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )
        _add_raw_storyboard_prompt(
            session,
            project_id=project_id,
            story_id=story_id,
            raw_text="镜头一：孩子收起画纸。",
        )
    repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    before = repository.get_canvas(project_id)
    before_director = next(
        node for node in before["nodes"] if node["type"] == "StoryboardDirectorNode"
    )
    assert before_director["data"]["storyboardDraftStatus"] == "needs_structuring"

    repository.save_manual_storyboard(
        project_id,
        expected_revision=0,
        payload=ManualStoryboardDraftRequest.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "收起画纸",
                        "direction": "孩子把画纸收进文件夹。",
                        "durationSeconds": 15,
                    }
                ]
            }
        ),
    )

    after = repository.get_canvas(project_id)
    after_director = next(
        node for node in after["nodes"] if node["type"] == "StoryboardDirectorNode"
    )
    assert after_director["data"]["storyboardRevision"] == 1
    assert "storyboardDraftStatus" not in after_director["data"]
    assert "rawStoryboardText" not in after_director["data"]


def test_manual_structuring_compares_the_projected_storyboard_revision(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    previous_story_id = uuid.uuid4()
    story_id = uuid.uuid4()
    storyboard_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="模型原文人工恢复",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=previous_story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="superseded",
                title="旧剧情",
                logline="旧剧情已经被替换。",
                synopsis="旧场景需要退出活动位置。",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=2,
                strategy="combined",
                status="approved",
                title="窗边纸星星",
                logline="猫咪推回纸星星。",
                synopsis="模型已返回嵌套分镜原文。",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )
        session.add(
            Scene(
                id=uuid.uuid4(),
                production_run_id=project_id,
                story_revision_id=previous_story_id,
                scene_key="old-scene",
                active=True,
                sort_order=1,
                title="旧场景",
                source_text="旧剧情的活动场景。",
                story_mode="single",
                target_shot_count=1,
                look_plan_json={},
                status="ready",
            )
        )
        session.add(
            StoryboardRevision(
                id=storyboard_id,
                production_run_id=project_id,
                story_revision_id=story_id,
                revision=1,
                status="draft",
                structure_hash="a" * 64,
                input_bindings_json=[],
            )
        )

    document = SqlAlchemyAigcCanvasRepository(storyboard_sessions).save_manual_storyboard(
        project_id,
        expected_revision=1,
        payload=ManualStoryboardDraftRequest.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "纸星星落下",
                        "direction": "纸星星被风吹落到窗台。",
                        "durationSeconds": 8,
                    }
                ]
            }
        ),
    )

    assert document["revision"] == 2
    with storyboard_sessions() as session:
        scenes = list(session.scalars(select(Scene).order_by(Scene.created_at)))
        assert [scene.active for scene in scenes] == [False, True]


def test_revised_generation_plan_does_not_invent_missing_director_fields(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="修订生成编排不伪造事实",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            ProductionRecipeInstance(
                id=instance_id,
                production_run_id=project_id,
                recipe_key="healing_child_cat_v1",
                theme="收好画纸",
                target_duration_seconds=15,
                quality_tier="balanced",
                canon_profile_id="canon-v3",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="approved",
                title="收好画纸",
                logline="孩子和猫收好画纸。",
                synopsis="镜头只保存用户提供的完整描述。",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )
    saved = SqlAlchemyAigcCanvasRepository(storyboard_sessions).save_storyboard_plan(
        project_id,
        story_id=story_id,
        plan=StoryboardPlanOutput.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "收起画纸",
                        "direction": "孩子把画纸收进文件夹。",
                        "durationSeconds": 8,
                    },
                    {
                        "order": 2,
                        "title": "放好文件夹",
                        "direction": "文件夹被放在廊下长椅上。",
                        "durationSeconds": 7,
                    },
                ]
            }
        ),
        durations=(8, 7),
        prompt_id=uuid.uuid4(),
        input_bindings=[],
    )
    storyboard_id = uuid.UUID(saved["storyboardRevisionId"])
    plan_id = uuid.UUID(saved["generationPlanId"])
    with storyboard_sessions.begin() as session:
        session.get(StoryboardRevision, storyboard_id).status = "structure_approved"
        beat_ids = list(
            session.scalars(
                select(ShotBeat.id)
                .where(ShotBeat.storyboard_revision_id == storyboard_id)
                .order_by(ShotBeat.sort_order)
            )
        )

    SqlAlchemyProductionRecipeRepository(storyboard_sessions).revise_generation_plan(
        instance_id,
        plan_id,
        expected_revision=1,
        payload=GenerationPlanRevisionDraft(
            provider=SEEDANCE_2_0_CAPABILITY.provider,
            model=SEEDANCE_2_0_CAPABILITY.model,
            capabilityRevision=SEEDANCE_2_0_CAPABILITY.capability_revision,
            clips=[
                {"shotBeatIds": [beat_ids[0]]},
                {"shotBeatIds": [beat_ids[1]]},
            ],
        ),
    )

    with storyboard_sessions() as session:
        revised = session.scalar(
            select(GenerationPlan)
            .where(GenerationPlan.storyboard_revision_id == storyboard_id)
            .order_by(GenerationPlan.revision.desc())
        )
        directions = list(
            session.scalars(
                select(ShotCard.direction)
                .where(ShotCard.generation_plan_id == revised.id)
                .order_by(ShotCard.plan_sort_order)
            )
        )
    assert directions == [
        "分镜1（8秒）：孩子把画纸收进文件夹。",
        "分镜1（7秒）：文件夹被放在廊下长椅上。",
    ]


def test_minimal_storyboard_with_dialogue_can_be_structure_approved_with_warning(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="对白仅提示",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            ProductionRecipeInstance(
                id=instance_id,
                production_run_id=project_id,
                recipe_key="healing_child_cat_v1",
                theme="找到纸飞机",
                target_duration_seconds=15,
                quality_tier="balanced",
                canon_profile_id="canon-v3",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="approved",
                title="找到纸飞机",
                logline="孩子和猫找到纸飞机。",
                synopsis="孩子和猫在院中找到纸飞机。",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )
    canvas_repository = SqlAlchemyAigcCanvasRepository(storyboard_sessions)
    saved = canvas_repository.save_storyboard_plan(
        project_id,
        story_id=story_id,
        plan=StoryboardPlanOutput.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "找到纸飞机",
                        "direction": "孩子和猫在长椅旁看到纸飞机。",
                        "dialogue": "孩子说：找到了。",
                        "durationSeconds": 15,
                    }
                ]
            }
        ),
        durations=(15,),
        prompt_id=uuid.uuid4(),
        input_bindings=[],
    )
    storyboard_id = uuid.UUID(saved["storyboardRevisionId"])
    with storyboard_sessions() as session:
        beats = list(
            session.scalars(
                select(ShotBeat).where(ShotBeat.storyboard_revision_id == storyboard_id)
            )
        )
        structure_hash = storyboard_structure_hash(beats)

    recipe_repository = SqlAlchemyProductionRecipeRepository(storyboard_sessions)
    review = recipe_repository.record_review(
        instance_id,
        HumanReviewDraft(
            targetType="storyboard_structure",
            targetId=storyboard_id,
            targetHash=structure_hash,
            decision=HumanReviewDecision.APPROVE,
        ),
    )

    assert review["decision"] == "approve"
    assert review["warnings"] == [
        {
            "code": "storyboard_dialogue_present",
            "severity": "warning",
            "message": "分镜包含对白；请确认口型、声音和镜头时长是否适合当前成片。",
            "targetId": None,
        }
    ]
    with storyboard_sessions() as session:
        assert session.get(ShotBeat, beats[0].id).status == "approved"


def test_legacy_story_scene_without_shot_is_saved_as_warning(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="旧场景覆盖仅提示",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            ProductionRecipeInstance(
                id=instance_id,
                production_run_id=project_id,
                recipe_key="healing_child_cat_v1",
                theme="院中故事",
                target_duration_seconds=15,
                quality_tier="balanced",
                canon_profile_id="canon-v3",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="approved",
                title="院中故事",
                logline="主要动作都发生在院中。",
                synopsis="旧数据曾预先拆出院中和廊下两个场景。",
                subject_ids_json=[],
                scene_plan_json=[
                    {
                        "sceneKey": "yard",
                        "title": "院中",
                        "purpose": "主要动作",
                        "synopsis": "孩子和猫找到纸飞机。",
                        "durationWeight": 1,
                        "continuity": {},
                    },
                    {
                        "sceneKey": "porch",
                        "title": "廊下",
                        "purpose": "旧版备用场景",
                        "synopsis": "旧结构中存在但新分镜无需使用。",
                        "durationWeight": 1,
                        "continuity": {},
                    },
                ],
                episode_rules_json={},
            )
        )

    document = SqlAlchemyAigcCanvasRepository(storyboard_sessions).save_storyboard_plan(
        project_id,
        story_id=story_id,
        plan=StoryboardPlanOutput.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "院中找到纸飞机",
                        "direction": "孩子和猫在院中找到纸飞机。",
                        "durationSeconds": 15,
                    }
                ]
            }
        ),
        durations=(15,),
        prompt_id=uuid.uuid4(),
        input_bindings=[],
    )

    assert [item["code"] for item in document["diagnostics"]] == ["storyboard_scene_uncovered"]
    with storyboard_sessions() as session:
        scenes = list(session.scalars(select(Scene).order_by(Scene.sort_order)))
        assert len(scenes) == 2
        assert scenes[1].target_shot_count == 1
    recipe_repository = SqlAlchemyProductionRecipeRepository(storyboard_sessions)
    review = recipe_repository.record_review(
        instance_id,
        HumanReviewDraft(
            targetType="storyboard_structure",
            targetId=uuid.UUID(document["storyboardRevisionId"]),
            targetHash=document["structureHash"],
            decision=HumanReviewDecision.APPROVE,
        ),
    )
    assert [item["code"] for item in review["warnings"]] == ["storyboard_scene_uncovered"]
    projected = recipe_repository.get_instance(instance_id)
    assert [item["code"] for item in projected["storyboard"]["warnings"]] == [
        "storyboard_scene_uncovered"
    ]


def test_recipe_can_select_creative_text_story_without_episode_rules_gate(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="完整文本选择",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            ProductionRecipeInstance(
                id=instance_id,
                production_run_id=project_id,
                recipe_key="healing_child_cat_v1",
                theme="雨后收画",
                target_duration_seconds=15,
                quality_tier="balanced",
                canon_profile_id="canon-v3",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="candidate",
                title="雨后收画",
                logline="孩子和猫一起收画。",
                synopsis="雨停后，孩子和猫一起把画纸收回长廊。",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )

    review = SqlAlchemyProductionRecipeRepository(storyboard_sessions).record_review(
        instance_id,
        HumanReviewDraft(
            targetType="story_revision",
            targetId=story_id,
            targetRevision=1,
            decision=HumanReviewDecision.APPROVE,
        ),
    )

    assert review["decision"] == "approve"
    with storyboard_sessions() as session:
        selected = session.get(StoryRevisionRecord, story_id)
        assert selected is not None
        assert selected.status == "approved"
        assert selected.episode_rules_json == {}


def test_recipe_story_switch_invalidates_only_previous_story_production_lineage(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    previous_story_id = uuid.uuid4()
    edited_story_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    character_design_id = uuid.uuid4()
    character_asset_id = uuid.uuid4()
    canon_asset_id = uuid.uuid4()
    production_asset_id = uuid.uuid4()
    sequence_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add_all(
            [
                ProductionRun(
                    id=project_id,
                    title="切换当前剧情精确失效",
                    content_date=date.today(),
                    status="active",
                    current_visual_profile_revision_id=profile_id,
                ),
                ProductionRecipeInstance(
                    id=instance_id,
                    production_run_id=project_id,
                    recipe_key="healing_child_cat_v1",
                    theme="雨后收画",
                    target_duration_seconds=15,
                    quality_tier="balanced",
                    canon_profile_id="canon-v3",
                ),
                StoryRevisionRecord(
                    id=previous_story_id,
                    production_run_id=project_id,
                    revision=1,
                    strategy="combined",
                    status="approved",
                    title="旧剧情",
                    logline="旧摘要",
                    synopsis="旧正文",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={},
                ),
                Subject(
                    id=subject_id,
                    production_run_id=project_id,
                    kind="person",
                    role="reference",
                    status="approved",
                ),
                VisualProfileRevision(
                    id=profile_id,
                    production_run_id=project_id,
                    revision=1,
                    profile_hash="a" * 64,
                    source_profile_id="canon-v3",
                    person_identity="固定儿童",
                    person_hair="固定发型",
                    person_body="固定体型",
                    cat_identity="固定猫咪",
                    style_positive_json=["水彩"],
                    style_negative_json=["3D"],
                    reference_bindings_json=[],
                    reference_snapshot_json=[],
                ),
            ]
        )
    storyboard = SqlAlchemyAigcCanvasRepository(storyboard_sessions).save_storyboard_plan(
        project_id,
        story_id=previous_story_id,
        plan=StoryboardPlanOutput.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "旧剧情镜头",
                        "direction": "孩子和猫一起收好画纸。",
                        "durationSeconds": 15,
                    }
                ]
            }
        ),
        durations=(15,),
        prompt_id=uuid.uuid4(),
        input_bindings=[],
    )
    storyboard_id = uuid.UUID(storyboard["storyboardRevisionId"])
    plan_id = uuid.UUID(storyboard["generationPlanId"])
    with storyboard_sessions.begin() as session:
        scene = session.scalar(select(Scene).where(Scene.story_revision_id == previous_story_id))
        beat = session.scalar(
            select(ShotBeat).where(ShotBeat.storyboard_revision_id == storyboard_id)
        )
        shot = session.scalar(select(ShotCard).where(ShotCard.generation_plan_id == plan_id))
        assert scene is not None and beat is not None and shot is not None
        session.get(StoryboardRevision, storyboard_id).status = "production_approved"
        session.get(GenerationPlan, plan_id).status = "approved"
        beat.status = "approved"
        shot.status = "approved"
        shot.prompt_id = uuid.uuid4()
        shot.selected_anchor_asset_id = production_asset_id
        shot.selected_video_asset_id = production_asset_id
        session.add_all(
            [
                StoryRevisionRecord(
                    id=edited_story_id,
                    production_run_id=project_id,
                    parent_revision_id=previous_story_id,
                    revision=2,
                    strategy="combined",
                    status="candidate",
                    title="新剧情",
                    logline="新摘要",
                    synopsis="人工编辑后的完整新正文。",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={},
                ),
                CharacterDesignRevision(
                    id=character_design_id,
                    production_recipe_instance_id=instance_id,
                    production_run_id=project_id,
                    source_story_revision_id=previous_story_id,
                    revision=1,
                    idempotency_key="character-design-stays-valid",
                    status="approved",
                ),
                Asset(
                    id=character_asset_id,
                    production_run_id=project_id,
                    role="character_design_child",
                    scope="project",
                    status="approved",
                    media_type="image",
                    storage_key="character.png",
                    sha256="b" * 64,
                    metadata_json={},
                ),
                Asset(
                    id=canon_asset_id,
                    production_run_id=project_id,
                    role="identity_reference",
                    scope="canon",
                    status="approved",
                    media_type="image",
                    storage_key="canon.png",
                    sha256="c" * 64,
                    metadata_json={},
                ),
                Asset(
                    id=production_asset_id,
                    production_run_id=project_id,
                    scene_id=scene.id,
                    shot_card_id=shot.id,
                    role="shot_video",
                    scope="shot",
                    status="approved",
                    media_type="video",
                    storage_key="shot.mp4",
                    sha256="d" * 64,
                    metadata_json={},
                ),
                CharacterDesignAsset(
                    character_design_revision_id=character_design_id,
                    asset_id=character_asset_id,
                    slot="child",
                    candidate_index=1,
                    semantic_role="character_appearance",
                    selected=True,
                ),
                VideoSequence(
                    id=sequence_id,
                    production_run_id=project_id,
                    revision=1,
                    status="approved",
                    duration_ms=15_000,
                    audio_policy="native_fades",
                    clips_json=[],
                ),
            ]
        )
        session.get(ProductionRun, project_id).selected_sequence_id = sequence_id

    SqlAlchemyProductionRecipeRepository(storyboard_sessions).record_review(
        instance_id,
        HumanReviewDraft(
            targetType="story_revision",
            targetId=edited_story_id,
            targetRevision=2,
            decision=HumanReviewDecision.APPROVE,
        ),
    )

    with storyboard_sessions() as session:
        scene = session.scalar(select(Scene).where(Scene.story_revision_id == previous_story_id))
        beat = session.scalar(
            select(ShotBeat).where(ShotBeat.storyboard_revision_id == storyboard_id)
        )
        shot = session.scalar(select(ShotCard).where(ShotCard.generation_plan_id == plan_id))
        assert scene is not None and scene.active is False and scene.stale_reason
        assert beat is not None and beat.status == "stale" and beat.stale_reason
        assert session.get(StoryboardRevision, storyboard_id).status == "superseded"
        assert session.get(GenerationPlan, plan_id).status == "stale"
        assert shot is not None and shot.status == "ready"
        assert shot.prompt_id is None
        assert shot.selected_anchor_asset_id is None
        assert shot.selected_video_asset_id is None
        assert session.get(Asset, production_asset_id).status == "stale"
        assert session.get(VideoSequence, sequence_id).status == "rejected"
        assert session.get(ProductionRun, project_id).selected_sequence_id is None
        assert session.get(CharacterDesignRevision, character_design_id).status == "approved"
        assert session.get(Asset, character_asset_id).status == "approved"
        assert session.get(Asset, canon_asset_id).status == "approved"
        assert session.get(Subject, subject_id).status == "approved"
        assert session.get(VisualProfileRevision, profile_id).profile_hash == "a" * 64


def test_confirm_storyboard_production_plan_approves_structure_and_plan_atomically(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="一次确认制作方案",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            ProductionRecipeInstance(
                id=instance_id,
                production_run_id=project_id,
                recipe_key="healing_child_cat_v1",
                theme="找到纸飞机",
                target_duration_seconds=15,
                quality_tier="balanced",
                canon_profile_id="canon-v3",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="approved",
                title="找到纸飞机",
                logline="孩子和猫找到纸飞机。",
                synopsis="孩子和猫在院中找到纸飞机。",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )
    saved = SqlAlchemyAigcCanvasRepository(storyboard_sessions).save_storyboard_plan(
        project_id,
        story_id=story_id,
        plan=StoryboardPlanOutput.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "找到纸飞机",
                        "direction": "孩子说找到了，猫在长椅旁停下。",
                        "dialogue": "找到了。",
                        "durationSeconds": 15,
                    }
                ]
            }
        ),
        durations=(15,),
        prompt_id=uuid.uuid4(),
        input_bindings=[],
    )
    storyboard_id = uuid.UUID(saved["storyboardRevisionId"])
    plan_id = uuid.UUID(saved["generationPlanId"])

    repository = SqlAlchemyProductionRecipeRepository(storyboard_sessions)
    confirmation = SimpleNamespace(
        idempotency_key="confirm-storyboard-once",
        storyboard_revision_id=storyboard_id,
        storyboard_revision=int(saved["revision"]),
        structure_hash=saved["structureHash"],
        generation_plan_id=plan_id,
        generation_plan_revision=1,
        generation_plan_hash=saved["generationPlanHash"],
        reason=None,
    )
    result = repository.confirm_storyboard_production_plan(
        instance_id,
        confirmation,
    )
    replay = repository.confirm_storyboard_production_plan(instance_id, confirmation)

    assert result["status"] == "approved"
    assert replay["confirmationId"] == result["confirmationId"]
    assert replay["reviewIds"] == result["reviewIds"]
    assert result["warnings"][0]["code"] == "storyboard_dialogue_present"
    with storyboard_sessions() as session:
        assert session.get(StoryboardRevision, storyboard_id).status == "structure_approved"
        assert session.get(GenerationPlan, plan_id).status == "approved"
        assert (
            session.scalar(
                select(ShotBeat.status).where(ShotBeat.storyboard_revision_id == storyboard_id)
            )
            == "approved"
        )
        assert len(list(session.scalars(select(HumanReviewDecisionRecord)))) == 2

    with pytest.raises(WorkflowConflictError, match="幂等|确认快照"):
        repository.confirm_storyboard_production_plan(
            instance_id,
            SimpleNamespace(
                **{
                    **vars(confirmation),
                    "structure_hash": "f" * 64,
                }
            ),
        )
    with storyboard_sessions() as session:
        assert len(list(session.scalars(select(HumanReviewDecisionRecord)))) == 2

    with storyboard_sessions.begin() as session:
        session.delete(
            session.get(
                HumanReviewDecisionRecord,
                uuid.UUID(result["reviewIds"][1]),
            )
        )
    with pytest.raises(WorkflowConflictError, match="幂等确认记录不完整"):
        repository.confirm_storyboard_production_plan(instance_id, confirmation)


def test_storyboard_confirmation_locks_execution_graph_in_dependency_order() -> None:
    storyboard_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    storyboard = StoryboardRevision(
        id=storyboard_id,
        production_run_id=uuid.uuid4(),
        story_revision_id=uuid.uuid4(),
        revision=1,
        status="draft",
        structure_hash="a" * 64,
        input_bindings_json=[],
    )
    plan = GenerationPlan(
        id=plan_id,
        storyboard_revision_id=storyboard_id,
        revision=1,
        status="proposed",
        provider="ark",
        model="seedance",
        capability_revision="test",
        input_hash="b" * 64,
        warnings_json=[],
        blockers_json=[],
    )

    class _Rows:
        def __init__(self, values: list[object]) -> None:
            self._values = values

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(self._values)

    class _RecordingSession:
        def __init__(self) -> None:
            self.entities: list[type[object]] = []

        def scalar(self, statement):  # type: ignore[no-untyped-def]
            entity = statement.column_descriptions[0]["entity"]
            self.entities.append(entity)
            return storyboard if entity is StoryboardRevision else plan

        def scalars(self, statement):  # type: ignore[no-untyped-def]
            entity = statement.column_descriptions[0]["entity"]
            self.entities.append(entity)
            return _Rows([])

    session = _RecordingSession()
    SqlAlchemyProductionRecipeRepository._lock_storyboard_execution_graph(
        session,  # type: ignore[arg-type]
        storyboard_revision_id=storyboard_id,
        generation_plan_id=plan_id,
    )

    assert session.entities == [
        StoryboardRevision,
        GenerationPlan,
        ShotBeat,
        GenerationClipShot,
        ShotCard,
    ]


def test_confirm_storyboard_production_plan_rolls_back_structure_when_plan_is_blocked(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="确认失败完整回滚",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            ProductionRecipeInstance(
                id=instance_id,
                production_run_id=project_id,
                recipe_key="healing_child_cat_v1",
                theme="找到纸飞机",
                target_duration_seconds=15,
                quality_tier="balanced",
                canon_profile_id="canon-v3",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="approved",
                title="找到纸飞机",
                logline="孩子和猫找到纸飞机。",
                synopsis="孩子和猫在院中找到纸飞机。",
                subject_ids_json=[],
                scene_plan_json=[],
                episode_rules_json={},
            )
        )
    saved = SqlAlchemyAigcCanvasRepository(storyboard_sessions).save_storyboard_plan(
        project_id,
        story_id=story_id,
        plan=StoryboardPlanOutput.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "找到纸飞机",
                        "direction": "孩子和猫在长椅旁找到纸飞机。",
                        "durationSeconds": 15,
                    }
                ]
            }
        ),
        durations=(15,),
        prompt_id=uuid.uuid4(),
        input_bindings=[],
    )
    storyboard_id = uuid.UUID(saved["storyboardRevisionId"])
    plan_id = uuid.UUID(saved["generationPlanId"])
    with storyboard_sessions.begin() as session:
        session.get(GenerationPlan, plan_id).blockers_json = ["Provider 不支持当前时长"]

    with pytest.raises(Exception, match="生成编排存在阻断"):
        SqlAlchemyProductionRecipeRepository(
            storyboard_sessions
        ).confirm_storyboard_production_plan(
            instance_id,
            SimpleNamespace(
                idempotency_key="confirm-blocked-plan",
                storyboard_revision_id=storyboard_id,
                storyboard_revision=int(saved["revision"]),
                structure_hash=saved["structureHash"],
                generation_plan_id=plan_id,
                generation_plan_revision=1,
                generation_plan_hash=saved["generationPlanHash"],
                reason=None,
            ),
        )

    with storyboard_sessions() as session:
        assert session.get(StoryboardRevision, storyboard_id).status == "draft"
        assert session.get(GenerationPlan, plan_id).status == "proposed"
        assert (
            session.scalar(
                select(ShotBeat.status).where(ShotBeat.storyboard_revision_id == storyboard_id)
            )
            == "ready"
        )
        assert list(session.scalars(select(HumanReviewDecisionRecord))) == []


def test_replacing_references_rehashes_current_plan_and_can_be_confirmed_again(
    storyboard_sessions: sessionmaker[Session],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    instance_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    (tmp_path / "shot-reference.png").write_bytes(b"shot-reference")
    with storyboard_sessions.begin() as session:
        session.add_all(
            [
                ProductionRun(
                    id=project_id,
                    title="替换引用后重新确认",
                    content_date=date.today(),
                    status="active",
                ),
                ProductionRecipeInstance(
                    id=instance_id,
                    production_run_id=project_id,
                    recipe_key="healing_child_cat_v1",
                    theme="窗边叶片",
                    target_duration_seconds=15,
                    quality_tier="balanced",
                    canon_profile_id="canon-v3",
                ),
                StoryRevisionRecord(
                    id=story_id,
                    production_run_id=project_id,
                    revision=1,
                    strategy="combined",
                    status="approved",
                    title="窗边叶片",
                    logline="女孩和猫咪观察叶片。",
                    synopsis="女孩和猫咪在窗边观察一片雨后叶片。",
                    subject_ids_json=[],
                    scene_plan_json=[],
                    episode_rules_json={},
                ),
                Asset(
                    id=asset_id,
                    production_run_id=project_id,
                    role="shot_reference",
                    scope="project",
                    status="approved",
                    media_type="image",
                    storage_key="shot-reference.png",
                    sha256="d" * 64,
                    byte_size=14,
                    metadata_json={},
                ),
            ]
        )
    canvas_repository = SqlAlchemyAigcCanvasRepository(
        storyboard_sessions,
        asset_root=tmp_path,
    )
    # SQLite cannot emulate PostgreSQL's non-PK Identity column on CanvasEvent;
    # keep the repository transaction real while excluding that dialect-only audit insert.
    monkeypatch.setattr(canvas_repository, "_record_event", lambda *_args, **_kwargs: None)
    saved = canvas_repository.save_storyboard_plan(
        project_id,
        story_id=story_id,
        plan=StoryboardPlanOutput.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "观察叶片",
                        "direction": "女孩与猫咪在窗边观察叶片上的水珠。",
                        "durationSeconds": 15,
                    }
                ]
            }
        ),
        durations=(15,),
        prompt_id=uuid.uuid4(),
        input_bindings=[],
    )
    storyboard_id = uuid.UUID(saved["storyboardRevisionId"])
    plan_id = uuid.UUID(saved["generationPlanId"])
    with storyboard_sessions() as session:
        beat_id = session.scalar(
            select(ShotBeat.id).where(ShotBeat.storyboard_revision_id == storyboard_id)
        )
    assert beat_id is not None
    recipe_repository = SqlAlchemyProductionRecipeRepository(storyboard_sessions)
    recipe_repository.confirm_storyboard_production_plan(
        instance_id,
        SimpleNamespace(
            idempotency_key="before-reference-change",
            storyboard_revision_id=storyboard_id,
            storyboard_revision=1,
            structure_hash=saved["structureHash"],
            generation_plan_id=plan_id,
            generation_plan_revision=1,
            generation_plan_hash=saved["generationPlanHash"],
            reason=None,
        ),
    )

    canvas_repository.replace_shot_beat_references(
        beat_id,
        expected_revision=1,
        payload=ShotBeatReferenceBindingsRequest.model_validate(
            {
                "bindings": [
                    {
                        "assetId": str(asset_id),
                        "semanticRole": "composition",
                        "instruction": "保持女孩与猫咪在窗边的相对位置",
                        "ordinal": 1,
                    }
                ]
            }
        ),
    )

    projected = recipe_repository.get_instance(instance_id)
    assert projected["storyboard"]["structureHash"] != saved["structureHash"]
    assert projected["generationPlan"]["inputHash"] != saved["generationPlanHash"]
    assert projected["storyboard"]["status"] == "draft"
    assert projected["generationPlan"]["status"] == "proposed"
    assert projected["editorialShots"][0]["referenceBindings"][0]["assetId"] == str(asset_id)
    with storyboard_sessions() as session:
        assert len(list(session.scalars(select(HumanReviewDecisionRecord)))) == 2

    result = recipe_repository.confirm_storyboard_production_plan(
        instance_id,
        SimpleNamespace(
            idempotency_key="after-reference-change",
            storyboard_revision_id=storyboard_id,
            storyboard_revision=1,
            structure_hash=projected["storyboard"]["structureHash"],
            generation_plan_id=plan_id,
            generation_plan_revision=1,
            generation_plan_hash=projected["generationPlan"]["inputHash"],
            reason=None,
        ),
    )
    assert result["status"] == "approved"
    with storyboard_sessions() as session:
        assert len(list(session.scalars(select(HumanReviewDecisionRecord)))) == 4


def test_manual_storyboard_keeps_legacy_uncovered_scene_as_warning(
    storyboard_sessions: sessionmaker[Session],
) -> None:
    project_id = uuid.uuid4()
    story_id = uuid.uuid4()
    with storyboard_sessions.begin() as session:
        session.add(
            ProductionRun(
                id=project_id,
                title="人工分镜场景仅提示",
                content_date=date.today(),
                status="active",
            )
        )
        session.add(
            StoryRevisionRecord(
                id=story_id,
                production_run_id=project_id,
                revision=1,
                strategy="combined",
                status="approved",
                title="院中故事",
                logline="主要动作发生在院中。",
                synopsis="旧数据预先拆出两个场景。",
                subject_ids_json=[],
                scene_plan_json=[
                    {
                        "sceneKey": "yard",
                        "title": "院中",
                        "purpose": "主要动作",
                        "synopsis": "找到纸飞机。",
                        "durationWeight": 1,
                        "continuity": {},
                    },
                    {
                        "sceneKey": "porch",
                        "title": "廊下",
                        "purpose": "旧版备用",
                        "synopsis": "当前分镜不使用。",
                        "durationWeight": 1,
                        "continuity": {},
                    },
                ],
                episode_rules_json={},
            )
        )

    document = SqlAlchemyAigcCanvasRepository(storyboard_sessions).save_manual_storyboard(
        project_id,
        expected_revision=0,
        payload=ManualStoryboardDraftRequest.model_validate(
            {
                "shots": [
                    {
                        "order": 1,
                        "title": "院中找到纸飞机",
                        "direction": "孩子和猫在院中找到纸飞机。",
                        "durationSeconds": 15,
                    }
                ]
            }
        ),
    )

    assert [item["code"] for item in document["diagnostics"]] == ["storyboard_scene_uncovered"]
