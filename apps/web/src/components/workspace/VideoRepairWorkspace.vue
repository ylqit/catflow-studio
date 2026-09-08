<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, ApiError } from "../../api/client";
import type { AssetDto, FrameEditTimelineDto, CandidatePlacementDto, EditVersionDto, FrameRangeDto, JobDto, RuntimeBootstrapDto, SegmentRepairPreviewDto, VideoEditDraftDto, VideoRepairDto, VideoReviewDto, WorkspaceDto } from "../../api/types";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { billingPresentation, errorPresentation, jobPresentation, paidModelBlockedReason } from "../../presentation";
import { isValidIssueRange, mediaTimeToFrame } from "../../videoRepair";
import FrameTimeline from "./FrameTimeline.vue";
import LinkedVideoComparison from "./LinkedVideoComparison.vue";
const comparisonPlayer = ref<InstanceType<typeof LinkedVideoComparison> | null>(null);

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
const view = ref<"base" | "raw" | "comparison" | "after">("base");
const selectedRepairId = ref(typeof route.query.candidateId === 'string' ? route.query.candidateId : '');
const selectedTrialId = ref(typeof route.query.trialJobId === 'string' ? route.query.trialJobId : '');
const takeStart = ref(0); const audioPolicy = ref<'preserve_current' | 'use_candidate'>('preserve_current');
const fadeIn = ref(0); const fadeOut = ref(0);
const audioSide = ref<'muted' | 'before' | 'after'>('muted');
const generationMode = ref<'edit_existing' | 'from_frame'>('edit_existing');
const audioMode = ref<'preserve_current' | 'generate_candidate'>('preserve_current');
const soundDescription = ref(''); const anchorStart = ref<number | null>(null); const anchorEnd = ref<number | null>(null);
const audioLabels = { soundIntent: '声音符合本次意图（包含有意无声）', sync: '声音与动作同步', continuity: '前后声音接缝连贯' };
const audioChecks = reactive<Record<string, 'pass' | 'warning' | 'fail' | 'not_applicable' | ''>>({ soundIntent: '', sync: '', continuity: '' });
let pendingFrame: number | null = null;
 const loop = ref<FrameRangeDto | null>(null);
