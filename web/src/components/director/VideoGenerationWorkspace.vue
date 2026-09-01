<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api, assetContentUrl } from "../../api/client";
import type {
  PersistentTaskDto,
  ProductionBoardDto,
  ProviderInputMode,
  ReferenceBinding,
  ReferenceRole,
  ShotDto,
  ShotGenerationWorkspaceDto,
  ShotPromptPreview,
  ShotPromptReference,
  VisualAssetVersion,
} from "../../api/types";
import { refreshRuntimeStatus, useRuntimeStatus } from "../../runtimeStatus";
import { registerTask, useTaskCenter } from "../../tasks/taskCenter";
import CanvasReviewDialog from "../canvas/CanvasReviewDialog.vue";
import type { DirectorDirtyRegistration } from "./directorDirtyState";
import VideoTaskStatus from "./VideoTaskStatus.vue";

type LoadState = "loading" | "ready" | "stale" | "error";
type RightPanel = "versions" | "tasks" | "audit";

const props = withDefaults(defineProps<{
  projectId: string;
  focusedItemId?: string;
  panel?: string;
}>(), { focusedItemId: "", panel: "main" });
const emit = defineEmits<{ "dirty-change": [registration?: DirectorDirtyRegistration] }>();
const router = useRouter();
const runtimeStatus = useRuntimeStatus();
const taskCenter = useTaskCenter();

const board = ref<ProductionBoardDto>();
const workspace = ref<ShotGenerationWorkspaceDto>();
const tasks = ref<PersistentTaskDto[]>([]);
const selectedShotId = ref("");
const selectedVersionId = ref("");
const rightPanel = ref<RightPanel>(props.panel === "tasks" ? "tasks" : props.panel === "history" ? "versions" : "versions");
const loadState = ref<LoadState>("loading");
const error = ref("");
const busy = ref("");
const pendingGenerate = ref<{
  preview: ShotPromptPreview;
  regenerate: boolean;
  reason: string;
  runtimeRevision: number;
}>();
const providerModeDraft = ref<ProviderInputMode>("reference_media");
const referencePickerAssetId = ref("");
const showHistoricalShots = ref(false);
let requestSequence = 0;

const scenes = computed(() => board.value?.projectGraph.scenes ?? []);
const shots = computed(() => scenes.value.flatMap((scene) => scene.shots.map((shot) => ({ shot, scene }))));
const currentSequenceShotIds = computed(() => {
  const sequence = board.value?.projectGraph.sequences.find(
    (item) => item.id === board.value?.projectGraph.project.selectedSequenceId,
  );
  return new Set((sequence?.plan.clips ?? []).map((clip) => clip.shot_card_id));
});
const currentShots = computed(() => {
  const ids = currentSequenceShotIds.value;
  return ids.size ? shots.value.filter(({ shot }) => ids.has(shot.id)) : shots.value;
});
const historicalShots = computed(() => {
  const ids = new Set(currentShots.value.map(({ shot }) => shot.id));
  return shots.value.filter(({ shot }) => !ids.has(shot.id));
});
const displayedShots = computed(() => (
  showHistoricalShots.value ? [...currentShots.value, ...historicalShots.value] : currentShots.value
));
const selectedShot = computed(() => workspace.value?.shot ?? shots.value.find((item) => item.shot.id === selectedShotId.value)?.shot);
const currentScene = computed(() => shots.value.find((item) => item.shot.id === selectedShotId.value)?.scene ?? workspace.value?.scene);
const preview = computed(() => workspace.value?.videoPreview);
const references = computed(() => preview.value?.actualInputs ?? []);
const versions = computed(() => workspace.value?.videoVersions ?? []);
const selectedVersion = computed(() => versions.value.find((item) => item.id === selectedVersionId.value)
  ?? versions.value.find((item) => item.id === selectedShot.value?.selectedVideoAssetId)
  ?? [...versions.value].reverse()[0]);
const shotTasks = computed(() => tasks.value.filter((task) => (
  task.shotId === selectedShotId.value
  && ["video:shot", "video:range-edit"].includes(task.operationKey)
)));
const activeVideoTask = computed(() => shotTasks.value.find((task) => !["succeeded", "failed", "cancelled"].includes(task.status)));
const unsafeStyleSource = computed(() => references.value.find((item) => (
  /style_source|leaf_material/i.test(`${item.purpose ?? ""} ${item.displayName} ${item.responsibility}`)
)));
const providerBlockers = computed(() => [...new Set([
  ...(workspace.value?.blockers ?? []),
  ...(preview.value?.blockers ?? []),
  ...(unsafeStyleSource.value ? ["style_source 只能用于画风提炼，不能提交视频 Provider"] : []),
])]);
const providerWarnings = computed(() => [...new Set([
  ...(workspace.value?.generationSpec.warnings ?? []),
  ...(preview.value?.linkWarnings ?? []),
  ...(preview.value?.localAnalysis.findings.map((item) => item.message) ?? []),
])]);
const maximumReferenceCount = computed(() => {
  const inputPlan = preview.value?.inputPlan ?? {};
  const value = Number(inputPlan.maximumImageReferences ?? inputPlan.max_image_references ?? 0);
  return Number.isFinite(value) && value > 0 ? value : null;
});
const selectableCustomReferences = computed(() => (workspace.value?.assets ?? []).filter((asset) => {
  if (!asset.contentReady || !["approved", "ready"].includes(asset.status) || asset.mediaType !== "image") return false;
  const metadata = asset.metadata ?? {};
  const authority = typeof metadata.authority === "object" && metadata.authority ? metadata.authority as Record<string, unknown> : {};
  if (authority.providerEligible === false || metadata.providerEligible === false) return false;
  const semantics = `${asset.role} ${asset.semanticKey ?? ""} ${String(metadata.purpose ?? "")} ${String(authority.role ?? "")}`;
  return !/style_source|final_video|export|delivery|identity|episode_appearance|pair_scale/i.test(semantics);
}));
const canGenerate = computed(() => Boolean(
  preview.value?.ready
  && !providerBlockers.value.length
  && !activeVideoTask.value
));

function versionInputHash(version: VisualAssetVersion): string {
  const snapshot = version.inputSnapshot ?? {};
  return String(snapshot.inputHash ?? snapshot.input_hash ?? snapshot.sourceRevisionHash ?? "");
}

