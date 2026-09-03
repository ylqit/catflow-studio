<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { api } from "../../api/client";
import type { AssetDto, GenerationPreviewDto, JobDto, ProjectUsageSummaryDto, WorkspaceDto } from "../../api/types";
import { buildAcceptanceEvidence } from "../../acceptanceEvidence";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { billingPresentation, errorPresentation, jobPresentation, paidModelBlockedReason, type PaidModelRuntime } from "../../presentation";
import { projectJobEvent } from "../../projectJobEvents";
import { useUiStore } from "../../stores/ui";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto; runtime?: PaidModelRuntime | null }>();
const emit = defineEmits<{ changed: [] }>();
const store = useUiStore();
const preview = ref<GenerationPreviewDto | null>(null);
const currentJob = ref<JobDto | null>(null);
const reviewVideoJob = ref<JobDto | null>(null);
const diagnosisJob = ref<JobDto | null>(null);
const videos = ref<AssetDto[]>([]);
const usageSummary = ref<ProjectUsageSummaryDto | null>(null);
const loadingPreview = ref(false);
const submitting = ref(false);
const error = ref("");
const errorDetail = ref("");
const reviewAssetId = ref<string | null>(null);
const reviewNotes = ref("");
const currentTime = ref(0);
const totalDuration = ref(0);
const playing = ref(false);
let events: EventSource | null = null;
const videoElements = new Map<string, HTMLVideoElement>();
const videoErrors = reactive<Record<string, string>>({});
const candidateJobs = reactive<Record<string, JobDto>>({});

const qualityItems = [
  ["childIdentity", "儿童身份"],
  ["catIdentity", "猫咪身份"],
  ["pairScale", "人猫比例"],
  ["styleConsistency", "画风一致性"],
  ["anatomy", "肢体与结构"],
  ["technical", "技术质量"],
  ["causalChainAndActiveEnding", "因果链与主动结尾"],
] as const;
type QualityKey = typeof qualityItems[number][0];
type Verdict = "pass" | "warning" | "fail" | "";
const verdictLabels = { pass: "通过", warning: "需留意", fail: "不通过" } as const;
const verdictOptions = ["pass", "warning", "fail"] as const;
const quality = reactive<Record<QualityKey, Verdict>>(Object.fromEntries(
  qualityItems.map(([key]) => [key, ""]),
) as Record<QualityKey, Verdict>);
const allPass = computed(() => qualityItems.every(([key]) => quality[key] === "pass"));
const activeAsset = computed(() => videos.value.find((asset) => asset.id === reviewAssetId.value));
const activeAssetId = computed(() => activeAsset.value?.id ?? "");
const activeInputSnapshot = computed(() => reviewVideoJob.value?.inputSnapshot ?? null);
const previewSummary = computed(() => {
  const value = preview.value?.prompt ?? "";
  return value.length > 180 ? `${value.slice(0, 180)}…` : value;
});
const generationButtonLabel = "生成视频";
const generationProviderNotice = "本次生成会产生模型费用，完成后显示实际用量。";
const currentJobPresentation = computed(() => currentJob.value ? jobPresentation(currentJob.value.status) : null);
const currentBillingPresentation = computed(() => currentJob.value
  ? billingPresentation(currentJob.value.billingStatus, currentJob.value.actualCostMicros, currentJob.value.provider)
  : null);
const paidBlockedReason = computed(() => paidModelBlockedReason(props.runtime));
const unresolvedCostJobs = computed(() => usageSummary.value?.jobs.filter((job) =>
  job.billingStatus !== "calculated" && job.billingStatus !== "provider_adjusted",
) ?? []);
const hasCalculatedCost = computed(() => usageSummary.value?.jobs.some((job) =>
  job.billingStatus === "calculated" || job.billingStatus === "provider_adjusted",
) ?? false);
const projectCostSummary = computed(() => {
  if (hasCalculatedCost.value && usageSummary.value) {
    return `¥${(usageSummary.value.calculatedCostMicros / 1_000_000).toFixed(4)}`;
  }
  if (unresolvedCostJobs.value.some((job) => job.billingStatus === "unpriced")) return "费用待核价";
  if (unresolvedCostJobs.value.length > 0) return "费用计算中";
  return "暂无费用";
});

