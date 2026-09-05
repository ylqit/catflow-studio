<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, ApiError } from "../../api/client";
import type { AssetDto, EditDecisionListV2Dto, EditVersionDto, FrameRangeDto, JobDto, RuntimeBootstrapDto, SegmentRepairPreviewDto, VideoEditDraftDto, VideoRepairDto, VideoReviewDto, WorkspaceDto } from "../../api/types";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { billingPresentation, errorPresentation, jobPresentation, paidModelBlockedReason } from "../../presentation";
import { isValidIssueRange, mediaTimeToFrame } from "../../videoRepair";
import FrameTimeline from "./FrameTimeline.vue";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const route = useRoute(); const router = useRouter();
const draftId = computed(() => typeof route?.query.draftId === "string" ? route.query.draftId : "");
const drafts = ref<VideoEditDraftDto[]>([]); const draft = ref<VideoEditDraftDto | null>(null);
const edits = ref<EditVersionDto[]>([]); const assets = ref<AssetDto[]>([]);
const repairs = ref<VideoRepairDto[]>([]); const jobs = ref<JobDto[]>([]); const reviews = ref<VideoReviewDto[]>([]);
const runtime = ref<RuntimeBootstrapDto | null>(null); const preview = ref<SegmentRepairPreviewDto | null>(null);
const issue = ref<FrameRangeDto>({ startFrame: 0, endFrame: 96 });
const instruction = ref(""); const endStatePolicy = ref<"match_original" | "replace">("match_original"); const desiredEndState = ref("");
const currentFrame = ref(0); const player = ref<HTMLVideoElement | null>(null);
const view = ref<"base" | "comparison">("base"); const loop = ref<FrameRangeDto | null>(null);
const busy = ref(false); const loading = ref(false); const error = ref(""); const technicalError = ref("");
const unresolvedSubmission = ref(false); const confirmReferences = ref(false); const notes = ref("");
const labels: Record<string, string> = { childIdentity: "儿童身份", catIdentity: "猫咪身份", pairScale: "人猫比例", styleConsistency: "画风一致性", anatomy: "肢体与结构", technical: "技术质量", causalChainAndActiveEnding: "因果链与主动结尾" };
const checks = reactive<Record<string, "pass" | "warning" | "fail" | "">>(Object.fromEntries(Object.keys(labels).map(key => [key, ""])));
const source = computed(() => assets.value.find(asset => asset.id === draft.value?.sourceVideoAssetId) ?? props.workspace.selections.video);
const sourceEdit = computed(() => edits.value.find(edit => !edit.editDraftId && edit.active && (edit.formatVersion === 2 ? (edit.edl as EditDecisionListV2Dto).rootVideoAssetId === source.value?.id : 'sourceVideoSelections' in edit.edl && edit.edl.sourceVideoSelections.every(item => item.assetId === source.value?.id))));
const legacyPrepared = computed(() => assets.value.some(asset => asset.role === 'edit_preview' && asset.metadata.editVersionId === sourceEdit.value?.id));
const legacyJob = ref<JobDto | null>(null);
const head = computed(() => edits.value.find(edit => edit.id === draft.value?.headEditVersionId));
const edl = computed(() => head.value?.formatVersion === 2 ? head.value.edl as EditDecisionListV2Dto : null);
const totalFrames = computed(() => edl.value?.videoSegments.reduce((n, segment) => n + segment.durationFrames, 0) ?? Number(source.value?.metadata.durationFrames ?? 0));
const repair = computed(() => repairs.value.find(item => item.preview.editDraftId === draftId.value && item.baseEditVersionId === head.value?.id) ?? null);
const repairJob = computed(() => jobs.value.find(job => job.videoRepairId === repair.value?.id && job.kind === "regenerate_video_segment"));
const terminal = (status: string) => ["succeeded", "failed", "cancelled"].includes(status);
const locked = computed(() => busy.value || unresolvedSubmission.value || (!!repairJob.value && !terminal(repairJob.value.status)) || repair.value?.status === "candidate_ready");
const basePreview = computed(() => assets.value.find(asset => asset.role === "edit_preview" && asset.metadata.editVersionId === head.value?.id && !asset.metadata.repairId));
const comparison = computed(() => assets.value.find(asset => asset.role === "edit_preview" && asset.metadata.editVersionId === head.value?.id && asset.metadata.repairId === repair.value?.id));
const displayedVideo = computed(() => view.value === "comparison" ? comparison.value : basePreview.value);
const localJob = computed(() => jobs.value.find(job => job.kind === "render_edit_preview" && job.frozenInput?.editVersionId === head.value?.id));
const localRunning = computed(() => !!localJob.value && !terminal(localJob.value.status));
const thumbnails = computed(() => assets.value.filter(asset => asset.role === "edit_thumbnail" && asset.metadata.previewAssetId === basePreview.value?.id).map(asset => ({ frame: Number(asset.metadata.sourceFrame), url: `/api/v1/assets/${asset.id}/content` })).sort((a, b) => a.frame - b.frame));
const blockedReason = computed(() => {
  if (!draft.value?.referencesConfirmed) return "历史参考不完整，请明确确认新的参考绑定后创建草稿。";
  if (unresolvedSubmission.value) return "请求结果尚不确定。请重新检查任务记录，不要重复提交。";
  if (repairJob.value?.status === "submission_unknown") return "模型提交状态需要人工确认，请不要再次生成。";
  if (locked.value) return repair.value?.status === "candidate_ready" ? "已有修改候选，请先比较并决定是否应用。" : "当前任务仍在处理，已锁定输入。";
  if (!basePreview.value) return "请先准备完整草稿预览，确认实际编辑画面。";
  if (!isValidIssueRange(issue.value, totalFrames.value)) return "请选择有效的 1–360 帧问题区间。";
  if (!instruction.value.trim()) return "请填写需要改变的动作或状态。";
  if (endStatePolicy.value === "replace" && !desiredEndState.value.trim()) return "请填写期望的结束状态。";
  if (!preview.value) return "请先检查本次修改指令与范围。";
  return paidModelBlockedReason(runtime.value) || (!runtime.value?.objectPublisher.ready ? "局部修改的视频发布通道尚未就绪。" : "");
});
const allPass = computed(() => Object.keys(labels).every(key => checks[key] === "pass"));
let timer: ReturnType<typeof setInterval> | undefined; let callbackId: number | undefined;
let disposed = false; let seekSequence = 0; let restoredHead = ""; let reviewedAsset = "";
let callbackOwner: HTMLVideoElement | null = null; let looping = false;
function fail(reason: unknown, message: string) { const failure = errorPresentation(reason, message); error.value = failure.message; technicalError.value = failure.technicalMessage; }
async function load() {
  if (loading.value) return;
  loading.value = true;
  try {
    [drafts.value, assets.value, edits.value, repairs.value, runtime.value] = await Promise.all([api.videoEditDrafts(props.projectId), api.assets(props.projectId), api.edits(props.projectId), api.videoRepairs(props.projectId), api.runtime()]);
    if (legacyJob.value && !terminal(legacyJob.value.status)) legacyJob.value = await api.job(legacyJob.value.id);
    if (!draftId.value) { draft.value = null; return; }
    draft.value = await api.videoEditDraft(props.projectId, draftId.value);
    jobs.value = (await api.videoDraftJobs(props.projectId, draftId.value)).sort((a, b) => b.createdAt!.localeCompare(a.createdAt!));
    const frozen = repair.value;
    if (frozen && (frozen.status === "candidate_ready" || frozen.status === "generating")) {
      issue.value = { ...frozen.issueRange }; instruction.value = frozen.instruction;
      endStatePolicy.value = frozen.preview.endStatePolicy ?? "match_original"; desiredEndState.value = frozen.preview.desiredEndState ?? "";
      preview.value = frozen.preview; unresolvedSubmission.value = false;
    } else if (head.value && restoredHead !== head.value.id) { issue.value = { startFrame: 0, endFrame: Math.min(96, totalFrames.value) }; preview.value = null; view.value = "base"; }
    if (head.value && restoredHead !== head.value.id) {
      restoredHead = head.value.id; reviews.value = source.value ? await api.videoReviews(props.projectId, source.value.id) : [];
      notes.value = reviews.value[0]?.notes ?? "";
      Object.keys(labels).forEach(key => { checks[key] = reviews.value[0]?.checks[key] ?? ""; });
      const sourceIssue = reviews.value[0]?.issues?.[0];
      // Source-frame notes apply to an unedited root only; edited timelines restore their own review below.
      if (!locked.value && !head.value.parentEditVersionId && sourceIssue && isValidIssueRange(sourceIssue.range, totalFrames.value)) issue.value = { ...sourceIssue.range };
      reviewedAsset = "";
    }
    if (basePreview.value && reviewedAsset !== basePreview.value.id) {
      const saved = await api.videoReviews(props.projectId, basePreview.value.id);
      reviewedAsset = basePreview.value.id;
      if (saved[0]) {
        notes.value = saved[0].notes;
        Object.keys(labels).forEach(key => { checks[key] = saved[0].checks[key] ?? ""; });
        const savedIssue = saved[0].issues?.[0];
        if (!locked.value && savedIssue) issue.value = { ...savedIssue.range };
      }
      reviews.value = [...saved, ...reviews.value];
    }
  } catch (reason) { fail(reason, "编辑记录暂时无法读取"); } finally { loading.value = false; }
}
async function openDraft(existing: VideoEditDraftDto) { await router.replace({ query: { ...route.query, draftId: existing.id } }); restoredHead = ""; await load(); }
async function createDraft() {
  if (!source.value || busy.value) return;
  busy.value = true; const scope = `edit-draft:${props.projectId}`; const fingerprint = `${source.value.id}:${sourceEdit.value?.id ?? ''}:${confirmReferences.value}`;
  try { const created = await api.createVideoEditDraft(props.projectId, { sourceVideoAssetId: source.value.id, sourceEditVersionId: sourceEdit.value?.id, confirmCurrentReferences: confirmReferences.value, idempotencyKey: pendingIdempotencyKey(scope, fingerprint) }); settleIdempotencyKey(scope, fingerprint); await openDraft(created); }
  catch (reason) { fail(reason, "编辑草稿没有成功创建"); } finally { busy.value = false; }
}
async function prepareLegacy() {
  if (!sourceEdit.value || busy.value) return;
  busy.value = true;
  try { legacyJob.value = await api.renderEditPreview(props.projectId, sourceEdit.value.id, `legacy-preview:${sourceEdit.value.id}`); await load(); }
  catch (reason) { fail(reason, "旧剪辑完整预览没有成功开始"); } finally { busy.value = false; }
}
async function rejectCandidate() {
  if (!repair.value || busy.value) return;
  busy.value = true;
  try { await api.rejectVideoRepair(props.projectId, repair.value.id); preview.value = null; view.value = 'base'; await load(); }
  catch (reason) { fail(reason, "候选决定未能保存"); } finally { busy.value = false; }
}
async function renderPreview(withCandidate = false) {
  if (!head.value?.timelineHash || !draft.value || busy.value || localRunning.value) return;
  busy.value = true; error.value = "";
  try { await api.renderDraftPreview(props.projectId, draft.value.id, { expectedEditVersionId: head.value.id, expectedTimelineHash: head.value.timelineHash, repairId: withCandidate ? repair.value?.id : null, idempotencyKey: `preview:${head.value.id}:${withCandidate ? repair.value?.id : 'base'}` }); await load(); }
  catch (reason) { fail(reason, "完整合成预览没有成功开始"); } finally { busy.value = false; }
}
function inputs() { return { baseVideoAssetId: source.value!.id, baseEditVersionId: head.value!.id, editDraftId: draft.value!.id, issueRange: { ...issue.value }, instruction: instruction.value.trim(), endStatePolicy: endStatePolicy.value, desiredEndState: desiredEndState.value.trim() }; }
async function prepare() {
  if (!head.value || !draft.value || locked.value) return;
  busy.value = true; error.value = "";
  try { preview.value = await api.previewVideoRepair(props.projectId, inputs()); } catch (reason) { fail(reason, "修改预览暂时无法准备"); } finally { busy.value = false; }
}
async function generate() {
  if (blockedReason.value || !preview.value || busy.value) return;
  const frozen = preview.value; busy.value = true; error.value = ""; const scope = `video-edit:${props.projectId}`;
  try { const job = await api.createVideoRepair(props.projectId, { ...inputs(), expectedInputHash: frozen.inputHash, idempotencyKey: pendingIdempotencyKey(scope, frozen.inputHash) }); settleIdempotencyKey(scope, frozen.inputHash); jobs.value.unshift(job); await load(); }
  catch (reason) {
    const definiteFailure = reason instanceof ApiError && [400, 401, 403, 404, 409, 422, 503].includes(reason.status);
    unresolvedSubmission.value = !definiteFailure;
    if (definiteFailure && reason.status !== 409) settleIdempotencyKey(scope, frozen.inputHash);
    fail(reason, definiteFailure ? "本次修改任务未创建，请检查提示后再操作" : "暂时无法确认修改任务是否已经创建，请重新检查，不要重复提交");
    if (definiteFailure) await load();
  } finally { busy.value = false; }
}
async function seek(frame: number) {
  if (!Number.isFinite(frame)) return;
  const media = player.value; if (!media) return; const sequence = ++seekSequence; media.pause();
  const target = Math.max(0, Math.min(totalFrames.value - 1, Math.trunc(frame))); const time = (target + 0.001) / 24;
  if (Math.abs(media.currentTime - time) > 0.00001) await new Promise<void>(resolve => {
    const timeout = window.setTimeout(done, 1500);
    function done() { clearTimeout(timeout); media?.removeEventListener("seeked", done); resolve(); }
    media.addEventListener("seeked", done, { once: true }); media.currentTime = time;
  });
  if (sequence === seekSequence) currentFrame.value = mediaTimeToFrame(media.currentTime, 24, totalFrames.value);
}
function presented(_now: number, metadata: VideoFrameCallbackMetadata) {
  if (disposed || !player.value || player.value !== callbackOwner) return;
  currentFrame.value = Math.max(0, Math.min(totalFrames.value - 1, Math.floor(metadata.mediaTime * 24 + 0.01)));
  if (loop.value && metadata.mediaTime * 24 >= loop.value.endFrame) void restartLoop();
  callbackId = player.value.requestVideoFrameCallback(presented);
}
function loaded() { if (callbackId !== undefined) callbackOwner?.cancelVideoFrameCallback?.(callbackId); callbackOwner = player.value; if (callbackOwner?.requestVideoFrameCallback) callbackId = callbackOwner.requestVideoFrameCallback(presented); }
async function restartLoop() { if (!loop.value || looping) return; looping = true; try { await seek(loop.value.startFrame); if (loop.value) await player.value?.play(); } finally { looping = false; } }
async function playSelection() { loop.value = { ...issue.value }; await restartLoop(); }
async function playWhole() { loop.value = null; await seek(0); await player.value?.play(); }
function timeUpdate() { if (!player.value?.requestVideoFrameCallback && player.value) { currentFrame.value = mediaTimeToFrame(player.value.currentTime, 24, totalFrames.value); if (loop.value && player.value.currentTime * 24 >= loop.value.endFrame) void restartLoop(); } }
async function applyToDraft() {
  const job = jobs.value.find(item => item.resultAssetIds.includes(comparison.value?.id ?? "")); const timeline = job?.frozenInput?.edl as EditDecisionListV2Dto | undefined;
  if (!comparison.value || !timeline || !draft.value || !head.value?.timelineHash || !repair.value || busy.value) return;
  busy.value = true; const scope = `apply-draft:${draft.value.id}`; const fingerprint = `${head.value.id}:${repair.value.id}`;
  try { await api.saveVideoDraft(props.projectId, draft.value.id, { expectedEditVersionId: head.value.id, expectedTimelineHash: head.value.timelineHash, repairId: repair.value.id, edl: timeline, idempotencyKey: pendingIdempotencyKey(scope, fingerprint) }); settleIdempotencyKey(scope, fingerprint); await load(); emit("changed"); }
  catch (reason) { fail(reason, "修改候选未能应用到草稿"); } finally { busy.value = false; }
}
async function saveReview(accept = false) {
  if (!basePreview.value || !head.value || busy.value || (accept && !allPass.value)) return; busy.value = true;
  try {
    const result = await api.createVideoReview(props.projectId, { assetId: basePreview.value.id, editVersionId: head.value.id, timelineHash: head.value.timelineHash,
      checks: Object.fromEntries(Object.entries(checks).filter(([, value]) => value !== "")) as Record<string, "pass" | "warning" | "fail">,
      notes: notes.value, issues: notes.value.trim() ? [{ range: { ...issue.value }, note: notes.value }] : [], idempotencyKey: crypto.randomUUID() });
    if (accept) { await api.selectAsset(props.projectId, "video", basePreview.value.id, result.id); emit("changed"); }
    error.value = accept ? "已通过验收并正式选择完整视频；原视频和历史选择仍保留。" : "验收记录已保存，未改变正式视频。";
  } catch (reason) { fail(reason, "验收记录未能保存"); } finally { busy.value = false; }
}
watch([() => issue.value.startFrame, () => issue.value.endFrame, instruction, endStatePolicy, desiredEndState], () => { if (!locked.value) preview.value = null; });
watch(() => props.workspace.eventCursor, () => void load()); watch(draftId, () => { restoredHead = ""; void load(); });
watch(() => displayedVideo.value?.id, () => { loop.value = null; currentFrame.value = 0; if (callbackId !== undefined) callbackOwner?.cancelVideoFrameCallback?.(callbackId); callbackOwner = null; });
onMounted(async () => { await load(); timer = setInterval(() => void load(), 3000); });
onBeforeUnmount(() => { disposed = true; clearInterval(timer); if (callbackId !== undefined) callbackOwner?.cancelVideoFrameCallback?.(callbackId); });
</script>

