<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { api } from "../../api/client";
import type { AssetDto, AssetGenerationKind, AssetGenerationPreviewDto, AssetSlot, FixedCanonRole, JobDto, WorkspaceDto } from "../../api/types";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { billingPresentation, errorPresentation, jobPresentation, paidModelBlockedReason, type PaidModelRuntime } from "../../presentation";
import { projectJobEvent } from "../../projectJobEvents";
import { useUiStore } from "../../stores/ui";
import AssetImageViewer from "./AssetImageViewer.vue";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto; runtime?: PaidModelRuntime | null }>();
const emit = defineEmits<{ changed: [] }>();
const store = useUiStore();
const assets = ref<AssetDto[]>([]);
const busySlot = ref<AssetSlot | null>(null);
const error = ref("");
const errorDetail = ref("");
const generationPreview = ref<AssetGenerationPreviewDto | null>(null);
const previewBusy = ref(false);
const previewReason = ref("");
const currentJob = ref<JobDto | null>(null);
const viewerOpen = ref(false);
const viewerTitle = ref("");
const viewerAssets = ref<AssetDto[]>([]);
const viewerActiveAssetId = ref<string | null>(null);
const viewerPrompt = ref<string | null>(null);
const viewerNegativePrompt = ref<string | null>(null);
const viewerPromptUnavailable = ref(false);
const viewerQualityReport = ref<Record<string, unknown> | null>(null);
let events: EventSource | null = null;
let previewTimer: ReturnType<typeof setTimeout> | null = null;
let previewRequest = 0;
let viewerRequest = 0;

const slots: Array<{ id: AssetGenerationKind; order: string; title: string; responsibility: string }> = [
  { id: "episode_child", order: "01", title: "本集儿童设计", responsibility: "锁定 6–7 岁、约 1.2 米、约 4.5–5 头身、齐下颌短发与脸型" },
  { id: "episode_cat", order: "02", title: "本集猫咪设计", responsibility: "锁定灰白分区、虎斑、眼鼻口、环纹尾巴与四足" },
  { id: "pair_scale", order: "03", title: "人猫同框比例", responsibility: "只负责可信的人猫尺寸与站位关系" },
  { id: "environment", order: "04", title: "当前环境参考", responsibility: "空场景只控制空间、道具、天气、构图与光线" },
  { id: "style_board", order: "05", title: "固定画风板", responsibility: "只控制线条、材质、色彩与柔和暖光" },
];
const environmentGenerationRoles = ["style_board", "episode_child", "episode_cat"] as const;
const comparisonRoles: FixedCanonRole[] = ["episode_child", "episode_cat", "pair_scale", "style_board"];

const grouped = computed(() => Object.fromEntries(slots.map((slot) => [slot.id, assets.value.filter((asset) => asset.role === slot.id)])) as Record<AssetSlot, AssetDto[]>);
const currentJobLabel = computed(() => currentJob.value?.kind === "diagnose_image" ? "画面检查" : "环境生成");
const currentJobPresentation = computed(() => currentJob.value ? jobPresentation(currentJob.value.status) : null);
const currentBillingPresentation = computed(() => currentJob.value ? billingPresentation(currentJob.value.billingStatus, currentJob.value.actualCostMicros, currentJob.value.provider) : null);
const paidBlockedReason = computed(() => paidModelBlockedReason(props.runtime));
const fixedReferencesReady = computed(() => environmentGenerationRoles.every((role) => Boolean(props.workspace.selections[role])));
const previewSummary = computed(() => generationPreview.value?.imageInputSnapshot?.environmentIntent ?? "");
const generateBlockedReason = computed(() => {
  if (!props.workspace.activeStory) return "请先采用一个故事。";
  if (!fixedReferencesReady.value) return "请先到运行设置完成固定角色与画风。";
  if (previewBusy.value) return "正在更新环境内容。";
  if (previewReason.value) return previewReason.value;
  if (!generationPreview.value) return "环境内容尚未准备好。";
  return paidBlockedReason.value;
});
const comparisonAssets = computed(() => {
  const labels: Record<string, string> = { episode_child: "固定儿童", episode_cat: "固定猫咪", pair_scale: "人猫比例", style_board: "固定画风板" };
  return comparisonRoles
    .map((role) => {
      const asset = props.workspace.selections[role];
      return asset ? { label: labels[role], asset } : null;
    })
    .filter((item): item is { label: string; asset: AssetDto } => item !== null);
});

