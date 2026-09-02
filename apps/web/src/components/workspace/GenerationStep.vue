<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { api } from "../../api/client";
import type { AssetDto, GenerationPreviewDto, JobDto, ProjectUsageSummaryDto, RuntimeBootstrapDto, WorkspaceDto } from "../../api/types";
import { buildAcceptanceEvidence } from "../../acceptanceEvidence";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { projectJobEvent } from "../../projectJobEvents";
import { useUiStore } from "../../stores/ui";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const store = useUiStore();
const preview = ref<GenerationPreviewDto | null>(null);
const currentJob = ref<JobDto | null>(null);
const reviewVideoJob = ref<JobDto | null>(null);
const diagnosisJob = ref<JobDto | null>(null);
const videos = ref<AssetDto[]>([]);
const runtime = ref<RuntimeBootstrapDto | null>(null);
const usageSummary = ref<ProjectUsageSummaryDto | null>(null);
const loadingPreview = ref(false);
const submitting = ref(false);
const error = ref("");
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
const generationButtonLabel = computed(() => runtime.value?.provider.name === "ark"
  ? "生成视频候选（Ark 付费）"
  : "生成视频候选（Fake）");
const generationProviderNotice = computed(() => runtime.value?.provider.name === "ark"
  ? `${runtime.value.provider.name} · ${runtime.value.provider.videoModel} · Ark 付费模型`
  : `${runtime.value?.provider.name ?? "Fake"} · ${runtime.value?.provider.videoModel ?? ""} · Fake Provider · 不产生 Ark 费用`);

function setVideoElement(assetId: string, element: HTMLVideoElement | null) {
  if (element) videoElements.set(assetId, element);
  else videoElements.delete(assetId);
}