<template>
  <section class="card draft-editor">
    <header><div><p class="eyebrow">非破坏性编辑</p><h2>编辑草稿 · 修改片段</h2><p>进入编辑不等于验收通过。应用到草稿、完整验收和正式采用是三个独立操作。</p></div><span v-if="head" class="pill">草稿版本 {{ head.revision }} · 正式视频未改变</span></header>
    <div v-if="!draftId" class="setup">
      <p>发现问题的视频也可以进入编辑，无需把不通过项改为通过。</p>
      <label><input v-model="confirmReferences" type="checkbox" />仅在历史视频缺少完整参考时，同意使用当前五张参考创建新的绑定。</label>
      <template v-if="sourceEdit?.formatVersion === 1">
        <p>已有旧版剪辑，必须先保留其裁切、转场和音轨，再创建编辑草稿。</p>
        <button class="secondary" :disabled="busy || (!!legacyJob && !terminal(legacyJob.status))" @click="prepareLegacy">准备旧剪辑完整预览（免费）</button>
        <p v-if="legacyJob" aria-live="polite">{{ jobPresentation(legacyJob.status).label }} {{ legacyJob.error?.message }}</p>
      </template>
      <button class="primary" :disabled="busy || !source || (sourceEdit?.formatVersion === 1 && !legacyPrepared)" @click="createDraft">从当前视频创建编辑草稿</button>
      <p v-if="!source">请在“生成与选择”打开一个视频并点击“进入编辑草稿”，不必正式选择。</p>
      <button v-for="item in drafts" :key="item.id" class="secondary" @click="openDraft(item)">继续草稿 · {{ new Date(item.createdAt).toLocaleString('zh-CN') }}</button>
    </div>
    <template v-else-if="head && draft">
      <div v-if="!draft.referencesConfirmed" class="notice">历史参考不完整，未偷偷使用当前参考。请返回并明确确认新绑定后创建草稿。</div>
      <div class="toolbar"><button class="secondary" :disabled="busy || localRunning" @click="renderPreview(false)">准备完整草稿预览（免费）</button><button class="secondary" @click="load">重新检查任务</button><small>本地视频合成，不调用模型、不正式导出。</small></div>
      <p v-if="localJob" class="notice" aria-live="polite">本地预览：{{ jobPresentation(localJob.status).label }} <span v-if="localJob.error">{{ localJob.error.message }}</span></p>
      <div v-if="displayedVideo" class="player-stage"><span>{{ view === 'comparison' ? '修改候选的完整合成效果 · 尚未应用到草稿' : '当前完整草稿' }}</span><video :key="displayedVideo.id" ref="player" controls :src="`/api/v1/assets/${displayedVideo.id}/content`" @loadeddata="loaded" @timeupdate="timeUpdate" @ended="restartLoop" /><div><button class="secondary" @click="seek(currentFrame - 1)">← 1 帧</button><button class="secondary" @click="seek(currentFrame + 1)">1 帧 →</button><button class="secondary" @click="playWhole">从头完整播放</button><button class="secondary" @click="loop = null">停止循环</button></div></div>
      <FrameTimeline v-model="issue" :total-frames="totalFrames" :current-frame="currentFrame" :disabled="locked" :context="preview?.generationRange" :thumbnails="thumbnails" @seek="seek" @play="playSelection" />
      <div class="form"><label>希望修改什么<textarea v-model="instruction" rows="4" :disabled="locked" placeholder="描述需要改变的动作和状态，以及需要保留的角色、道具、构图和光线。" /></label><label>结束状态策略<select v-model="endStatePolicy" :disabled="locked"><option value="match_original">匹配原结束状态（原来的结尾正确）</option><option value="replace">替换结束状态（原来的结尾也需要修改）</option></select></label><label v-if="endStatePolicy === 'replace'">期望的结束状态<textarea v-model="desiredEndState" rows="2" :disabled="locked" /></label><p v-if="issue.endFrame === totalFrames">本次修改到片尾，出点接缝检查不适用。{{ endStatePolicy === 'replace' ? '不会强制匹配原来的错误尾帧。' : '' }}</p><button class="secondary" :disabled="locked || !instruction.trim()" @click="prepare">检查修改范围与完整指令（免费）</button></div>
      <section v-if="preview" class="prompt-preview"><h3>本次修改预览</h3><dl><dt>问题／替换区间</dt><dd>[{{ preview.issueRange.startFrame }}, {{ preview.issueRange.endFrame }}) · {{ (preview.issueRange.startFrame / 24).toFixed(3) }}–{{ (preview.issueRange.endFrame / 24).toFixed(3) }} 秒</dd><dt>模型生成上下文</dt><dd>[{{ preview.generationRange.startFrame }}, {{ preview.generationRange.endFrame }}) · 模型生成 {{ preview.providerDurationSeconds }} 秒</dd><dt>候选取用区间</dt><dd>[{{ preview.candidateCoreRange.startFrame }}, {{ preview.candidateCoreRange.endFrame }}) · 与替换区间等长</dd></dl><p>上下文补足的末尾画面不进入最终视频。原片保持 {{ totalFrames }} 帧，未改动区间保持原时间线。</p><details open><summary>完整修改指令</summary><pre>{{ preview.prompt }}</pre><p>需要避免的问题：{{ preview.negativePrompt }}</p></details><div class="references"><figure v-for="reference in preview.imageReferences" :key="reference.role"><img v-if="reference.assetId" :src="`/api/v1/assets/${reference.assetId}/content`" :alt="reference.role" /><figcaption>{{ reference.role === 'anchor_in' ? '入点锚帧 · 按完整时间线提取' : reference.role === 'anchor_out' ? '原始出点锚帧' : ({ episode_child: '儿童', episode_cat: '猫咪', pair_scale: '人猫比例', environment: '环境', style_board: '画风' } as Record<string, string>)[reference.role] }}</figcaption></figure></div><details><summary>技术详情</summary><p>输入标识 {{ preview.inputHash }} · 模型 {{ preview.model }}</p><p>草稿版本 {{ preview.baseEditVersionId }} · 完整时间线 {{ preview.baseTimelineHash }}</p></details></section>
      <div class="submit"><p>本次操作产生一次模型费用；不自动重试。<br /><span>{{ blockedReason }}</span></p><button class="primary" :disabled="Boolean(blockedReason) || busy" @click="generate">生成修改结果（付费）</button></div>
      <section v-if="repairJob" class="notice" aria-live="polite"><b>修改进度：{{ jobPresentation(repairJob.status).label }}</b><p v-if="repairJob.status === 'submission_unknown'">提交状态需要人工确认，请不要再次生成。</p><details><summary>查看生成记录</summary><p>任务 {{ repairJob.id }}</p><p v-if="repairJob.providerTaskId">模型任务 {{ repairJob.providerTaskId }}</p><pre>{{ repairJob.actualUsage }}</pre><p>{{ billingPresentation(repairJob.billingStatus, repairJob.actualCostMicros, repairJob.provider).detail }}</p><p v-if="repairJob.error">{{ repairJob.error.message }}</p></details></section>
      <section v-if="repair?.status === 'candidate_ready'" class="comparison"><h3>修改结果已返回 · 尚未应用</h3><p>先生成完整合成视频，再检查入点、道具状态、结尾及原音轨。生成候选本身不表示问题已修复。</p><button class="secondary" :disabled="busy || localRunning" @click="renderPreview(true)">生成完整合成对比（免费）</button><button class="secondary" :disabled="busy" @click="rejectCandidate">不采用本次候选（保留记录）</button><div v-if="comparison"><button class="secondary" @click="view = 'base'">查看修改前完整草稿</button><button class="secondary" @click="view = 'comparison'">查看修改后完整效果</button><button class="primary" :disabled="busy" @click="applyToDraft">应用到草稿（不正式采用）</button></div></section>
      <section v-if="basePreview" class="review"><h3>当前完整草稿的验收</h3><p>可以保存不通过项，继续修复；只有真正全部通过后才能正式选择。</p><div class="checks"><label v-for="(label, key) in labels" :key="key">{{ label }}<select v-model="checks[key]"><option value="">未判断</option><option value="pass">通过</option><option value="warning">需留意</option><option value="fail">不通过</option></select></label></div><label>问题备注（关联当前所选区间）<textarea v-model="notes" rows="3" /></label><button class="secondary" :disabled="busy" @click="saveReview(false)">保存问题与验收记录</button><button class="primary" :disabled="busy || !allPass" @click="saveReview(true)">验收通过并正式选择完整视频</button></section>
      <details class="history"><summary>来源问题记录与不可变版本历史</summary><p v-for="review in reviews" :key="review.id">{{ review.notes }} <span v-for="item in review.issues" :key="item.note">[{{ item.range.startFrame }}, {{ item.range.endFrame }}) {{ item.note }}</span></p><p v-for="edit in edits.filter(item => item.editDraftId === draftId)" :key="edit.id">草稿版本 {{ edit.revision }} · {{ new Date(edit.createdAt).toLocaleString('zh-CN') }} · {{ edit.id === head.id ? '当前编辑' : '历史保留' }}</p></details>
    </template>
    <div v-if="error" class="notice creator-error" role="alert"><p>{{ error }}</p><details v-if="technicalError"><summary>技术详情</summary><code>{{ technicalError }}</code></details></div>
  </section>
