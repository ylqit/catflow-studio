<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { api } from "../../api/client";
import type {
  AssetDto,
  EditVersionDto,
  FrameRangeDto,
  JobDto,
  RuntimeBootstrapDto,
  SegmentRepairPreviewDto,
  ValidationRunDto,
  VideoRepairDto,
  WorkspaceDto,
} from "../../api/types";
import {
  allRepairChecksPass,
  formatFrameTimecode,
  mediaTimeToFrame,
  moveCandidateCoreRange,
  snapFrame,
  type RepairVerdict,
} from "../../videoRepair";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const baseVideo = computed(() => props.workspace.selections.video ?? null);
const totalFrames = computed(() => {
  const value = baseVideo.value?.metadata.durationFrames;
  return typeof value === "number" ? value : props.workspace.project.targetDurationSeconds * 24;
});
const issue = reactive<FrameRangeDto>({ startFrame: 96, endFrame: 192 });
const prompt = ref(props.workspace.project.theme === "雨天擦爪"
  ? "孩子蹲下，用软毛巾逐只擦干猫爪；猫咪自然抬爪配合，湿爪和地面水印明显减少。"
  : "只重拍所选问题区间，保持人物、猫咪、机位、构图、光线和前后动作连续。");
const currentFrame = ref(0);
const zoom = ref(100);
const preview = ref<SegmentRepairPreviewDto | null>(null);
const repairs = ref<VideoRepairDto[]>([]);
const assets = ref<AssetDto[]>([]);
const edits = ref<EditVersionDto[]>([]);
const validationRun = ref<ValidationRunDto | null>(null);
const runtime = ref<RuntimeBootstrapDto | null>(null);
const currentJob = ref<JobDto | null>(null);
const busy = ref(false);
const error = ref("");
const candidateCore = reactive<FrameRangeDto>({ startFrame: 0, endFrame: 1 });
const transitionFrames = ref<0 | 2 | 4 | 6>(0);
const basePlayer = ref<HTMLVideoElement | null>(null);
const candidatePlayer = ref<HTMLVideoElement | null>(null);
const loop = ref<FrameRangeDto | null>(null);

const qualityLabels = {
  child_identity: "儿童身份",
  cat_identity: "猫咪身份",
  pair_scale: "人猫比例",
  style: "画风",
  structure: "肢体与结构",
  motion_continuity: "运动连续性",
  causal_chain: "因果链",
} as const;
const quality = reactive<Record<keyof typeof qualityLabels, RepairVerdict>>({
  child_identity: "",
  cat_identity: "",
  pair_scale: "",
  style: "",
  structure: "",
  motion_continuity: "",
  causal_chain: "",
});
const seams = reactive<Record<"in" | "out", RepairVerdict>>({ in: "", out: "" });
const notes = ref("");