const busy = ref(false); const loading = ref(false); const error = ref(""); const technicalError = ref("");
const unresolvedSubmission = ref(false); const confirmReferences = ref(false); const notes = ref("");
const labels: Record<string, string> = { childIdentity: "儿童身份", catIdentity: "猫咪身份", pairScale: "人猫比例", styleConsistency: "画风一致性", anatomy: "肢体与结构", technical: "技术质量", causalChainAndActiveEnding: "因果链与主动结尾" };
const checks = reactive<Record<string, "pass" | "warning" | "fail" | "">>(Object.fromEntries(Object.keys(labels).map(key => [key, ""])));
const source = computed(() => assets.value.find(asset => asset.id === draft.value?.sourceVideoAssetId) ?? props.workspace.selections.video);
const sourceEdit = computed(() => edits.value.find(edit => !edit.editDraftId && edit.active && ((edit.formatVersion === 2 || edit.formatVersion === 3) ? (edit.edl as FrameEditTimelineDto).rootVideoAssetId === source.value?.id : 'sourceVideoSelections' in edit.edl && edit.edl.sourceVideoSelections.every(item => item.assetId === source.value?.id))));
const legacyPrepared = computed(() => assets.value.some(asset => asset.role === 'edit_preview' && asset.metadata.editVersionId === sourceEdit.value?.id));
const legacyJob = ref<JobDto | null>(null);
const head = computed(() => edits.value.find(edit => edit.id === draft.value?.headEditVersionId));
const edl = computed(() => (head.value?.formatVersion === 2 || head.value?.formatVersion === 3) ? head.value.edl as FrameEditTimelineDto : null);
const totalFrames = computed(() => edl.value?.videoSegments.reduce((n, segment) => n + segment.durationFrames, 0) ?? Number(source.value?.metadata.durationFrames ?? 0));
const draftRepairs = computed(() => repairs.value.filter(item => item.preview.editDraftId === draftId.value));
const candidateGroups = computed(() => {
  const groups = new Map<string, VideoRepairDto[]>();
  for (const item of draftRepairs.value) { const key = item.baseEditVersionId ?? ''; groups.set(key, [...(groups.get(key) ?? []), item]); }
  return [...groups].map(([id, items]) => ({ id, revision: edits.value.find(edit => edit.id === id)?.revision, items }));
});
const repair = computed(() => draftRepairs.value.find(item => item.id === selectedRepairId.value) ?? draftRepairs.value[0] ?? null);
const candidate = computed(() => assets.value.find(asset => asset.id === repair.value?.candidateAssetId));
const repairJob = computed(() => jobs.value.find(job => job.videoRepairId === repair.value?.id && job.kind === "regenerate_video_segment"));
const terminal = (status: string) => ["succeeded", "failed", "cancelled"].includes(status);
const pendingPaidJob = computed(() => jobs.value.find(job => job.kind === 'regenerate_video_segment' && !terminal(job.status)));
const locked = computed(() => busy.value || unresolvedSubmission.value || !!pendingPaidJob.value);
const basePreview = computed(() => assets.value.find(asset => asset.role === "edit_preview" && asset.metadata.editVersionId === head.value?.id && !asset.metadata.repairId));
const trials = computed(() => jobs.value.filter(job => job.kind === 'render_edit_preview' && job.frozenInput?.repairId === repair.value?.id));
const trial = computed(() => trials.value.find(job => job.id === selectedTrialId.value) ?? trials.value[0]);
const trialVideo = computed(() => assets.value.find(asset => asset.role === 'edit_preview' && asset.producingJobId === trial.value?.id));
const parentVersion = computed(() => edits.value.find(edit => edit.id === repair.value?.baseEditVersionId));
const trialBase = computed(() => assets.value.find(asset => asset.role === 'edit_trial_base' && asset.producingJobId === trial.value?.id));
const appliedSegments = computed(() => { let start = 0; return ((view.value === 'base' ? edl.value : parentVersion.value?.edl as FrameEditTimelineDto | undefined)?.videoSegments ?? []).flatMap(segment => { const first = start; start += segment.durationFrames; return segment.repairId ? [{ startFrame: first, endFrame: start, label: '已应用修改' }] : []; }); });
const candidateThumbnails = computed(() => [...new Map(assets.value.filter(asset => asset.role === 'edit_thumbnail' && asset.metadata.previewAssetId === candidate.value?.id).map(asset => [Number(asset.metadata.sourceFrame), { frame: Number(asset.metadata.sourceFrame), url: `/api/v1/assets/${asset.id}/content` }])).values()].sort((a, b) => a.frame - b.frame));
const comparison = computed(() => assets.value.find(asset => asset.role === 'edit_comparison' && asset.producingJobId === trial.value?.id && asset.metadata.audioSide === (audioSide.value === 'after' ? 'after' : 'before')));
const displayedVideo = computed(() => view.value === 'raw' ? candidate.value : view.value === 'comparison' ? comparison.value : view.value === 'after' ? trialVideo.value : basePreview.value);
const mediaFrames = computed(() => view.value === 'raw' ? Number(candidate.value?.metadata.durationFrames ?? 0) : totalFrames.value);
const mediaRate = computed(() => view.value === 'raw' ? Number(candidate.value?.metadata.frameRateNumerator ?? 24) / Number(candidate.value?.metadata.frameRateDenominator ?? 1) : 24);
const localJob = computed(() => jobs.value.find(job => job.kind === "render_edit_preview" && !terminal(job.status)) ?? trial.value ?? jobs.value.find(job => job.kind === 'render_edit_preview'));
const localRunning = computed(() => !!localJob.value && !terminal(localJob.value.status));
const takeLength = computed(() => repair.value ? repair.value.issueRange.endFrame - repair.value.issueRange.startFrame : 0);
const placement = computed<CandidatePlacementDto>(() => ({ candidateSourceRange: { startFrame: takeStart.value, endFrame: takeStart.value + takeLength.value }, audioPolicy: audioPolicy.value, fadeInMs: audioPolicy.value === 'use_candidate' ? fadeIn.value : 0, fadeOutMs: audioPolicy.value === 'use_candidate' ? fadeOut.value : 0 }));
const trialStale = computed(() => {
  const frozen = trial.value?.frozenInput?.placement as CandidatePlacementDto | undefined;
  return !frozen || frozen.candidateSourceRange.startFrame !== takeStart.value || frozen.candidateSourceRange.endFrame !== placement.value.candidateSourceRange.endFrame || frozen.audioPolicy !== audioPolicy.value || (frozen.fadeInMs ?? 0) !== placement.value.fadeInMs || (frozen.fadeOutMs ?? 0) !== placement.value.fadeOutMs;
});
const placementError = computed(() => !candidate.value ? '候选媒体尚未落盘。' : (candidate.value.metadata.frameRateNumerator !== 24 || candidate.value.metadata.frameRateDenominator !== 1) ? '候选实际帧率不是 24 fps，不能自动变速或补帧。' : !Number.isInteger(takeStart.value) || takeStart.value < 0 || placement.value.candidateSourceRange.endFrame > Number(candidate.value.metadata.durationFrames ?? 0) ? '素材不足，无法形成等长试装。请移动取用起点。' : audioPolicy.value === 'use_candidate' && !candidate.value.metadata.hasAudio ? '候选没有音轨，可免费改为保留父草稿声音。' : !Number.isInteger(fadeIn.value) || !Number.isInteger(fadeOut.value) || fadeIn.value < 0 || fadeOut.value < 0 || (fadeIn.value + fadeOut.value) * 24 > takeLength.value * 1000 ? '淡入淡出须为非负整数毫秒，总长不得超过取用范围。' : '');
const canApply = computed(() => !busy.value && !trialStale.value && trial.value?.status === 'succeeded' && !!trialVideo.value && repair.value?.baseEditVersionId === head.value?.id && repair.value?.status === 'candidate_ready');
const thumbnails = computed(() => assets.value.filter(asset => asset.role === "edit_thumbnail" && asset.metadata.previewAssetId === (view.value === 'comparison' ? trialVideo.value?.id : displayedVideo.value?.id)).map(asset => ({ frame: Number(asset.metadata.sourceFrame), url: `/api/v1/assets/${asset.id}/content` })).sort((a, b) => a.frame - b.frame));
const needsSoundReview = computed(() => head.value?.formatVersion === 3 || !!basePreview.value?.metadata.requestedAudio || (head.value?.formatVersion === 2 && !!source.value?.metadata.requestedAudio));
function soundState(asset?: AssetDto) { if (!asset) return '尚未检测'; if (asset.metadata.audioRequestMissing) return '请求声音但未返回音轨'; if (asset.metadata.audioState === 'silent') return '存在音轨，内容为静音'; if (asset.metadata.hasAudio === false) return '无音轨'; if (asset.metadata.hasAudio) return `${asset.metadata.audioChannels === 1 ? '单声道' : '有声音轨'} · ${asset.metadata.audioSampleRate ?? '未知'} Hz`; return '历史音轨信息未检测'; }
function inCurrentDraft(item: VideoRepairDto) { return !!edl.value?.videoSegments.some(segment => segment.repairId === item.id) || (edl.value?.format === 'catflow-edl-v3' && edl.value.audio.segments.some(segment => segment.repairId === item.id)); }
const blockedReason = computed(() => {
  if (generationMode.value === "edit_existing" && !draft.value?.referencesConfirmed) return "历史参考不完整，请明确确认新的参考绑定后创建草稿。";
  if (unresolvedSubmission.value) return "请求结果尚不确定。请重新检查任务记录，不要重复提交。";
  if (pendingPaidJob.value?.status === "submission_unknown") return "模型提交状态需要人工确认，请不要再次生成。";
  if (locked.value) return "当前任务仍在处理，已锁定输入。";
  if (!basePreview.value) return "请先准备完整草稿预览，确认实际编辑画面。";
  if (!isValidIssueRange(issue.value, totalFrames.value)) return "请选择有效的 1–360 帧问题区间。";
  if (!instruction.value.trim()) return "请填写需要改变的动作或状态。";
  if (endStatePolicy.value === "replace" && !desiredEndState.value.trim()) return "请填写期望的结束状态。";
  if (generationMode.value === 'from_frame' && anchorStart.value === null) return '请明确选定正确起始帧。';
  if (!preview.value) return "请先检查本次修改指令与范围。";
  return paidModelBlockedReason(runtime.value) || (generationMode.value === "edit_existing" && !runtime.value?.objectPublisher.ready ? "局部修改的视频发布通道尚未就绪。" : "");
});
const allPass = computed(() => Object.keys(labels).every(key => checks[key] === "pass") && (!needsSoundReview.value || Object.keys(audioLabels).every(key => audioChecks[key] === (key !== "soundIntent" && basePreview.value?.metadata.hasAudio === false ? "not_applicable" : "pass"))));
let timer: ReturnType<typeof setInterval> | undefined; let callbackId: number | undefined;
let disposed = false; let seekSequence = 0; let restoredHead = ""; let reviewedAsset = "";
let initializedCandidate = "";
let callbackOwner: HTMLVideoElement | null = null; let looping = false;
function fail(reason: unknown, message: string) { const failure = errorPresentation(reason, message); error.value = failure.message; technicalError.value = failure.technicalMessage; }
async function load() {
  if (disposed || loading.value) return;
  const projectId = props.projectId;
  const requestedDraftId = draftId.value;
  loading.value = true;
  try {
    const projectState = await Promise.all([api.videoEditDrafts(projectId), api.assets(projectId), api.edits(projectId), api.videoRepairs(projectId), api.runtime()]);
    if (disposed || props.projectId !== projectId || draftId.value !== requestedDraftId) return;
    [drafts.value, assets.value, edits.value, repairs.value, runtime.value] = projectState;
    if (legacyJob.value && !terminal(legacyJob.value.status)) legacyJob.value = await api.job(legacyJob.value.id);
    if (disposed || props.projectId !== projectId || draftId.value !== requestedDraftId) return;
    if (!requestedDraftId) { draft.value = null; return; }
    const [loadedDraft, loadedJobs] = await Promise.all([api.videoEditDraft(projectId, requestedDraftId), api.videoDraftJobs(projectId, requestedDraftId)]);
    if (disposed || props.projectId !== projectId || draftId.value !== requestedDraftId) return;
    draft.value = loadedDraft;
    jobs.value = loadedJobs.sort((a, b) => b.createdAt!.localeCompare(a.createdAt!));
    if (repair.value && initializedCandidate !== repair.value.id) {
      initializedCandidate = repair.value.id;
      restorePlacement();
      if (pendingPaidJob.value) reuseParameters();
    }
    if (head.value && restoredHead !== head.value.id && !pendingPaidJob.value) {
      issue.value = { startFrame: 0, endFrame: Math.min(96, totalFrames.value) }; preview.value = null; view.value = !restoredHead && ['raw', 'comparison', 'after'].includes(String(route.query.view)) ? route.query.view as typeof view.value : 'base';
    }
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
      Object.keys(audioLabels).forEach(key => { audioChecks[key] = saved[0]?.audioChecks?.[key as keyof typeof audioLabels] ?? ''; });
      if (saved[0]) {
        notes.value = saved[0].notes;
        Object.keys(labels).forEach(key => { checks[key] = saved[0].checks[key] ?? ""; });
        const savedIssue = saved[0].issues?.[0];
        if (!locked.value && savedIssue) issue.value = { ...savedIssue.range };
      }
      reviews.value = [...saved, ...reviews.value];
    }
  } catch (reason) { if (!disposed) fail(reason, "编辑记录暂时无法读取"); } finally { loading.value = false; }
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
function restorePlacement() {
  const frozen = trial.value?.frozenInput?.placement as CandidatePlacementDto | undefined;
  takeStart.value = frozen?.candidateSourceRange.startFrame ?? repair.value?.candidateCoreRange?.startFrame ?? repair.value?.preview.candidateCoreRange.startFrame ?? 0;
  audioPolicy.value = frozen?.audioPolicy ?? (repair.value?.preview.audioMode === 'generate_candidate' && candidate.value?.metadata.hasAudio ? 'use_candidate' : 'preserve_current');
  fadeIn.value = frozen?.fadeInMs ?? 0; fadeOut.value = frozen?.fadeOutMs ?? 0;
}
async function chooseCandidate(item: VideoRepairDto) {
  selectedRepairId.value = item.id; selectedTrialId.value = ''; initializedCandidate = item.id; restorePlacement();
  setView('raw'); await router.replace({ query: { ...route.query, candidateId: item.id, trialJobId: undefined, view: 'raw' } });
}
async function chooseTrial(id: string) {
  selectedTrialId.value = id; restorePlacement(); setView(trialBase.value ? 'comparison' : 'after');
  await router.replace({ query: { ...route.query, candidateId: repair.value?.id, trialJobId: id, view: view.value } });
}
function reuseParameters() {
  if (!repair.value) return;
  const frozen = repair.value.preview;
  instruction.value = repair.value.instruction; issue.value = { ...repair.value.issueRange };
  endStatePolicy.value = frozen.endStatePolicy ?? 'match_original'; desiredEndState.value = frozen.desiredEndState ?? '';
  generationMode.value = frozen.generationMode ?? 'edit_existing'; audioMode.value = frozen.audioMode ?? 'preserve_current';
  soundDescription.value = frozen.soundDescription ?? ''; anchorStart.value = frozen.anchorStartFrame ?? null; anchorEnd.value = frozen.anchorEndFrame ?? null;
  preview.value = pendingPaidJob.value ? frozen : null;
  if (!pendingPaidJob.value) setView('base');
}
async function renderPreview(withCandidate = false) {
  const parent = withCandidate ? edits.value.find(edit => edit.id === repair.value?.baseEditVersionId) : head.value;
  if (!parent?.timelineHash || !draft.value || busy.value || localRunning.value || (withCandidate && placementError.value)) return;
  busy.value = true; error.value = '';
  const fingerprint = JSON.stringify({ parent: parent.id, repair: withCandidate ? repair.value?.id : null, placement: withCandidate ? placement.value : null });
  const scope = `trial:${draft.value.id}`;
  try {
    const job = await api.renderDraftPreview(props.projectId, draft.value.id, { expectedEditVersionId: parent.id, expectedTimelineHash: parent.timelineHash, repairId: withCandidate ? repair.value?.id : null, placement: withCandidate ? placement.value : null, idempotencyKey: pendingIdempotencyKey(scope, fingerprint) });
    settleIdempotencyKey(scope, fingerprint);
    if (withCandidate && job) {
      selectedTrialId.value = job.id;
      await router.replace({ query: { ...route.query, candidateId: repair.value?.id, trialJobId: job.id } });
    }
    await load();
  } catch (reason) { fail(reason, '完整试装没有成功开始'); } finally { busy.value = false; }
}
function setView(next: typeof view.value) { pendingFrame = next === 'raw' || view.value === 'raw' ? 0 : currentFrame.value; view.value = next; loop.value = null; void router.replace({ query: { ...route.query, view: next } }); }