function versionIsStale(version: VisualAssetVersion): boolean {
  const hash = versionInputHash(version);
  return Boolean(hash && preview.value?.inputHash && hash !== preview.value.inputHash);
}

function referenceAsset(reference: ShotPromptReference) {
  return workspace.value?.assets.find((item) => item.id === reference.assetId)
    ?? workspace.value?.upstreamLineage.find((item) => item.id === reference.assetId);
}

function resolveInitialShot(nextBoard: ProductionBoardDto): ShotDto | undefined {
  const allShots = nextBoard.projectGraph.scenes.flatMap((scene) => scene.shots);
  const requested = props.focusedItemId;
  const direct = allShots.find((shot) => shot.id === requested || shot.assets.some((asset) => asset.id === requested));
  if (direct) return direct;
  const selectedSequence = nextBoard.projectGraph.sequences.find(
    (sequence) => sequence.id === nextBoard.projectGraph.project.selectedSequenceId,
  );
  const sequenceShotId = [...(selectedSequence?.plan.clips ?? [])].sort((left, right) => left.order - right.order)[0]?.shot_card_id;
  return allShots.find((shot) => shot.id === sequenceShotId)
    ?? allShots.find((shot) => Boolean(shot.selectedVideoAssetId))
    ?? allShots.find((shot) => shot.status !== "approved")
    ?? allShots[0];
}

function syncSelectedVersion() {
  providerModeDraft.value = workspace.value?.videoPreview.providerInputMode ?? "reference_media";
  const current = workspace.value?.shot.selectedVideoAssetId;
  if (current && versions.value.some((item) => item.id === current)) selectedVersionId.value = current;
  else if (!versions.value.some((item) => item.id === selectedVersionId.value)) selectedVersionId.value = versions.value.at(-1)?.id ?? "";
}

function customReferenceBinding(assetId: string): ReferenceBinding | undefined {
  return workspace.value?.shot.referenceBindings.find((binding) => (
    binding.assetId === assetId && ["video", "both"].includes(binding.applyTo)
  ));
}

function editableRole(assetId: string, fallback: ReferenceRole = "composition"): ReferenceRole {
  const asset = workspace.value?.assets.find((item) => item.id === assetId);
  const metadataRole = String(asset?.metadata.referenceRole ?? asset?.metadata.semanticRole ?? "").toLowerCase();
  if (["style", "scene", "prop", "composition"].includes(metadataRole)) return metadataRole as ReferenceRole;
  if (/style_board|style:/.test(`${asset?.role ?? ""} ${asset?.semanticKey ?? ""}`)) return "style";
  if (/environment|scene/.test(`${asset?.role ?? ""} ${asset?.semanticKey ?? ""}`)) return "scene";
  if (/prop/.test(`${asset?.role ?? ""} ${asset?.semanticKey ?? ""}`)) return "prop";
  return fallback;
}

function sourceLayerLabel(sourceLayer: ShotPromptReference["sourceLayer"]): string {
  return {
    episode_design: "本集权威",
    scene_look: "当前场景",
    project: "项目权威",
    shot: "镜头专用",
    previous_tail: "上一镜尾帧",
    candidate: "候选素材",
  }[sourceLayer];
}

async function load(background = false) {
  const sequence = ++requestSequence;
  loadState.value = background && board.value ? "ready" : "loading";
  error.value = "";
  try {
    const [nextBoard, nextTasks] = await Promise.all([
      api.productionBoard(props.projectId),
      api.projectTasks(props.projectId),
    ]);
    if (sequence !== requestSequence) return;
    const target = resolveInitialShot(nextBoard);
    const nextWorkspace = target ? await api.shotGenerationWorkspace(target.id) : undefined;
    if (sequence !== requestSequence) return;
    board.value = nextBoard;
    tasks.value = nextTasks;
    workspace.value = nextWorkspace;
    selectedShotId.value = target?.id ?? "";
    syncSelectedVersion();
    loadState.value = "ready";
  } catch (reason) {
    if (sequence !== requestSequence) return;
    error.value = reason instanceof Error ? reason.message : String(reason);
    loadState.value = board.value && workspace.value ? "stale" : "error";
  }
}

async function loadSelectedShot(shotId: string, background = false) {
  const sequence = ++requestSequence;
  if (!background && workspace.value?.shot.id !== shotId) workspace.value = undefined;
  loadState.value = background && workspace.value ? "ready" : "loading";
  error.value = "";
  try {
    const [nextWorkspace, nextTasks] = await Promise.all([
      api.shotGenerationWorkspace(shotId),
      api.projectTasks(props.projectId),
    ]);
    if (sequence !== requestSequence) return;
    workspace.value = nextWorkspace;
    tasks.value = nextTasks;
    selectedShotId.value = shotId;
    syncSelectedVersion();
    loadState.value = "ready";
  } catch (reason) {
    if (sequence !== requestSequence) return;
    error.value = reason instanceof Error ? reason.message : String(reason);
    loadState.value = workspace.value ? "stale" : "error";
  }
}

function selectShot(shot: ShotDto) {
  if (shot.id === selectedShotId.value) return;
  selectedShotId.value = shot.id;
  selectedVersionId.value = "";
  void router.replace({
    name: "project-production",
    params: { projectId: props.projectId },
    query: {
      workspace: "video",
      tab: "generate",
      shot: shot.id,
      ...(rightPanel.value === "tasks" ? { panel: "tasks" } : rightPanel.value === "audit" ? { panel: "assistant" } : {}),
    },
  });
  void loadSelectedShot(shot.id);
}

function setRightPanel(panel: RightPanel) {
  rightPanel.value = panel;
  void router.replace({
    name: "project-production",
    params: { projectId: props.projectId },
    query: { workspace: "video", tab: "generate", shot: selectedShotId.value, panel: panel === "versions" ? "history" : panel === "audit" ? "assistant" : "tasks" },
  });
}

