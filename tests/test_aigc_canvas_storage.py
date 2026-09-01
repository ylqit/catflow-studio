from __future__ import annotations

import uuid
from types import SimpleNamespace

from cat_video_generator.domain.aigc_canvas import CanvasNodeType
from cat_video_generator.infrastructure.db.aigc_canvas_repository import (
    _active_track_id,
    _apply_canvas_layout_hints,
    _apply_canvas_node_archive_projection,
    _apply_canvas_workflow_step_projection,
    _compile_storyboard_prompt_text,
    _edge_disconnect_policy,
    _preset_subject_targets_node,
)
from cat_video_generator.infrastructure.db.models import SCHEMA_NAME, Base, CanvasGraphNode
from cat_video_generator.infrastructure.db.session import ALEMBIC_HEAD


def test_video_workbench_active_track_is_always_a_real_shot_track() -> None:
    waiting_id = uuid.uuid4()
    selected_id = uuid.uuid4()

    active_track_id = _active_track_id(
        [
            SimpleNamespace(id=waiting_id, selected_video_asset_id=None),
            SimpleNamespace(id=selected_id, selected_video_asset_id=uuid.uuid4()),
        ]
    )

    assert active_track_id == str(selected_id)


def test_canvas_v2_schema_contains_domain_truth_tables() -> None:
    expected = {
        "story_briefs",
        "subjects",
        "subject_revisions",
        "subject_references",
        "story_revisions",
        "story_scores",
        "scene_subject_bindings",
        "shot_beats",
        "shot_subject_states",
        "canvas_layouts",
        "provider_capabilities",
        "generation_attempts",
        "canvas_graph_nodes",
        "canvas_graph_edges",
        "canvas_events",
        "media_generation_batches",
        "video_edit_recipes",
        "video_edit_annotations",
        "video_edit_references",
        "subject_completion_runs",
        "node_generation_configs",
        "canvas_recovery_points",
        "canvas_node_archives",
    }

    assert expected <= {
        table.name
        for table in Base.metadata.tables.values()
        if table.schema == SCHEMA_NAME
    }


def test_workflow_steps_have_durable_lease_and_recovery_columns() -> None:
    columns = Base.metadata.tables[f"{SCHEMA_NAME}.workflow_steps"].columns

    assert {
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "next_retry_at",
        "request_hash",
        "retry_chain_json",
    } <= set(columns.keys())


def test_canvas_v2_is_enabled_per_project() -> None:
    columns = Base.metadata.tables[f"{SCHEMA_NAME}.production_runs"].columns

    assert {
        "canvas_v2_enabled",
        "universal_canvas_enabled",
        "product_ad_template_enabled",
        "video_edit_v2_enabled",
    } <= set(columns.keys())


def test_prompt_records_have_full_audit_columns() -> None:
    columns = Base.metadata.tables[f"{SCHEMA_NAME}.prompt_records"].columns

    assert {
        "call_purpose",
        "node_id",
        "business_object_type",
        "business_object_id",
        "parent_prompt_id",
        "template_name",
        "template_version",
        "system_prompt",
        "user_prompt",
        "final_prompt",
        "provider_request_json",
        "provider_internal_transform",
        "input_snapshot_json",
        "raw_response_json",
        "structured_response_json",
        "accepted_response_json",
        "response_diff_json",
        "parameters_json",
        "token_usage_json",
        "cost_micros",
        "duration_ms",
        "status",
        "error_json",
        "input_hash",
        "output_hash",
        "completed_at",
    } <= set(columns.keys())


def test_canvas_v2_migration_is_current_head() -> None:
    assert ALEMBIC_HEAD == "0031_workflow_task_cancellation"