</template>

<style scoped>
.draft-editor { margin-top: 20px; overflow: hidden; min-width: 0; }header { display: flex; justify-content: space-between; gap: 15px; padding: 22px 24px; border-bottom: 1px solid var(--line); }h2,h3 { margin: 0 0 8px; }p { line-height: 1.6; }header p,.history { color: var(--muted); }
.setup,.toolbar,.form,.prompt-preview,.submit,.comparison,.review,.history { padding: 20px 24px; }.toolbar,.submit { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }.submit { justify-content: space-between; background: #fbf4ed; }.setup { display: grid; gap: 14px; }.setup input { width: auto; }
.player-stage { display: grid; justify-items: center; gap: 12px; background: #292622; color: white; padding: 20px; }.player-stage video { height: min(55vh, 520px); max-width: 100%; background: #111; }.player-stage button { margin: 3px; }
.form { display: grid; gap: 14px; border-block: 1px solid var(--line); }.form label,.review > label { display: grid; gap: 8px; }textarea { width: 100%; }.prompt-preview { background: #fffdf9; }pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.65; }dl { display: grid; grid-template-columns: 160px 1fr; gap: 10px; }dd { margin: 0; }.references { display: flex; gap: 12px; overflow-x: auto; }figure { margin: 0; min-width: 100px; max-width: 130px; }figure img { width: 100%; height: 100px; object-fit: contain; }figcaption { font-size: 12px; }.notice { margin: 12px 24px; }details { margin-top: 12px; }summary { cursor: pointer; font-weight: 600; }code { overflow-wrap: anywhere; }
.comparison { border: 1px solid #d9b79e; margin: 20px; border-radius: 12px; }.comparison button,.review button { margin: 8px 8px 0 0; }.checks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }.checks label { display: flex; justify-content: space-between; align-items: center; gap: 10px; }.checks select { width: 110px; }@media (max-width: 900px) { .checks { grid-template-columns: 1fr; }dl { grid-template-columns: 1fr; }header { flex-direction: column; } }
</style>