async function saveProviderMode() {
  const current = workspace.value;
  if (!current || busy.value) return;
  const mode = providerModeDraft.value;
  if (mode === "first_last_frame") {
    providerModeDraft.value = current.videoPreview.providerInputMode;
    ElMessage.warning("当前镜头契约尚未绑定可执行的结尾控制图；不能把首尾帧模式伪装成已支持");
    return;
  }
  if (mode === "first_frame" && !current.shot.selectedAnchorAssetId) {
    providerModeDraft.value = current.videoPreview.providerInputMode;
    ElMessage.warning("首帧模式要求一张已批准且未 stale 的开场图");
    return;
  }
  if (current.shot.selectedVideoAssetId) {
    try {
      await ElMessageBox.confirm(
        "修改视频控制模式会更新镜头输入并使当前 Prompt、视频和时间线引用 stale；已批准版本不会被覆盖。",
        "修改视频控制模式",
        { confirmButtonText: "保存新输入版本", cancelButtonText: "保持当前模式", type: "warning" },
      );
    } catch {
      providerModeDraft.value = current.videoPreview.providerInputMode;
      return;
    }
  }
  busy.value = "save-mode";
  try {
    await api.updateShot(current.shot.id, {
      title: current.shot.title,
      direction: current.shot.direction,
      durationSeconds: current.shot.durationSeconds,
      anchorMode: mode === "first_frame" ? "existing" : "text_only",
      referenceBindings: mode === "first_frame"
        ? current.shot.referenceBindings
        : current.shot.referenceBindings.filter((binding) => binding.usage !== "approved_anchor"),
      inheritProjectReferences: mode !== "text_only",
      sceneLookUsage: mode === "reference_media" ? "off" : current.shot.sceneLookUsage,
    });
    await loadSelectedShot(current.shot.id, true);
    ElMessage.success("视频控制模式已保存；没有调用 Provider");
  } catch (reason) {
    providerModeDraft.value = current.videoPreview.providerInputMode;
    ElMessage.error(`保存视频控制模式失败：${reason instanceof Error ? reason.message : String(reason)}`);
  } finally {
    busy.value = "";
  }
}

function manageReference(reference: ShotPromptReference) {
  if (customReferenceBinding(reference.assetId)) {
    referencePickerAssetId.value = reference.assetId;
    return;
  }
  void router.push({
    name: "project-assets",
    params: { projectId: props.projectId },
    query: { item: reference.assetId, panel: "references" },
  });
}

async function persistCustomReferences(nextBindings: ReferenceBinding[], message: string) {
  const current = workspace.value;
  if (!current || busy.value) return;
  if (current.shot.selectedVideoAssetId) {
    try {
      await ElMessageBox.confirm(
        "修改镜头专用参考会使当前 Prompt、视频和时间线引用 stale；历史版本不会被删除。",
        "修改视频参考",
        { confirmButtonText: "保存新输入版本", cancelButtonText: "保留当前参考", type: "warning" },
      );
    } catch {
      return;
    }
  }
  busy.value = "save-references";
  try {
    await api.updateReferences(current.shot.id, nextBindings);
    referencePickerAssetId.value = "";
    await loadSelectedShot(current.shot.id, true);
    ElMessage.success(`${message}；没有调用 Provider`);
  } catch (reason) {
    ElMessage.error(`保存视频参考失败：${reason instanceof Error ? reason.message : String(reason)}`);
  } finally {
    busy.value = "";
  }
}

function removeCustomReference(assetId: string) {
  const current = workspace.value;
  if (!current || !customReferenceBinding(assetId)) return;
  void persistCustomReferences(
    current.shot.referenceBindings.filter((binding) => binding.assetId !== assetId),
    "镜头专用参考已移除",
  );
}

function replaceCustomReference(nextAssetId: string) {
  const current = workspace.value;
  const previous = customReferenceBinding(referencePickerAssetId.value);
  if (!current || !previous || !selectableCustomReferences.value.some((asset) => asset.id === nextAssetId)) return;
  const next = current.shot.referenceBindings.filter((binding) => (
    binding.assetId !== previous.assetId && binding.assetId !== nextAssetId
  ));
  next.push({
    assetId: nextAssetId,
    usage: "generation_reference",
    role: editableRole(nextAssetId, previous.role),
    applyTo: previous.applyTo,
  });
  void persistCustomReferences(next, "镜头专用参考已替换");
}

async function prepareGeneration() {
  const current = workspace.value;
  if (!current) return;
  if (activeVideoTask.value) {
    ElMessage.warning(`当前任务为 ${activeVideoTask.value.status}，必须等待明确终态后才能再次提交`);
    setRightPanel("tasks");
    return;
  }
  let nextPreview = current.videoPreview;
  if (!nextPreview.ready || providerBlockers.value.length) {
    ElMessage.error(providerBlockers.value.join("；") || "当前视频输入尚不可执行");
    return;
  }
  let reason = "生成当前导演镜头视频";
  const regenerate = current.videoVersions.length > 0;
  if (regenerate) {
    let instruction = "";
    try {
      const answer = await ElMessageBox.prompt(
        "请只填写这次重做需要修正的一项问题。现有版本、Prompt 与审计记录不会被覆盖。",
        "视频版本重做目标",
        { inputPlaceholder: "例如：保持人物和猫咪身份，只修正猫咪尾巴环纹漂移" },
      );
      instruction = answer.value.trim();
      if (!instruction) return ElMessage.warning("重新生成必须填写明确的单项修正目标");
    } catch {
      return;
    }
    try {
      reason = instruction;
      nextPreview = await api.promptPreview(current.shot.id, "video", reason);
      if (!nextPreview.ready) return ElMessage.error(nextPreview.blockers.join("；") || "重做输入尚不可执行");
    } catch (reason) {
      ElMessage.error(`无法编译重做 Prompt：${reason instanceof Error ? reason.message : String(reason)}`);
      return;
    }
  }
  if (!runtimeStatus.settings.value) await refreshRuntimeStatus();
  const settings = runtimeStatus.settings.value;
  if (!settings?.videoGenerationReady || !settings.arkReady) {
    ElMessage.error("当前视频 Provider 或运行配置尚未就绪");
    return;
  }
  pendingGenerate.value = {
    preview: nextPreview,
    regenerate,
    reason,
    runtimeRevision: settings.current.revision,
  };
}