def test_storyboard_prompt_compiler_keeps_reference_layers_and_exclusions_separate() -> None:
    profile = SimpleNamespace(
        person_identity="固定儿童脸部身份",
        person_hair="固定短发",
        person_body="儿童全身比例",
        cat_identity="固定橘猫身份",
        style_positive_json=["细腻柔和的数字插画材质", "克制轮廓线"],
        style_negative_json=["摄影写实", "绿色污染"],
    )
    story = SimpleNamespace(
        episode_rules_json={
            "wardrobe": "黄色雨衣",
            "catBehaviorMode": "natural",
            "soundPlan": {"dialogue": False},
        }
    )
    scene = SimpleNamespace(
        title="雨后小院",
        source_text="孩子和猫咪发现一片发亮的叶子",
        context_note=(
            '{"continuity":{"location":"小院","weather":"雨后",'
            '"props":["木凳","发亮的叶子"]}}'
        ),
    )
    shot = {
        "order": 1,
        "title": "发现亮叶",
        "durationSeconds": 10,
        "action": "孩子蹲下，猫咪自然靠近叶子",
        "shotSize": "中景",
        "lighting": "雨后柔光",
        "camera": "缓慢推近",
        "soundEffect": "雨滴与猫咪脚步声",
        "temporalBeats": [
            {"label": "开始", "action": "孩子蹲下"},
            {"label": "变化", "action": "叶子反光"},
            {"label": "收尾", "action": "孩子和猫咪安静观看"},
        ],
    }
    bindings = [
        {
            "role": "identity",
            "purpose": "person_identity",
            "semanticKey": "person:headshot",
            "title": "儿童面部身份",
        },
        {
            "role": "environment",
            "purpose": "scene_look",
            "semanticKey": "scene:rainy-yard",
            "title": "雨后小院 Scene Look",
        },
    ]

    prompt = _compile_storyboard_prompt_text(
        profile=profile,
        story=story,
        scene=scene,
        shot=shot,
        reference_bindings=bindings,
        healing_recipe=True,
    )

    assert "身份连续性" in prompt
    assert "本集与场景" in prompt
    assert "镜头正文" in prompt
    assert "运动与声音" in prompt
    assert "参考职责" in prompt
    assert "连续性与排除项" in prompt
    assert "@图片1「儿童面部身份」" in prompt
    assert "@图片2「雨后小院 Scene Look」" in prompt
    assert '"role": "identity"' not in prompt
    assert '"role": "environment"' not in prompt
    assert "跨场景环境与道具串用" in prompt
    assert "猫咪出现人手、人形肢体" in prompt
    assert "未提供额外声音或对白要求" not in prompt
    assert "声音：雨滴与猫咪脚步声" in prompt
    assert "无对白，不做口型" not in prompt


def test_approved_story_scenes_and_compiled_prompts_have_version_pins() -> None:
    scene_columns = Base.metadata.tables[f"{SCHEMA_NAME}.scenes"].columns
    beat_columns = Base.metadata.tables[f"{SCHEMA_NAME}.shot_beats"].columns

    assert {"story_revision_id", "scene_key", "active", "stale_reason"} <= set(
        scene_columns.keys()
    )
    assert {"story_revision_id", "prompt_id", "temporal_beats_json"} <= set(
        beat_columns.keys()
    )


def test_storyboard_prompt_uses_direction_without_inventing_advanced_facts() -> None:
    profile = SimpleNamespace(
        person_identity="固定人物身份",
        person_hair="固定发型",
        person_body="固定比例",
        cat_identity="固定猫咪身份",
        style_positive_json=["原创柔和线条"],
        style_negative_json=["身份漂移"],
    )
    story = SimpleNamespace(episode_rules_json={})
    scene = SimpleNamespace(
        title="雨前小院",
        source_text="孩子和猫收起画纸",
        context_note=None,
    )

    prompt = _compile_storyboard_prompt_text(
        profile=profile,
        story=story,
        scene=scene,
        shot={
            "order": 1,
            "title": "收画",
            "direction": "孩子将画纸收进文件夹，猫咪停在一旁观察。",
            "action": "旧动作字段不应胜出。",
            "durationSeconds": 10,
            "dialogue": "要下雨了。",
        },
        reference_bindings=[],
        healing_recipe=True,
    )

    assert "孩子将画纸收进文件夹，猫咪停在一旁观察。" in prompt
    assert "旧动作字段不应胜出" not in prompt
    assert "对白：要下雨了。" in prompt
    assert "猫咪以已批准行为模式自然参与" not in prompt
    assert "固定机位" not in prompt
    assert "发生一个微小可见变化" not in prompt