async function load() {
  [videos.value, runtime.value, usageSummary.value] = await Promise.all([
    api.assets(props.projectId).then((items) => items.filter((asset) => asset.mediaType === "video")),
    api.runtime(),
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
    error.value = reason instanceof Error ? reason.message : "当前生成输入预览失败";
  } finally {
    loadingPreview.value = false;
  }
}

async function generateVideo() {
  if (loadingPreview.value || submitting.value || !preview.value) return;
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
    error.value = reason instanceof Error ? reason.message : "视频任务提交失败";
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
        <header><div><p class="eyebrow">Video generation</p><h2>专业分镜生成视频</h2><p class="paid-hint">{{ generationProviderNotice }}<br>{{ runtime?.provider.name === "ark" ? "点击后直接提交一个任务，费用按任务完成后的 Provider usage 记录。" : "用于本机流程验证，不会提交真实 Provider。" }}</p></div><button class="primary" :disabled="loadingPreview || submitting || !preview" @click="generateVideo"><span v-if="loadingPreview || submitting" class="spinner" />{{ generationButtonLabel }}</button></header>
        <p v-if="error" class="notice error">{{ error }}</p>
        <div v-if="!preview" class="empty preview-empty"><div>▦</div><p>{{ loadingPreview ? "正在编译当前生成输入预览……" : "请先完成当前 Story、Shot Plan 和五个资产槽位；页面会自动编译 Prompt。" }}</p></div>
        <template v-else>
          <div class="preview-status"><b>当前输入预览，尚未提交</b><span>Preview 不调用 Ark</span></div>
          <div class="model-strip"><span><small>Provider</small><b>{{ preview.provider }}</b></span><span><small>Model</small><b>{{ preview.model }}</b></span><span><small>Capability</small><b>{{ preview.capabilityRevision }}</b></span><span><small>规格</small><b>{{ preview.durationSeconds }} 秒 · 480p · 9:16</b></span><span><small>Story / Shot</small><b>Rev {{ preview.storyVersionId.slice(0, 8) }} / {{ preview.shotPlanVersionId.slice(0, 8) }}</b></span><span><small>费用</small><b>{{ preview.costEstimateStatus === "unmetered_paid" ? "未计价付费调用" : `¥ ${((preview.expectedCostMicros ?? 0) / 1_000_000).toFixed(4)}` }}</b></span></div>
          <div class="prompt-block"><label>Prompt 摘要</label><p>{{ previewSummary }}</p><details><summary>展开完整生成输入</summary><div class="prompt-actions"><button class="secondary" @click="copyText(preview.prompt)">复制 Prompt</button><button class="secondary" @click="copyText(preview.negativePrompt)">复制 Negative Prompt</button></div><label>完整 Prompt</label><p>{{ preview.prompt }}</p><label>Negative Prompt</label><p>{{ preview.negativePrompt }}</p><div class="reference-list"><div v-for="reference in preview.references" :key="reference.role" :class="{ omitted: !reference.included }"><span class="priority">{{ reference.priority }}</span><b>{{ reference.role }}</b><code>{{ reference.sha256.slice(0, 16) }}…</code><span>{{ reference.included ? "已冻结" : `省略：${reference.omittedReason}` }}</span></div></div><details class="technical-details"><summary>技术详情</summary><div class="hash-row"><span>Input hash</span><code>{{ preview.inputHash }}</code></div><p>Capability {{ preview.capabilityRevision }} · Story {{ preview.storyVersionId }} · Shot {{ preview.shotPlanVersionId }} · Selection {{ preview.selectionHash }}</p></details></details></div>
        </template>
      </div>

      <div class="candidates-card card">
        <header><div><p class="eyebrow">Video candidates</p><h2>播放、逐帧检查与选择</h2></div><span v-if="currentJob" class="pill" :class="{ good: currentJob.status === 'succeeded', warn: ['failed', 'submission_unknown'].includes(currentJob.status) }">{{ currentJob.status }}</span></header>
        <section v-if="currentJob" class="job-status" :class="currentJob.status">
          <div><b>最近视频任务</b><code>{{ currentJob.id }}</code></div>
          <span class="pill">{{ currentJob.status }}</span>
          <p v-if="currentJob.providerTaskId">Provider task ID: <code>{{ currentJob.providerTaskId }}</code></p>
          <p v-if="currentJob.actualUsage">实际 usage：<code>{{ JSON.stringify(currentJob.actualUsage) }}</code></p>
          <p v-if="currentJob.billingStatus === 'unpriced'">费用：待核价</p>
          <p v-else-if="currentJob.actualCostMicros != null">按冻结费率计算：¥{{ (currentJob.actualCostMicros / 1_000_000).toFixed(4) }}</p>
          <p v-if="currentJob.error">
            {{ currentJob.error.code || "provider_error" }}：{{ currentJob.error.message || "视频任务失败" }}
            <small v-if="currentJob.error.requestId">Request ID: {{ currentJob.error.requestId }}</small>
          </p>
          <button v-if="currentJob.error?.code === 'result_storage_failed'" class="secondary" @click="resumeStorage">重试下载与落盘（不提交 Provider）</button>
          <p v-else-if="!['succeeded', 'failed', 'cancelled', 'submission_unknown'].includes(currentJob.status)">Worker 正在继续原任务；浏览器可以安全关闭，重新打开后仍从 PostgreSQL 恢复。</p>
          <p v-else-if="currentJob.status === 'submission_unknown'">Provider 是否已接收任务暂时未知；系统不会自动重提。</p>
        </section>
        <div v-if="!videos.length" class="empty">{{ currentJob && !['failed', 'cancelled', 'submission_unknown'].includes(currentJob.status) ? "任务执行中，尚无视频候选。" : "尚无视频候选。" }}</div>
        <div v-else class="video-grid">
          <article v-for="asset in videos" :key="asset.id" :class="{ chosen: workspace.selections.video?.id === asset.id, reviewing: reviewAssetId === asset.id }">
            <video v-if="reviewAssetId !== asset.id" controls preload="metadata" :src="`/api/v1/assets/${asset.id}/content`" />
            <div v-else class="reviewing-placeholder">正在页面下方验收此候选</div>
            <p v-if="videoErrors[asset.id]" class="notice error">{{ videoErrors[asset.id] }}</p>
            <div class="candidate-input-summary"><b>{{ candidateInputState(candidateJobs[asset.id]) }}</b><p>{{ candidateJobs[asset.id]?.inputSnapshot?.prompt?.slice(0, 120) || "旧任务未记录 Prompt" }}</p><small>{{ candidateJobs[asset.id]?.provider }} · {{ candidateJobs[asset.id]?.model }}</small></div>
            <footer><span>{{ asset.sha256.slice(0, 10) }}</span><button class="secondary" @click="startReview(asset)">开始验收</button><span v-if="workspace.selections.video?.id === asset.id" class="pill good">当前视频</span></footer>
          </article>
        </div>
      </div>

      <section v-if="activeAsset" class="review-card card" data-testid="video-acceptance">
        <header><div><p class="eyebrow">Black-box evidence</p><h2>{{ workspace.project.theme }} · 页面内质量验收</h2></div><span>{{ currentTime.toFixed(1) }} / {{ totalDuration.toFixed(1) }} 秒</span></header>
        <video class="review-video" :ref="(element) => setVideoElement(activeAssetId, element as HTMLVideoElement | null)" controls preload="metadata" :src="`/api/v1/assets/${activeAssetId}/content`" @timeupdate="updateTime(activeAssetId)" @loadedmetadata="updateTime(activeAssetId)" @play="updatePlayback(activeAssetId, true)" @pause="updatePlayback(activeAssetId, false)" @error="reportVideoError(activeAssetId)" />
        <p v-if="videoErrors[activeAssetId]" class="notice error">{{ videoErrors[activeAssetId] }}</p>
        <div class="checkpoints"><button class="secondary" @click="togglePlayback">{{ playing ? "暂停" : "播放" }}</button><button v-for="time in [0.5, 3, 6, 9, 11.5]" :key="time" class="secondary" @click="jumpTo(time)">跳到 {{ time }}s</button><button class="secondary" @click="videoElements.get(activeAssetId)?.requestFullscreen()">全屏查看</button></div>
        <div class="technical"><span>SHA256 <code>{{ activeAsset.sha256 }}</code></span><span>规格 {{ activeAsset.metadata.resolution ?? "读取中" }} · {{ activeAsset.metadata.ratio ?? "读取中" }}</span><span>尺寸 {{ activeAsset.metadata.width }} × {{ activeAsset.metadata.height }}</span><span>时长 {{ Number(activeAsset.metadata.durationMs ?? 0) / 1000 }}s</span><span>编码 {{ activeAsset.metadata.codec ?? "读取中" }}</span></div>
        <section class="submitted-prompt"><b>该候选实际使用的 Prompt · {{ candidateInputState(reviewVideoJob ?? undefined) }}</b><p v-if="activeInputSnapshot">{{ activeInputSnapshot.prompt }}</p><p v-else>旧任务未记录完整 Prompt，系统不会使用当前输入推测历史内容。</p><details v-if="activeInputSnapshot"><summary>Negative Prompt 与技术输入</summary><p>{{ activeInputSnapshot.negativePrompt }}</p><code>{{ activeInputSnapshot.inputHash }}</code></details></section>
        <div class="quality-grid"><fieldset v-for="[key, label] in qualityItems" :key="key"><legend>{{ label }}</legend><label v-for="verdict in ['pass', 'warning', 'fail']" :key="verdict"><input v-model="quality[key]" type="radio" :name="key" :value="verdict" />{{ verdict }}</label></fieldset></div>
        <label class="notes"><span>验收备注</span><textarea v-model="reviewNotes" rows="4" placeholder="记录失败时间点、角色漂移、结构或主动结尾情况。" /></label>
        <div class="review-actions"><button v-if="workspace.project.theme === '雨天擦爪'" class="secondary" @click="diagnoseVideo">Ark 抽帧诊断（仅雨天擦爪使用）</button><button class="secondary" @click="exportJson">导出 JSON</button><button class="secondary" @click="exportMarkdown">导出 Markdown</button><button class="primary" :disabled="!allPass" @click="chooseVideo">七项全部通过后选择此视频</button></div>
      </section>
    </div>
    <aside class="generation-aside"><section class="safety-card card"><p class="eyebrow">Generation safety</p><h2>不会悄悄重提</h2><ol><li><b>1</b><span>服务端先编译并冻结输入哈希、模型与五张参考</span></li><li><b>2</b><span>相同幂等键只产生一个 Job</span></li><li><b>3</b><span>Provider task ID 获得后立即写入 PostgreSQL</span></li><li><b>4</b><span>重启后只轮询、下载、取消或对账</span></li></ol><p class="notice">参考固定顺序：儿童 → 猫咪 → 同框比例 → 环境 → 净化画风板。前端不能重排，style_source 永远排除。</p></section><section v-if="usageSummary" class="usage-card card"><p class="eyebrow">Project usage</p><h2>项目实际用量</h2><dl><div v-for="(value, metric) in usageSummary.totals" :key="metric"><dt>{{ metric }}</dt><dd>{{ value }}</dd></div><div><dt>按费率表计算</dt><dd>¥{{ (usageSummary.calculatedCostMicros / 1_000_000).toFixed(4) }}</dd></div><div><dt>待核价任务</dt><dd>{{ usageSummary.unpricedJobCount }}</dd></div></dl><small>本地 FFmpeg 不计入模型费用；Provider 最终账单可能调整。</small></section></aside>
  </section>
</template>

<style scoped>
.generation-layout { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 20px; align-items: start; }
.generation-main { display: grid; gap: 20px; }
.preview-card, .candidates-card, .safety-card, .review-card { padding: 23px; }
.preview-card > header, .candidates-card > header, .review-card > header { display: flex; justify-content: space-between; align-items: start; }
header h2 { margin-bottom: 0; font-size: 20px; }
.paid-hint { margin: 7px 0 0; color: var(--muted); font-size: 10px; line-height: 1.55; }
.preview-empty { padding: 45px; }
.preview-status { display: flex; justify-content: space-between; gap: 10px; margin-top: 18px; padding: 10px 12px; border-radius: 10px; background: var(--sage-soft); color: #58705c; font-size: 10px; }
.model-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 20px 0; }
.model-strip span { padding: 12px; border-radius: 12px; background: #f4eee7; }
.model-strip small, .model-strip b { display: block; }
.model-strip small { margin-bottom: 4px; color: var(--muted); font-size: 9px; }
.model-strip b { font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
.prompt-block { padding: 15px; border: 1px solid var(--line); border-radius: 13px; background: #fff; }
.prompt-block label { color: #b35f49; font-size: 10px; font-weight: 800; }
.prompt-block p { margin: 7px 0 13px; color: #615a54; font-size: 12px; line-height: 1.65; }
.prompt-block summary { cursor: pointer; font-weight: 700; }.prompt-actions { display: flex; gap: 7px; margin: 12px 0; }.technical-details { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--line); }
.reference-list { display: grid; gap: 7px; margin: 15px 0; }
.reference-list > div { display: grid; grid-template-columns: 28px 120px 1fr 80px; align-items: center; padding: 9px; border-radius: 9px; background: var(--sage-soft); color: #58705c; font-size: 10px; }
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
.technical { display: grid; grid-template-columns: repeat(2, 1fr); gap: 7px; padding: 12px; border-radius: 11px; background: #f4eee7; font-size: 10px; }
.technical code { font-size: 9px; }
.submitted-prompt { margin: 14px 0; padding: 13px; border: 1px solid var(--line); border-radius: 11px; background: #fff; }.submitted-prompt p { color: #615a54; font-size: 11px; line-height: 1.6; white-space: pre-wrap; }.submitted-prompt summary { cursor: pointer; font-size: 10px; font-weight: 700; }.submitted-prompt code { overflow-wrap: anywhere; font-size: 9px; }
.quality-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 9px; margin: 16px 0; }
.quality-grid fieldset { display: flex; gap: 12px; padding: 11px; border: 1px solid var(--line); border-radius: 10px; }
.quality-grid legend { font-size: 11px; font-weight: 700; }
.quality-grid label { font-size: 10px; }
.notes { display: grid; gap: 7px; font-size: 11px; }
.notes textarea { padding: 10px; border: 1px solid var(--line); border-radius: 10px; }
.review-actions { justify-content: flex-end; }
.generation-aside { position: sticky; top: 96px; display: grid; gap: 14px; }.safety-card { position: static; }
.safety-card ol { list-style: none; padding: 0; display: grid; gap: 12px; }
.safety-card li { display: grid; grid-template-columns: 27px 1fr; gap: 8px; color: var(--muted); font-size: 11px; }
.usage-card { padding: 20px; }.usage-card h2 { margin: 0 0 12px; }.usage-card dl { display: grid; margin: 0; }.usage-card dl div { display: flex; justify-content: space-between; gap: 8px; padding: 7px 0; border-bottom: 1px solid var(--line); font-size: 10px; }.usage-card dd { margin: 0; font-weight: 700; }.usage-card small { display: block; margin-top: 10px; color: var(--muted); line-height: 1.5; }
</style>