async function submitGeneration() {
  const current = workspace.value;
  const pending = pendingGenerate.value;
  if (!current || !pending || busy.value) return;
  if (activeVideoTask.value) {
    pendingGenerate.value = undefined;
    ElMessage.error("任务状态已经变化，未提交重复视频任务");
    return;
  }
  busy.value = "generate";
  try {
    const result = await api.generateVideo(
      current.shot.id,
      pending.regenerate,
      pending.reason,
      pending.preview.inputHash,
      pending.runtimeRevision,
    );
    registerTask(result.jobId, {
      kind: "generate_video",
      label: `视频片段 · ${current.shot.title}`,
      operationKey: "video:shot",
      projectId: props.projectId,
      sceneId: current.shot.sceneId,
      shotId: current.shot.id,
    });
    pendingGenerate.value = undefined;
    setRightPanel("tasks");
    await loadSelectedShot(current.shot.id, true);
    ElMessage.success("视频任务已进入任务中心；页面不会重复提交");
  } catch (reason) {
    ElMessage.error(`提交视频失败：${reason instanceof Error ? reason.message : String(reason)}`);
  } finally {
    busy.value = "";
  }
}

async function reviewVersion(version: VisualAssetVersion, decision: "approved" | "rejected") {
  if (busy.value) return;
  const stale = versionIsStale(version);
  if (decision === "approved" && stale) {
    try {
      await ElMessageBox.confirm("该视频基于旧输入。批准会将它设为当前版本，但不会改变旧 Prompt 和血缘，是否继续？", "采用历史输入视频", { type: "warning" });
    } catch {
      return;
    }
  }
  busy.value = `review:${version.id}`;
  try {
    await api.reviewAsset(version.id, decision, decision === "approved" ? "导演视频工作区人工观看通过" : "导演视频工作区人工观看未通过");
    await loadSelectedShot(selectedShotId.value, true);
    ElMessage.success(decision === "approved" ? "视频版本已批准并设为当前" : "视频版本已拒绝");
  } catch (reason) {
    ElMessage.error(`视频审核失败：${reason instanceof Error ? reason.message : String(reason)}`);
  } finally {
    busy.value = "";
  }
}

async function chooseVersion(version: VisualAssetVersion) {
  if (!selectedShot.value || busy.value || version.id === selectedShot.value.selectedVideoAssetId) return;
  if (versionIsStale(version)) {
    try {
      await ElMessageBox.confirm("该版本的输入哈希与当前 Prompt 不一致。选择后仍保留当前输入的 stale 提示，是否继续？", "选择历史版本", { type: "warning" });
    } catch {
      return;
    }
  }
  busy.value = `select:${version.id}`;
  try {
    await api.selectVersion(selectedShot.value.id, version.id);
    await loadSelectedShot(selectedShot.value.id, true);
    ElMessage.success("当前视频版本已更新");
  } catch (reason) {
    ElMessage.error(`选择版本失败：${reason instanceof Error ? reason.message : String(reason)}`);
  } finally {
    busy.value = "";
  }
}

function mergeChangedTask(changed: PersistentTaskDto) {
  const index = tasks.value.findIndex((task) => task.stepId === changed.stepId);
  if (index >= 0) tasks.value.splice(index, 1, changed);
  else tasks.value.unshift(changed);
  if (["cancelled", "failed", "succeeded"].includes(changed.status)) void loadSelectedShot(selectedShotId.value, true);
}

function openDelivery() {
  void router.push({
    name: "project-production",
    params: { projectId: props.projectId },
    query: { workspace: "video", tab: "edit", ...(selectedShotId.value ? { shot: selectedShotId.value } : {}) },
  });
}

watch(() => props.projectId, () => {
  board.value = undefined;
  workspace.value = undefined;
  selectedShotId.value = "";
  selectedVersionId.value = "";
  void load(false);
}, { immediate: true });
watch(() => props.focusedItemId, (item) => {
  if (!item || !board.value) return;
  const target = shots.value.find(({ shot }) => shot.id === item || shot.assets.some((asset) => asset.id === item))?.shot;
  if (target && target.id !== selectedShotId.value) selectShot(target);
});
watch(() => props.panel, (panel) => {
  if (panel === "tasks") rightPanel.value = "tasks";
  else if (panel === "history") rightPanel.value = "versions";
  else if (panel === "assistant") rightPanel.value = "audit";
}, { immediate: true });
watch(
  () => taskCenter.shotSignals.value[selectedShotId.value]?.revision ?? 0,
  () => { if (selectedShotId.value) void loadSelectedShot(selectedShotId.value, true); },
);
watch(
  () => taskCenter.workspaceRefreshRequest.value?.revision ?? 0,
  () => {
    const request = taskCenter.workspaceRefreshRequest.value;
    if (!request || request.projectId !== props.projectId) return;
    if (!request.shotId || request.shotId === selectedShotId.value) void loadSelectedShot(selectedShotId.value, true);
  },
);
emit("dirty-change", undefined);
onBeforeUnmount(() => {
  requestSequence += 1;
  emit("dirty-change", undefined);
});
</script>