def test_assets_can_belong_to_a_universal_canvas_node() -> None:
    columns = Base.metadata.tables[f"{SCHEMA_NAME}.assets"].columns

    assert "canvas_node_id" in columns


def test_archived_canvas_projection_filters_nodes_edges_and_group_members() -> None:
    canvas = {
        "nodes": [
            {
                "id": "candidate",
                "type": "StoryCandidateNode",
                "data": {"status": "candidate"},
                "availableActions": [],
            },
            {
                "id": "brief",
                "type": "BriefNode",
                "data": {"status": "ready"},
                "availableActions": [],
            },
        ],
        "edges": [
            {"sourceNodeId": "candidate", "targetNodeId": "brief"},
        ],
        "groups": [{"memberNodeIds": ["candidate", "brief"]}],
    }

    _apply_canvas_node_archive_projection(canvas, {"candidate"})

    assert [node["id"] for node in canvas["nodes"]] == ["brief"]
    assert canvas["edges"] == []
    assert canvas["groups"][0]["memberNodeIds"] == ["brief"]
    archive_action = next(
        action
        for action in canvas["nodes"][0]["availableActions"]
        if action["key"] == "archive_node"
    )
    assert archive_action["enabled"] is False
    assert "六阶段" in archive_action["disabledReason"]


def test_unapproved_story_candidate_can_be_archived_but_approved_story_cannot() -> None:
    canvas = {
        "nodes": [
            {
                "id": "candidate",
                "type": "StoryCandidateNode",
                "data": {"status": "candidate"},
                "availableActions": [],
            },
            {
                "id": "approved",
                "type": "StoryCandidateNode",
                "data": {"status": "approved"},
                "availableActions": [],
            },
        ],
        "edges": [],
        "groups": [{"memberNodeIds": ["candidate", "approved"]}],
    }

    _apply_canvas_node_archive_projection(canvas, set())

    actions = {
        node["id"]: next(
            action for action in node["availableActions"] if action["key"] == "archive_node"
        )
        for node in canvas["nodes"]
    }
    assert actions["candidate"]["enabled"] is True
    assert actions["approved"]["enabled"] is False


def test_visual_preset_subject_edges_follow_character_design_slots() -> None:
    child = CanvasGraphNode(
        production_run_id=uuid.uuid4(),
        node_type=CanvasNodeType.CHARACTER_DESIGN.value,
        object_type="character_design_slot",
        status="pending",
        data_json={"slot": "child"},
    )
    cat = CanvasGraphNode(
        production_run_id=child.production_run_id,
        node_type=CanvasNodeType.CHARACTER_DESIGN.value,
        object_type="character_design_slot",
        status="pending",
        data_json={"slot": "cat"},
    )
    pair = CanvasGraphNode(
        production_run_id=child.production_run_id,
        node_type=CanvasNodeType.CHARACTER_DESIGN.value,
        object_type="character_design_slot",
        status="pending",
        data_json={"slot": "pair_scale"},
    )

    assert _preset_subject_targets_node("protagonist", child) is True
    assert _preset_subject_targets_node("co_protagonist", child) is False
    assert _preset_subject_targets_node("protagonist", cat) is False
    assert _preset_subject_targets_node("co_protagonist", cat) is True
    assert _preset_subject_targets_node("protagonist", pair) is True
    assert _preset_subject_targets_node("co_protagonist", pair) is True


