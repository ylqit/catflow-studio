"""Application ownership of production plans, unit generation and adoption."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from .job_execution import generation_request
from .production_compiler import compile_unit, unit_references
from .production_plan import (
    PRODUCTION_REVISION, ProductionPlanDraft, ProductionUnit, document_hash,
    group_shots, unit_design_hash, unit_shots, validate_units,
)
from .production_selection import (
    ProductionAssembly, UnitSelectionCommand, assemble_timeline,
    selection_can_continue, validate_selection,
)
from .service import (
    EditVersionDto, JobDto, StudioConflictError, StudioNotFoundError,
    VideoEditDraftDto,
)
from .shot_production import ShotTarget


class ProductionService:
    def __init__(self, studio, repository):
        self.studio = studio
        self.repository = repository

    def shot_reference_context(self, project_id, shot):
        """Resolve actual frame-making inputs from the active plan, including props."""
        plan = next((p for p in self.repository.list_production_plans(project_id) if p.active), None)
        if plan is None:
            return None
        unit = ProductionUnit(id="frame-preparation", shotIds=[shot.id])
        document = {**plan.document, "shots": [shot.model_dump(mode="json", by_alias=True)]}
        scene_key = shot.information.scene_key if shot.information else "main"
        scene = next((s for s in document["scenes"] if s["key"] == scene_key), None)
        if scene is None:
            raise StudioConflictError("请先为当前镜头绑定场景")
        used = set(shot.information.prop_keys if shot.information else [])
        props = [p for p in document["props"] if p["key"] in used]
        if used != {p["key"] for p in props}:
            raise StudioConflictError("请先为当前镜头绑定全部关键道具")
        refs = unit_references(document, unit)
        if len(refs) > self.studio.provider_runtime.maximum_video_references:
            raise StudioConflictError("首帧制作参考超出当前额度，请调整绑定")
        if any(self.studio.get_asset(uuid.UUID(r["assetId"])).sha256 != r["sha256"] for r in refs):
            raise StudioConflictError("首帧制作参考内容已变化")
        return {"references": refs, "initialDescription": "；".join([
            scene["name"], scene.get("axis", ""), *[
                f"{p['name']}：{p['identity']}；起点{p['initialState']}，位置{p['location']}" for p in props]]),
                "productionPlanId": str(plan.id)}

    def preview_plan(self, project_id, command: ProductionPlanDraft):
        project = self.repository.get_project(project_id)
        if project is None:
            raise StudioNotFoundError("project not found")
        plan = self.repository.active_shot_plan(project_id)
        story = self.repository.active_story(project_id)
        if (plan is None or plan.id != command.shot_plan_version_id or story is None
                or story.id != plan.source_story_version_id):
            raise StudioConflictError("故事或分镜版本已变化")
        if plan.source_selection_hash != self.studio.current_selection_hash(project_id):
            raise StudioConflictError("分镜参考已过期，请先确认当前分镜")
        canon = self.studio._require_complete_canon(project.canon_profile_id)
        fixed = [{"assetId": str(canon.fixed_assets[role].id),
                  "sha256": canon.fixed_assets[role].sha256, "role": role}
                 for role in ("episode_child", "episode_cat", "pair_scale", "style_board")]
        scenes = [scene.model_copy(deep=True) for scene in command.scenes]
        if not scenes:
            environment = self.repository.current_selections(project_id).get("environment")
            if environment is None:
                raise StudioConflictError("请先选择环境或绑定场景图片")
            from .production_plan import SceneBinding
            scenes = [SceneBinding(key="main", name="本集场景", assetId=environment.id)]
        props = [prop.model_copy(deep=True) for prop in command.props]
        for binding in [*scenes, *props]:
            asset = self.studio.get_asset(binding.asset_id)
            if asset.media_type != "image" or asset.role == "style_source":
                raise StudioConflictError("场景与道具必须使用可提交的图片资产")
            if binding.sha256 is not None and binding.sha256 != asset.sha256:
                raise StudioConflictError("资产哈希与预览不一致")
            binding.sha256 = asset.sha256
        for shot in plan.shots:
            scene_key = shot.information.scene_key if shot.information else "main"
            if scene_key not in {s.key for s in scenes}:
                raise StudioConflictError(f"镜头{shot.id}缺少场景图片：{scene_key}")
            missing_props = set(shot.information.prop_keys if shot.information else []) - {p.key for p in props}
            if missing_props:
                raise StudioConflictError(f"镜头{shot.id}缺少道具图片：" + "、".join(sorted(missing_props)))
        units = command.units or group_shots(
            plan.shots, scenes, props, fixed, self.studio.provider_runtime.maximum_video_references)
        validate_units(plan.shots, units)
        document = {
            "format": PRODUCTION_REVISION, "projectId": str(project_id),
            "parentPlanId": str(command.expected_active_plan_id) if command.expected_active_plan_id else None,
            "storyVersionId": str(story.id), "shotPlanVersionId": str(plan.id),
            "sourceSelectionHash": plan.source_selection_hash,
            "targetDurationFrames": project.target_duration_seconds * 24,
            "canon": {"id": str(canon.id), "hash": canon.profile_hash,
                      "catIdentity": canon.cat_identity_prompt, "references": fixed},
            "narrativeDesign": story.narrative_design.model_dump(mode="json", by_alias=True)
            if story.narrative_design else None,
            "shots": [s.model_dump(mode="json", by_alias=True) for s in plan.shots],
            "scenes": [s.model_dump(mode="json", by_alias=True) for s in scenes],
            "props": [p.model_dump(mode="json", by_alias=True) for p in props],
            "units": [u.model_dump(mode="json", by_alias=True) for u in units],
        }
        for unit in units:
            references = unit_references(document, unit)
            count = len(references) + int(unit.continuity == "inherit")
            if unit.generation_mode == "references" and count > self.studio.provider_runtime.maximum_video_references:
                raise StudioConflictError(f"{unit.id}需要{count}张参考图（含承接证据），超过当前能力；请减少关联资产")
        return {"document": document, "inputHash": document_hash(document),
                "unitCount": len(units), "paidTaskCount": len(units)}

    def create_plan(self, project_id, command: ProductionPlanDraft):
        request_hash = document_hash({"projectId": str(project_id), **command.model_dump(
            mode="json", by_alias=True, exclude={"idempotency_key"})})
        existing = self.repository.production_plan_by_key(command.idempotency_key)
        if existing:
            if existing.request_hash != request_hash:
                raise StudioConflictError("幂等键已用于不同生产计划")
            return existing
        preview = self.preview_plan(project_id, command)
        return self.repository.save_production_plan(
            project_id, command, preview["document"], request_hash)

    def require_plan(self, project_id, plan_id):
        plan = self.repository.get_production_plan(plan_id)
        if plan is None or plan.project_id != project_id:
            raise StudioNotFoundError("production plan not found")
        current = self.repository.active_shot_plan(project_id)
        story = self.repository.active_story(project_id)
        if (not plan.active or current is None or str(current.id) != plan.document["shotPlanVersionId"]
                or story is None or str(story.id) != plan.document["storyVersionId"]):
            raise StudioConflictError("生产计划或来源已过期，请刷新")
        return plan

    def unit_context(self, project_id, unit_id, plan_id):
        plan = self.require_plan(project_id, plan_id)
        units = [ProductionUnit.model_validate(u) for u in plan.document["units"]]
        index = next((i for i, u in enumerate(units) if u.id == unit_id), None)
        if index is None:
            raise StudioNotFoundError("production unit not found")
        unit = units[index]
        upstream = None
        cursor = index
        while cursor > 0 and units[cursor].continuity == "inherit":
            previous = self.repository.active_unit_selection(project_id, units[cursor - 1].id)
            if (previous is None or not selection_can_continue(previous)
                    or previous.document["unitDesignHash"] != unit_design_hash(plan.document, units[cursor - 1])):
                raise StudioConflictError("请先采用上游候选并确认关键实际结束状态")
            if cursor == index:
                upstream = previous
            ancestor = (self.repository.active_unit_selection(project_id, units[cursor - 2].id)
                        if cursor > 1 and units[cursor - 1].continuity == "inherit" else None)
            if previous.document["upstreamSelectionHash"] != (ancestor.input_hash if ancestor else None):
                raise StudioConflictError("上游选片的承接依据已变化，请重新确认")
            cursor -= 1
        return plan, unit, upstream

    def preview_unit(self, project_id, unit_id, plan_id):
        plan, unit, upstream = self.unit_context(project_id, unit_id, plan_id)
        runtime = self.studio.provider_runtime
        references = unit_references(plan.document, unit)
        if upstream and unit.generation_mode == "references":
            evidence = self.studio.get_asset(uuid.UUID(upstream.document["endState"]["evidenceAssetId"]))
            references.append({"assetId": str(evidence.id), "sha256": evidence.sha256,
                               "role": "continuity_end", "duties": ["continuity_end"]})
        upstream_references = references
        if unit.generation_mode == "from_frame":
            shot = unit_shots(plan.document, unit)[0]
            context = self.studio.shot_production_context(project_id, ShotTarget(
                shotPlanVersionId=uuid.UUID(plan.document["shotPlanVersionId"]), shotId=shot.id))
            if not context["frameCurrent"]:
                raise StudioConflictError("请先确认当前设计的镜头首帧")
            expected = {(r["assetId"], r["sha256"]) for r in upstream_references}
            actual = {(r["assetId"], r["sha256"]) for r in context["references"]}
            if expected != actual:
                raise StudioConflictError("首帧确认的参考与当前单元不一致，请用当前场景和道具重新确认首帧")
            frame = context["shot"]["confirmedFrame"] if "shot" in context else shot.confirmed_frame.model_dump(
                mode="json", by_alias=True)
            references = [{"assetId": str(frame["assetId"]), "sha256": frame["sha256"],
                           "role": "first_frame", "duties": ["first_frame"]}]
        if len(references) > runtime.maximum_video_references:
            raise StudioConflictError("单元实际参考超过当前模型额度")
        for reference in references:
            if self.studio.get_asset(uuid.UUID(reference["assetId"])).sha256 != reference["sha256"]:
                raise StudioConflictError("实际参考资产已变化")
        compiled = compile_unit(plan.document, unit, references=references,
                                incoming=upstream.document["endState"] if upstream else None)
        frozen = {
            **compiled, "purpose": "production_unit", "role": "production_unit_video",
            "productionRevision": PRODUCTION_REVISION, "productionPlanId": str(plan.id),
            "productionPlanHash": plan.input_hash, "productionUnitId": unit.id,
            "unitDesignHash": unit_design_hash(plan.document, unit),
            "unitContentHash": unit_design_hash(plan.document, unit, edit_independent=True),
            "shotPlanVersionId": plan.document["shotPlanVersionId"],
            "storyVersionId": plan.document["storyVersionId"], "projectId": str(project_id),
            "canonProfileId": plan.document["canon"]["id"],
            "canonProfileHash": plan.document["canon"]["hash"],
            "upstreamSelectionId": str(upstream.id) if upstream else None,
            "upstreamSelectionHash": upstream.input_hash if upstream else None,
            "references": references, "upstreamReferences": upstream_references,
            "referenceAssetIds": [r["assetId"] for r in references],
            "referenceSha256": [r["sha256"] for r in references],
            "referenceRoles": [r["role"] for r in references],
            "provider": runtime.provider, "model": runtime.video_model,
            "capabilityRevision": runtime.capability_revision, "providerPromptVersion": 1,
            "promptCompilerRevision": PRODUCTION_REVISION, "generationMode": unit.generation_mode,
            "resolution": "480p", "aspectRatio": "9:16", "frameRate": 24, "generateAudio": True,
        }
        return {**frozen, "inputHash": document_hash(frozen),
                "expectedCostMicros": None, "costEstimateStatus": "unmetered_paid"}

    @generation_request
    def generate_unit(self, project_id, unit_id, command):
        existing = next((j for j in self.repository.list_project_jobs(project_id)
                         if j.idempotency_key == command.idempotency_key), None)
        if existing:
            if (existing.input_hash != command.expected_input_hash
                    or existing.frozen_input.get("productionUnitId") != unit_id):
                raise StudioConflictError("幂等键已用于不同单元输入")
            return existing
        preview = self.preview_unit(project_id, unit_id, command.plan_id)
        if preview["inputHash"] != command.expected_input_hash:
            raise StudioConflictError("单元输入已变化，请重新预览")
        self.studio._require_paid_calls_enabled()
        now = datetime.now(UTC)
        return self.studio._create_job(JobDto(
            id=uuid.uuid4(), projectId=project_id, kind="generate_video", status="queued",
            inputHash=preview["inputHash"], idempotencyKey=command.idempotency_key,
            provider=preview["provider"], model=preview["model"], expectedCostMicros=None,
            frozenInput=preview, resultAssetIds=[], createdAt=now, updatedAt=now,
        ))

    def select_unit(self, project_id, unit_id, command: UnitSelectionCommand):
        request_hash = document_hash({"projectId": str(project_id), "unitId": unit_id,
                                      **command.model_dump(mode="json", by_alias=True, exclude={"idempotency_key"})})
        previous = self.repository.unit_selection_by_key(command.idempotency_key)
        if previous:
            if previous.request_hash != request_hash:
                raise StudioConflictError("幂等键已用于不同选片")
            return previous
        plan, unit, upstream = self.unit_context(project_id, unit_id, command.plan_id)
        design_hash = unit_design_hash(plan.document, unit)
        if design_hash != command.expected_design_hash:
            raise StudioConflictError("单元设计已变化")
        asset = self.studio.get_asset(command.asset_id)
        if (asset.project_id != project_id or asset.media_type != "video"
                or (asset.metadata.get("unitDesignHash") != design_hash
                    and asset.metadata.get("unitContentHash") != unit_design_hash(plan.document, unit,
                                                                                  edit_independent=True))):
            raise StudioConflictError("候选不属于当前单元设计")
        if asset.producing_job_id is None:
            raise StudioConflictError("单元候选缺少来源任务")
        job = self.studio.get_job(asset.producing_job_id)
        upstream_hash = upstream.input_hash if upstream else None
        if job.frozen_input.get("upstreamSelectionHash") != upstream_hash:
            raise StudioConflictError("候选的上游状态已过期")
        evidence = self.studio.get_asset(command.end_state.evidence_asset_id)
        if (evidence.project_id != project_id or evidence.media_type != "image"
                or evidence.metadata.get("sourceVideoAssetId") != str(asset.id)
                or evidence.metadata.get("sourceVideoSha256") != asset.sha256
                or evidence.metadata.get("sourceFrame") != command.end_state.evidence_frame):
            raise StudioConflictError("结束帧证据不属于当前候选取用点，请先本地提取")
        events = validate_selection(plan.document, unit, command, asset)
        document = {**command.model_dump(mode="json", by_alias=True, exclude={"idempotency_key"}),
                    "unitDesignHash": design_hash, "assetSha256": asset.sha256,
                    "producingJobId": str(job.id), "events": events,
                    "upstreamSelectionHash": upstream_hash,
                    "upstreamSelectionId": str(upstream.id) if upstream else None}
        return self.repository.save_unit_selection(project_id, unit_id, command, document, request_hash)

    def prepare_evidence(self, project_id, unit_id, command):
        plan, unit, _ = self.unit_context(project_id, unit_id, command.plan_id)
        asset = self.studio.get_asset(command.asset_id)
        if (asset.project_id != project_id or asset.media_type != "video"
                or (asset.metadata.get("unitDesignHash") != unit_design_hash(plan.document, unit)
                    and asset.metadata.get("unitContentHash") != unit_design_hash(plan.document, unit,
                                                                                  edit_independent=True))
                or command.source_frame >= asset.metadata.get("durationFrames", 0)):
            raise StudioConflictError("证据帧必须来自本单元有效候选")
        frozen = {"purpose": "unit_evidence", "productionUnitId": unit_id,
                  "sourceVideoAssetId": str(asset.id), "sourceVideoSha256": asset.sha256,
                  "sourceFrame": command.source_frame}
        fingerprint = document_hash(frozen)
        now = datetime.now(UTC)
        return self.studio._create_job(JobDto(
            id=uuid.uuid4(), projectId=project_id, kind="extract_continuity_frames", status="queued",
            inputHash=fingerprint, idempotencyKey="unit-evidence:" + fingerprint,
            provider="local_ffmpeg", model="ffmpeg-frame-v1", expectedCostMicros=0,
            frozenInput=frozen, resultAssetIds=[], createdAt=now, updatedAt=now))

    def assemble(self, project_id, plan_id, command: ProductionAssembly):
        plan = self.require_plan(project_id, plan_id)
        selections = []
        for raw in plan.document["units"]:
            unit = ProductionUnit.model_validate(raw)
            preview = self.preview_unit(project_id, unit.id, plan_id)
            selection = self.repository.active_unit_selection(project_id, unit.id)
            if (selection is None or selection.document["disposition"] == "rejected"
                    or selection.input_hash != command.expected_selection_hashes.get(unit.id)
                    or selection.document["unitDesignHash"] != preview["unitDesignHash"]
                    or selection.document["upstreamSelectionHash"] != preview["upstreamSelectionHash"]):
                raise StudioConflictError("单元尚未采用或选片已变化")
            selections.append(selection)
        timeline = assemble_timeline(selections, plan_id=plan.id)
        if timeline.total_frames != plan.document["targetDurationFrames"]:
            raise StudioConflictError("组装帧数与作品目标不一致")
        now, draft_id, edit_id = datetime.now(UTC), uuid.uuid4(), uuid.uuid4()
        fingerprint = document_hash({"planId": str(plan_id), **command.model_dump(
            mode="json", by_alias=True, exclude={"idempotency_key"})})
        edit = EditVersionDto(id=edit_id, projectId=project_id, revision=1,
                              sourceSelectionHash=fingerprint, edl=timeline, status="draft", formatVersion=3,
                              active=False, timelineHash=document_hash(timeline.model_dump(mode="json", by_alias=True)),
                              editDraftId=draft_id, createdAt=now)
        draft = VideoEditDraftDto(id=draft_id, projectId=project_id,
                                  sourceVideoAssetId=timeline.root_video_asset_id, headEditVersionId=edit_id,
                                  references=[*plan.document["canon"]["references"], *[
                                      {"role": "environment" if len(plan.document["scenes"]) == 1 else "scene:" + s[
                                          "key"],
                                       "assetId": s["assetId"], "sha256": s["sha256"]} for s in
                                      plan.document["scenes"]], *[
                                      {"role": "prop:" + p["key"], "assetId": p["assetId"], "sha256": p["sha256"]}
                                      for p in plan.document["props"]]], referencesConfirmed=True,
                                  inputHash=fingerprint, idempotencyKey=command.idempotency_key, createdAt=now)
        return self.repository.create_video_edit_draft(draft, edit, production_guard={
            "planId": plan.id, "selections": command.expected_selection_hashes})