<template>
  <section class="video-generation-workspace">
    <div v-if="loadState === 'loading' && !workspace" class="video-state" aria-busy="true">正在加载镜头、冻结参考、Provider Prompt 与版本历史…</div>
    <div v-else-if="loadState === 'error' && !workspace" class="video-state error" role="alert"><b>视频生成工作区加载失败</b><span>{{ error }}</span><button type="button" @click="load(false)">重新加载</button></div>
    <template v-else-if="workspace && selectedShot">
      <header class="video-reference-strip">
        <div class="reference-heading"><span>FROZEN REFERENCES</span><b>{{ references.length }} 张实际 Provider 输入</b><small>{{ preview?.providerInputMode }}</small></div>
        <div class="reference-scroll">
          <article v-for="reference in references" :key="`${reference.index}:${reference.assetId}`" :class="{ missing: !reference.contentReady }">
            <img v-if="referenceAsset(reference)?.contentReady" :src="assetContentUrl(reference.assetId)" :alt="reference.displayName" />
            <div v-else class="reference-missing">素材不可读</div>
            <span>{{ reference.promptAlias }}</span><b>{{ reference.displayName }}</b><small>{{ reference.responsibility }}</small><mark>{{ sourceLayerLabel(reference.sourceLayer) }}</mark>
            <footer><button type="button" @click="manageReference(reference)">{{ customReferenceBinding(reference.assetId) ? '更换' : '管理' }}</button><button v-if="customReferenceBinding(reference.assetId)" type="button" @click="removeCustomReference(reference.assetId)">移除</button><i v-else>权威输入</i></footer>
          </article>
          <p v-if="!references.length">当前冻结输入没有图片参考；请检查制作包与 Provider 模式。</p>
        </div>
      </header>

      <nav class="video-controls" aria-label="视频生成参数">
        <div><span>Provider</span><b>{{ runtimeStatus.health.value?.provider || '等待配置' }}</b></div>
        <div><span>模型</span><b>{{ runtimeStatus.settings.value?.current.videoModel || '等待配置' }}</b></div>
        <label><span>模式</span><select v-model="providerModeDraft" :disabled="Boolean(activeVideoTask) || Boolean(busy)" aria-label="视频控制模式" @change="saveProviderMode"><option value="reference_media">多参考图</option><option value="first_frame">批准首帧</option><option value="text_only">纯文本</option><option value="first_last_frame" disabled>首尾帧（当前不可用）</option></select></label>
        <div><span>时长</span><b>{{ selectedShot.durationSeconds }} 秒</b></div>
        <div><span>画幅</span><b>9:16</b></div>
        <div><span>分辨率</span><b>{{ runtimeStatus.settings.value?.current.videoResolution || '待加载' }}</b></div>
        <div><span>参考上限</span><b>{{ references.length }}{{ maximumReferenceCount ? ` / ${maximumReferenceCount}` : ' 张' }}</b></div><button type="button" :disabled="!canGenerate || Boolean(busy)" @click="prepareGeneration">{{ versions.length ? '生成新版本' : '生成视频' }}</button>
      </nav>

      <section v-if="referencePickerAssetId" class="video-reference-picker" aria-label="更换镜头专用参考"><header><div><span>REFERENCE PICKER</span><b>更换镜头专用参考</b><small>仅显示已批准、可读取且不会成为第二身份权威的素材。</small></div><button type="button" @click="referencePickerAssetId = ''">关闭</button></header><div><button v-for="asset in selectableCustomReferences" :key="asset.id" type="button" :disabled="asset.id === referencePickerAssetId" @click="replaceCustomReference(asset.id)"><img :src="assetContentUrl(asset.id)" :alt="asset.displayName" /><span>{{ asset.displayName }}</span><small>{{ editableRole(asset.id) }}</small></button><p v-if="!selectableCustomReferences.length">没有可替换的普通镜头参考。人物、猫咪和画风权威请在角色资产模块管理。</p></div></section>

      <div v-if="loadState === 'stale'" class="video-stale">数据可能过期 · {{ error }}</div>
      <main class="video-work-area">
        <aside class="video-prompt-panel">
          <header><div><span>PROVIDER PROMPT</span><b>专业自然语言 Prompt</b></div><small>{{ preview?.charCount ?? 0 }} 字符</small></header>
          <p v-if="preview?.legacyPromptLabels" class="legacy-prompt-notice">历史制作包仍保存内部素材标识；下方已按职责转为可读预览。重新编译并确认制作包前，系统不会允许提交新的 Provider 任务。</p>
          <pre>{{ preview?.prompt || '当前镜头尚未编译视频 Prompt。' }}</pre>
          <section v-if="providerBlockers.length" class="prompt-blockers"><b>执行阻塞</b><p v-for="item in providerBlockers" :key="item">{{ item }}</p></section>
          <details v-if="providerWarnings.length"><summary>非阻塞质量建议 · {{ providerWarnings.length }}</summary><p v-for="item in providerWarnings" :key="item">{{ item }}</p></details>
          <details class="audit-details"><summary>审计信息</summary><dl><div><dt>Input Hash</dt><dd>{{ preview?.inputHash }}</dd></div><div><dt>Source Revision</dt><dd>{{ preview?.sourceRevisionHash }}</dd></div><div><dt>Reference Policy</dt><dd>{{ preview?.providerReferencePolicy }}</dd></div><div><dt>系统外壳</dt><dd><pre>{{ preview?.systemShell }}</pre></dd></div></dl></details>
        </aside>

        <section class="video-result-stage">
          <header><div><span>CURRENT RESULT</span><h1>{{ selectedShot.title }}</h1><p>{{ currentScene?.title }} · {{ selectedShot.durationSeconds }} 秒</p></div><mark>{{ selectedVersion?.status || workspace.nextActionLabel }}</mark></header>
          <div class="video-player">
            <video v-if="selectedVersion?.contentReady" :key="selectedVersion.id" :src="assetContentUrl(selectedVersion.id)" controls preload="metadata" />
            <div v-else><b>{{ versions.length ? '视频文件尚不可播放' : '尚未生成视频候选' }}</b><p>{{ activeVideoTask ? `当前任务：${activeVideoTask.status}` : '确认冻结输入后，从上方提交一次视频生成。' }}</p></div>
          </div>
          <footer v-if="selectedVersion"><div><span>版本</span><b>{{ selectedVersion.displayName }}</b><small :class="{ stale: versionIsStale(selectedVersion) }">{{ versionIsStale(selectedVersion) ? '输入已变化' : '匹配当前输入' }}</small></div><div class="result-actions"><template v-if="selectedVersion.status === 'candidate'"><button type="button" :disabled="Boolean(busy)" @click="reviewVersion(selectedVersion,'rejected')">拒绝</button><button type="button" :disabled="Boolean(busy)" @click="reviewVersion(selectedVersion,'approved')">批准并采用</button></template><button class="primary" type="button" :disabled="!selectedShot.selectedVideoAssetId" @click="openDelivery">进入成片交付</button></div></footer>
        </section>

        <aside class="video-history-panel">
          <nav><button v-for="item in ([['versions','版本'],['tasks','任务'],['audit','诊断']] as const)" :key="item[0]" type="button" :class="{ active: rightPanel === item[0] }" @click="setRightPanel(item[0])">{{ item[1] }}</button></nav>
          <div v-if="rightPanel === 'versions'" class="version-list"><header><b>视频版本</b><small>{{ versions.length }}</small></header><article v-for="(version,index) in [...versions].reverse()" :key="version.id" :class="{ active: selectedVersion?.id === version.id }" @click="selectedVersionId = version.id"><video v-if="version.contentReady" :src="assetContentUrl(version.id)" muted preload="metadata" /><div><b>V{{ versions.length - index }} · {{ version.status }}</b><small>{{ version.createdAt || '时间未记录' }}</small><span :class="{ stale: versionIsStale(version) }">{{ versionIsStale(version) ? '历史输入' : '当前输入' }}</span></div><button v-if="version.status === 'approved'" type="button" :disabled="version.id === selectedShot.selectedVideoAssetId || Boolean(busy)" @click.stop="chooseVersion(version)">{{ version.id === selectedShot.selectedVideoAssetId ? '当前采用' : '选择' }}</button><button v-else type="button" disabled>先审核</button></article><p v-if="!versions.length">还没有视频版本。</p></div>
          <VideoTaskStatus v-else-if="rightPanel === 'tasks'" :tasks="shotTasks" @changed="mergeChangedTask" />
          <div v-else class="video-diagnostics"><header><b>身份与画风检查</b><small>人工决定优先</small></header><p>审核视频时重点检查同一个 8–9 岁短发儿童、同一只灰白虎斑猫、四足结构、尾巴环纹和 Canon v4 画风。</p><article v-for="finding in preview?.localAnalysis.findings ?? []" :key="finding.code"><b>{{ finding.severity }}</b><span>{{ finding.message }}</span></article><p v-if="!preview?.localAnalysis.findings.length">当前没有本地质量提示。</p></div>
        </aside>
      </main>

      <footer class="video-filmstrip" aria-label="视频镜头胶片条"><div><span>SHOT FILMSTRIP</span><b>当前 {{ currentShots.length }} 镜</b><button v-if="historicalShots.length" type="button" @click="showHistoricalShots = !showHistoricalShots">{{ showHistoricalShots ? '收起历史' : `历史 ${historicalShots.length}` }}</button></div><button v-for="({shot,scene},index) in displayedShots" :key="shot.id" type="button" :class="{ active: shot.id === selectedShotId, historical: historicalShots.some(({shot:item}) => item.id === shot.id) }" @click="selectShot(shot)"><span>{{ index + 1 }}</span><div><b>{{ shot.title }}</b><small>{{ scene.title }} · {{ shot.durationSeconds }}s</small></div><mark>{{ historicalShots.some(({shot:item}) => item.id === shot.id) ? '历史镜头' : shot.selectedVideoAssetId ? '当前采用' : shot.status }}</mark></button></footer>

      <CanvasReviewDialog v-if="pendingGenerate" title="确认视频 Provider 调用" @close="pendingGenerate = undefined">
        <div class="video-fee-review"><header><span>PAID EXECUTION</span><h2>{{ pendingGenerate.regenerate ? '生成新视频版本' : '生成首个视频版本' }}</h2><p>确认后将创建一次真实视频 Provider 任务。关闭或取消本窗口不会创建任务。</p></header><dl><div><dt>Provider</dt><dd>{{ runtimeStatus.health.value?.provider || 'Ark' }}</dd></div><div><dt>模型</dt><dd>{{ runtimeStatus.settings.value?.current.videoModel }}</dd></div><div><dt>镜头</dt><dd>{{ selectedShot.title }} · {{ selectedShot.durationSeconds }} 秒</dd></div><div><dt>输出</dt><dd>9:16 · {{ runtimeStatus.settings.value?.current.videoResolution }}</dd></div><div><dt>参考素材</dt><dd>{{ pendingGenerate.preview.actualInputCount }} 张 · {{ pendingGenerate.preview.providerInputMode }}</dd></div><div><dt>调用数量</dt><dd>1 次视频调用</dd></div><div><dt>Input Hash</dt><dd><code>{{ pendingGenerate.preview.inputHash }}</code></dd></div><div><dt>费用</dt><dd>将产生一次 Provider 费用，具体金额以 Provider 账单为准</dd></div></dl><section><b>实际提交参考</b><ol><li v-for="item in pendingGenerate.preview.actualInputs" :key="item.assetId">{{ item.promptAlias }} · {{ item.displayName }} · {{ item.responsibility }}</li></ol></section></div>
        <template #actions><button type="button" @click="pendingGenerate = undefined">取消，不创建任务</button><button class="fee-confirm" type="button" :disabled="Boolean(busy)" @click="submitGeneration">确认并提交一次视频任务</button></template>
      </CanvasReviewDialog>
    </template>
    <div v-else class="video-state"><b>当前项目没有可生成的视频镜头</b><span>请先在分镜生产模块创建并保存至少一个真实镜头。</span></div>
  </section>