const activeEdit = computed(() => edits.value.find((item) => item.active) ?? null);
const activeRepair = computed(() => repairs.value.find((item) => ["generating", "candidate_ready"].includes(item.status)) ?? repairs.value[0] ?? null);
const candidate = computed(() => assets.value.find((item) => item.id === activeRepair.value?.candidateAssetId) ?? null);
const candidateFrames = computed(() => {
  const value = candidate.value?.metadata.durationFrames;
  return typeof value === "number" ? value : activeRepair.value?.providerDurationSeconds ? activeRepair.value.providerDurationSeconds * 24 : 0;
});
const exactFrames = computed(() => Array.from({ length: totalFrames.value }, (_, index) => index));
const timelineFrames = computed(() => zoom.value >= 100 ? exactFrames.value : exactFrames.value.filter((frame) => frame % 12 === 0));
const boundaries = computed(() => {
  const values = [0, totalFrames.value];
  let cursor = 0;
  for (const shot of props.workspace.activeShotPlan?.shots ?? []) {
    cursor += Math.round(shot.durationSeconds * 24);
    values.push(cursor);
  }
  return values;
});
const issueDuration = computed(() => issue.endFrame - issue.startFrame);
const canApprove = computed(() => {
  if (!activeRepair.value || !candidate.value || activeRepair.value.status !== "candidate_ready") return false;
  if (candidateCore.endFrame - candidateCore.startFrame !== issueDuration.value) return false;
  return allRepairChecksPass(quality, seams);
});
const candidateOpacity = computed(() => {
  if (!candidate.value || currentFrame.value < issue.startFrame || currentFrame.value >= issue.endFrame) return 0;
  if (!transitionFrames.value) return 1;
  const fromIn = currentFrame.value - issue.startFrame + 1;
  const toOut = issue.endFrame - currentFrame.value;
  return Math.min(1, fromIn / transitionFrames.value, toOut / transitionFrames.value);
});
const repairQuota = computed(() => {
  const limit = validationRun.value?.callLimits.regenerate_video_segment ?? 0;
  const used = validationRun.value?.usage.regenerate_video_segment ?? 0;
  return { limit, used, remaining: Math.max(0, limit - used) };
});
const publicationId = computed(() => {
  if (currentJob.value?.publication) return currentJob.value.publication.id;
  const value = currentJob.value?.providerResult?.publicationId;
  return typeof value === "string" ? value : null;
});
const publicationDeleteAfter = computed(
  () => currentJob.value?.publication?.deleteAfter ?? null,
);
const jobRequestId = computed(() => {
  const value = currentJob.value?.providerResult?.requestId;
  return typeof value === "string" ? value : null;
});
const candidateRequestId = computed(() => {
  const value = candidate.value?.metadata.providerRequestId;
  return typeof value === "string" ? value : jobRequestId.value;
});
const requiresReferencePublisher = computed(() => preview.value?.provider === "ark");
const referencePublisherReady = computed(() => runtime.value?.objectPublisher.ready === true);

async function load() {
  [repairs.value, assets.value, edits.value, validationRun.value, runtime.value] = await Promise.all([
    api.videoRepairs(props.projectId),
    api.assets(props.projectId),
    api.edits(props.projectId),
    api.currentValidationRun(),
    api.runtime(),
  ]);
  const repair = activeRepair.value;
  if (repair) {
    Object.assign(issue, repair.issueRange);
    Object.assign(candidateCore, repair.candidateCoreRange);
    preview.value = repair.preview;
  }
  const durableJob = props.workspace.latestRepairJob;
  if (durableJob && (!repair || durableJob.videoRepairId === repair.id)) {
    currentJob.value = durableJob;
  }
}

function clampIssue() {
  issue.startFrame = Math.max(0, Math.min(totalFrames.value - 1, Math.trunc(issue.startFrame)));
  issue.endFrame = Math.max(issue.startFrame + 1, Math.min(totalFrames.value, Math.trunc(issue.endFrame)));
  preview.value = null;
}

function seek(frame: number) {
  currentFrame.value = Math.max(0, Math.min(totalFrames.value - 1, Math.trunc(frame)));
  if (basePlayer.value) basePlayer.value.currentTime = currentFrame.value / 24;
  if (candidatePlayer.value && currentFrame.value >= issue.startFrame && currentFrame.value < issue.endFrame) {
    candidatePlayer.value.currentTime = (candidateCore.startFrame + currentFrame.value - issue.startFrame) / 24;
  }
}

function setIn(disableSnap = false) {
  issue.startFrame = disableSnap ? currentFrame.value : snapFrame(currentFrame.value, boundaries.value, 3);
  if (issue.startFrame >= issue.endFrame) issue.endFrame = Math.min(totalFrames.value, issue.startFrame + 1);
  clampIssue();
}