function inspectCandidateFrame(frame: number) { if (view.value === 'raw') { void seek(frame); return; } setView('raw'); pendingFrame = frame; }
function inputs() { return { baseVideoAssetId: source.value!.id, baseEditVersionId: head.value!.id, editDraftId: draft.value!.id, issueRange: { ...issue.value }, instruction: instruction.value.trim(), endStatePolicy: endStatePolicy.value, desiredEndState: desiredEndState.value.trim(), generationMode: generationMode.value, audioMode: audioMode.value, soundDescription: soundDescription.value.trim(), anchorStartFrame: generationMode.value === "from_frame" ? anchorStart.value : null, anchorEndFrame: generationMode.value === "from_frame" && endStatePolicy.value !== "replace" ? anchorEnd.value : null }; }
async function prepare() {
  if (!head.value || !draft.value || locked.value) return;
  busy.value = true; error.value = "";
  try { preview.value = await api.previewVideoRepair(props.projectId, inputs()); } catch (reason) { fail(reason, "修改预览暂时无法准备"); } finally { busy.value = false; }
}
async function generate() {
  if (blockedReason.value || !preview.value || busy.value) return;
  const frozen = preview.value; busy.value = true; error.value = ""; const scope = `video-edit:${props.projectId}`;
  try {
    const job = await api.createVideoRepair(props.projectId, { ...inputs(), expectedInputHash: frozen.inputHash, idempotencyKey: pendingIdempotencyKey(scope, frozen.inputHash) });
    settleIdempotencyKey(scope, frozen.inputHash);
    jobs.value.unshift(job);
    if (job.videoRepairId) {
      selectedRepairId.value = job.videoRepairId;
      selectedTrialId.value = '';
      await router.replace({ query: { ...route.query, candidateId: job.videoRepairId, trialJobId: undefined } });
    }
    await load();
  }
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
  if (view.value === 'comparison') { await comparisonPlayer.value?.seek(frame); return; }
  const media = player.value; if (!media) return; const sequence = ++seekSequence; media.pause();
  const target = Math.max(0, Math.min(mediaFrames.value - 1, Math.trunc(frame))); const time = (target + 0.001) / mediaRate.value;
  if (Math.abs(media.currentTime - time) > 0.00001) await new Promise<void>(resolve => {
    const timeout = window.setTimeout(done, 1500);
    function done() { clearTimeout(timeout); media?.removeEventListener("seeked", done); resolve(); }
    media.addEventListener("seeked", done, { once: true }); media.currentTime = time;
  });
  if (sequence === seekSequence) currentFrame.value = mediaTimeToFrame(media.currentTime, mediaRate.value, mediaFrames.value);
}
function presented(_now: number, metadata: VideoFrameCallbackMetadata) {
  if (disposed || !player.value || player.value !== callbackOwner) return;
  currentFrame.value = Math.max(0, Math.min(mediaFrames.value - 1, Math.floor(metadata.mediaTime * mediaRate.value + 0.01)));
  if (loop.value && metadata.mediaTime * mediaRate.value >= loop.value.endFrame) void restartLoop();
  callbackId = player.value.requestVideoFrameCallback(presented);
}
function loaded() { if (player.value) player.value.muted = view.value === 'comparison' && audioSide.value === 'muted'; if (pendingFrame !== null) { void seek(pendingFrame); pendingFrame = null; } if (callbackId !== undefined) callbackOwner?.cancelVideoFrameCallback?.(callbackId); callbackOwner = player.value; if (callbackOwner?.requestVideoFrameCallback) callbackId = callbackOwner.requestVideoFrameCallback(presented); }
async function restartLoop() { if (!loop.value || looping) return; looping = true; try { await seek(loop.value.startFrame); if (loop.value) await player.value?.play(); } finally { looping = false; } }
async function playSelection() { if (view.value === 'comparison') { await comparisonPlayer.value?.playRange(repair.value?.issueRange ?? issue.value); return; } loop.value = { ...(view.value === 'base' ? issue.value : repair.value?.issueRange ?? issue.value) }; await restartLoop(); }
async function playWhole() { if (view.value === 'comparison') { await comparisonPlayer.value?.playWhole(); return; } loop.value = null; await seek(0); await player.value?.play(); }
async function playJunction() { const selected = repair.value?.issueRange ?? issue.value; const window = { startFrame: Math.max(0, selected.startFrame - 24), endFrame: Math.min(totalFrames.value, selected.endFrame + 24) }; if (view.value === 'comparison') await comparisonPlayer.value?.playRange(window); else { loop.value = window; await restartLoop(); } }
function timeUpdate() { if (!player.value?.requestVideoFrameCallback && player.value) { currentFrame.value = mediaTimeToFrame(player.value.currentTime, mediaRate.value, mediaFrames.value); if (loop.value && player.value.currentTime * mediaRate.value >= loop.value.endFrame) void restartLoop(); } }
async function applyToDraft() {
  if (!canApply.value || !trial.value || !draft.value || !head.value?.timelineHash || !repair.value) return;
  busy.value = true; const scope = `apply-draft:${draft.value.id}`; const fingerprint = `${head.value.id}:${trial.value.id}`;
  try { await api.saveVideoDraft(props.projectId, draft.value.id, { expectedEditVersionId: head.value.id, expectedTimelineHash: head.value.timelineHash, repairId: repair.value.id, previewJobId: trial.value.id, idempotencyKey: pendingIdempotencyKey(scope, fingerprint) }); settleIdempotencyKey(scope, fingerprint); await load(); emit('changed'); }
  catch (reason) { fail(reason, '修改试装未能应用到草稿'); } finally { busy.value = false; }
}
async function saveReview(accept = false) {
  if (!basePreview.value || !head.value || busy.value || (accept && !allPass.value)) return; busy.value = true;
  try {
    const result = await api.createVideoReview(props.projectId, { assetId: basePreview.value.id, editVersionId: head.value.id, timelineHash: head.value.timelineHash,
      checks: Object.fromEntries(Object.entries(checks).filter(([, value]) => value !== "")) as Record<string, "pass" | "warning" | "fail">,
      audioChecks: Object.fromEntries(Object.entries(audioChecks).filter(([, value]) => value !== '')) as Record<keyof typeof audioLabels, 'pass' | 'warning' | 'fail' | 'not_applicable'>,
      notes: notes.value, issues: notes.value.trim() ? [{ range: { ...issue.value }, note: notes.value }] : [], idempotencyKey: crypto.randomUUID() });
    if (accept) { await api.selectAsset(props.projectId, "video", basePreview.value.id, result.id); emit("changed"); }
    error.value = accept ? "已通过验收并正式选择完整视频；原视频和历史选择仍保留。" : "验收记录已保存，未改变正式视频。";
  } catch (reason) { fail(reason, "验收记录未能保存"); } finally { busy.value = false; }
}
watch([() => issue.value.startFrame, () => issue.value.endFrame, instruction, endStatePolicy, desiredEndState, generationMode, audioMode, soundDescription, anchorStart, anchorEnd], () => { if (!loading.value) preview.value = null; });
watch(() => props.workspace.eventCursor, () => void load()); watch(draftId, () => { restoredHead = ""; void load(); });
watch(() => displayedVideo.value?.id, () => { loop.value = null; currentFrame.value = pendingFrame ?? 0; if (callbackId !== undefined) callbackOwner?.cancelVideoFrameCallback?.(callbackId); callbackOwner = null; });
onMounted(async () => { await load(); if (!disposed) timer = setInterval(() => void load(), 3000); });
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
      <div v-if="!draft.referencesConfirmed" class="notice">此草稿未绑定完整身份参考。可选择“从正确起点重新生成”；使用现有片段路径需要先确认参考绑定。</div>
      <div class="toolbar"><button class="secondary" :disabled="busy || localRunning" @click="renderPreview(false)">准备完整草稿预览（免费）</button><button class="secondary" @click="load">重新检查任务</button><small>本地视频合成，不调用模型、不正式导出。</small></div>
      <p v-if="localJob" class="notice" aria-live="polite">本地预览：{{ jobPresentation(localJob.status).label }} <span v-if="localJob.error">{{ localJob.error.message }}</span></p>
      <div class="candidate-workbench">
        <aside class="candidate-list" aria-label="候选素材列表">
          <h3>候选素材</h3><p v-if="!candidateGroups.length">生成结果会保留在这里。</p>
          <section v-for="group in candidateGroups" :key="group.id"><h4>父草稿版本 {{ group.revision ?? '历史' }} {{ group.id === head.id ? '· 当前' : '' }}</h4>
            <button v-for="item in group.items" :key="item.id" class="candidate-card" :class="{ selected: repair?.id === item.id }" :aria-pressed="repair?.id === item.id" @click="chooseCandidate(item)">
              <video v-if="item.candidateAssetId" :src="`/api/v1/assets/${item.candidateAssetId}/content#t=0.1`" muted preload="metadata" /><b>{{ item.instruction.slice(0, 44) }}{{ item.instruction.length > 44 ? '…' : '' }}</b><small>[{{ item.issueRange.startFrame }}, {{ item.issueRange.endFrame }}) · {{ (item.issueRange.startFrame / 24).toFixed(2) }}–{{ (item.issueRange.endFrame / 24).toFixed(2) }} 秒</small><span>{{ item.candidateAssetId ? '候选已返回' : item.status === 'generating' ? '正在生成' : '生成记录' }}</span>
              <small>{{ item.approvedEditVersionId ? '曾应用' : '未应用' }} · {{ inCurrentDraft(item) ? '当前草稿正在使用' : '当前草稿未使用' }}</small>
            </button>
          </section>
        </aside>
        <div class="viewing-area">
          <div class="view-heading"><strong>当前草稿 · 版本 {{ head.revision }}</strong><span>正在查看：{{ view === 'base' ? '当前完整草稿' : view === 'raw' ? '模型原始结果' : view === 'comparison' ? '真实试装对比' : '试装后完整视频' }}</span></div>
          <nav class="view-tabs" aria-label="查看内容"><button class="secondary" @click="setView('base')">当前草稿</button><button class="secondary" :disabled="!candidate" @click="setView('raw')">模型原始结果</button><button class="secondary" :disabled="!trialBase || !trialVideo" @click="setView('comparison')">试装对比</button><button class="secondary" :disabled="!trialVideo" @click="setView('after')">修改后完整播放</button></nav>
          <p v-if="(view === 'comparison' || view === 'after') && trialStale" class="notice" role="status">对应上一份试装。当前取用或声音设置已变化，请重新准备试装。</p>
          <p v-if="view !== 'base' && repair" class="comparison-origin">本次比较基于父草稿 v{{ parentVersion?.revision }} → 所选试装 · 候选 [{{ takeStart }}, {{ takeStart + takeLength }}) → 替换 [{{ repair.issueRange.startFrame }}, {{ repair.issueRange.endFrame }})</p>
          <LinkedVideoComparison v-if="view === 'comparison' && trialBase && trialVideo" ref="comparisonPlayer" :before="`/api/v1/assets/${trialBase.id}/content`" :after="`/api/v1/assets/${trialVideo.id}/content`" :total-frames="totalFrames" :parent-revision="parentVersion?.revision" :stale="trialStale" @frame="currentFrame = $event" />
          <div v-else-if="displayedVideo" class="player-stage">
            <video :key="displayedVideo.id" ref="player" controls :src="`/api/v1/assets/${displayedVideo.id}/content`" @loadeddata="loaded" @timeupdate="timeUpdate" @ended="restartLoop" />
            <small>{{ currentFrame }} / {{ mediaFrames - 1 }} 帧 · {{ soundState(displayedVideo) }}</small>
          </div>
          <p v-else class="notice">请使用下方的本地免费准备按钮生成预览。</p>
          <div class="transport"><button class="secondary" @click="seek(currentFrame - 1)">← 1 帧</button><button class="secondary" @click="seek(currentFrame + 1)">1 帧 →</button><button class="secondary" @click="playWhole">从头完整播放</button><button v-if="view !== 'raw'" class="secondary" @click="playSelection">只看修改区间</button><button v-if="view !== 'raw'" class="secondary" @click="playJunction">查看接头</button><button class="secondary" @click="loop = null; comparisonPlayer?.stopLoop()">停止循环</button></div>
          <FrameTimeline :model-value="view === 'base' ? issue : repair?.issueRange ?? issue" @update:model-value="issue = $event" :total-frames="totalFrames" :current-frame="view === 'raw' ? 0 : currentFrame" :disabled="locked || view !== 'base'" :context="view === 'base' ? preview?.generationRange : null" :thumbnails="view === 'raw' ? [] : thumbnails" :segments="appliedSegments" compact label="草稿时间轴 · 本次替换" @seek="view === 'raw' ? setView('base') : seek($event)" @play="playSelection" />
          <template v-if="candidate && repair">
            <FrameTimeline :model-value="placement.candidateSourceRange" @update:model-value="takeStart = $event.startFrame" :total-frames="Number(candidate.metadata.durationFrames ?? 0)" :rate="Number(candidate.metadata.frameRateNumerator ?? 24) / Number(candidate.metadata.frameRateDenominator ?? 1)" :current-frame="view === 'raw' ? currentFrame : takeStart" :thumbnails="candidateThumbnails" fixed-length compact label="候选素材轴 · 等长取用" @seek="inspectCandidateFrame" />
            <p class="range-mapping">候选 [{{ takeStart }}, {{ takeStart + takeLength }}) → 父草稿 [{{ repair.issueRange.startFrame }}, {{ repair.issueRange.endFrame }}) · 固定 {{ takeLength }} 帧</p>
            <p class="sound-strip">声音来源：{{ audioPolicy === 'preserve_current' ? '保留父草稿完整音轨（继承已有修改）' : `仅替换选区为候选原生音轨 · 淡入 ${fadeIn} ms / 淡出 ${fadeOut} ms` }}</p>
          </template>
          <details v-if="candidate" class="raw-inspection"><summary>完整修改目标与原始素材信息</summary><p>{{ repair?.instruction }}</p><p>模型实际返回 {{ candidate.metadata.durationFrames }} 帧。免费裁取只能调整时机，不能补出从未发生的动作。</p><a v-if="comparison" :href="`/api/v1/assets/${comparison.id}/content`" target="_blank" rel="noopener">查看历史并排合成资源</a></details>
        </div>
      </div>
      <section v-if="repair" class="comparison trial-controls">
        <h3>免费取用与试装</h3><p>目标变化是否发生、正确内容是否保留、接回草稿是否连贯，由你检查判断。模型任务成功不代表内容通过。</p>
        <div v-if="candidate" class="trial-fields"><label>候选取用起点（帧）<input v-model.number="takeStart" type="number" min="0" :max="Math.max(0, Number(candidate.metadata.durationFrames ?? 0) - takeLength)" step="1" /></label><p>取用 [{{ takeStart }}, {{ takeStart + takeLength }})，替换父草稿 [{{ repair.issueRange.startFrame }}, {{ repair.issueRange.endFrame }})。长度固定 {{ takeLength }} 帧。</p>
          <label>本次试装声音<select v-model="audioPolicy"><option value="preserve_current">保留父草稿声音（继承已有修改）</option><option value="use_candidate" :disabled="!candidate.metadata.hasAudio">使用候选原生混合音轨</option></select></label>
          <p>{{ soundState(candidate) }}。候选声音与取用画面共同移动；替换区段内不叠加原声。</p>
          <template v-if="audioPolicy === 'use_candidate'"><label>候选内淡入（毫秒，0 为关闭）<input v-model.number="fadeIn" type="number" min="0" step="1" /></label><label>候选内淡出（毫秒，0 为关闭）<input v-model.number="fadeOut" type="number" min="0" step="1" /></label></template>
        </div>
        <p v-if="placementError" role="status">{{ placementError }}</p>
        <button class="secondary" :disabled="busy || localRunning || !!placementError" @click="renderPreview(true)">准备试装对比（本地免费）</button>
        <button class="secondary" :disabled="locked" @click="reuseParameters">复用参数准备下一次修改</button>
        <label v-if="trials.length">保留的试装<select :value="trial?.id" @change="chooseTrial(($event.target as HTMLSelectElement).value)"><option v-for="(item, index) in trials" :key="item.id" :value="item.id">{{ new Date(item.createdAt ?? '').toLocaleString('zh-CN') }} · {{ jobPresentation(item.status).label }} · {{ trials.length - index }}</option></select></label>
        <p v-if="trial && trialStale">旧预览对应上一份试装，取用设置未应用。</p><p v-if="repair.baseEditVersionId !== head.id">此候选来自旧父版本，可继续查看和试装，不能应用到已变化的草稿。</p>
        <button class="primary" :disabled="!canApply" @click="applyToDraft">应用此试装到草稿（不正式采用）</button>
      </section>
      <div class="form"><h3>准备下一次生成</h3><label>生成路径<select v-model="generationMode" :disabled="locked"><option value="edit_existing">修改现有片段</option><option value="from_frame">从正确起点重新生成</option></select></label><p v-if="generationMode === 'edit_existing'">发送冻结原视频上下文及来源参考。入点、出点图片通过提示词说明用途，属于普通参考，并非严格首尾帧约束；错误动作仍存在于参考视频中。</p><template v-else><p>仅发送明确选定的正确起始帧和可选结束帧，不发送原动作视频或五张身份参考。动作有更多重新生成空间，但原运动与独立身份约束会减少。</p><label>正确起始帧<input :value="anchorStart" @input="anchorStart = ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value)" type="number" min="0" :max="totalFrames - 1" :disabled="locked" /></label><button class="secondary" :disabled="locked || view !== 'base'" @click="anchorStart = currentFrame">使用当前草稿正在查看的帧作为正确起点</button><label v-if="endStatePolicy !== 'replace'">确认正确的结束帧（可留空）<input :value="anchorEnd" type="number" min="0" :max="totalFrames - 1" :disabled="locked" @input="anchorEnd = ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value)" /></label></template><label>本次声音生成<select v-model="audioMode" :disabled="locked"><option value="preserve_current">保留当前草稿声音</option><option value="generate_candidate">生成并替换选区声音</option></select></label><label v-if="audioMode === 'generate_candidate'">修改后的声音描述<textarea v-model="soundDescription" rows="2" :disabled="locked" placeholder="补充与新动作对应的环境、物件、动作声音；有音乐或对白设计时照实填写。" /></label><p v-if="audioMode === 'generate_candidate'">请求原生混合音轨，不传原混合声音参考。实际未返回声音时保留素材，不自动重试；试装可免费切换为保留父草稿声音。</p><label>希望修改什么<textarea v-model="instruction" rows="4" :disabled="locked" placeholder="描述需要改变的动作和状态，以及需要保留的角色、道具、构图和光线。" /></label><label>结束状态策略<select v-model="endStatePolicy" :disabled="locked"><option value="match_original">匹配原结束状态（原来的结尾正确）</option><option value="replace">替换结束状态（原来的结尾也需要修改）</option></select></label><label v-if="endStatePolicy === 'replace'">期望的结束状态<textarea v-model="desiredEndState" rows="2" :disabled="locked" /></label><p v-if="issue.endFrame === totalFrames">本次修改到片尾，出点接缝检查不适用。{{ endStatePolicy === 'replace' ? '不会强制匹配原来的错误尾帧。' : '' }}</p><button class="secondary" :disabled="locked || !instruction.trim()" @click="prepare">检查修改范围与完整指令（免费）</button></div>
      <section v-if="preview" class="prompt-preview"><h3>本次修改预览</h3><p>本次实际输入：{{ preview.generationMode === 'from_frame' ? '严格帧模式，不发送参考视频' : '原视频上下文（无声）与普通图像参考' }} · {{ preview.audioMode === 'generate_candidate' ? '要求生成原生声音' : '不要求生成声音' }}</p><dl><dt>问题／替换区间</dt><dd>[{{ preview.issueRange.startFrame }}, {{ preview.issueRange.endFrame }}) · {{ (preview.issueRange.startFrame / 24).toFixed(3) }}–{{ (preview.issueRange.endFrame / 24).toFixed(3) }} 秒</dd><dt>模型生成上下文</dt><dd>[{{ preview.generationRange.startFrame }}, {{ preview.generationRange.endFrame }}) · 模型生成 {{ preview.providerDurationSeconds }} 秒</dd><dt>默认建议取用区间</dt><dd>[{{ preview.candidateCoreRange.startFrame }}, {{ preview.candidateCoreRange.endFrame }}) · 与替换区间等长</dd></dl><p>候选返回后可免费移动取用起点。原片保持 {{ totalFrames }} 帧，未改动区间保持原时间线。</p><details open><summary>完整修改指令</summary><pre>{{ preview.prompt }}</pre><p>需要避免的问题：{{ preview.negativePrompt }}</p></details><div class="references"><figure v-for="reference in preview.imageReferences" :key="reference.role"><img v-if="reference.assetId" :src="`/api/v1/assets/${reference.assetId}/content`" :alt="reference.role" /><figcaption>{{ reference.role === 'first_frame' ? `严格首帧 · 第 ${reference.frameNumber} 帧` : reference.role === 'last_frame' ? `严格尾帧 · 第 ${reference.frameNumber} 帧` : reference.role === 'anchor_in' ? `入点参考 · 第 ${reference.frameNumber} 帧` : reference.role === 'anchor_out' ? `出点参考 · 第 ${reference.frameNumber} 帧` : ({ episode_child: '儿童', episode_cat: '猫咪', pair_scale: '人猫比例', environment: '环境', style_board: '画风' } as Record<string, string>)[reference.role] }}</figcaption></figure></div><details><summary>技术详情</summary><p>输入标识 {{ preview.inputHash }} · 模型 {{ preview.model }}</p><p>草稿版本 {{ preview.baseEditVersionId }} · 完整时间线 {{ preview.baseTimelineHash }}</p></details></section>
      <div class="submit"><p>本次操作产生一次模型费用；不自动重试。<br /><span>{{ blockedReason }}</span></p><button class="primary" :disabled="Boolean(blockedReason) || busy" @click="generate">生成修改结果（付费）</button></div>
      <section v-if="repairJob" class="notice" aria-live="polite"><b>修改进度：{{ jobPresentation(repairJob.status).label }}</b><p v-if="repairJob.status === 'submission_unknown'">提交状态需要人工确认，请不要再次生成。</p><details><summary>查看生成记录</summary><p>任务 {{ repairJob.id }}</p><p v-if="repairJob.providerTaskId">模型任务 {{ repairJob.providerTaskId }}</p><pre>{{ repairJob.actualUsage }}</pre><p>{{ billingPresentation(repairJob.billingStatus, repairJob.actualCostMicros, repairJob.provider).detail }}</p><p v-if="repairJob.error">{{ repairJob.error.message }}</p></details></section>
      <section v-if="basePreview" class="review"><h3>当前完整草稿的验收</h3><p>可以保存不通过项，继续修复；只有真正全部通过后才能正式选择。</p><div class="checks"><label v-for="(label, key) in labels" :key="key">{{ label }}<select v-model="checks[key]"><option value="">未判断</option><option value="pass">通过</option><option value="warning">需留意</option><option value="fail">不通过</option></select></label></div><div v-if="needsSoundReview" class="checks"><label v-for="(label, key) in audioLabels" :key="key">{{ label }}<select v-model="audioChecks[key]"><option value="">未判断</option><option value="pass">通过</option><option value="warning">需留意</option><option value="fail">不通过</option><option v-if="key !== 'soundIntent' && basePreview.metadata.hasAudio === false" value="not_applicable">无音轨，不适用</option></select></label></div><label>问题备注（关联当前所选区间）<textarea v-model="notes" rows="3" /></label><button class="secondary" :disabled="busy" @click="saveReview(false)">保存问题与验收记录</button><button class="primary" :disabled="busy || !allPass" @click="saveReview(true)">验收通过并正式选择完整视频</button></section>
      <details class="history"><summary>来源问题记录与不可变版本历史</summary><p v-for="review in reviews" :key="review.id">{{ review.notes }} <span v-for="item in review.issues" :key="item.note">[{{ item.range.startFrame }}, {{ item.range.endFrame }}) {{ item.note }}</span></p><p v-for="edit in edits.filter(item => item.editDraftId === draftId)" :key="edit.id">草稿版本 {{ edit.revision }} · {{ new Date(edit.createdAt).toLocaleString('zh-CN') }} · {{ edit.id === head.id ? '当前编辑' : '历史保留' }}</p></details>
    </template>
    <div v-if="error" class="notice creator-error" role="alert"><p>{{ error }}</p><details v-if="technicalError"><summary>技术详情</summary><code>{{ technicalError }}</code></details></div>
  </section>
