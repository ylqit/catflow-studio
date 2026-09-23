"""Optional prop image preparation using the existing image job lifecycle."""
import uuid
from datetime import UTC, datetime

from pydantic import Field

from catflow.domain.contract import ContractModel
from .job_execution import PaidJobCommand, generation_request, replacing_unknown
from .media_prompt import compile_provider_media_prompt
from .production_plan import PRODUCTION_REVISION, document_hash
from .service import JobDto, StudioConflictError


class PropImageInput(ContractModel):
    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    appearance: str = Field(min_length=1, max_length=1500)


class PropImageGeneration(PropImageInput, PaidJobCommand):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


def prop_image_preview(studio, project_id, command: PropImageInput):
    project = studio._require_project(project_id)
    canon = studio._require_complete_canon(project.canon_profile_id)
    style = canon.fixed_assets["style_board"]
    prompt = (f"为本片制作道具参考图：{command.name}。外观要求：{command.appearance}。"
              "单一道具实例，完整入画，浅中性背景，清晰可见材质与轮廓。"
              "使用参考画风的柔和数字插画、哑光色块与暖灰细线，不复制画风板中的物品。"
              "不含人、猫、文字、水印，不添加额外道具。")
    negative = "人物、动物、重复道具、文字、水印、照片写实、三维玩具"
    frozen = {"purpose": "production_prop", "role": "prop", "productionPropKey": command.key,
              "propInput": command.model_dump(mode="json", by_alias=True),
              "prompt": prompt, "negativePrompt": negative,
              "compiledProviderPrompt": compile_provider_media_prompt(prompt=prompt, negative_prompt=negative,
                                                                      reference_roles=("style_board",)),
              "providerPromptVersion": 1,
              "referenceAssetIds": [str(style.id)], "referenceRoles": ["style_board"],
              "referenceSha256": [style.sha256], "canonProfileId": str(canon.id),
              "canonProfileHash": canon.profile_hash, "promptCompilerRevision": PRODUCTION_REVISION,
              "provider": studio.provider_runtime.provider, "model": studio.provider_runtime.image_model,
              "capabilityRevision": studio.provider_runtime.capability_revision}
    return {**frozen, "inputHash": document_hash(frozen), "costEstimateStatus": "unmetered_paid"}


@generation_request
def generate_prop_image(studio, project_id, command: PropImageGeneration):
    existing = next((j for j in studio._repository.list_project_jobs(project_id)
                     if j.idempotency_key == command.idempotency_key), None)
    if existing:
        if existing.input_hash != command.expected_input_hash:
            raise StudioConflictError("幂等键已用于不同道具输入")
        return existing
    if any(j.frozen_input.get("productionPropKey") == command.key
           and j.status not in {"succeeded", "failed", "cancelled"} and not replacing_unknown(j.id)
           for j in studio._repository.list_project_jobs(project_id)):
        raise StudioConflictError("此道具仍有运行或提交未知任务，请先恢复")
    preview = prop_image_preview(studio, project_id, PropImageInput(
        key=command.key, name=command.name, appearance=command.appearance))
    if preview["inputHash"] != command.expected_input_hash:
        raise StudioConflictError("道具输入已变化，请重新预览")
    studio._require_paid_calls_enabled()
    now = datetime.now(UTC)
    return studio._create_job(JobDto(id=uuid.uuid4(), projectId=project_id, kind="generate_image",
                                     status="queued", inputHash=preview["inputHash"],
                                     idempotencyKey=command.idempotency_key,
                                     provider=preview["provider"], model=preview["model"], expectedCostMicros=None,
                                     frozenInput=preview, resultAssetIds=[], createdAt=now, updatedAt=now))
