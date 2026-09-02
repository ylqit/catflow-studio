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
  VideoRepairDto,
  WorkspaceDto,
} from "../../api/types";
import {
  allRepairChecksPass,
  clampIssueEnd,
  clampIssueStart,
  EDIT_FRAMES_PER_SECOND,
  formatFrameTimecode,
  isValidIssueRange,
  MAX_ISSUE_FRAMES,
  MIN_ISSUE_FRAMES,
  mediaTimeToFrame,
  moveCandidateCoreRange,
  snapFrame,
  type RepairVerdict,
} from "../../videoRepair";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const baseVideo = computed(() => props.workspace.selections.video ?? null);
const totalFrames = computed(() => {
  const value = baseVideo.value?.metadata.durationFrames;
  return typeof value === "number" ? value : props.workspace.project.targetDurationSeconds * 24;
});
const issue = reactive<FrameRangeDto>({ startFrame: 0, endFrame: MIN_ISSUE_FRAMES });
const prompt = ref(props.workspace.project.theme === "雨天擦爪"
  ? "孩子蹲下，用软毛巾逐只擦干猫爪；猫咪自然抬爪配合，湿爪和地面水印明显减少。"
  : "只重拍所选问题区间，保持人物、猫咪、机位、构图、光线和前后动作连续。");
const currentFrame = ref(0);
const zoom = ref(100);
const preview = ref<SegmentRepairPreviewDto | null>(null);
const repairs = ref<VideoRepairDto[]>([]);
const assets = ref<AssetDto[]>([]);
const edits = ref<EditVersionDto[]>([]);
const runtime = ref<RuntimeBootstrapDto | null>(null);
const currentJob = ref<JobDto | null>(null);
const busy = ref(false);
const previewing = ref(false);
const submitting = ref(false);
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
const activeRepair = computed(() => repairs.value.find((item) => ["generating", "candidate_ready"].includes(item.status)) ?? null);
const candidate = computed(() => assets.value.find((item) => item.id === activeRepair.value?.candidateAssetId) ?? null);
const isArkProvider = computed(() => runtime.value?.provider.name === "ark");
const providerNotice = computed(() => isArkProvider.value
  ? `${runtime.value?.provider.name ?? "ark"} · ${runtime.value?.provider.videoModel ?? ""} · Ark 付费模型`
  : `${runtime.value?.provider.name ?? "Fake"} · ${runtime.value?.provider.videoModel ?? ""} · Fake Provider · 不产生 Ark 费用`);
const generateButtonLabel = computed(() => isArkProvider.value ? "生成修改候选（Ark 付费）" : "生成修改候选（Fake）");
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
const issueIsValid = computed(() => isValidIssueRange(issue, totalFrames.value));
const startMinimum = computed(() => Math.max(0, issue.endFrame - MAX_ISSUE_FRAMES));
const startMaximum = computed(() => Math.max(startMinimum.value, issue.endFrame - MIN_ISSUE_FRAMES));
const endMinimum = computed(() => Math.min(totalFrames.value, issue.startFrame + MIN_ISSUE_FRAMES));
const endMaximum = computed(() => Math.min(totalFrames.value, issue.startFrame + MAX_ISSUE_FRAMES));
const previewIsCurrent = computed(() => Boolean(
  preview.value
  && preview.value.issueRange.startFrame === issue.startFrame
  && preview.value.issueRange.endFrame === issue.endFrame
  && preview.value.instruction === prompt.value.trim(),
));
const promptSummary = computed(() => {
  const value = preview.value?.prompt ?? "";
  return value.length > 180 ? `${value.slice(0, 180)}…` : value;
});
const submitDisabledReason = computed(() => {
  if (totalFrames.value < MIN_ISSUE_FRAMES) return "源视频不足 4.00 秒，不能创建局部修改。";
  if (!issueIsValid.value) return "修改区间必须为 4.00–15.00 秒，且不能超出视频边界。";
  if (!prompt.value.trim()) return "请先填写修改要求。";
  if (previewing.value || !previewIsCurrent.value) return "正在更新当前输入预览。";
  if (activeRepair.value?.status === "generating") return "已有修改任务正在生成。";
  if (isArkProvider.value && !referencePublisherReady.value) return "对象发布器未就绪，无法提交 Ark。";
  return "";
});
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
  [repairs.value, assets.value, edits.value, runtime.value] = await Promise.all([
    api.videoRepairs(props.projectId),
    api.assets(props.projectId),
    api.edits(props.projectId),
    api.runtime(),
  ]);
  const repair = activeRepair.value;
  if (repair) {
    Object.assign(issue, repair.issueRange);
    Object.assign(candidateCore, repair.candidateCoreRange);
    prompt.value = repair.instruction;
    preview.value = repair.preview;
  }
  const durableJob = props.workspace.latestRepairJob;
  if (durableJob && (!repair || durableJob.videoRepairId === repair.id)) {
    currentJob.value = durableJob;
  }
  if (candidate.value?.producingJobId) {
    currentJob.value = await api.job(candidate.value.producingJobId);
  }
}