function inheritedLabel(slot: AssetGenerationKind) {
  if (slot === "episode_child" || slot === "episode_cat") return "已使用固定角色";
  if (slot === "pair_scale") return "已使用固定比例";
  return "已使用固定画风";
}

function generationReferenceLabel(role: string) {
  return ({
    style_board: "画风板：控制色彩、光线、材质与线条",
    episode_child: "儿童设计：只匹配插画渲染语言，不绘制儿童",
    episode_cat: "猫咪设计：只匹配插画渲染语言，不绘制猫咪",
  } as Record<string, string>)[role] ?? role;
}

async function load() {
  try {
    assets.value = await api.assets(props.projectId);
  } catch (reason) {
    const failure = errorPresentation(reason, "角色与画风暂时无法读取");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  }
}

async function loadEnvironmentPreview() {
  const request = ++previewRequest;
  generationPreview.value = null;
  previewReason.value = "";
  if (!props.workspace.activeStory) {
    previewReason.value = "请先采用一个故事。";
    previewBusy.value = false;
    return;
  }
  if (!fixedReferencesReady.value) {
    previewReason.value = "请先到运行设置完成固定角色与画风。";
    previewBusy.value = false;
    return;
  }
  previewBusy.value = true;
  try {
    const prepared = await api.previewAssetGeneration(props.projectId, "environment");
    if (request === previewRequest) generationPreview.value = prepared;
  } catch (reason) {
    if (request !== previewRequest) return;
    const failure = errorPresentation(reason, "环境内容暂时无法准备");
    previewReason.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    if (request === previewRequest) previewBusy.value = false;
  }
}

function scheduleEnvironmentPreview() {
  if (previewTimer) clearTimeout(previewTimer);
  previewBusy.value = true;
  previewTimer = setTimeout(() => { void loadEnvironmentPreview(); }, 400);
}

async function upload(slot: AssetSlot, event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  busySlot.value = slot;
  error.value = "";
  try {
    await api.uploadAsset(props.projectId, slot, file);
    await load();
  } catch (reason) {
    const failure = errorPresentation(reason, "图片没有成功上传");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    busySlot.value = null;
    input.value = "";
  }
}

async function select(slot: AssetSlot, assetId: string) {
  busySlot.value = slot;
  try {
    await api.selectAsset(props.projectId, slot, assetId);
    emit("changed");
  } catch (reason) {
    const failure = errorPresentation(reason, "图片没有成功选用");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    busySlot.value = null;
  }
}

async function generateEnvironment() {
  const prepared = generationPreview.value;
  if (busySlot.value || generateBlockedReason.value || !prepared) return;
  busySlot.value = "environment";
  error.value = "";
  try {
    const scope = `asset-generation:${props.projectId}:environment`;
    currentJob.value = await api.createAssetGeneration(props.projectId, {
      kind: "environment",
      expectedInputHash: prepared.inputHash,
      idempotencyKey: pendingIdempotencyKey(scope, prepared.inputHash),
    });
    settleIdempotencyKey(scope, prepared.inputHash);
  } catch (reason) {
    const failure = errorPresentation(reason, "环境图片没有成功开始生成");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
    scheduleEnvironmentPreview();
  } finally {
    busySlot.value = null;
  }
}

async function diagnose(asset: AssetDto) {
  if (busySlot.value || paidBlockedReason.value) return;
  error.value = "";
  busySlot.value = asset.role as AssetGenerationKind;
  const scope = `asset-diagnosis:${props.projectId}:${asset.id}`;
  try {
    currentJob.value = await api.diagnoseAsset(props.projectId, asset.id, pendingIdempotencyKey(scope, asset.sha256));
    settleIdempotencyKey(scope, asset.sha256);
  } catch (reason) {
    const failure = errorPresentation(reason, "画面检查没有成功开始");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    busySlot.value = null;
  }
}