function setOut(disableSnap = false) {
  const exclusive = currentFrame.value + 1;
  issue.endFrame = disableSnap ? exclusive : snapFrame(exclusive, boundaries.value, 3);
  if (issue.endFrame <= issue.startFrame) issue.startFrame = Math.max(0, issue.endFrame - 1);
  clampIssue();
}

function onKey(event: KeyboardEvent) {
  if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return;
  if (event.key.toLowerCase() === "i") setIn(event.altKey);
  else if (event.key.toLowerCase() === "o") setOut(event.altKey);
  else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    seek(currentFrame.value + direction * (event.shiftKey ? 10 : 1));
  }
}

async function createPreview() {
  if (!baseVideo.value) return;
  busy.value = true;
  error.value = "";
  try {
    preview.value = await api.previewVideoRepair(props.projectId, {
      baseVideoAssetId: baseVideo.value.id,
      baseEditVersionId: activeEdit.value?.id,
      issueRange: { ...issue },
      prompt: prompt.value,
      validationRunId: validationRun.value?.status === "authorized" ? validationRun.value.id : undefined,
    });
    Object.assign(candidateCore, preview.value.candidateCoreRange);
    await load();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "片段修复预览失败";
  } finally {
    busy.value = false;
  }
}

async function submitOneCandidate() {
  if (!preview.value) return;
  busy.value = true;
  error.value = "";
  try {
    currentJob.value = await api.createVideoRepair(props.projectId, {
      repairId: preview.value.repairId,
      expectedInputHash: preview.value.inputHash,
      expectedCostMicros: preview.value.expectedCostMicros,
      idempotencyKey: crypto.randomUUID(),
      validationRunId: validationRun.value?.status === "authorized" ? validationRun.value.id : undefined,
      paidConfirmation: true,
    });
    await load();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "候选提交失败";
  } finally {
    busy.value = false;
  }
}

function moveCore(delta: number) {
  Object.assign(candidateCore, moveCandidateCoreRange(candidateCore, delta, candidateFrames.value));
  seek(currentFrame.value);
}

function completedChecks(checks: Record<string, RepairVerdict>): Record<string, "pass" | "warning" | "fail"> {
  if (Object.values(checks).some((value) => value === "")) throw new Error("人工验收尚未完成");
  return Object.fromEntries(Object.entries(checks)) as Record<string, "pass" | "warning" | "fail">;
}

function syncComposite() {
  if (!basePlayer.value) return;
  currentFrame.value = mediaTimeToFrame(basePlayer.value.currentTime, 24, totalFrames.value);
  if (loop.value && currentFrame.value >= loop.value.endFrame) seek(loop.value.startFrame);
  if (!candidatePlayer.value) return;
  if (currentFrame.value >= issue.startFrame && currentFrame.value < issue.endFrame) {
    const target = (candidateCore.startFrame + currentFrame.value - issue.startFrame) / 24;
    if (Math.abs(candidatePlayer.value.currentTime - target) > 0.08) candidatePlayer.value.currentTime = target;
    if (!basePlayer.value.paused) void candidatePlayer.value.play();
  } else {
    candidatePlayer.value.pause();
  }
}

function previewSeam(point: "in" | "out") {
  const center = point === "in" ? issue.startFrame : issue.endFrame;
  loop.value = { startFrame: Math.max(0, center - 12), endFrame: Math.min(totalFrames.value, center + 12) };
  seek(loop.value.startFrame);
  void basePlayer.value?.play();
}

async function approve() {
  if (!activeRepair.value || !candidate.value || !canApprove.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api.approveVideoRepair(props.projectId, activeRepair.value.id, {
      candidateAssetId: candidate.value.id,
      candidateSourceRange: { ...candidateCore },
      transition: transitionFrames.value
        ? { type: "dissolve", durationFrames: transitionFrames.value }
        : { type: "cut", durationFrames: 0 },
      expectedBaseTimelineHash: activeRepair.value.baseTimelineHash,
      idempotencyKey: crypto.randomUUID(),
      qualityChecks: completedChecks({ ...quality }),
      seamChecks: completedChecks({ ...seams }),
    });
    await load();
    emit("changed");
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "批准合并失败";
  } finally {
    busy.value = false;
  }
}