def test_layout_hints_place_canon_inputs_and_six_stages_in_stable_lanes() -> None:
    canvas = {
        "nodes": [
            {
                "id": "style",
                "type": "StylePresetNode",
                "objectType": "visual_preset",
                "data": {"title": "线条材质"},
            },
            {
                "id": "cat",
                "type": "SubjectNode",
                "objectType": "subject",
                "data": {"role": "co_protagonist"},
            },
            {
                "id": "child",
                "type": "SubjectNode",
                "objectType": "subject",
                "data": {"role": "protagonist"},
            },
            {
                "id": "brief",
                "type": "BriefNode",
                "objectType": "story_brief",
                "data": {},
            },
            {
                "id": "director",
                "type": "StoryboardDirectorNode",
                "objectType": "storyboard",
                "data": {},
            },
            {
                "id": "timeline",
                "type": "TimelineNode",
                "objectType": "timeline",
                "data": {},
            },
        ]
    }

    _apply_canvas_layout_hints(canvas, positioned_node_ids={"cat"})

    by_id = {node["id"]: node for node in canvas["nodes"]}
    assert by_id["child"]["layoutHint"] == {
        "lane": "canon",
        "laneOrder": 0,
        "itemOrder": 0,
        "positioned": False,
        "stackKey": "canon_identity",
    }
    assert by_id["cat"]["layoutHint"]["itemOrder"] == 1
    assert by_id["cat"]["layoutHint"]["positioned"] is True
    assert "position" not in by_id["cat"]
    assert by_id["style"]["layoutHint"]["itemOrder"] == 2
    assert by_id["brief"]["layoutHint"]["lane"] == "creative"
    assert by_id["director"]["layoutHint"]["lane"] == "storyboard"
    assert by_id["timeline"]["layoutHint"]["lane"] == "export"
    assert by_id["child"]["position"] == {"x": 90, "y": 110}


def test_edge_disconnect_policy_protects_canon_and_allows_user_references() -> None:
    assert _edge_disconnect_policy(
        source_port="image_reference[]",
        target_port="image_reference[]",
        relation_type="canon_identity_reference",
    ) == (
        False,
        "该连线由 Canon 身份规则派生，需修改视觉档案，不能直接剪断",
    )

    assert _edge_disconnect_policy(
        source_port="media_reference[]",
        target_port="media_reference[]",
        relation_type="media_reference[]->media_reference[]",
    ) == (True, None)

    enabled, reason = _edge_disconnect_policy(
        source_port="brief",
        target_port="story_revision",
        relation_type="brief->story_revision",
    )
    assert enabled is False
    assert "业务血缘" in str(reason)


def test_canvas_workflow_projection_matches_exact_business_object_and_not_recipe_only() -> None:
    target_id = uuid.uuid4()
    unrelated_id = uuid.uuid4()
    recipe_id = uuid.uuid4()
    target_step = SimpleNamespace(
        id=uuid.uuid4(),
        input_snapshot_json={
            "recipeInstanceId": str(recipe_id),
            "businessObjectId": str(target_id),
            "phase": "story",
        },
        scene_id=None,
        shot_card_id=None,
        operation_key="recipe:story_script",
        status="running",
        progress_json={"percent": 48, "message": "正在扩写剧情脚本"},
    )
    unrelated_step = SimpleNamespace(
        id=uuid.uuid4(),
        input_snapshot_json={
            "recipeInstanceId": str(recipe_id),
            "businessObjectId": str(unrelated_id),
            "phase": "story",
        },
        scene_id=None,
        shot_card_id=None,
        operation_key="recipe:story_script",
        status="succeeded",
        progress_json={"percent": 100, "message": "另一个脚本已完成"},
    )
    canvas = {
        "nodes": [
            {
                "id": "script-node",
                "executionScope": {
                    "kind": "business_object",
                    "objectType": "story_event",
                    "recipeInstanceId": str(recipe_id),
                    "businessObjectId": str(target_id),
                    "operationKeys": ["recipe:story_script"],
                    "phases": ["story"],
                    "includeChildTasks": True,
                },
                "data": {},
            }
        ]
    }

    _apply_canvas_workflow_step_projection(canvas, [target_step, unrelated_step])

    assert canvas["nodes"][0]["workflowSteps"] == [
        {
            "key": str(target_step.id),
            "label": "扩写剧情脚本",
            "status": "running",
            "detail": "正在扩写剧情脚本 · 48%",
        }
    ]
