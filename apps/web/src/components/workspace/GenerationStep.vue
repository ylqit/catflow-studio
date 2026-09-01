<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from "vue";

import { api } from "../../api/client";
import type { AssetDto, GenerationPreviewDto, JobDto, RuntimeBootstrapDto, ValidationRunDto, WorkspaceDto } from "../../api/types";
import { buildAcceptanceEvidence } from "../../acceptanceEvidence";
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
const validationRun = ref<ValidationRunDto | null>(null);
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

function setVideoElement(assetId: string, element: HTMLVideoElement | null) {
  if (element) videoElements.set(assetId, element);
  else videoElements.delete(assetId);
}

async function load() {
  [videos.value, runtime.value, validationRun.value] = await Promise.all([
    api.assets(props.projectId).then((items) => items.filter((asset) => asset.mediaType === "video")),
    api.runtime(),
    api.currentValidationRun(),
  ]);
  if (!currentJob.value && props.workspace.latestVideoJob) {
    currentJob.value = props.workspace.latestVideoJob;
  }
}

async function prepare() {
  loadingPreview.value = true;
  error.value = "";
  try {
    preview.value = await api.previewVideo(props.projectId);
    if (preview.value.references.some((reference) => !reference.included)) {
      error.value = "首批验收要求五类参考全部包含，当前能力存在省略，已停止提交。";
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "生成预览不可用";
  } finally {
    loadingPreview.value = false;
  }
}

async function submit() {
  if (!preview.value || preview.value.references.some((item) => !item.included)) return;
  if (runtime.value?.provider.name === "ark" && validationRun.value?.status !== "authorized") {
    error.value = "需要先在首批真实验收页面授权付费调用";
    return;
  }
  submitting.value = true;
  try {
    currentJob.value = await api.createVideoJob(props.projectId, {
      expectedInputHash: preview.value.inputHash,
      expectedCostMicros: preview.value.expectedCostMicros,
      idempotencyKey: crypto.randomUUID(),
      validationRunId: runtime.value?.provider.name === "ark" ? validationRun.value?.id : undefined,
      paidCallAcknowledged: runtime.value?.provider.name === "ark",
    });
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "视频任务提交失败";
  } finally {
    submitting.value = false;
  }
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
  diagnosisJob.value = await api.diagnoseVideo(
    props.projectId,
    reviewAssetId.value,
    runtime.value?.provider.name === "ark" ? validationRun.value?.id : undefined,
  );
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

onMounted(async () => { await load(); connectEvents(); });
onBeforeUnmount(() => events?.close());
</script>

<template>
  <section class="generation-layout">
    <div class="generation-main">
      <div class="preview-card card">
        <header><div><p class="eyebrow">Frozen generation input</p><h2>付费前先看清输入</h2></div><button class="secondary" :disabled="loadingPreview" @click="prepare">{{ loadingPreview ? "编译中…" : "生成预览" }}</button></header>
        <p v-if="error" class="notice error">{{ error }}</p>
        <div v-if="!preview" class="empty preview-empty"><div>▦</div><p>服务端会根据当前 Story、Shot Plan 和五个资产槽位编译冻结输入。</p></div>
        <template v-else>
          <div class="model-strip"><span><small>Provider</small><b>{{ preview.provider }}</b></span><span><small>Model</small><b>{{ preview.model }}</b></span><span><small>Capability</small><b>{{ preview.capabilityRevision }}</b></span><span><small>规格</small><b>12 秒 · 480p · 9:16</b></span><span><small>Story / Shot</small><b>Rev {{ preview.storyVersionId.slice(0, 8) }} / {{ preview.shotPlanVersionId.slice(0, 8) }}</b></span><span><small>费用</small><b>{{ preview.costEstimateStatus === "unmetered_paid" ? "未计价付费调用" : `¥ ${((preview.expectedCostMicros ?? 0) / 1_000_000).toFixed(4)}` }}</b></span></div>
          <div class="prompt-block"><label>正式 Prompt</label><p>{{ preview.prompt }}</p><label>Negative Prompt</label><p>{{ preview.negativePrompt }}</p></div>
          <div class="reference-list"><div v-for="reference in preview.references" :key="reference.role" :class="{ omitted: !reference.included }"><span class="priority">{{ reference.priority }}</span><b>{{ reference.role }}</b><code>{{ reference.sha256.slice(0, 16) }}…</code><span>{{ reference.included ? "已冻结" : `省略：${reference.omittedReason}` }}</span></div></div>
          <div class="hash-row"><span>Input hash</span><code>{{ preview.inputHash }}</code></div>
          <button class="primary submit-generation" :disabled="submitting || preview.references.some((item) => !item.included)" @click="submit"><span v-if="submitting" class="spinner" />确认并提交视频（占用 generate_video 额度 1 次）</button>
        </template>
      </div>

      <div class="candidates-card card">
        <header><div><p class="eyebrow">Video candidates</p><h2>播放、逐帧检查与选择</h2></div><span v-if="currentJob" class="pill" :class="{ good: currentJob.status === 'succeeded', warn: ['failed', 'submission_unknown'].includes(currentJob.status) }">{{ currentJob.status }}</span></header>
        <section v-if="currentJob" class="job-status" :class="currentJob.status">
          <div><b>最近视频任务</b><code>{{ currentJob.id }}</code></div>
          <span class="pill">{{ currentJob.status }}</span>
          <p v-if="currentJob.providerTaskId">Provider task ID: <code>{{ currentJob.providerTaskId }}</code></p>
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
        <div class="quality-grid"><fieldset v-for="[key, label] in qualityItems" :key="key"><legend>{{ label }}</legend><label v-for="verdict in ['pass', 'warning', 'fail']" :key="verdict"><input v-model="quality[key]" type="radio" :name="key" :value="verdict" />{{ verdict }}</label></fieldset></div>
        <label class="notes"><span>验收备注</span><textarea v-model="reviewNotes" rows="4" placeholder="记录失败时间点、角色漂移、结构或主动结尾情况。" /></label>
        <div class="review-actions"><button v-if="workspace.project.theme === '雨天擦爪'" class="secondary" @click="diagnoseVideo">Ark 抽帧诊断（仅雨天擦爪使用）</button><button class="secondary" @click="exportJson">导出 JSON</button><button class="secondary" @click="exportMarkdown">导出 Markdown</button><button class="primary" :disabled="!allPass" @click="chooseVideo">七项全部通过后选择此视频</button></div>
      </section>
    </div>
    <aside class="safety-card card"><p class="eyebrow">Generation safety</p><h2>不会悄悄重提</h2><ol><li><b>1</b><span>预览确认输入哈希、模型、规格与调用额度</span></li><li><b>2</b><span>相同幂等键只产生一个 Job</span></li><li><b>3</b><span>Provider task ID 获得后立即写入 PostgreSQL</span></li><li><b>4</b><span>重启后只轮询、下载、取消或对账</span></li></ol><p class="notice">参考固定顺序：儿童 → 猫咪 → 同框比例 → 环境 → 净化画风板。前端不能重排，style_source 永远排除。</p></aside>
  </section>
</template>

<style scoped>
.generation-layout { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 20px; align-items: start; }
.generation-main { display: grid; gap: 20px; }
.preview-card, .candidates-card, .safety-card, .review-card { padding: 23px; }
.preview-card > header, .candidates-card > header, .review-card > header { display: flex; justify-content: space-between; align-items: start; }
header h2 { margin-bottom: 0; font-size: 20px; }
.preview-empty { padding: 45px; }
.model-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 20px 0; }
.model-strip span { padding: 12px; border-radius: 12px; background: #f4eee7; }
.model-strip small, .model-strip b { display: block; }
.model-strip small { margin-bottom: 4px; color: var(--muted); font-size: 9px; }
.model-strip b { font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
.prompt-block { padding: 15px; border: 1px solid var(--line); border-radius: 13px; background: #fff; }
.prompt-block label { color: #b35f49; font-size: 10px; font-weight: 800; }
.prompt-block p { margin: 7px 0 13px; color: #615a54; font-size: 12px; line-height: 1.65; }
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
.review-video { display: block; width: min(100%, 420px); margin: 18px auto; aspect-ratio: 9 / 16; border-radius: 13px; background: #27231f; }
.checkpoints, .review-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0; }
.technical { display: grid; grid-template-columns: repeat(2, 1fr); gap: 7px; padding: 12px; border-radius: 11px; background: #f4eee7; font-size: 10px; }
.technical code { font-size: 9px; }
.quality-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 9px; margin: 16px 0; }
.quality-grid fieldset { display: flex; gap: 12px; padding: 11px; border: 1px solid var(--line); border-radius: 10px; }
.quality-grid legend { font-size: 11px; font-weight: 700; }
.quality-grid label { font-size: 10px; }
.notes { display: grid; gap: 7px; font-size: 11px; }
.notes textarea { padding: 10px; border: 1px solid var(--line); border-radius: 10px; }
.review-actions { justify-content: flex-end; }
.safety-card { position: sticky; top: 96px; }
.safety-card ol { list-style: none; padding: 0; display: grid; gap: 12px; }
.safety-card li { display: grid; grid-template-columns: 27px 1fr; gap: 8px; color: var(--muted); font-size: 11px; }
</style>