const referenceLabels: Record<string, string> = {
  episode_child: "儿童角色",
  episode_cat: "猫咪角色",
  pair_scale: "人猫比例",
  environment: "当前环境",
  style_board: "固定画风",
};

const usageLabels: Record<string, string> = {
  inputTokens: "输入用量",
  outputTokens: "输出用量",
  completionTokens: "视频生成用量",
  totalTokens: "总用量",
  generatedImages: "生成图片",
  generatedVideoSeconds: "生成视频秒数",
};

function setVideoElement(assetId: string, element: HTMLVideoElement | null) {
  if (element) videoElements.set(assetId, element);
  else videoElements.delete(assetId);
}

async function load() {
  [videos.value, usageSummary.value] = await Promise.all([
    api.assets(props.projectId).then((items) => items.filter((asset) => asset.mediaType === "video")),
    api.projectUsageSummary(props.projectId),
  ]);
  if (!currentJob.value && props.workspace.latestVideoJob) {
    currentJob.value = props.workspace.latestVideoJob;
  }
  await Promise.all(videos.value.map(async (asset) => {
    if (!asset.producingJobId || candidateJobs[asset.id]) return;
    candidateJobs[asset.id] = await api.job(asset.producingJobId);
  }));
}

async function refreshPreview() {
  loadingPreview.value = true;
  error.value = "";
  try {
    preview.value = await api.previewVideo(props.projectId);
  } catch (reason) {
    preview.value = null;
    const failure = errorPresentation(reason, "暂时无法准备本次画面");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    loadingPreview.value = false;
  }
}

async function generateVideo() {
  if (loadingPreview.value || submitting.value || !preview.value || paidBlockedReason.value) return;
  const prepared = preview.value;
  if (prepared.references.some((reference) => !reference.included)) {
    error.value = "视频生成要求五类参考完整；当前 Provider 能力存在省略，未提交任务。";
    return;
  }
  submitting.value = true;
  error.value = "";
  try {
    const scope = `video-generation:${props.projectId}`;
    currentJob.value = await api.createVideoJob(props.projectId, {
      expectedInputHash: prepared.inputHash,
      idempotencyKey: pendingIdempotencyKey(scope, prepared.inputHash),
    });
    settleIdempotencyKey(scope, prepared.inputHash);
  } catch (reason) {
    const failure = errorPresentation(reason, "视频生成没有成功开始");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
    await refreshPreview();
  } finally {
    submitting.value = false;
  }
}

async function copyText(value: string) {
  await navigator.clipboard.writeText(value);
}

function candidateInputState(job: JobDto | undefined): string {
  const source = job?.inputSnapshot?.source;
  if (!source) return "旧任务未记录完整输入";
  const current = source.storyVersionId === props.workspace.activeStory?.id
    && source.shotPlanVersionId === props.workspace.activeShotPlan?.id
    && source.selectionHash === props.workspace.selectionHash;
  return current ? "当前输入" : "历史输入 / 已过期";
}

async function resumeStorage() {
  if (!currentJob.value || currentJob.value.error?.code !== "result_storage_failed") return;
  error.value = "";
  try {
    currentJob.value = await api.resumeJobStorage(currentJob.value.id);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "结果下载与落盘恢复失败";
  }
}