function reportStatus(asset: AssetDto): string | null {
  const report = asset.metadata.qualityReport as Record<string, unknown> | undefined;
  if (!report) return null;
  if (report.technical === "fail") return "图片文件异常";
  if (report.characterFree === "fail" || report.characterFree === "warning") return "发现人物或动物";
  if (report.intentMatch === "fail" || report.intentMatch === "warning") return "环境不够吻合";
  if (report.styleMatch === "fail" || report.styleMatch === "warning" || report.style === "warning" || report.style === "fail") return "画风有差异";
  if (report.stagingSpace === "fail" || report.stagingSpace === "warning") return "活动空间需要调整";
  return "环境吻合";
}

async function openViewer(slot: AssetGenerationKind, asset: AssetDto) {
  viewerTitle.value = slots.find((item) => item.id === slot)?.title ?? "图片";
  viewerAssets.value = slot === "environment"
    ? [...(props.workspace.selections.environment ? [props.workspace.selections.environment] : []), ...grouped.value.environment.filter((item) => item.id !== props.workspace.selections.environment?.id)]
    : [asset];
  viewerActiveAssetId.value = asset.id;
  viewerPrompt.value = null;
  viewerNegativePrompt.value = null;
  viewerPromptUnavailable.value = false;
  viewerQualityReport.value = asset.metadata.qualityReport as Record<string, unknown> | undefined ?? null;
  viewerOpen.value = true;
  await loadViewerPrompt(asset);
}

async function loadViewerPrompt(asset: AssetDto) {
  const request = ++viewerRequest;
  viewerActiveAssetId.value = asset.id;
  viewerPrompt.value = null;
  viewerNegativePrompt.value = null;
  viewerPromptUnavailable.value = false;
  viewerQualityReport.value = asset.metadata.qualityReport as Record<string, unknown> | undefined ?? null;
  if (!asset.producingJobId) return;
  try {
    const job = await api.job(asset.producingJobId);
    if (request !== viewerRequest) return;
    viewerPrompt.value = job.imageInputSnapshot?.prompt ?? null;
    viewerNegativePrompt.value = job.imageInputSnapshot?.negativePrompt ?? null;
    viewerPromptUnavailable.value = !job.imageInputSnapshot;
  } catch {
    if (request === viewerRequest) viewerPromptUnavailable.value = true;
  }
}

function connectEvents() {
  events = new EventSource(api.eventsUrl(store.lastEventId));
  const refresh = async (event: Event) => {
    const message = event as MessageEvent;
    if (message.lastEventId) store.lastEventId = Number(message.lastEventId);
    const jobEvent = projectJobEvent(message, props.projectId);
    if (!jobEvent) return;
    if (currentJob.value?.id === jobEvent.jobId) currentJob.value = await api.job(jobEvent.jobId);
    if (jobEvent.eventType === "job.succeeded") await load();
  };
  for (const type of ["job.succeeded", "job.failed", "job.submission_unknown"]) events.addEventListener(type, (event) => { void refresh(event); });
}

watch(
  () => [props.projectId, props.workspace.activeStory?.id, props.workspace.selections.episode_child?.id, props.workspace.selections.episode_cat?.id, props.workspace.selections.style_board?.id],
  () => scheduleEnvironmentPreview(),
);

onMounted(async () => {
  await load();
  await loadEnvironmentPreview();
  connectEvents();
});
onBeforeUnmount(() => {
  events?.close();
  if (previewTimer) clearTimeout(previewTimer);
  previewRequest += 1;
});
</script>