const draftStorageKey = computed(() => {
  const timelineIdentity = activeEdit.value?.timelineHash ?? baseVideo.value?.sha256 ?? "no-video";
  return `catflow:video-edit-draft:${props.projectId}:${timelineIdentity}`;
});

function restoreDraft() {
  const serialized = sessionStorage.getItem(draftStorageKey.value);
  if (!serialized) return;
  try {
    const draft = JSON.parse(serialized) as { issueRange?: FrameRangeDto; instruction?: string };
    if (draft.issueRange && isValidIssueRange(draft.issueRange, totalFrames.value)) {
      Object.assign(issue, draft.issueRange);
    }
    if (typeof draft.instruction === "string" && draft.instruction.trim()) {
      prompt.value = draft.instruction;
    }
  } catch {
    sessionStorage.removeItem(draftStorageKey.value);
  }
}

function persistDraft() {
  sessionStorage.setItem(draftStorageKey.value, JSON.stringify({
    issueRange: { ...issue },
    instruction: prompt.value,
  }));
}

function changeIssueStart(value: number = issue.startFrame) {
  issue.startFrame = clampIssueStart(value, issue.endFrame, totalFrames.value);
  preview.value = null;
}

function changeIssueEnd(value: number = issue.endFrame) {
  issue.endFrame = clampIssueEnd(value, issue.startFrame, totalFrames.value);
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
  const frame = disableSnap ? currentFrame.value : snapFrame(currentFrame.value, boundaries.value, 3);
  changeIssueStart(frame);
}