function connectEvents() {
  events = new EventSource(api.eventsUrl(store.lastEventId));
  const refresh = async (event: Event) => {
    const message = event as MessageEvent;
    if (message.lastEventId) store.lastEventId = Number(message.lastEventId);
    const jobEvent = projectJobEvent(message, props.projectId);
    if (!jobEvent) return;
    if (currentJob.value && jobEvent.jobId === currentJob.value.id) {
      currentJob.value = await api.job(currentJob.value.id);
    }
    if (reviewVideoJob.value && jobEvent.jobId === reviewVideoJob.value.id) {
      reviewVideoJob.value = await api.job(reviewVideoJob.value.id);
    }
    if (diagnosisJob.value && jobEvent.jobId === diagnosisJob.value.id) {
      diagnosisJob.value = await api.job(diagnosisJob.value.id);
    }
    if (jobEvent.eventType === "job.succeeded") await load();
  };
  for (const type of ["job.submitted", "job.polling", "job.storing", "job.succeeded", "job.failed", "job.submission_unknown"]) {
    events.addEventListener(type, refresh);
  }
}

async function startReview(asset: AssetDto) {
  reviewAssetId.value = asset.id;
  reviewVideoJob.value = null;
  diagnosisJob.value = null;
  reviewNotes.value = "";
  for (const [key] of qualityItems) quality[key] = "";
  await nextTick();
  const element = videoElements.get(asset.id);
  element?.pause();
  playing.value = false;
  currentTime.value = element?.currentTime ?? 0;
  totalDuration.value = element && Number.isFinite(element.duration)
    ? element.duration
    : Number(asset.metadata.durationMs ?? 0) / 1000;
  const diagnosisJobId = typeof asset.metadata.videoDiagnosisJobId === "string"
    ? asset.metadata.videoDiagnosisJobId
    : undefined;
  const [videoJob, persistedDiagnosisJob] = await Promise.all([
    asset.producingJobId ? api.job(asset.producingJobId) : Promise.resolve(null),
    diagnosisJobId ? api.job(diagnosisJobId) : Promise.resolve(null),
  ]);
  reviewVideoJob.value = videoJob;
  diagnosisJob.value = persistedDiagnosisJob;
}

function jumpTo(seconds: number) {
  if (!reviewAssetId.value) return;
  const element = videoElements.get(reviewAssetId.value);
  if (!element) return;
  element.pause();
  element.currentTime = Math.min(seconds, Number.isFinite(element.duration) ? element.duration : seconds);
  currentTime.value = element.currentTime;
  playing.value = false;
}

async function togglePlayback() {
  if (!reviewAssetId.value) return;
  const element = videoElements.get(reviewAssetId.value);
  if (!element) return;
  if (element.paused) {
    await element.play();
  } else {
    element.pause();
  }
}

function updatePlayback(assetId: string, isPlaying: boolean) {
  if (reviewAssetId.value === assetId) playing.value = isPlaying;
}

function updateTime(assetId: string) {
  if (reviewAssetId.value !== assetId) return;
  const element = videoElements.get(assetId);
  if (!element) return;
  currentTime.value = element.currentTime;
  if (Number.isFinite(element.duration)) totalDuration.value = element.duration;
}

function reportVideoError(assetId: string) {
  const mediaError = videoElements.get(assetId)?.error;
  videoErrors[assetId] = mediaError
    ? `浏览器媒体加载失败（code ${mediaError.code}）：${mediaError.message || "无法解码或读取视频"}`
    : "浏览器媒体加载失败";
}

async function diagnoseVideo() {
  if (!reviewAssetId.value) return;
  const asset = activeAsset.value;
  if (!asset) return;
  const scope = `video-diagnosis:${props.projectId}:${asset.id}`;
  diagnosisJob.value = await api.diagnoseVideo(
    props.projectId,
    asset.id,
    pendingIdempotencyKey(scope, asset.sha256),
  );
  settleIdempotencyKey(scope, asset.sha256);
}

async function chooseVideo() {
  if (!reviewAssetId.value || !allPass.value) return;
  await api.selectAsset(props.projectId, "video", reviewAssetId.value);
  emit("changed");
}