<template>
  <section class="assets-layout">
    <div class="asset-intro card">
      <p class="eyebrow">固定角色与画风</p>
      <h2>保持每条视频中的角色一致</h2>
      <p>儿童、猫咪、同框比例与画风已固定；这里只需为本条视频选择独立环境。</p>
      <div class="canon-portrait"><span class="child">⌒◡⌒</span><span class="cat">= ᵔᴗᵔ =</span></div>
      <ul><li><b>儿童</b><span>同一位 6–7 岁、约 1.2 米短发儿童</span></li><li><b>猫咪</b><span>同一只灰白虎斑猫</span></li><li><b>画风</b><span>二维柔和数字插画</span></li><li><b>来源</b><span>只使用已经确认的画风板</span></li></ul>
      <p class="notice">画面检查只提供建议；只有文件损坏、无法解码或格式错误会阻止选择。</p>
    </div>

    <div class="slot-list">
      <div v-if="error" class="notice error creator-error"><p>{{ error }}</p><details v-if="errorDetail && errorDetail !== error"><summary>技术详情</summary><code>{{ errorDetail }}</code></details></div>
      <section v-if="currentJob && currentJobPresentation" class="notice job-notice" :class="{ error: currentJobPresentation.tone === 'danger' || currentJobPresentation.tone === 'warn' }">
        <b>{{ currentJobLabel }}：{{ currentJobPresentation.label }}</b><span>{{ currentJob.error?.message || currentJobPresentation.description }}</span>
        <details><summary>查看生成记录</summary><p>任务编号：<code>{{ currentJob.id }}</code></p><p>模型服务：{{ currentJob.provider || "旧任务未记录" }} · {{ currentJob.model || "旧任务未记录" }}</p><p>原始状态：{{ currentJob.status }}</p><p v-if="currentJob.actualUsage">实际用量：{{ JSON.stringify(currentJob.actualUsage) }}</p><p v-if="currentBillingPresentation">费用：{{ currentBillingPresentation.label }} · {{ currentBillingPresentation.detail }}</p><p v-if="currentJob.error?.code">错误代码：{{ currentJob.error.code }}</p></details>
      </section>

      <article v-for="slot in slots" :key="slot.id" class="asset-slot card">
        <div class="slot-order">{{ slot.order }}</div>
        <div class="slot-main">
          <header><div><h3>{{ slot.title }}</h3><p>{{ slot.responsibility }}</p></div><span class="pill" :class="{ good: workspace.selections[slot.id] }">{{ workspace.selections[slot.id] ? "已选择" : "待选择" }}</span></header>
          <div v-if="slot.id !== 'environment'" class="candidates inherited">
            <div v-if="workspace.selections[slot.id]" class="candidate selected">
              <button class="image-open" @click="openViewer(slot.id, workspace.selections[slot.id]!)"><img :src="`/api/v1/assets/${workspace.selections[slot.id]!.id}/content`" :alt="`${slot.title}全局继承`" /><span class="current-badge">{{ inheritedLabel(slot.id) }}</span><span class="view-label">查看大图</span></button>
            </div>
            <p v-else class="notice warn">请先到运行设置完成固定角色与画风。</p>
          </div>

          <template v-else>
            <div class="environment-preview" :class="{ loading: previewBusy }">
              <div><b>本次环境内容</b><p v-if="previewSummary">{{ previewSummary }}</p><p v-else>{{ previewReason || "正在准备环境内容。" }}</p><span class="empty-scene-label">空场景，不含人物与猫咪</span></div>
              <ul v-if="generationPreview"><li v-for="reference in generationPreview.references.filter((item) => item.included)" :key="reference.assetId">{{ generationReferenceLabel(reference.role) }}</li></ul>
              <details v-if="generationPreview"><summary>查看完整生成指令</summary><p>{{ generationPreview.prompt }}</p><p><b>需要避免的问题</b><br />{{ generationPreview.negativePrompt }}</p><details class="technical-details"><summary>技术详情</summary><p>{{ generationPreview.provider }} · {{ generationPreview.model }} · {{ generationPreview.capabilityRevision }}</p><code>{{ generationPreview.inputHash }}</code></details></details>
            </div>

            <div class="candidates environment-candidates">
              <div v-if="workspace.selections[slot.id]" class="candidate selected">
                <button class="image-open" @click="openViewer(slot.id, workspace.selections[slot.id]!)"><img :src="`/api/v1/assets/${workspace.selections[slot.id]!.id}/content`" :alt="`${slot.title}当前选择`" /><span class="current-badge">已选择当前环境</span><span class="view-label">查看大图</span></button>
                <div class="candidate-actions single"><button :disabled="busySlot === slot.id || Boolean(paidBlockedReason)" @click="diagnose(workspace.selections[slot.id]!)">画面检查</button></div>
              </div>
              <div v-for="asset in grouped[slot.id].filter((candidate) => candidate.id !== workspace.selections[slot.id]?.id)" :key="asset.id" class="candidate">
                <button class="image-open" @click="openViewer(slot.id, asset)"><img :src="`/api/v1/assets/${asset.id}/content`" :alt="slot.title" /><span v-if="reportStatus(asset)" class="quality-badge">{{ reportStatus(asset) }}</span><span class="view-label">查看大图</span></button>
                <div class="candidate-actions"><button :disabled="busySlot === slot.id || Boolean(paidBlockedReason)" @click="diagnose(asset)">画面检查</button><button v-if="workspace.selections[slot.id]?.id !== asset.id" :disabled="busySlot === slot.id" @click="select(slot.id, asset.id)">选择</button></div>
              </div>
              <label class="upload-candidate" :class="{ busy: busySlot === slot.id }"><input type="file" accept="image/png,image/jpeg,image/webp" @change="upload(slot.id, $event)" /><b>上传</b><span>{{ busySlot === slot.id ? "处理中…" : "上传候选" }}</span></label>
              <button class="generate-candidate" :disabled="busySlot === slot.id || Boolean(generateBlockedReason)" @click="generateEnvironment"><b>生成</b><span>环境候选</span></button>
            </div>
            <section class="paid-model-note"><b>{{ generateBlockedReason || "本次会使用付费模型，完成后显示实际用量。" }}</b><span>环境候选只属于当前项目；生成或检查任务离开页面后仍会继续。</span></section>
          </template>
        </div>
      </article>
    </div>

    <AssetImageViewer :open="viewerOpen" :title="viewerTitle" :assets="viewerAssets" :active-asset-id="viewerActiveAssetId" :comparisons="viewerTitle === '当前环境参考' ? comparisonAssets : []" :prompt="viewerPrompt" :negative-prompt="viewerNegativePrompt" :prompt-unavailable="viewerPromptUnavailable" :quality-report="viewerQualityReport" @asset-change="loadViewerPrompt" @close="viewerOpen = false" />
  </section>