async function reject() {
  if (!activeRepair.value) return;
  await api.rejectVideoRepair(props.projectId, activeRepair.value.id);
  await load();
}

onMounted(async () => {
  await load();
  window.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
watch(() => props.workspace.eventCursor, async () => {
  await load();
});
</script>

<template>
  <section class="repair card" data-testid="video-repair-workspace">
    <header>
      <div><p class="eyebrow">Frame-accurate segment repair</p><h2>片段修复 · 非破坏性合并</h2></div>
      <div class="repair-state"><span class="pill">24 fps</span><span class="pill">{{ totalFrames }} 帧</span><span v-if="activeRepair" class="pill" :class="{ good: activeRepair.status === 'approved' }">{{ activeRepair.status }}</span></div>
    </header>

    <div class="repair-grid">
      <div class="player-stack">
        <video ref="basePlayer" controls preload="metadata" :src="baseVideo ? `/api/v1/assets/${baseVideo.id}/content` : undefined" @timeupdate="syncComposite" />
        <video v-if="candidate" ref="candidatePlayer" class="candidate-layer" muted playsinline preload="auto" :style="{ opacity: candidateOpacity }" :src="`/api/v1/assets/${candidate.id}/content`" />
        <span class="preview-badge">{{ candidate ? (transitionFrames ? `${transitionFrames} 帧叠化预览` : "硬切合并预览") : "原片 A" }}</span>
      </div>
      <aside class="frame-readout">
        <p>当前帧</p><strong>{{ currentFrame }}</strong><code>{{ formatFrameTimecode(currentFrame) }}</code><small>{{ (currentFrame / 24).toFixed(3) }} 秒</small>
        <div><button class="secondary" @click="seek(currentFrame - 1)">← 1 帧</button><button class="secondary" @click="seek(currentFrame + 1)">1 帧 →</button></div>
        <div><button class="secondary" @click="setIn()">I · 设入点</button><button class="secondary" @click="setOut()">O · 设出点</button></div>
        <small>I/O 吸附分镜与边界；按住 Alt 取消。方向键 1 帧，Shift 10 帧。</small>
      </aside>
    </div>

    <div class="frame-timeline">
      <div class="timeline-toolbar"><label>时间轴缩放 <input v-model.number="zoom" type="range" min="25" max="100" step="25" /></label><b>{{ zoom === 100 ? "逐帧精确模式" : `${zoom}%` }}</b></div>
      <div class="range-bars">
        <i v-if="preview" class="generation-range" :style="{ left: `${preview.generationRange.startFrame / totalFrames * 100}%`, width: `${(preview.generationRange.endFrame - preview.generationRange.startFrame) / totalFrames * 100}%` }" />
        <i class="issue-range" :style="{ left: `${issue.startFrame / totalFrames * 100}%`, width: `${issueDuration / totalFrames * 100}%` }" />
        <i class="playhead" :style="{ left: `${currentFrame / totalFrames * 100}%` }" />
      </div>
      <div class="frame-buttons" :class="{ exact: zoom === 100 }">
        <button v-for="frame in timelineFrames" :key="frame" :title="`${frame} · ${formatFrameTimecode(frame)}`" :class="{ issue: frame >= issue.startFrame && frame < issue.endFrame, current: frame === currentFrame }" @click="seek(frame)">{{ zoom === 100 ? frame : formatFrameTimecode(frame).slice(6) }}</button>
      </div>
      <div class="range-inputs">
        <label>入点（包含）<input v-model.number="issue.startFrame" type="number" min="0" :max="issue.endFrame - 1" @change="clampIssue" /><code>{{ formatFrameTimecode(issue.startFrame) }}</code></label>
        <label>出点（不包含）<input v-model.number="issue.endFrame" type="number" :min="issue.startFrame + 1" :max="totalFrames" @change="clampIssue" /><code>{{ formatFrameTimecode(issue.endFrame) }}</code></label>
        <b>{{ issueDuration }} 帧 · {{ (issueDuration / 24).toFixed(3) }} 秒</b>
      </div>
    </div>

    <div class="repair-command">
      <label>希望如何重拍<textarea v-model="prompt" rows="3" /></label>
      <button class="secondary" :disabled="busy || !prompt.trim()" @click="createPreview">查看正式预览（不调用 Provider）</button>
    </div>

    <section v-if="preview" class="paid-preview" data-testid="repair-paid-preview">
      <header><div><p class="eyebrow">Paid preview</p><h3>一次确认只生成一个候选</h3></div><span class="pill">剩余额度 {{ repairQuota.remaining }} / {{ repairQuota.limit }}</span></header>
      <dl>
        <div><dt>基础视频</dt><dd>{{ preview.baseVideoAssetId }}<br><code>{{ preview.videoReference.sha256 }}</code></dd></div>
        <div><dt>用户区间</dt><dd>[{{ preview.issueRange.startFrame }}, {{ preview.issueRange.endFrame }}) · {{ issueDuration }} 帧</dd></div>
        <div><dt>扩展生成区间</dt><dd>[{{ preview.generationRange.startFrame }}, {{ preview.generationRange.endFrame }}) · 实际 {{ preview.providerDurationSeconds }} 秒</dd></div>
        <div><dt>Provider / 模型</dt><dd>{{ preview.provider }} / {{ preview.model }}<br>{{ preview.capabilityRevision }}</dd></div>
        <div><dt>费用</dt><dd>{{ preview.costEstimateStatus === "unmetered_paid" ? "未计价付费调用" : preview.expectedCostMicros }}</dd></div>
        <div><dt>输入哈希</dt><dd><code>{{ preview.inputHash }}</code></dd></div>
      </dl>
      <div class="references"><article><b>reference_video</b><span>{{ preview.videoReference.range.startFrame }}–{{ preview.videoReference.range.endFrame }}</span><small v-if="requiresReferencePublisher && referencePublisherReady" class="publisher-ready">HTTPS 已验证 · {{ runtime?.objectPublisher.publicHost }}</small><small v-else-if="requiresReferencePublisher" class="publisher-blocked">HTTPS 发布器未就绪，禁止付费提交</small></article><article v-for="item in preview.imageReferences" :key="item.role"><b>{{ item.role }}</b><code>{{ item.sha256.slice(0, 12) }}</code></article></div>
      <details><summary>Prompt / Negative Prompt</summary><p>{{ preview.prompt }}</p><p>{{ preview.negativePrompt }}</p></details>
      <button class="primary" :disabled="busy || activeRepair?.status === 'generating' || (requiresReferencePublisher && !referencePublisherReady)" @click="submitOneCandidate">确认并生成一个候选</button>
    </section>

    <p v-if="currentJob" class="notice">Repair Job {{ currentJob.id }} · {{ currentJob.status }}<span v-if="currentJob.providerTaskId"> · Task {{ currentJob.providerTaskId }}</span><span v-if="jobRequestId"> · Request {{ jobRequestId }}</span><span v-if="publicationId"> · Publication {{ publicationId }}</span><span v-if="publicationDeleteAfter"> · Delete after {{ publicationDeleteAfter }}</span></p>
    <p v-if="error" class="notice error">{{ error }}</p>

    <section v-if="candidate && activeRepair" class="candidate-review" data-testid="repair-candidate-review">
      <header><div><p class="eyebrow">Candidate review</p><h3>A/B、接缝循环与等长核心窗口</h3><code>{{ candidate.sha256 }}</code><small v-if="candidateRequestId">Provider request {{ candidateRequestId }}</small></div><span class="pill">{{ candidate.sha256.slice(0, 12) }}</span></header>
      <div class="ab-grid"><article><b>原片 A</b><video controls :src="`/api/v1/assets/${baseVideo?.id}/content`" /></article><article><b>候选 B</b><video controls :src="`/api/v1/assets/${candidate.id}/content`" /></article></div>
      <div class="candidate-controls">
        <button class="secondary" @click="previewSeam('in')">循环入点 ±12 帧</button><button class="secondary" @click="previewSeam('out')">循环出点 ±12 帧</button><button class="secondary" @click="loop = null">停止循环</button>
        <button class="secondary" @click="moveCore(-1)">核心窗口 ←1</button><code>[{{ candidateCore.startFrame }}, {{ candidateCore.endFrame }})</code><button class="secondary" @click="moveCore(1)">核心窗口 +1→</button>
      </div>
      <fieldset><legend>合并预览</legend><label v-for="frames in [0, 2, 4, 6]" :key="frames"><input v-model.number="transitionFrames" type="radio" :value="frames" />{{ frames ? `${frames} 帧叠化` : "硬切（默认）" }}</label></fieldset>
      <div class="checks"><label v-for="(label, key) in qualityLabels" :key="key"><span>{{ label }}</span><select v-model="quality[key]"><option value="">未判断</option><option value="pass">pass</option><option value="warning">warning</option><option value="fail">fail</option></select></label></div>
      <div class="checks seam"><label><span>入点无跳变/双影</span><select v-model="seams.in"><option value="">未判断</option><option value="pass">pass</option><option value="warning">warning</option><option value="fail">fail</option></select></label><label><span>出点无跳变/双影</span><select v-model="seams.out"><option value="">未判断</option><option value="pass">pass</option><option value="warning">warning</option><option value="fail">fail</option></select></label></div>
      <p class="seam-guide">同时检查手部、猫爪、尾巴双影，背景与光线突变，以及根视频原音轨同步。</p>
      <label>验收备注<textarea v-model="notes" rows="2" /></label>
      <footer><button class="secondary" @click="reject">保留候选并标记不通过</button><button class="primary" :disabled="busy || !canApprove" @click="approve">全部通过后批准并合并</button></footer>
    </section>

    <section class="repair-history"><h3>修复历史</h3><article v-for="repair in repairs" :key="repair.id"><code>{{ repair.id }}</code><span>[{{ repair.issueRange.startFrame }}, {{ repair.issueRange.endFrame }})</span><span class="pill">{{ repair.status }}</span><span v-if="repair.approvedEditVersionId">Edit {{ repair.approvedEditVersionId }}</span></article></section>
  </section>
</template>

<style scoped>
.repair { margin-top: 20px; overflow: hidden; }
.repair > header, .paid-preview > header, .candidate-review > header { display: flex; justify-content: space-between; align-items: start; padding: 20px 24px; border-bottom: 1px solid var(--line); }
.repair h2, .repair h3 { margin: 0; }
.repair-state, .candidate-controls, .paid-preview footer { display: flex; gap: 7px; flex-wrap: wrap; }
.repair-grid { display: grid; grid-template-columns: minmax(0, 1fr) 230px; gap: 18px; padding: 20px 24px; background: #292622; color: white; }
.player-stack { position: relative; display: grid; place-items: center; min-height: 390px; }
.player-stack video { grid-area: 1 / 1; height: 380px; aspect-ratio: 9 / 16; background: #111; }
.player-stack .candidate-layer { pointer-events: none; transition: opacity 40ms linear; }
.preview-badge { position: absolute; top: 8px; left: 8px; padding: 6px 9px; border-radius: 8px; background: #111b; font-size: 10px; }
.frame-readout { display: grid; align-content: center; gap: 10px; }
.frame-readout strong { font: 48px Georgia, serif; }.frame-readout code { font-size: 17px; }.frame-readout div { display: flex; gap: 6px; }
.frame-timeline { padding: 18px 24px; background: #f7f1e9; }
.timeline-toolbar, .range-inputs { display: flex; justify-content: space-between; align-items: center; gap: 16px; }.timeline-toolbar label { display: flex; gap: 9px; align-items: center; }
.range-bars { position: relative; height: 34px; margin: 10px 0 5px; border-radius: 8px; background: #ddd3c8; overflow: hidden; }.range-bars i { position: absolute; top: 0; bottom: 0; }.generation-range { background: #d9b86f88; }.issue-range { top: 7px !important; bottom: 7px !important; background: #ce715e; }.playhead { width: 2px; background: #28231f; z-index: 2; }
.frame-buttons { display: flex; gap: 1px; overflow-x: auto; padding: 5px 0 12px; }.frame-buttons button { flex: 0 0 35px; height: 25px; padding: 0; border: 0; color: #776f68; background: #e5ddd4; font-size: 8px; }.frame-buttons.exact button { flex-basis: 22px; font-size: 7px; }.frame-buttons button.issue { color: white; background: #c97965; }.frame-buttons button.current { outline: 2px solid #332d28; z-index: 1; }
.range-inputs label { display: grid; grid-template-columns: auto 80px; gap: 4px 8px; align-items: center; font-size: 10px; }.range-inputs code { grid-column: span 2; }.range-inputs input { width: 80px; }
.repair-command { display: grid; grid-template-columns: 1fr auto; align-items: end; gap: 14px; padding: 18px 24px; }.repair-command label, .candidate-review > label { display: grid; gap: 7px; }
.paid-preview, .candidate-review { margin: 0 24px 22px; border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }.paid-preview { padding-bottom: 20px; }.paid-preview dl { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; padding: 0 20px; }.paid-preview dl div { padding: 10px; border-radius: 9px; background: #f5eee6; }.paid-preview dt { color: var(--muted); font-size: 9px; }.paid-preview dd { margin: 5px 0 0; overflow-wrap: anywhere; font-size: 10px; }.references { display: flex; gap: 7px; padding: 0 20px; overflow-x: auto; }.references article { min-width: 120px; display: grid; gap: 5px; padding: 9px; border: 1px solid var(--line); border-radius: 9px; font-size: 9px; }.references small { overflow-wrap: anywhere; color: var(--muted); line-height: 1.4; }.references .publisher-ready { color: #54735a; }.references .publisher-blocked { color: #a05042; }.paid-preview details { margin: 14px 20px; }.paid-preview > button { margin-left: 20px; }
.candidate-review { padding-bottom: 20px; }.ab-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; padding: 18px 20px; }.ab-grid article { display: grid; gap: 8px; }.ab-grid video { width: 100%; max-height: 300px; background: #111; }.candidate-controls, .candidate-review fieldset, .checks, .candidate-review > label, .candidate-review footer, .seam-guide { margin: 0 20px 14px; }.candidate-review fieldset { display: flex; gap: 16px; border: 1px solid var(--line); border-radius: 10px; }.checks { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }.checks label { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 8px; border-radius: 8px; background: #f5eee6; font-size: 10px; }.checks select { width: 92px; }.checks.seam { grid-template-columns: 1fr 1fr; }.seam-guide { color: var(--muted); font-size: 10px; }.candidate-review footer { display: flex; justify-content: flex-end; gap: 8px; }
.repair-history { padding: 0 24px 22px; }.repair-history article { display: grid; grid-template-columns: 90px 1fr auto 1fr; gap: 8px; align-items: center; padding: 8px 0; border-top: 1px solid var(--line); font-size: 10px; }
@media (max-width: 900px) { .repair-grid { grid-template-columns: 1fr; }.paid-preview dl, .checks { grid-template-columns: 1fr; }.range-inputs { align-items: stretch; flex-direction: column; } }
</style>