function evidenceDocument() {
  const asset = activeAsset.value;
  if (!asset) throw new Error("没有正在验收的视频");
  return buildAcceptanceEvidence({
    exportedAt: new Date().toISOString(),
    projectId: props.projectId,
    theme: props.workspace.project.theme,
    asset,
    videoJob: reviewVideoJob.value,
    diagnosisJob: diagnosisJob.value,
    quality: Object.fromEntries(qualityItems.map(([key]) => [key, quality[key]])),
    notes: reviewNotes.value,
  });
}

function download(name: string, content: string, type: string) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([content], { type }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

function exportJson() {
  const document = evidenceDocument();
  download(`${props.workspace.project.theme}-acceptance.json`, JSON.stringify(document, null, 2), "application/json");
}

function exportMarkdown() {
  const document = evidenceDocument();
  const rows = qualityItems.map(([key, label]) => `| ${label} | ${document.quality[key]} |`).join("\n");
  download(`${props.workspace.project.theme}-acceptance.md`, `# ${document.theme} 验收记录\n\n- Asset: ${document.videoAssetId}\n- SHA256: ${document.mediaSha256}\n- Video Job: ${document.providerJobId ?? "-"}\n- Provider Task: ${document.providerTaskId ?? "-"}\n- Provider Request: ${document.providerRequestId ?? "-"}\n- Diagnosis Job: ${document.diagnosisJobId ?? "-"}\n- Diagnostic Task: ${document.diagnosisProviderTaskId ?? "-"}\n- Diagnostic Request: ${document.diagnosisProviderRequestId ?? "-"}\n- Passed: ${document.passed}\n\n| 项目 | 判定 |\n|---|---|\n${rows}\n\n## 备注\n\n${document.notes}\n`, "text/markdown");
}

onMounted(async () => { await load(); await refreshPreview(); connectEvents(); });
onBeforeUnmount(() => events?.close());
watch(
  () => [props.workspace.activeStory?.id, props.workspace.activeShotPlan?.id, props.workspace.selectionHash],
  () => { void refreshPreview(); },
);
</script>

<template>
  <section class="generation-layout">
    <div class="generation-main">
      <div class="preview-card card">
        <header><div><p class="eyebrow">本次生成</p><h2>生成视频</h2><p class="paid-hint">{{ paidBlockedReason || generationProviderNotice }}<br>生成任务会自动保存，可以放心离开此页面。</p></div><button class="primary" :disabled="loadingPreview || submitting || !preview || Boolean(paidBlockedReason)" @click="generateVideo"><span v-if="loadingPreview || submitting" class="spinner" />{{ generationButtonLabel }}</button></header>
        <div v-if="error" class="notice error creator-error"><p>{{ error }}</p><details v-if="errorDetail && errorDetail !== error"><summary>技术详情</summary><code>{{ errorDetail }}</code></details></div>
        <div v-if="!preview" class="empty preview-empty"><div>▦</div><p>{{ loadingPreview ? "正在整理本次画面内容……" : "请先完成故事、分镜和五张参考图；完成后会自动生成画面描述。" }}</p></div>
        <template v-else>
          <div class="preview-status"><b>本次画面内容</b><span>预览不产生费用</span></div>
          <div class="model-strip"><span><small>视频规格</small><b>{{ preview.durationSeconds }} 秒 · 480p · 9:16</b></span><span><small>内容来源</small><b>故事版本 {{ workspace.activeStory?.revision }} · 分镜版本 {{ workspace.activeShotPlan?.revision }}</b></span><span><small>参考图</small><b>{{ preview.references.filter((item) => item.included).length }}/{{ preview.references.length }} 张</b></span><span><small>费用</small><b>{{ preview.costEstimateStatus === "unmetered_paid" ? "待核价付费调用" : `预计 ¥${((preview.expectedCostMicros ?? 0) / 1_000_000).toFixed(4)}` }}</b></span></div>
          <div class="prompt-block"><label>画面描述</label><p class="prompt-summary">{{ previewSummary }}</p><details><summary>查看完整生成指令</summary><div class="prompt-actions"><button class="secondary" @click="copyText(preview.prompt)">复制生成指令</button><button class="secondary" @click="copyText(preview.negativePrompt)">复制需要避免的问题</button></div><label>完整生成指令</label><p>{{ preview.prompt }}</p><label>需要避免的问题</label><p>{{ preview.negativePrompt }}</p><div class="reference-list"><div v-for="reference in preview.references" :key="reference.role" :class="{ omitted: !reference.included }"><span class="priority">{{ reference.priority }}</span><b>{{ referenceLabels[reference.role] ?? reference.role }}</b><span>{{ reference.included ? "已使用" : `未使用：${reference.omittedReason}` }}</span></div></div><details class="technical-details"><summary>技术详情</summary><p>模型服务：{{ preview.provider }} · {{ preview.model }}</p><div class="hash-row"><span>输入标识</span><code>{{ preview.inputHash }}</code></div><p>能力版本 {{ preview.capabilityRevision }} · 故事 {{ preview.storyVersionId }} · 分镜 {{ preview.shotPlanVersionId }} · 选择 {{ preview.selectionHash }}</p><div class="reference-technical"><p v-for="reference in preview.references" :key="`technical-${reference.assetId}`">{{ referenceLabels[reference.role] ?? reference.role }} · {{ reference.assetId }} · {{ reference.sha256 }}</p></div></details></details></div>
        </template>
      </div>

      <div class="candidates-card card">
        <header><div><p class="eyebrow">视频候选</p><h2>选择视频</h2></div><span v-if="currentJobPresentation" class="pill" :class="{ good: currentJobPresentation.tone === 'good', warn: ['warn', 'danger'].includes(currentJobPresentation.tone) }">{{ currentJobPresentation.label }}</span></header>
        <section v-if="currentJob && currentJobPresentation" class="job-status" :class="currentJobPresentation.tone">
          <div data-testid="video-job-summary" class="job-summary"><b>生成进度：{{ currentJobPresentation.label }}</b><span>{{ currentJob.error?.message || currentJobPresentation.description }}</span><span v-if="currentBillingPresentation" class="billing-summary">{{ currentBillingPresentation.label }}</span></div>
          <button v-if="currentJob.error?.code === 'result_storage_failed'" class="secondary" @click="resumeStorage">继续保存结果</button>
          <details data-testid="video-job-details" class="job-record"><summary>查看生成记录</summary><dl><div><dt>任务编号</dt><dd><code>{{ currentJob.id }}</code></dd></div><div><dt>原始状态</dt><dd>{{ currentJob.status }}</dd></div><div v-if="currentJob.providerTaskId"><dt>模型任务</dt><dd><code>{{ currentJob.providerTaskId }}</code></dd></div><div v-if="currentJob.providerRequestId || currentJob.error?.requestId"><dt>请求编号</dt><dd><code>{{ currentJob.providerRequestId || currentJob.error?.requestId }}</code></dd></div><div v-if="currentJob.actualUsage"><dt>实际用量</dt><dd><code>{{ JSON.stringify(currentJob.actualUsage) }}</code></dd></div><div v-if="currentBillingPresentation"><dt>费用</dt><dd>{{ currentBillingPresentation.detail }}</dd></div><div v-if="currentJob.error?.code"><dt>错误代码</dt><dd>{{ currentJob.error.code }}</dd></div><div><dt>输入标识</dt><dd><code>{{ currentJob.inputHash }}</code></dd></div></dl></details>
        </section>
        <div v-if="!videos.length" class="empty">{{ currentJob && !['failed', 'cancelled', 'submission_unknown'].includes(currentJob.status) ? "任务执行中，尚无视频候选。" : "尚无视频候选。" }}</div>
        <div v-else class="video-grid">
          <article v-for="asset in videos" :key="asset.id" :class="{ chosen: workspace.selections.video?.id === asset.id, reviewing: reviewAssetId === asset.id }">
            <video v-if="reviewAssetId !== asset.id" controls preload="metadata" :src="`/api/v1/assets/${asset.id}/content`" />
            <div v-else class="reviewing-placeholder">正在页面下方验收此候选</div>
            <p v-if="videoErrors[asset.id]" class="notice error">{{ videoErrors[asset.id] }}</p>
            <div class="candidate-input-summary"><b>{{ candidateInputState(candidateJobs[asset.id]) }}</b><p>{{ candidateJobs[asset.id]?.inputSnapshot?.prompt?.slice(0, 120) || "旧任务未记录生成指令" }}</p><details><summary>查看该候选的生成记录</summary><small>{{ candidateJobs[asset.id]?.provider }} · {{ candidateJobs[asset.id]?.model }} · {{ asset.sha256 }}</small></details></div>
            <footer><button class="secondary" @click="startReview(asset)">检查视频</button><span v-if="workspace.selections.video?.id === asset.id" class="pill good">当前视频</span></footer>
          </article>
        </div>
      </div>

      <section v-if="activeAsset" class="review-card card" data-testid="video-acceptance">
        <header><div><p class="eyebrow">视频检查</p><h2>{{ workspace.project.theme }} · 检查并选择</h2></div><span>{{ currentTime.toFixed(1) }} / {{ totalDuration.toFixed(1) }} 秒</span></header>
        <video class="review-video" :ref="(element) => setVideoElement(activeAssetId, element as HTMLVideoElement | null)" controls preload="metadata" :src="`/api/v1/assets/${activeAssetId}/content`" @timeupdate="updateTime(activeAssetId)" @loadedmetadata="updateTime(activeAssetId)" @play="updatePlayback(activeAssetId, true)" @pause="updatePlayback(activeAssetId, false)" @error="reportVideoError(activeAssetId)" />
        <p v-if="videoErrors[activeAssetId]" class="notice error">{{ videoErrors[activeAssetId] }}</p>
        <div class="checkpoints"><button class="secondary" @click="togglePlayback">{{ playing ? "暂停" : "播放" }}</button><button v-for="time in [0.5, 3, 6, 9, 11.5]" :key="time" class="secondary" @click="jumpTo(time)">跳到 {{ time }}s</button><button class="secondary" @click="videoElements.get(activeAssetId)?.requestFullscreen()">全屏查看</button></div>
        <details class="technical"><summary>查看视频技术信息</summary><div><span>文件校验值 <code>{{ activeAsset.sha256 }}</code></span><span>规格 {{ activeAsset.metadata.resolution ?? "读取中" }} · {{ activeAsset.metadata.ratio ?? "读取中" }}</span><span>尺寸 {{ activeAsset.metadata.width }} × {{ activeAsset.metadata.height }}</span><span>时长 {{ Number(activeAsset.metadata.durationMs ?? 0) / 1000 }}s</span><span>编码 {{ activeAsset.metadata.codec ?? "读取中" }}</span></div></details>
        <section class="submitted-prompt"><b>该候选使用的生成指令 · {{ candidateInputState(reviewVideoJob ?? undefined) }}</b><p v-if="activeInputSnapshot">{{ activeInputSnapshot.prompt }}</p><p v-else>旧任务未记录完整生成指令，系统不会用当前内容推测。</p><details v-if="activeInputSnapshot"><summary>查看需要避免的问题与技术信息</summary><p>{{ activeInputSnapshot.negativePrompt }}</p><code>{{ activeInputSnapshot.inputHash }}</code></details></section>
        <div class="quality-grid"><fieldset v-for="[key, label] in qualityItems" :key="key"><legend>{{ label }}</legend><label v-for="verdict in verdictOptions" :key="verdict"><input v-model="quality[key]" type="radio" :name="key" :value="verdict" />{{ verdictLabels[verdict] }}</label></fieldset></div>
        <label class="notes"><span>验收备注</span><textarea v-model="reviewNotes" rows="4" placeholder="记录失败时间点、角色漂移、结构或主动结尾情况。" /></label>
        <div class="review-actions"><button v-if="workspace.project.theme === '雨天擦爪'" class="secondary" @click="diagnoseVideo">Ark 抽帧诊断（仅雨天擦爪使用）</button><button class="secondary" @click="exportJson">导出 JSON</button><button class="secondary" @click="exportMarkdown">导出 Markdown</button><button class="primary" :disabled="!allPass" @click="chooseVideo">七项全部通过后选择此视频</button></div>
      </section>
    </div>
    <aside v-if="usageSummary" class="generation-aside"><section class="usage-card card"><p class="eyebrow">本项目费用</p><h2>用量概览</h2><dl><div v-for="(value, metric) in usageSummary.totals" :key="metric"><dt>{{ usageLabels[String(metric)] ?? metric }}</dt><dd>{{ value }}</dd></div><div><dt>{{ hasCalculatedCost ? "已计算费用" : "费用状态" }}</dt><dd>{{ projectCostSummary }}</dd></div><div><dt>待处理计费任务</dt><dd>{{ unresolvedCostJobs.length }}</dd></div></dl><details><summary>费用说明</summary><small>本地剪辑不计入模型费用；只有已完成核价的任务才会计入金额，最终账单可能由模型服务调整。</small></details></section></aside>
  </section>
</template>

<style scoped>
.generation-layout { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 20px; align-items: start; }
.generation-main { display: grid; gap: 20px; }
.preview-card, .candidates-card, .review-card { padding: 23px; }
.preview-card > header, .candidates-card > header, .review-card > header { display: flex; justify-content: space-between; align-items: start; }
header h2 { margin-bottom: 0; font-size: 20px; }
.paid-hint { margin: 7px 0 0; color: var(--muted); font-size: 10px; line-height: 1.55; }
.preview-empty { padding: 45px; }
.preview-status { display: flex; justify-content: space-between; gap: 10px; margin-top: 18px; padding: 10px 12px; border-radius: 10px; background: var(--sage-soft); color: #58705c; font-size: 10px; }
.model-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 20px 0; }
.model-strip span { padding: 12px; border-radius: 12px; background: #f4eee7; }
.model-strip small, .model-strip b { display: block; }
.model-strip small { margin-bottom: 4px; color: var(--muted); font-size: 9px; }
.model-strip b { font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
.prompt-block { padding: 15px; border: 1px solid var(--line); border-radius: 13px; background: #fff; }
.prompt-block label { color: #b35f49; font-size: 10px; font-weight: 800; }
.prompt-block p { margin: 7px 0 13px; color: #615a54; font-size: 12px; line-height: 1.65; }
.prompt-block summary { cursor: pointer; font-weight: 700; }.prompt-actions { display: flex; gap: 7px; margin: 12px 0; }.technical-details { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--line); }
.reference-list { display: grid; gap: 7px; margin: 15px 0; }
.reference-list > div { display: grid; grid-template-columns: 28px 120px 1fr; align-items: center; padding: 9px; border-radius: 9px; background: var(--sage-soft); color: #58705c; font-size: 10px; }
.reference-list > div.omitted { background: #f3ece4; color: #8a8178; opacity: .75; }
.priority { width: 20px; height: 20px; display: grid; place-items: center; border-radius: 50%; background: #ffffffaa; }
.hash-row { display: grid; grid-template-columns: auto 1fr; gap: 10px; color: var(--muted); font-size: 10px; }
.hash-row code { overflow: hidden; text-overflow: ellipsis; }
.submit-generation { width: 100%; margin-top: 17px; }
.video-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
.video-grid article { padding: 7px; border: 1px solid var(--line); border-radius: 13px; background: #f4eee7; }
.video-grid article.chosen, .video-grid article.reviewing { border: 2px solid #79957d; }
.video-grid video { width: 100%; aspect-ratio: 9 / 16; border-radius: 9px; background: #27231f; }
.reviewing-placeholder { display: grid; min-height: 150px; place-items: center; border-radius: 9px; background: #ebe4dc; color: var(--muted); font-size: 11px; }
.video-grid footer { display: flex; justify-content: space-between; align-items: center; gap: 6px; padding: 7px 3px 1px; font-size: 9px; }
.candidate-input-summary { display: grid; gap: 5px; padding: 9px 4px 3px; font-size: 9px; }.candidate-input-summary p { margin: 0; color: var(--muted); line-height: 1.45; }.candidate-input-summary small { color: var(--muted); }
.review-video { display: block; width: min(100%, 420px); margin: 18px auto; aspect-ratio: 9 / 16; border-radius: 13px; background: #27231f; }
.checkpoints, .review-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0; }
.technical { padding: 12px; border-radius: 11px; background: #f4eee7; font-size: 10px; }.technical summary { cursor: pointer; font-weight: 700; }.technical > div { display: grid; grid-template-columns: repeat(2, 1fr); gap: 7px; margin-top: 10px; }
.technical code { font-size: 9px; }
.submitted-prompt { margin: 14px 0; padding: 13px; border: 1px solid var(--line); border-radius: 11px; background: #fff; }.submitted-prompt p { color: #615a54; font-size: 11px; line-height: 1.6; white-space: pre-wrap; }.submitted-prompt summary { cursor: pointer; font-size: 10px; font-weight: 700; }.submitted-prompt code { overflow-wrap: anywhere; font-size: 9px; }
.quality-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 9px; margin: 16px 0; }
.quality-grid fieldset { display: flex; gap: 12px; padding: 11px; border: 1px solid var(--line); border-radius: 10px; }
.quality-grid legend { font-size: 11px; font-weight: 700; }
.quality-grid label { font-size: 10px; }
.notes { display: grid; gap: 7px; font-size: 11px; }
.notes textarea { padding: 10px; border: 1px solid var(--line); border-radius: 10px; }
.review-actions { justify-content: flex-end; }
.generation-aside { position: sticky; top: 96px; display: grid; gap: 14px; }
.usage-card { padding: 20px; }.usage-card h2 { margin: 0 0 12px; }.usage-card dl { display: grid; margin: 0; }.usage-card dl div { display: flex; justify-content: space-between; gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--line); font-size: 10px; }.usage-card dd { margin: 0; font-weight: 700; }.usage-card details { margin-top: 10px; }.usage-card summary { cursor: pointer; color: var(--muted); font-size: 10px; }.usage-card small { display: block; margin-top: 8px; color: var(--muted); line-height: 1.5; }
.job-status { display: grid; gap: 9px; margin: 16px 0; padding: 13px; border: 1px solid var(--line); border-radius: 12px; background: #faf7f2; }.job-status.good { background: var(--sage-soft); }.job-status.warn, .job-status.danger { border-color: #d8aaa2; background: #fff3f1; }.job-summary { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center; font-size: 11px; }.job-summary > span { color: var(--muted); }.billing-summary { font-weight: 700; }.job-record summary, .candidate-input-summary summary { cursor: pointer; font-size: 10px; font-weight: 700; }.job-record dl { display: grid; gap: 6px; margin: 10px 0 0; padding-top: 10px; border-top: 1px solid var(--line); }.job-record dl div { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 8px; font-size: 10px; }.job-record dt { color: var(--muted); }.job-record dd { margin: 0; overflow-wrap: anywhere; }.prompt-summary { display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }.reference-technical { overflow-wrap: anywhere; }
</style>