</template>

<style scoped>
.assets-layout { display: grid; grid-template-columns: 310px minmax(0, 1fr); gap: 20px; align-items: start; }.asset-intro { position: sticky; top: 96px; padding: 24px; }.asset-intro > p:not(.eyebrow, .notice) { color: var(--muted); line-height: 1.65; font-size: 13px; }.canon-portrait { height: 180px; margin: 20px 0; border-radius: 16px; display: flex; align-items: end; justify-content: center; gap: 22px; padding-bottom: 38px; color: #fff; background: radial-gradient(circle at 60% 20%, #f8e8cd, transparent 30%), linear-gradient(145deg, #b8c6b5, #788e7c); font: 600 20px Georgia, serif; }.canon-portrait .cat { font-size: 17px; }.asset-intro ul { list-style: none; padding: 0; margin: 0 0 18px; display: grid; gap: 9px; }.asset-intro li { display: grid; grid-template-columns: 42px 1fr; font-size: 12px; color: var(--muted); }.asset-intro li b { color: #b55f49; }
.slot-list { display: grid; gap: 14px; }.asset-slot { display: grid; grid-template-columns: 64px 1fr; padding: 20px; }.slot-order { font: 500 28px Georgia, serif; color: #d3c7ba; }.slot-main header { display: flex; justify-content: space-between; gap: 15px; }.slot-main h3 { margin: 0 0 5px; font: 600 17px Georgia, "Songti SC", serif; }.slot-main header p { margin: 0; color: var(--muted); font-size: 11px; }
.candidates { display: flex; gap: 12px; margin-top: 16px; overflow-x: auto; padding-bottom: 3px; }.candidate, .upload-candidate, .generate-candidate { flex: 0 0 144px; border: 1px solid var(--line); border-radius: 13px; overflow: hidden; position: relative; background: var(--paper-2); }.candidate { display: grid; grid-template-rows: 152px auto; }.candidate.selected { border: 2px solid #76947b; grid-template-rows: 152px auto; }.candidates.inherited .candidate.selected { grid-template-rows: 152px; }.image-open { position: relative; width: 100%; min-width: 0; padding: 0; border: 0; overflow: hidden; background: #e8e1d9; cursor: zoom-in; }.image-open img { width: 100%; height: 100%; object-fit: contain; display: block; }.image-open:focus-visible { outline: 3px solid #c46d52; outline-offset: -3px; }.current-badge, .quality-badge, .view-label { position: absolute; left: 7px; padding: 5px 7px; border-radius: 7px; color: white; font-size: 9px; }.current-badge { bottom: 7px; background: #5e775fcc; }.quality-badge { top: 7px; background: #7a624ecc; }.view-label { right: 7px; bottom: 7px; left: auto; background: #403a35c7; }
.candidate-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; border-top: 1px solid var(--line); }.candidate-actions.single { grid-template-columns: 1fr; }.candidate-actions button { min-height: 34px; border: 0; background: white; color: var(--ink); cursor: pointer; font-size: 10px; }.candidate-actions button + button { border-left: 1px solid var(--line); }.candidate-actions button:disabled { cursor: not-allowed; color: #aaa; }.upload-candidate, .generate-candidate { height: 188px; display: grid; place-content: center; justify-items: center; gap: 5px; color: #8b8177; cursor: pointer; }.upload-candidate { border-style: dashed; }.upload-candidate input { display: none; }.upload-candidate b, .generate-candidate b { font-size: 13px; }.upload-candidate span, .generate-candidate span { font-size: 10px; }.upload-candidate:hover { border-color: #d47a60; color: #b95e48; }.upload-candidate.busy { opacity: .5; pointer-events: none; }.generate-candidate { color: #a25c48; background: #f9eae3; }.generate-candidate:disabled { cursor: not-allowed; opacity: .55; }
.candidates.inherited { overflow: visible; }.environment-preview { display: grid; grid-template-columns: minmax(0, 1fr) minmax(250px, .8fr); gap: 12px 24px; margin-top: 14px; padding: 14px 16px; border-radius: 13px; background: #f5efe5; }.environment-preview.loading { opacity: .7; }.environment-preview b { color: var(--ink); }.environment-preview p { margin: 5px 0; color: var(--muted); line-height: 1.55; }.environment-preview ul { margin: 0; padding-left: 18px; color: var(--muted); font-size: 11px; line-height: 1.7; }.environment-preview details { grid-column: 1 / -1; }.environment-preview summary { cursor: pointer; font-weight: 700; }.empty-scene-label { display: inline-block; margin-top: 4px; padding: 4px 8px; border-radius: 999px; color: #54715a; background: #e3eee4; font-size: 10px; }.paid-model-note { display: grid; gap: 5px; margin-top: 12px; padding: 12px 14px; border-radius: 12px; background: #fff7ef; color: var(--muted); font-size: 10px; }.paid-model-note b { color: var(--ink); }.job-notice { display: grid; gap: 6px; }.job-notice details summary, .technical-details summary { cursor: pointer; font-weight: 700; }.job-notice details p { margin: 6px 0 0; overflow-wrap: anywhere; }
@media (max-width: 980px) { .assets-layout { grid-template-columns: 1fr; }.asset-intro { position: static; }.environment-preview { grid-template-columns: 1fr; } }
</style>