</template>

<style scoped>
.video-generation-workspace { position: relative; min-height: 0; height: 100%; display: grid; grid-template-rows: 180px 64px minmax(0,1fr) 118px; overflow: hidden; color: #dfe7f0; background: #0b1016; }.video-state { min-height: 360px; grid-row: 1/-1; display: grid; place-content: center; gap: 10px; padding: 28px; color: #8997a8; text-align: center; }.video-state.error { color: #e7a79d; }.video-state button { min-height: 44px; color: #d9e4ef; background: #25313d; border: 1px solid #435363; border-radius: 9px; cursor: pointer; }
.video-reference-strip { min-width: 0; display: grid; grid-template-columns: 190px minmax(0,1fr); gap: 12px; padding: 12px 16px; overflow: hidden; background: #121820; border-bottom: 1px solid #2d3742; }.reference-heading { display: grid; align-content: center; gap: 4px; }.reference-heading span,.video-prompt-panel header span,.video-result-stage>header span { color: #6688a8; font-size: 9px; font-weight: 800; letter-spacing: .12em; }.reference-heading small { color: #7f8e9f; }.reference-scroll { min-width: 0; display: flex; gap: 8px; overflow-x: auto; }.reference-scroll article { position: relative; flex: 0 0 164px; display: grid; grid-template-columns: 24px minmax(0,1fr); grid-template-rows: 78px auto auto auto; gap: 3px 6px; padding: 6px; background: #19212a; border: 1px solid #303b47; border-radius: 9px; }.reference-scroll article.missing { border-color: #754a38; }.reference-scroll img,.reference-missing { grid-column: 1/3; width: 100%; height: 78px; object-fit: cover; border-radius: 6px; background: #090d12; }.reference-missing { display: grid; place-items: center; color: #c08d68; }.reference-scroll article>span { width: 24px; height: 20px; display: grid; place-items: center; color: #a7c4de; background: #2c4258; border-radius: 5px; font-size: 9px; }.reference-scroll article>b { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; }.reference-scroll article>small { grid-column: 1/3; overflow: hidden; color: #7c8b9c; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }.reference-scroll mark { position: absolute; top: 10px; right: 10px; padding: 2px 5px; color: #afbdca; background: #18212be8; border-radius: 5px; font-size: 8px; }.reference-scroll article>footer { grid-column: 1/3; display: flex; align-items: center; gap: 4px; }.reference-scroll article>footer button { min-height: 28px; flex: 1; color: #afbecd; background: #25313c; border: 1px solid #3a4a59; border-radius: 6px; cursor: pointer; font-size: 8px; }.reference-scroll article>footer i { color: #728397; font-size: 8px; font-style: normal; }
.video-controls { min-width: 0; display: flex; align-items: stretch; gap: 1px; overflow-x: auto; padding: 8px 12px; background: #151c24; border-bottom: 1px solid #2d3742; }.video-controls>div,.video-controls>label { min-width: 112px; display: grid; align-content: center; gap: 2px; padding: 0 12px; border-right: 1px solid #2d3742; }.video-controls span { color: #6f7f90; font-size: 9px; }.video-controls b { font-size: 10px; white-space: nowrap; }.video-controls select { min-height: 30px; padding: 0 7px; color: #d9e3ed; background: #202936; border: 1px solid #3c4b5b; border-radius: 6px; font-size: 10px; }.video-controls>button { min-width: 150px; min-height: 44px; margin-left: auto; padding: 0 16px; color: #f1f7fc; background: #3576a9; border: 1px solid #4e91c6; border-radius: 9px; cursor: pointer; }.video-controls>button:disabled { opacity: .4; cursor: not-allowed; }.video-stale { position: absolute; z-index: 30; top: 252px; left: 50%; max-width: 70%; padding: 8px 11px; transform: translateX(-50%); color: #dcb978; background: #31281d; border: 1px solid #604d31; border-radius: 8px; }
.video-reference-picker { position: absolute; z-index: 70; top: 180px; right: 12px; left: 12px; max-height: min(420px,calc(100% - 210px)); display: grid; grid-template-rows: auto minmax(0,1fr); overflow: hidden; padding: 12px; background: #171e26f5; border: 1px solid #46596c; border-radius: 12px; box-shadow: 0 22px 50px rgb(0 0 0 / 48%); backdrop-filter: blur(12px); }.video-reference-picker>header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.video-reference-picker>header div { display: grid; gap: 3px; }.video-reference-picker>header span { color: #6a8bab; font-size: 9px; font-weight: 800; letter-spacing: .12em; }.video-reference-picker>header small { color: #7f8f9f; }.video-reference-picker>header button { min-height: 44px; padding: 0 12px; color: #d2dde8; background: #27323d; border: 1px solid #455463; border-radius: 8px; cursor: pointer; }.video-reference-picker>div { min-height: 0; display: grid; grid-template-columns: repeat(auto-fill,minmax(140px,1fr)); gap: 8px; overflow: auto; padding-top: 10px; }.video-reference-picker>div>button { min-height: 150px; display: grid; gap: 5px; padding: 7px; color: #cdd8e3; background: #11171e; border: 1px solid #303c48; border-radius: 9px; text-align: left; cursor: pointer; }.video-reference-picker>div>button:disabled { opacity: .4; cursor: not-allowed; }.video-reference-picker img { width: 100%; height: 94px; object-fit: cover; background: #080b0f; border-radius: 6px; }.video-reference-picker small { color: #78899a; }
.video-work-area { min-height: 0; display: grid; grid-template-columns: minmax(300px,380px) minmax(420px,1fr) minmax(280px,330px); overflow: hidden; }.video-prompt-panel,.video-history-panel { min-height: 0; overflow: auto; scrollbar-gutter: stable; background: #141a21; }.video-prompt-panel { padding: 14px; border-right: 1px solid #2d3742; }.video-prompt-panel>header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.video-prompt-panel>header div { display: grid; gap: 3px; }.video-prompt-panel>header small { color: #788798; }.video-prompt-panel>pre { min-height: 220px; margin: 12px 0; padding: 12px; overflow: auto; white-space: pre-wrap; color: #cbd5df; background: #0c1117; border: 1px solid #2c3742; border-radius: 9px; font-family: inherit; line-height: 1.65; }.prompt-blockers { padding: 10px; color: #e3a69c; background: #2b211f; border: 1px solid #68433b; border-radius: 9px; }.prompt-blockers p,.video-prompt-panel details p { margin: 6px 0 0; line-height: 1.5; }.video-prompt-panel details { margin-top: 9px; padding: 10px; background: #19212a; border: 1px solid #303b47; border-radius: 9px; }.video-prompt-panel summary { min-height: 32px; color: #9eb2c7; cursor: pointer; }.audit-details dl { margin: 8px 0 0; display: grid; gap: 7px; }.audit-details dl div { display: grid; grid-template-columns: 88px minmax(0,1fr); gap: 7px; }.audit-details dt { color: #748497; }.audit-details dd { margin: 0; overflow-wrap: anywhere; }.audit-details dd pre { min-height: 0; max-height: 160px; margin: 0; padding: 8px; }
.legacy-prompt-notice { margin: 10px 0 0; padding: 9px 10px; color: #d9b980; background: #2a241a; border: 1px solid #665334; border-radius: 8px; font-size: 10px; line-height: 1.55; }
.video-result-stage { min-width: 0; min-height: 0; display: grid; grid-template-rows: auto minmax(0,1fr) auto; padding: 14px; background: #0d131a; }.video-result-stage>header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding-bottom: 10px; }.video-result-stage h1 { margin: 3px 0; font-size: 18px; }.video-result-stage p { margin: 0; color: #7f8e9f; }.video-result-stage>header mark { padding: 5px 8px; color: #a9bac9; background: #26313c; border-radius: 999px; font-size: 9px; }.video-player { min-height: 0; display: grid; place-items: center; overflow: hidden; background: #070a0e; border: 1px solid #29343f; border-radius: 11px; }.video-player video { width: 100%; height: 100%; max-height: 100%; object-fit: contain; }.video-player>div { padding: 28px; color: #8c99aa; text-align: center; }.video-player>div b { color: #d9e1ea; font-size: 17px; }.video-result-stage>footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding-top: 10px; }.video-result-stage>footer>div:first-child { display: grid; gap: 3px; }.video-result-stage>footer span { color: #768597; font-size: 9px; }.video-result-stage>footer small { color: #75bf93; }.video-result-stage>footer small.stale { color: #d5aa68; }.result-actions { display: flex; flex-wrap: wrap; gap: 6px; }.result-actions button { min-height: 44px; padding: 0 11px; color: #d7e1eb; background: #252f3a; border: 1px solid #414e5c; border-radius: 8px; cursor: pointer; }.result-actions button.primary { color: #f2f8fd; background: #3476a9; border-color: #4d91c6; }.result-actions button:disabled { opacity: .42; cursor: not-allowed; }
.video-history-panel { border-left: 1px solid #2d3742; }.video-history-panel>nav { position: sticky; z-index: 2; top: 0; display: grid; grid-template-columns: repeat(3,1fr); gap: 4px; padding: 8px; background: #141a21; border-bottom: 1px solid #2d3742; }.video-history-panel>nav button { min-height: 44px; color: #7e8d9e; background: transparent; border: 1px solid transparent; border-radius: 8px; cursor: pointer; }.video-history-panel>nav button.active { color: #e6eef7; background: #253240; border-color: #3e5268; }.version-list,.video-task-status,.video-diagnostics { padding: 12px; }.version-list>header,.video-diagnostics>header { display: flex; justify-content: space-between; gap: 8px; }.version-list>header small,.video-diagnostics>header small { color: #7d8b9c; }.version-list article { display: grid; grid-template-columns: 68px minmax(0,1fr) auto; gap: 8px; align-items: center; margin-top: 8px; padding: 7px; background: #1a2129; border: 1px solid #2e3945; border-radius: 9px; cursor: pointer; }.version-list article.active { background: #223146; border-color: #4d7cab; }.version-list video { width: 68px; height: 54px; object-fit: cover; background: #080b0f; border-radius: 6px; }.version-list article>div { min-width: 0; display: grid; gap: 3px; }.version-list article small { overflow: hidden; color: #788798; text-overflow: ellipsis; white-space: nowrap; }.version-list article span { color: #72bd91; font-size: 9px; }.version-list article span.stale { color: #d3a969; }.version-list article>button { min-height: 40px; padding: 0 8px; color: #ccd7e2; background: #26313c; border: 1px solid #414f5e; border-radius: 7px; cursor: pointer; }.version-list article>button:disabled { opacity: .45; }.video-diagnostics p { color: #8795a6; line-height: 1.55; }.video-diagnostics article { display: grid; gap: 4px; margin-top: 8px; padding: 8px; background: #1a2129; border: 1px solid #303a45; border-radius: 8px; }
.video-filmstrip { min-width: 0; display: flex; gap: 8px; align-items: stretch; overflow-x: auto; padding: 10px 12px; background: #11171e; border-top: 1px solid #303b47; }.video-filmstrip>div { flex: 0 0 112px; display: grid; align-content: center; gap: 4px; }.video-filmstrip>div span { color:#627e9b;font-size:9px;font-weight:800;letter-spacing:.1em}.video-filmstrip>div button{min-height:28px;padding:0 7px;color:#9bacbd;background:#202a34;border:1px solid #364655;border-radius:6px;cursor:pointer;font-size:9px}.video-filmstrip>button { flex: 0 0 210px; display: grid; grid-template-columns: 28px minmax(0,1fr) auto; gap: 8px; align-items: center; padding: 8px; color: #cbd5df; background: #1a2129; border: 1px solid #303b47; border-radius: 9px; text-align: left; cursor: pointer; }.video-filmstrip>button.active { background: #223247; border-color: #4d7eae; }.video-filmstrip>button.historical{opacity:.66}.video-filmstrip>button>span { width: 28px; height: 28px; display: grid; place-items: center; background: #293746; border-radius: 7px; }.video-filmstrip>button div { min-width: 0; display: grid; gap: 3px; }.video-filmstrip>button b,.video-filmstrip>button small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.video-filmstrip>button small { color: #7c8b9d; }.video-filmstrip mark { padding: 3px 5px; color: #9cb0c3; background: #27313b; border-radius: 999px; font-size: 8px; }
.video-fee-review { padding: 22px; }.video-fee-review header span { color: #c69d61; font-size: 9px; font-weight: 800; letter-spacing: .12em; }.video-fee-review h2 { margin: 6px 0; }.video-fee-review p { color: #8c9aab; }.video-fee-review dl { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }.video-fee-review dl div { padding: 9px; background: #171d24; border: 1px solid #303a45; border-radius: 9px; }.video-fee-review dt { color: #778697; font-size: 9px; }.video-fee-review dd { margin: 5px 0 0; overflow-wrap: anywhere; }.video-fee-review section { margin-top: 12px; padding: 11px; background: #151b22; border: 1px solid #303a45; border-radius: 9px; }.video-fee-review li { margin-top: 5px; color: #9aa9b9; }.video-fee-review+* button,.fee-confirm { min-height: 44px; padding: 0 14px; }.fee-confirm { color: #f3f8fd; background: #3675a8; border: 1px solid #4e8fc4; border-radius: 9px; }
@media (max-width: 1439px) { .video-work-area { grid-template-columns: minmax(280px,340px) minmax(390px,1fr) 290px; } }
@media (max-width: 1180px) { .video-generation-workspace { grid-template-rows: 180px auto minmax(0,1fr) 112px; }.video-reference-strip { grid-template-columns: 160px minmax(0,1fr); }.video-work-area { grid-template-columns: 320px minmax(0,1fr); }.video-history-panel { position: absolute; z-index: 40; top: 244px; right: 0; bottom: 112px; width: 320px; box-shadow: -18px 0 40px rgb(0 0 0 / 45%); }.video-result-stage { padding-right: 22px; } }
@media (max-width: 900px) { .video-generation-workspace { overflow: auto; grid-template-rows: auto auto auto auto; }.video-reference-strip { grid-template-columns: 1fr; overflow: visible; }.reference-scroll { min-height: 126px; }.video-work-area { min-height: 760px; grid-template-columns: 1fr; overflow: visible; }.video-prompt-panel,.video-result-stage,.video-history-panel { position: relative; inset: auto; width: auto; min-height: 420px; border: 0; border-bottom: 1px solid #2d3742; box-shadow: none; }.video-history-panel { min-height: 320px; }.video-filmstrip { min-height: 110px; }.video-fee-review dl { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; transition: none !important; animation: none !important; } }
</style>