</template>

<style scoped>
.draft-editor { margin-top: 20px; overflow: hidden; min-width: 0; }header { display: flex; justify-content: space-between; gap: 15px; padding: 22px 24px; border-bottom: 1px solid var(--line); }h2,h3 { margin: 0 0 8px; }p { line-height: 1.6; }header p,.history { color: var(--muted); }
.setup,.toolbar,.form,.prompt-preview,.submit,.comparison,.review,.history { padding: 20px 24px; }.toolbar,.submit { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }.submit { justify-content: space-between; background: #fbf4ed; }.setup { display: grid; gap: 14px; }.setup input { width: auto; }
.player-stage { display: grid; justify-items: center; gap: 12px; background: #292622; color: white; padding: 20px; }.player-stage video { height: clamp(150px, 31vh, 320px); max-width: 100%; background: #111; }.player-stage button { margin: 3px; }
.form { display: grid; gap: 14px; border-block: 1px solid var(--line); }.form label,.review > label { display: grid; gap: 8px; }textarea { width: 100%; }.prompt-preview { background: #fffdf9; overflow-wrap: anywhere; }pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.65; }dl { display: grid; grid-template-columns: 160px 1fr; gap: 10px; }dd { margin: 0; }.references { display: flex; gap: 12px; overflow-x: auto; }figure { margin: 0; min-width: 100px; max-width: 130px; }figure img { width: 100%; height: 100px; object-fit: contain; }figcaption { font-size: 12px; }.notice { margin: 12px 24px; }details { margin-top: 12px; }summary { cursor: pointer; font-weight: 600; }code { overflow-wrap: anywhere; }
.comparison { border: 1px solid #d9b79e; margin: 20px; border-radius: 12px; }.comparison button,.review button { margin: 8px 8px 0 0; }.checks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }.checks label { display: flex; justify-content: space-between; align-items: center; gap: 10px; }.checks select { width: 110px; }@media (max-width: 900px) { .checks { grid-template-columns: 1fr; }dl { grid-template-columns: 1fr; }header { flex-direction: column; } }
.candidate-workbench { display: grid; grid-template-columns: 240px minmax(0, 1fr); border-block: 1px solid var(--line); }
.candidate-list { padding: 18px; background: #f7f3ef; border-right: 1px solid var(--line); max-height: 850px; overflow-y: auto; }
.candidate-card { display: grid; gap: 7px; width: 100%; text-align: left; margin: 10px 0; padding: 12px; background: white; border: 1px solid var(--line); border-radius: 10px; overflow-wrap: anywhere; }
.candidate-card.selected { border-color: #b76b38; box-shadow: 0 0 0 1px #b76b38; }.candidate-card small { color: var(--muted); }.viewing-area { min-width: 0; }.view-heading,.view-tabs { display: flex; flex-wrap: wrap; gap: 10px; padding: 12px 18px; }.view-heading { justify-content: space-between; }.video-viewport { overflow: hidden; width: 100%; display: flex; justify-content: center; }.video-viewport video { max-width: 100%; }.video-viewport.focus-before,.video-viewport.focus-after { width: min(100%, 340px); display: block; }.video-viewport.focus-before video,.video-viewport.focus-after video { width: 200%; max-width: none; height: auto; display: block; }.video-viewport.focus-after video { transform: translateX(-50%); }.compare-labels { display: flex; justify-content: space-around; width: 100%; }.trial-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.trial-fields label,.trial-controls > label,.raw-inspection label { display: grid; gap: 8px; }.raw-inspection { padding: 16px; }.comparison-options { display: flex; flex-wrap: wrap; justify-content: center; }.comparison-options label { display: flex; align-items: center; gap: 8px; }.trial-controls > label { margin-top: 14px; }
@media (max-width: 760px) { .candidate-workbench { grid-template-columns: 1fr; }.candidate-list { max-height: 240px; border-right: 0; border-bottom: 1px solid var(--line); }.candidate-list section { display: flex; gap: 10px; align-items: center; }.candidate-card { min-width: 190px; max-width: 250px; }.trial-fields { grid-template-columns: 1fr; }.player-stage { padding: 10px; }.view-heading { font-size: 13px; } }
</style>

<style scoped>
.candidate-list { max-height: 650px; padding: 12px; }.candidate-card { font-size: 12px; gap: 5px; padding: 9px; }.candidate-card video { width: 52px; height: 70px; object-fit: cover; float: left; }.candidate-card b { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }.candidate-list h4 { font-size: 12px; margin: 12px 0; }.view-heading,.view-tabs { padding: 8px 14px; }.view-tabs button,.transport button { font-size: 12px; padding: 7px 10px; }.comparison-origin,.range-mapping,.sound-strip { margin: 0; padding: 5px 16px; font-size: 12px; }.range-mapping { background: #f8e9df; }.sound-strip { background: #e9f1ed; }.transport { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 14px; }.player-stage { padding: 10px; gap: 4px; }.raw-inspection { margin: 0; padding: 8px 16px; font-size: 12px; }
@media (max-width: 760px) { .candidate-list { max-height: 120px; }.candidate-card { min-width: 175px; }.candidate-card video { display: none; }.candidate-list h3 { font-size: 13px; margin: 0; } }
</style>

<style scoped>.view-tabs button,.transport button { min-height: 30px; padding: 5px 10px; }.view-heading,.view-tabs { padding-block: 6px; }.comparison-origin,.range-mapping,.sound-strip { padding-block: 3px; font-size: 12px; line-height: 1.4; }.player-stage video { height: clamp(140px, calc(100dvh - 610px), 300px); }@media(max-width:760px) {.candidate-list { max-height: 100px; }.candidate-card { margin: 4px 0; gap: 2px; }.candidate-list section { overflow-x: auto; }.candidate-list h4 { min-width: 60px; }.candidate-card small { font-size: 10px; }.view-tabs { gap: 5px; }.view-heading { font-size: 12px; }}</style>