function setOut(disableSnap = false) {
  const exclusive = currentFrame.value + 1;
  const frame = disableSnap ? exclusive : snapFrame(exclusive, boundaries.value, 3);
  changeIssueEnd(frame);
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

async function createPreview(): Promise<SegmentRepairPreviewDto | null> {
  if (!baseVideo.value || !issueIsValid.value || !prompt.value.trim()) return null;
  previewing.value = true;
  error.value = "";
  const requestedRange = { ...issue };
  const requestedInstruction = prompt.value.trim();
  try {
    const prepared = await api.previewVideoRepair(props.projectId, {
      baseVideoAssetId: baseVideo.value.id,
      baseEditVersionId: activeEdit.value?.id,
      issueRange: requestedRange,
      instruction: requestedInstruction,
    });
    if (
      requestedRange.startFrame !== issue.startFrame
      || requestedRange.endFrame !== issue.endFrame
      || requestedInstruction !== prompt.value.trim()
    ) return null;
    preview.value = prepared;
    Object.assign(candidateCore, preview.value.candidateCoreRange);
    return prepared;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "片段修复预览失败";
  } finally {
    previewing.value = false;
  }
  return null;
}

async function submitOneCandidate(prepared: SegmentRepairPreviewDto | null = preview.value) {
  if (!prepared || !baseVideo.value || !previewIsCurrent.value) return;
  submitting.value = true;
  error.value = "";
  const scope = `video-edit:${props.projectId}`;
  try {
    currentJob.value = await api.createVideoRepair(props.projectId, {
      baseVideoAssetId: baseVideo.value.id,
      baseEditVersionId: activeEdit.value?.id,
      issueRange: { ...issue },
      instruction: prompt.value.trim(),
      expectedInputHash: prepared.inputHash,
      idempotencyKey: pendingIdempotencyKey(scope, prepared.inputHash),
    });
    settleIdempotencyKey(scope, prepared.inputHash);
    sessionStorage.removeItem(draftStorageKey.value);
    await load();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "候选提交失败";
  } finally {
    submitting.value = false;
  }
}

async function generateCandidate() {
  if (submitDisabledReason.value) return;
  await submitOneCandidate(preview.value);
}

function playSelection() {
  loop.value = { ...issue };
  seek(issue.startFrame);
  void basePlayer.value?.play();
}

async function copyText(value: string) {
  await navigator.clipboard.writeText(value);
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
  const scope = `video-edit-approval:${props.projectId}:${activeRepair.value.id}`;
  const fingerprint = `${activeRepair.value.baseTimelineHash}:${candidate.value.sha256}:${candidateCore.startFrame}:${transitionFrames.value}`;
  try {
    await api.approveVideoRepair(props.projectId, activeRepair.value.id, {
      candidateAssetId: candidate.value.id,
      candidateSourceRange: { ...candidateCore },
      transition: transitionFrames.value
        ? { type: "dissolve", durationFrames: transitionFrames.value }
        : { type: "cut", durationFrames: 0 },
      expectedBaseTimelineHash: activeRepair.value.baseTimelineHash,
      idempotencyKey: pendingIdempotencyKey(scope, fingerprint),
      qualityChecks: completedChecks({ ...quality }),
      seamChecks: completedChecks({ ...seams }),
    });
    settleIdempotencyKey(scope, fingerprint);
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
  restoreDraft();
  if (!activeRepair.value) await createPreview();
  window.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
watch(() => props.workspace.eventCursor, async () => {
  await load();
});
let previewTimer: number | undefined;
watch([() => issue.startFrame, () => issue.endFrame, prompt], () => {
  if (activeRepair.value) return;
  persistDraft();
  preview.value = null;
  window.clearTimeout(previewTimer);
  previewTimer = window.setTimeout(() => { void createPreview(); }, 400);
});
onBeforeUnmount(() => window.clearTimeout(previewTimer));
</script>

<template>
  <section class="repair card" data-testid="video-repair-workspace">
    <header>
      <div><p class="eyebrow">AI video edit</p><h2>AI 修改片段</h2><p>选择一段画面，描述要修改的内容；采用前不会改变当前视频。</p></div>
      <div class="repair-state"><span class="pill">非破坏性版本</span><span v-if="activeRepair" class="pill" :class="{ good: activeRepair.status === 'approved' }">{{ activeRepair.status }}</span></div>
    </header>

    <div class="creator-player">
      <div class="player-stack">
        <video ref="basePlayer" controls preload="metadata" :src="baseVideo ? `/api/v1/assets/${baseVideo.id}/content` : undefined" @timeupdate="syncComposite" />
        <video v-if="candidate" ref="candidatePlayer" class="candidate-layer" muted playsinline preload="auto" :style="{ opacity: candidateOpacity }" :src="`/api/v1/assets/${candidate.id}/content`" />
        <span class="preview-badge">{{ candidate ? (transitionFrames ? `${transitionFrames} 帧叠化预览` : "硬切合并预览") : "当前版本" }}</span>
      </div>
    </div>

    <div class="filmstrip" data-testid="filmstrip-range">
      <div class="filmstrip-cells">
        <video v-for="index in 12" :key="index" muted preload="metadata" :src="baseVideo ? `/api/v1/assets/${baseVideo.id}/content#t=${((index - 1) * totalFrames / 12 / 24).toFixed(2)}` : undefined" />
      </div>
      <div class="selected-window" :style="{ left: `${issue.startFrame / totalFrames * 100}%`, width: `${issueDuration / totalFrames * 100}%` }" />
      <input v-model.number="issue.startFrame" aria-label="修改区间起点" :aria-valuemin="startMinimum" :aria-valuemax="startMaximum" :aria-valuenow="issue.startFrame" :aria-valuetext="`${formatFrameTimecode(issue.startFrame)}，${(issue.startFrame / 24).toFixed(3)} 秒`" class="range-handle range-start" type="range" :min="startMinimum" :max="startMaximum" step="1" @input="changeIssueStart()" />
      <input v-model.number="issue.endFrame" aria-label="修改区间终点" :aria-valuemin="endMinimum" :aria-valuemax="endMaximum" :aria-valuenow="issue.endFrame" :aria-valuetext="`${formatFrameTimecode(issue.endFrame)}，${(issue.endFrame / 24).toFixed(3)} 秒，不包含结束帧`" class="range-handle range-end" type="range" :min="endMinimum" :max="endMaximum" step="1" @input="changeIssueEnd()" />
    </div>
    <div class="selection-summary"><b>{{ (issue.startFrame / 24).toFixed(2) }}s — {{ (issue.endFrame / 24).toFixed(2) }}s</b><span>已选 {{ issueDuration }} 帧 · {{ (issueDuration / 24).toFixed(2) }} 秒</span><span :class="issueIsValid ? 'range-valid' : 'range-invalid'">最短修改区间为 4.00 秒</span><button class="secondary" :disabled="!issueIsValid" @click="playSelection">播放选区</button></div>

    <div class="repair-command">
      <label>修改要求<textarea v-model="prompt" data-testid="edit-instruction" rows="3" placeholder="例如：猫咪自然抬起前爪，孩子用软毛巾擦拭，湿爪印逐渐减少。" /><small>可以同时描述多个相互关联的修改，请按重要程度写清初始状态、变化过程和结束状态。不同时间区间请分别创建修改。</small></label>
      <div class="direct-submit"><span>{{ providerNotice }}<br><small>{{ isArkProvider ? "本次费用将在任务完成后根据 Provider usage 计算" : "用于本机流程验证，不会提交真实 Provider" }}</small></span><button class="primary" :disabled="busy || submitting || Boolean(submitDisabledReason)" @click="generateCandidate"><span v-if="submitting" class="spinner" />{{ generateButtonLabel }}</button></div>
      <p v-if="submitDisabledReason" class="submit-disabled-reason" role="status">{{ submitDisabledReason }}</p>
    </div>

    <section class="prompt-preview" data-testid="repair-prompt-preview">
      <header><div><p class="eyebrow">Ark input preview</p><h3>当前输入预览，尚未提交</h3></div><span class="pill">Preview 不调用 Ark</span></header>
      <p v-if="previewing" class="notice">正在更新当前输入预览……</p>
      <p v-else-if="!preview" class="notice">{{ error || "选择合法区间并填写修改要求后自动编译 Prompt。" }}</p>
      <template v-else>
        <div class="prompt-summary"><b>{{ preview.provider }} · {{ preview.model }} · {{ preview.providerDurationSeconds }} 秒上下文</b><p>{{ promptSummary }}</p></div>
        <details>
          <summary>展开完整生成输入</summary>
          <div class="prompt-actions"><button class="secondary" @click="copyText(preview.prompt)">复制 Prompt</button><button class="secondary" @click="copyText(preview.negativePrompt)">复制 Negative Prompt</button></div>
          <h4>Prompt</h4><p>{{ preview.prompt }}</p>
          <h4>Negative Prompt</h4><p>{{ preview.negativePrompt }}</p>
          <div class="references"><article><b>reference_video</b><span>{{ preview.videoReference.range.startFrame }}–{{ preview.videoReference.range.endFrame }}</span></article><article v-for="item in preview.imageReferences" :key="item.role"><b>{{ item.role }}</b><code>{{ item.sha256.slice(0, 12) }}</code></article></div>
        </details>
      </template>
    </section>

    <details class="advanced-editor">
      <summary>高级编辑 · 逐帧、锚帧、上下文与接缝</summary>
    <div class="repair-grid">
      <div class="player-stack">
        <div class="anchor-placeholder">入点 / 出点锚帧由 Worker 按当前选区精确提取</div>
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
        <label>入点（包含）<input v-model.number="issue.startFrame" type="number" :min="startMinimum" :max="startMaximum" @change="changeIssueStart()" /><code>{{ formatFrameTimecode(issue.startFrame) }}</code></label>
        <label>出点（不包含）<input v-model.number="issue.endFrame" type="number" :min="endMinimum" :max="endMaximum" @change="changeIssueEnd()" /><code>{{ formatFrameTimecode(issue.endFrame) }}</code></label>
        <b>{{ issueDuration }} 帧 · {{ (issueDuration / 24).toFixed(3) }} 秒</b>
      </div>
    </div>

    <section v-if="preview" class="paid-preview" data-testid="repair-technical-preview">
      <header><div><p class="eyebrow">Technical preview</p><h3>本次提交的冻结技术输入</h3></div><span class="pill">一次点击只生成一个候选</span></header>
      <dl>
        <div><dt>基础视频</dt><dd>{{ preview.baseVideoAssetId }}<br><code>{{ preview.videoReference.sha256 }}</code></dd></div>
        <div><dt>用户区间</dt><dd>[{{ preview.issueRange.startFrame }}, {{ preview.issueRange.endFrame }}) · {{ issueDuration }} 帧</dd></div>
        <div><dt>扩展生成区间</dt><dd>[{{ preview.generationRange.startFrame }}, {{ preview.generationRange.endFrame }}) · 实际 {{ preview.providerDurationSeconds }} 秒</dd></div>
        <div><dt>Provider / 模型</dt><dd>{{ preview.provider }} / {{ preview.model }}<br>{{ preview.capabilityRevision }}</dd></div>
        <div><dt>费用状态</dt><dd>{{ preview.costEstimateStatus === "unmetered_paid" ? "待核价；完成后显示实际 usage" : preview.expectedCostMicros }}</dd></div>
        <div><dt>输入哈希</dt><dd><code>{{ preview.inputHash }}</code></dd></div>
      </dl>
      <div class="references"><article><b>reference_video</b><span>{{ preview.videoReference.range.startFrame }}–{{ preview.videoReference.range.endFrame }}</span><small v-if="requiresReferencePublisher && referencePublisherReady" class="publisher-ready">HTTPS 已验证 · {{ runtime?.objectPublisher.publicHost }}</small><small v-else-if="requiresReferencePublisher" class="publisher-blocked">HTTPS 发布器未就绪，禁止付费提交</small></article><article v-for="item in preview.imageReferences" :key="item.role"><b>{{ item.role }}</b><code>{{ item.sha256.slice(0, 12) }}</code></article></div>
    </section>
    </details>

    <p v-if="currentJob" class="notice">修改任务 {{ currentJob.id }} · {{ currentJob.status }}<span v-if="currentJob.providerTaskId"> · Task {{ currentJob.providerTaskId }}</span><span v-if="jobRequestId"> · Request {{ jobRequestId }}</span><span v-if="publicationId"> · Publication {{ publicationId }}</span><span v-if="publicationDeleteAfter"> · Delete after {{ publicationDeleteAfter }}</span><span v-if="currentJob.actualUsage"> · Usage {{ JSON.stringify(currentJob.actualUsage) }}</span><span v-if="currentJob.billingStatus === 'unpriced'"> · 费用待核价</span><span v-else-if="currentJob.actualCostMicros != null"> · ¥{{ (currentJob.actualCostMicros / 1_000_000).toFixed(4) }}</span></p>
    <p v-if="error" class="notice error">{{ error }}</p>

    <section v-if="candidate && activeRepair" class="candidate-review" data-testid="repair-candidate-review">
      <header><div><p class="eyebrow">Candidate review</p><h3>A/B、接缝循环与等长核心窗口</h3><code>{{ candidate.sha256 }}</code><small v-if="candidateRequestId">Provider request {{ candidateRequestId }}</small></div><span class="pill">{{ candidate.sha256.slice(0, 12) }}</span></header>
      <section class="submitted-prompt">
        <b>该候选实际使用的 Prompt · 已提交输入</b>
        <p>{{ currentJob?.inputSnapshot?.prompt ?? activeRepair.prompt }}</p>
        <details>
          <summary>查看 Negative Prompt 与版本来源</summary>
          <p>{{ currentJob?.inputSnapshot?.negativePrompt ?? activeRepair.negativePrompt }}</p>
          <small v-if="currentJob?.inputSnapshot">Compiler {{ currentJob.inputSnapshot.promptCompilerRevision ?? "旧任务未记录" }} · {{ currentJob.inputSnapshot.inputHash }}</small>
          <small v-else>旧任务未记录完整类型化快照；以上内容来自不可变局部修改记录。</small>
        </details>
      </section>
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

    <section class="repair-history"><h3>修复历史</h3><article v-for="repair in repairs" :key="repair.id"><code>{{ repair.id }}</code><span>[{{ repair.issueRange.startFrame }}, {{ repair.issueRange.endFrame }})<small v-if="repair.selectionPolicyVersion === 1 && repair.issueRange.endFrame - repair.issueRange.startFrame < 96">旧规则记录 · 不足 4 秒</small></span><span class="pill">{{ repair.status }}</span><span v-if="repair.approvedEditVersionId">Edit {{ repair.approvedEditVersionId }}</span><details><summary>查看实际 Prompt</summary><p>{{ repair.prompt }}</p><small v-if="repair.legacyEditIntent">旧版类型：{{ repair.legacyEditIntent }}</small></details></article></section>
  </section>
</template>

<style scoped>
.repair { margin-top: 20px; overflow: hidden; }
.repair > header, .paid-preview > header, .candidate-review > header { display: flex; justify-content: space-between; align-items: start; padding: 20px 24px; border-bottom: 1px solid var(--line); }
.repair h2, .repair h3 { margin: 0; }
.repair-state, .candidate-controls, .paid-preview footer { display: flex; gap: 7px; flex-wrap: wrap; }
.creator-player { display: grid; place-items: center; padding: 20px 24px 12px; background: #292622; color: white; }
.creator-player .player-stack { width: min(100%, 660px); }
.filmstrip { position: relative; height: 92px; margin: 20px 24px 8px; overflow: hidden; border: 1px solid #b9aa9b; border-radius: 12px; background: #25221f; }
.filmstrip-cells { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); height: 100%; }
.filmstrip-cells video { width: 100%; height: 100%; object-fit: cover; border-right: 1px solid #ffffff33; pointer-events: none; }
.selected-window { position: absolute; top: 0; bottom: 0; z-index: 1; border: 3px solid #e2765d; background: #e2765d1c; pointer-events: none; }
.range-handle { position: absolute; inset: 0; z-index: 2; width: 100%; height: 100%; margin: 0; appearance: none; background: transparent; pointer-events: none; }
.range-handle::-webkit-slider-thumb { width: 18px; height: 92px; appearance: none; border: 3px solid white; border-radius: 8px; background: #da725b; box-shadow: 0 2px 14px #0008; cursor: ew-resize; pointer-events: auto; }
.range-handle::-moz-range-thumb { width: 18px; height: 86px; border: 3px solid white; border-radius: 8px; background: #da725b; box-shadow: 0 2px 14px #0008; cursor: ew-resize; pointer-events: auto; }
.range-handle:focus-visible { outline: 3px solid #315c9d; outline-offset: -4px; }
.selection-summary { display: flex; align-items: center; gap: 14px; padding: 0 24px 18px; color: var(--muted); }
.selection-summary b { color: var(--ink); }
.selection-summary button { margin-left: auto; }
.range-valid { color: #54735a; }.range-invalid { color: #a05042; font-weight: 700; }
.repair-command { display: grid; grid-template-columns: 1fr; align-items: end; gap: 14px; padding: 18px 24px; border-top: 1px solid var(--line); background: #fbf7f2; }
.repair-command label, .candidate-review > label { display: grid; gap: 7px; }
.repair-command label small { color: var(--muted); line-height: 1.55; }
.direct-submit { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding-top: 4px; color: var(--muted); }
.direct-submit small { line-height: 1.6; }
.submit-disabled-reason { margin: 0; color: #955343; font-size: 10px; }
.prompt-preview { margin: 0 24px 20px; padding: 18px; border: 1px solid var(--line); border-radius: 14px; background: #fff; }
.prompt-preview > header { display: flex; justify-content: space-between; align-items: start; gap: 12px; }
.prompt-preview h3 { margin: 0; }.prompt-summary { margin-top: 14px; padding: 13px; border-radius: 10px; background: #f5eee6; }.prompt-summary p, .prompt-preview details p { color: #615a54; line-height: 1.65; white-space: pre-wrap; }.prompt-preview details { margin-top: 12px; }.prompt-preview summary { cursor: pointer; font-weight: 700; }.prompt-actions { display: flex; flex-wrap: wrap; gap: 7px; margin: 12px 0; }
.advanced-editor { margin: 0 24px 20px; border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.advanced-editor > summary { padding: 14px 18px; cursor: pointer; color: var(--muted); font-weight: 700; background: #f4ede5; }
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
.paid-preview, .candidate-review { margin: 0 24px 22px; border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }.paid-preview { padding-bottom: 20px; }.paid-preview dl { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; padding: 0 20px; }.paid-preview dl div { padding: 10px; border-radius: 9px; background: #f5eee6; }.paid-preview dt { color: var(--muted); font-size: 9px; }.paid-preview dd { margin: 5px 0 0; overflow-wrap: anywhere; font-size: 10px; }.references { display: flex; gap: 7px; padding: 0 20px; overflow-x: auto; }.references article { min-width: 120px; display: grid; gap: 5px; padding: 9px; border: 1px solid var(--line); border-radius: 9px; font-size: 9px; }.references small { overflow-wrap: anywhere; color: var(--muted); line-height: 1.4; }.references .publisher-ready { color: #54735a; }.references .publisher-blocked { color: #a05042; }.paid-preview details { margin: 14px 20px; }.paid-preview > button { margin-left: 20px; }
.candidate-review { padding-bottom: 20px; }.ab-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; padding: 18px 20px; }.ab-grid article { display: grid; gap: 8px; }.ab-grid video { width: 100%; max-height: 300px; background: #111; }.candidate-controls, .candidate-review fieldset, .checks, .candidate-review > label, .candidate-review footer, .seam-guide { margin: 0 20px 14px; }.candidate-review fieldset { display: flex; gap: 16px; border: 1px solid var(--line); border-radius: 10px; }.checks { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }.checks label { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 8px; border-radius: 8px; background: #f5eee6; font-size: 10px; }.checks select { width: 92px; }.checks.seam { grid-template-columns: 1fr 1fr; }.seam-guide { color: var(--muted); font-size: 10px; }.candidate-review footer { display: flex; justify-content: flex-end; gap: 8px; }
.submitted-prompt { margin: 14px 20px; padding: 13px; border: 1px solid var(--line); border-radius: 11px; background: #fff; }.submitted-prompt p { color: #615a54; line-height: 1.6; white-space: pre-wrap; }.submitted-prompt summary { cursor: pointer; font-weight: 700; }.submitted-prompt small { display: block; margin-top: 8px; overflow-wrap: anywhere; color: var(--muted); }
.repair-history { padding: 0 24px 22px; }.repair-history article { display: grid; grid-template-columns: 90px minmax(140px, 1fr) auto minmax(100px, 1fr); gap: 8px; align-items: center; padding: 8px 0; border-top: 1px solid var(--line); font-size: 10px; }.repair-history article > details { grid-column: 1 / -1; }.repair-history article > details p { white-space: pre-wrap; }.repair-history span small { display: block; color: #a05042; }
@media (max-width: 900px) { .repair-grid, .repair-command { grid-template-columns: 1fr; }.paid-preview dl, .checks { grid-template-columns: 1fr; }.range-inputs { align-items: stretch; flex-direction: column; }.direct-submit { align-items: stretch; flex-direction: column; }.filmstrip { height: 72px; }.range-handle::-webkit-slider-thumb { height: 72px; } }
</style>
