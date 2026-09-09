<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import JobStatusCard from "../JobStatusCard.vue";
import { subscribeJobs } from "../../jobUpdates";
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
const view = ref<"base" | "result" | "comparison" | "after">("base");
const selectedRepairId = ref(typeof route.query.candidateId === 'string' ? route.query.candidateId : '');
const selectedTrialId = ref(typeof route.query.trialJobId === 'string' ? route.query.trialJobId : '');
const takeStart = ref(0); const audioPolicy = ref<'preserve_current' | 'use_candidate'>('preserve_current');
const fadeIn = ref(0); const fadeOut = ref(0);
const autoAttempts = new Set<string>();
const resultError = ref('');
const rangeInputInvalid = ref(false);
const resultBusy = ref(false);
const inputResult = ref<JobDto | null>(null);
const referenceJob = ref<JobDto | null>(null);
const compareOriginal = ref(false);
let restoredReference = '';
let restoringInputs = false; let inputRevision = 0;
const sourceResultId = computed(() => typeof route.query.sourceResultId === 'string' ? route.query.sourceResultId : '');
const sourceRange = computed(() => (inputResult.value?.frozenInput?.resultRange ?? inputResult.value?.frozenInput?.issueRange) as FrameRangeDto | undefined);
const sourceVideo = computed(() => assets.value.find(asset => asset.role === 'edit_preview' && asset.producingJobId === inputResult.value?.id));
const referenceVideo = computed(() => assets.value.find(asset => asset.role === 'repair_context' && asset.producingJobId === referenceJob.value?.id));
const resultRange = computed(() => (trial.value?.frozenInput?.resultRange as FrameRangeDto | undefined) ?? repair.value?.preview.resultRange ?? repair.value?.issueRange ?? issue.value);
const canContinue = computed(() => !locked.value && !trialStale.value && trial.value?.status === 'succeeded' && !!trialVideo.value && resultRange.value.endFrame - resultRange.value.startFrame >= 96);
const startingLabel = computed(() => inputResult.value ? '候选结果 ' + String(inputResult.value.frozenInput?.repairId).slice(0, 8) : '当前草稿 v' + head.value?.revision);
const beforeLabel = computed(() => !compareOriginal.value && repair.value?.preview.sourceResultJobId ? '上一结果 · ' + sourceCandidateName(repair.value) : '开始修改前 · 父草稿 v' + parentVersion.value?.revision);

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
const head = computed(() => draft.value?.id === draftId.value ? edits.value.find(edit => edit.id === draft.value?.headEditVersionId) : undefined);
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
const baseJob = computed(() => jobs.value.find(job => job.kind === "render_edit_preview" && job.frozenInput?.editVersionId === head.value?.id && !job.frozenInput?.repairId));
const basePreview = computed(() => assets.value.find(asset => asset.role === "edit_preview" && asset.metadata.editVersionId === head.value?.id && !asset.metadata.repairId));
const trials = computed(() => jobs.value.filter(job => job.kind === 'render_edit_preview' && job.frozenInput?.repairId === repair.value?.id));
const trial = computed(() => trials.value.find(job => job.id === selectedTrialId.value) ?? trials.value[0]);
const trialVideo = computed(() => assets.value.find(asset => asset.role === 'edit_preview' && asset.producingJobId === trial.value?.id));
const parentVersion = computed(() => edits.value.find(edit => edit.id === repair.value?.baseEditVersionId));
const appliedSegments = computed(() => { let start = 0; return ((view.value === 'base' ? (inputResult.value?.frozenInput?.edl as FrameEditTimelineDto | undefined) ?? edl.value : (trial.value?.frozenInput?.edl as FrameEditTimelineDto | undefined) ?? parentVersion.value?.edl as FrameEditTimelineDto | undefined)?.videoSegments ?? []).flatMap(segment => { const first = start; start += segment.durationFrames; return segment.repairId ? [{ startFrame: first, endFrame: start, label: view.value === 'base' && !inputResult.value ? '已应用修改' : '候选链片段' }] : []; }); });
const activeRange = computed(() => view.value === 'base' ? issue.value : repair.value?.issueRange ?? issue.value);
const segmentView = computed(() => view.value === 'result' || view.value === 'comparison');
const mediaOffset = computed(() => segmentView.value ? resultRange.value.startFrame : 0);
const beforeSegment = computed(() => assets.value.find(asset => asset.role === (compareOriginal.value && repair.value?.preview.sourceResultJobId ? 'edit_result_original' : 'edit_result_before') && asset.producingJobId === trial.value?.id));
const afterSegment = computed(() => assets.value.find(asset => asset.role === 'edit_result_after' && asset.producingJobId === trial.value?.id));
const displayedVideo = computed(() => segmentView.value ? afterSegment.value : view.value === 'after' ? trialVideo.value : inputResult.value ? sourceVideo.value : basePreview.value);
const mediaFrames = computed(() => segmentView.value ? resultRange.value.endFrame - resultRange.value.startFrame : totalFrames.value);
const mediaRate = computed(() => 24);
const localJob = computed(() => jobs.value.find(job => job.kind === "render_edit_preview" && !terminal(job.status)) ?? trial.value ?? jobs.value.find(job => job.kind === 'render_edit_preview'));
const localRunning = computed(() => !!localJob.value && !terminal(localJob.value.status));
const takeLength = computed(() => repair.value ? repair.value.issueRange.endFrame - repair.value.issueRange.startFrame : 0);
const placement = computed<CandidatePlacementDto>(() => ({ candidateSourceRange: { startFrame: takeStart.value, endFrame: takeStart.value + takeLength.value }, audioPolicy: audioPolicy.value, fadeInMs: audioPolicy.value === 'use_candidate' ? fadeIn.value : 0, fadeOutMs: audioPolicy.value === 'use_candidate' ? fadeOut.value : 0 }));
const trialStale = computed(() => {
  const frozen = trial.value?.frozenInput?.placement as CandidatePlacementDto | undefined;
  return !frozen || frozen.candidateSourceRange.startFrame !== takeStart.value || frozen.candidateSourceRange.endFrame !== placement.value.candidateSourceRange.endFrame || frozen.audioPolicy !== audioPolicy.value || (frozen.fadeInMs ?? 0) !== placement.value.fadeInMs || (frozen.fadeOutMs ?? 0) !== placement.value.fadeOutMs;
});
const canApply = computed(() => !busy.value && !trialStale.value && trial.value?.status === 'succeeded' && !!trialVideo.value && repair.value?.baseEditVersionId === head.value?.id && repair.value?.status === 'candidate_ready');
const thumbnails = computed(() => assets.value.filter(asset => asset.role === "edit_thumbnail" && asset.metadata.previewAssetId === (view.value === 'base' ? (sourceVideo.value ?? basePreview.value)?.id : trialVideo.value?.id)).map(asset => ({ frame: Number(asset.metadata.sourceFrame), url: `/api/v1/assets/${asset.id}/content` })).sort((a, b) => a.frame - b.frame));
const needsSoundReview = computed(() => head.value?.formatVersion === 3 || !!basePreview.value?.metadata.requestedAudio || (head.value?.formatVersion === 2 && !!source.value?.metadata.requestedAudio));
function soundState(asset?: AssetDto) { if (!asset) return '尚未检测'; if (asset.metadata.audioRequestMissing) return '请求声音但未返回音轨'; if (asset.metadata.audioState === 'silent') return '存在音轨，内容为静音'; if (asset.metadata.hasAudio === false) return '无音轨'; if (asset.metadata.hasAudio) return `${asset.metadata.audioChannels === 1 ? '单声道' : '有声音轨'} · ${asset.metadata.audioSampleRate ?? '未知'} Hz`; return '历史音轨信息未检测'; }
function timelineUsesRepair(timeline: EditVersionDto['edl'] | undefined | null, repairId: string) {
  return !!timeline && 'videoSegments' in timeline && (timeline.videoSegments.some(segment => segment.repairId === repairId) || (timeline.format === 'catflow-edl-v3' && timeline.audio.segments.some(segment => segment.repairId === repairId)));
}
function inCurrentDraft(item: VideoRepairDto) { return timelineUsesRepair(edl.value, item.id); }
const blockedReason = computed(() => {
  if (generationMode.value === "edit_existing" && !draft.value?.referencesConfirmed) return "历史参考不完整，请明确确认新的参考绑定后创建草稿。";
  if (unresolvedSubmission.value) return "请求结果尚不确定。请重新检查任务记录，不要重复提交。";
  if (pendingPaidJob.value?.status === "submission_unknown") return "模型提交状态需要人工确认，请不要再次生成。";
  if (locked.value) return "当前任务仍在处理，已锁定输入。";
  if (!(sourceVideo.value ?? basePreview.value)) return "请先准备完整草稿预览，确认实际编辑画面。";
  if (rangeInputInvalid.value) return "请先修正无效的选区时间输入。";
  if (!isValidIssueRange(issue.value, totalFrames.value)) return "请选择 4–15 秒（96–360 帧）的生成区间。";
  if (sourceRange.value && (issue.value.startFrame < sourceRange.value.startFrame || issue.value.endFrame > sourceRange.value.endFrame)) return "选区必须位于来源结果内。";
  if (!instruction.value.trim()) return "请填写需要改变的动作或状态。";
  if (endStatePolicy.value === "replace" && !desiredEndState.value.trim()) return "请填写期望的结束状态。";
  if (generationMode.value === 'from_frame' && anchorStart.value === null) return '请明确选定正确起始帧。';
  if (!preview.value?.referencePreparationJobId) return "请先准备并检查本次实际参考。";
  return paidModelBlockedReason(runtime.value) || (generationMode.value === "edit_existing" && !runtime.value?.objectPublisher.ready ? "局部修改的视频发布通道尚未就绪。" : "");
});
const allPass = computed(() => Object.keys(labels).every(key => checks[key] === "pass") && (!needsSoundReview.value || Object.keys(audioLabels).every(key => audioChecks[key] === (key !== "soundIntent" && basePreview.value?.metadata.hasAudio === false ? "not_applicable" : "pass"))));
let unsubscribeJobs: (() => void) | undefined; let callbackId: number | undefined;
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
    inputResult.value = sourceResultId.value ? await api.job(sourceResultId.value) : null;
    const referenceId = typeof route.query.referenceJobId === 'string' ? route.query.referenceJobId : '';
    if (referenceId) {
      const revision = inputRevision; const received = await api.job(referenceId);
      if (revision === inputRevision && referenceId === route.query.referenceJobId) referenceJob.value = received;
    }
    if (referenceJob.value && restoredReference !== referenceJob.value.id) {
      const command = referenceJob.value.frozenInput?.command as ReturnType<typeof inputs> | undefined;
      if (command) {
        restoringInputs = true;
        issue.value = { ...command.issueRange }; instruction.value = command.instruction;
        generationMode.value = command.generationMode; audioMode.value = command.audioMode;
        soundDescription.value = command.soundDescription; endStatePolicy.value = command.endStatePolicy;
        desiredEndState.value = command.desiredEndState; anchorStart.value = command.anchorStartFrame; anchorEnd.value = command.anchorEndFrame;
        restoringInputs = false;
      }
      restoredReference = referenceJob.value.id;
    }
    if (repair.value && initializedCandidate !== repair.value.id) {
      initializedCandidate = repair.value.id;
      restorePlacement();
      if (pendingPaidJob.value) reuseParameters();
    }
    if (head.value && restoredHead !== head.value.id && !pendingPaidJob.value && !referenceJob.value) {
      restoringInputs = true;
      issue.value = sourceRange.value ? { ...sourceRange.value } : { startFrame: 0, endFrame: Math.min(96, totalFrames.value) };
      restoringInputs = false; preview.value = null; view.value = !restoredHead && ['result', 'raw', 'comparison', 'after'].includes(String(route.query.view)) ? (route.query.view === 'raw' ? 'result' : route.query.view as typeof view.value) : 'base';
    }
    if (head.value && restoredHead !== head.value.id) {
      restoredHead = head.value.id; reviews.value = source.value ? await api.videoReviews(props.projectId, source.value.id) : [];
      notes.value = reviews.value[0]?.notes ?? "";
      Object.keys(labels).forEach(key => { checks[key] = reviews.value[0]?.checks[key] ?? ""; });
      const sourceIssue = reviews.value[0]?.issues?.[0];
      // Source-frame notes apply to an unedited root only; edited timelines restore their own review below.
      if (!sourceResultId.value && !referenceJob.value && !locked.value && !head.value.parentEditVersionId && sourceIssue && isValidIssueRange(sourceIssue.range, totalFrames.value)) { restoringInputs = true; issue.value = { ...sourceIssue.range }; restoringInputs = false; }
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
        if (!sourceResultId.value && !referenceJob.value && !locked.value && savedIssue && isValidIssueRange(savedIssue.range, totalFrames.value)) { restoringInputs = true; issue.value = { ...savedIssue.range }; restoringInputs = false; }
      }
      reviews.value = [...saved, ...reviews.value];
    }
  } catch (reason) { if (!disposed) fail(reason, "编辑记录暂时无法读取"); } finally { loading.value = false; }
  if (!disposed && referenceJob.value?.status === 'succeeded' && !preview.value && view.value === 'base') {
    const revision = inputRevision; const preparedId = referenceJob.value.id;
    try {
      const received = await api.previewVideoRepair(props.projectId, { ...inputs(), referencePreparationJobId: preparedId });
      if (revision === inputRevision && referenceJob.value?.id === preparedId) preview.value = received;
    } catch (reason) { if (revision === inputRevision) fail(reason, '实际参考与当前输入不一致，请重新准备'); }
  }
  if (!disposed) await ensurePreviews();
}
async function openDraft(existing: VideoEditDraftDto) { referenceJob.value = null; inputResult.value = null; preview.value = null; await router.replace({ query: { draftId: existing.id, view: 'base' } }); restoredHead = ""; await load(); }
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
  audioPolicy.value = frozen?.audioPolicy ?? (repair.value?.preview.audioMode === 'generate_candidate' ? 'use_candidate' : 'preserve_current');
  fadeIn.value = frozen?.fadeInMs ?? 0; fadeOut.value = frozen?.fadeOutMs ?? 0;
}
async function chooseCandidate(item: VideoRepairDto) {
  inputResult.value = null; compareOriginal.value = false;
  selectedRepairId.value = item.id; selectedTrialId.value = ''; initializedCandidate = item.id; restorePlacement();
  resultError.value = ''; setView('result'); await router.replace({ query: { ...route.query, candidateId: item.id, trialJobId: undefined, sourceResultId: undefined, referenceJobId: undefined, view: 'result' } });
  await ensurePreviews();
}
async function chooseTrial(id: string) {
  selectedTrialId.value = id; restorePlacement(); setView('result');
  await router.replace({ query: { ...route.query, candidateId: repair.value?.id, trialJobId: id, view: view.value } });
  await ensurePreviews();
}
function sourceCandidateName(item: VideoRepairDto) {
  const parentJob = jobs.value.find(job => job.id === item.preview.sourceResultJobId);
  return String(parentJob?.frozenInput?.repairId ?? item.preview.sourceResultJobId ?? '').slice(0, 8);
}
function everUsed(item: VideoRepairDto) { return edits.value.some(edit => timelineUsesRepair(edit.edl, item.id)); }
function usedAsStart(item: VideoRepairDto) {
  return drafts.value.some(entry => {
    const origin = inputResult.value?.id === entry.sourceResultJobId ? inputResult.value : jobs.value.find(job => job.id === entry.sourceResultJobId);
    return timelineUsesRepair(origin?.frozenInput?.edl as FrameEditTimelineDto | undefined, item.id);
  });
}
async function continueResult() {
  if (!canContinue.value || !trial.value || !repair.value || !source.value) return;
  const result = trial.value; const range = { ...resultRange.value };
  busy.value = true;
  try {
    let targetDraft = draft.value!;
    if (repair.value.baseEditVersionId !== head.value?.id) {
      const previousForks = drafts.value.filter(item => item.sourceResultJobId === result.id).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
      const reusable = previousForks.find(item => edits.value.find(edit => edit.id === item.headEditVersionId)?.timelineHash === result.frozenInput?.timelineHash);
      targetDraft = reusable ?? await api.createVideoEditDraft(props.projectId, {
        sourceVideoAssetId: source.value.id, sourceResultJobId: result.id, confirmCurrentReferences: false,
        expectedSourceTimelineHash: String(result.frozenInput?.timelineHash),
        idempotencyKey: 'fork-result:' + result.id + (previousForks[0] ? ':' + previousForks[0].headEditVersionId : ''),
      });
    }
    referenceJob.value = null; preview.value = null; restoredReference = ''; instruction.value = '';
    await router.replace({ query: { draftId: targetDraft.id, sourceResultId: result.id, view: 'base' } });
    draft.value = targetDraft;
    inputResult.value = result; issue.value = range; rangeInputInvalid.value = false;
    selectedRepairId.value = ''; selectedTrialId.value = ''; view.value = 'base';
    restoredHead = ''; await load(); pendingFrame = range.startFrame; await nextTick(); void seek(range.startFrame);
  } catch (reason) { fail(reason, '继续修改未能准备'); } finally { busy.value = false; }
}
async function changeComparison() {
  comparisonPlayer.value?.pause(); const frame = currentFrame.value;
  compareOriginal.value = !compareOriginal.value; await nextTick(); await comparisonPlayer.value?.seek(frame - mediaOffset.value);
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
async function renderPreview(retry = false) {
  if (!head.value?.timelineHash || !draft.value || busy.value || localRunning.value) return;
  busy.value = true;
  try {
    await api.renderDraftPreview(props.projectId, draft.value.id, {
      expectedEditVersionId: head.value.id, expectedTimelineHash: head.value.timelineHash,
      idempotencyKey: 'base-preview:' + head.value.id + ':2' + (retry ? ':' + baseJob.value?.id : ''),
    });
  } catch (reason) { fail(reason, '完整草稿预览没有成功开始'); }
  finally { busy.value = false; }
  await load();
}
async function prepareResult(preserveOriginalAudio = false, retry = false) {
  if (!repair.value || !draft.value || resultBusy.value) return;
  resultBusy.value = true; resultError.value = '';
  try {
    const job = await api.prepareRepairResult(props.projectId, draft.value.id, {
      repairId: repair.value.id, previousPreviewJobId: trial.value?.id,
      preserveOriginalAudio, retryAfterJobId: retry ? trial.value?.id : undefined,
    });
    selectedTrialId.value = job.id;
    await router.replace({ query: { ...route.query, candidateId: repair.value.id, trialJobId: job.id, view: view.value } });
    jobs.value = [job, ...jobs.value.filter(item => item.id !== job.id)];
    restorePlacement();
  } catch (reason) { resultError.value = errorPresentation(reason, '本地结果未能准备').message; }
  finally { resultBusy.value = false; }
}
async function ensurePreviews() {
  if (disposed || loading.value || busy.value || resultBusy.value || !head.value || !runtime.value?.worker.ready) return;
  if (view.value === 'base' && !basePreview.value && !localRunning.value) {
    const key = 'base:' + head.value.id;
    if (!autoAttempts.has(key)) { autoAttempts.add(key); await renderPreview(); }
  } else if (view.value !== 'base' && candidate.value && (!beforeSegment.value || !afterSegment.value) && !localRunning.value && !['failed', 'cancelled'].includes(trial.value?.status ?? '')) {
    const key = 'result:' + repair.value?.id + ':' + (trial.value?.id ?? 'default');
    if (!autoAttempts.has(key)) { autoAttempts.add(key); await prepareResult(); }
  }
}
function setView(next: typeof view.value) {
  player.value?.pause(); comparisonPlayer.value?.pause();
  const frame = currentFrame.value;
  view.value = next; loop.value = null;
  pendingFrame = next === 'result' || next === 'comparison'
    ? Math.max(resultRange.value.startFrame, Math.min(resultRange.value.endFrame - 1, frame)) : frame;
  currentFrame.value = pendingFrame;
  void router.replace({ query: { ...route.query, view: next } });
}
async function startNextEdit() {
  inputResult.value = null; referenceJob.value = null; restoredReference = '';
  await router.replace({ query: { ...route.query, sourceResultId: undefined, referenceJobId: undefined, view: 'base' } });
  preview.value = null; resultError.value = ''; rangeInputInvalid.value = false;
  const first = Math.min(Math.max(0, currentFrame.value), Math.max(0, totalFrames.value - 96));
  issue.value = { startFrame: first, endFrame: Math.min(totalFrames.value, first + 96) };
  setView('base'); void ensurePreviews();
}
function inputs() { return { sourceResultJobId: inputResult.value?.id, expectedSourceTimelineHash: inputResult.value ? String(inputResult.value.frozenInput?.timelineHash) : undefined, baseVideoAssetId: source.value!.id, baseEditVersionId: head.value!.id, editDraftId: draft.value!.id, issueRange: { ...issue.value }, instruction: instruction.value.trim(), endStatePolicy: endStatePolicy.value, desiredEndState: desiredEndState.value.trim(), generationMode: generationMode.value, audioMode: audioMode.value, soundDescription: soundDescription.value.trim(), anchorStartFrame: generationMode.value === "from_frame" ? anchorStart.value : null, anchorEndFrame: generationMode.value === "from_frame" && endStatePolicy.value !== "replace" ? anchorEnd.value : null }; }
async function prepare(retry = false) {
  if (!head.value || !draft.value || locked.value || rangeInputInvalid.value) return;
  busy.value = true; error.value = "";
  try {
    const previous = referenceJob.value;
    preview.value = null;
    referenceJob.value = await api.prepareSegmentReferences(props.projectId, { ...inputs(), retryAfterJobId: retry ? previous?.id : undefined });
    restoredReference = referenceJob.value.id;
    await router.replace({ query: { ...route.query, referenceJobId: referenceJob.value.id } });
    await load();
  } catch (reason) { fail(reason, "实际参考暂时无法准备"); } finally { busy.value = false; }
}
async function generate() {
  if (blockedReason.value || !preview.value || busy.value) return;
  const frozen = preview.value; busy.value = true; error.value = ""; const scope = `video-edit:${props.projectId}`;
  try {
    const job = await api.createVideoRepair(props.projectId, { ...inputs(), referencePreparationJobId: frozen.referencePreparationJobId, expectedInputHash: frozen.inputHash, idempotencyKey: pendingIdempotencyKey(scope, frozen.inputHash) });
    settleIdempotencyKey(scope, frozen.inputHash);
    jobs.value.unshift(job);
    if (job.videoRepairId) {
      selectedRepairId.value = job.videoRepairId;
      selectedTrialId.value = ''; view.value = 'result';
      await router.replace({ query: { ...route.query, candidateId: job.videoRepairId, trialJobId: undefined, sourceResultId: undefined, referenceJobId: undefined, view: 'result' } });
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
  if (view.value === 'comparison') {
    const target = Math.max(mediaOffset.value, Math.min(mediaOffset.value + mediaFrames.value - 1, Math.trunc(frame)));
    currentFrame.value = target;
    await comparisonPlayer.value?.seek(target - mediaOffset.value); return;
  }
  const media = player.value; if (!media) return; const sequence = ++seekSequence; media.pause();
  const target = Math.max(0, Math.min(mediaFrames.value - 1, Math.trunc(frame - mediaOffset.value))); const time = (target + 0.001) / mediaRate.value;
  if (Math.abs(media.currentTime - time) > 0.00001) await new Promise<void>(resolve => {
    const timeout = window.setTimeout(done, 1500);
    function done() { clearTimeout(timeout); media?.removeEventListener("seeked", done); resolve(); }
    media.addEventListener("seeked", done, { once: true }); media.currentTime = time;
  });
  if (sequence === seekSequence) currentFrame.value = mediaOffset.value + mediaTimeToFrame(media.currentTime, mediaRate.value, mediaFrames.value);
}
function presented(_now: number, metadata: VideoFrameCallbackMetadata) {
  if (disposed || !player.value || player.value !== callbackOwner) return;
  currentFrame.value = mediaOffset.value + Math.max(0, Math.min(mediaFrames.value - 1, Math.floor(metadata.mediaTime * mediaRate.value + 0.01)));
  if (loop.value && currentFrame.value >= loop.value.endFrame - 1) void restartLoop();
  callbackId = player.value.requestVideoFrameCallback(presented);
}
function loaded() { if (loop.value) { void restartLoop(); pendingFrame = null; } else if (pendingFrame !== null) { void seek(pendingFrame); pendingFrame = null; } if (callbackId !== undefined) callbackOwner?.cancelVideoFrameCallback?.(callbackId); callbackOwner = player.value; if (callbackOwner?.requestVideoFrameCallback) callbackId = callbackOwner.requestVideoFrameCallback(presented); }
async function restartLoop() { if (!loop.value || looping) return; looping = true; try { await seek(loop.value.startFrame); if (loop.value) await player.value?.play(); } finally { looping = false; } }
async function playSelection() { if (view.value === 'comparison') { await comparisonPlayer.value?.playRange({ startFrame: activeRange.value.startFrame - mediaOffset.value, endFrame: activeRange.value.endFrame - mediaOffset.value }); return; } loop.value = { ...(view.value === 'base' ? issue.value : repair.value?.issueRange ?? issue.value) }; await restartLoop(); }
async function playWhole() { if (view.value === 'comparison') { await comparisonPlayer.value?.playWhole(); return; } loop.value = null; await seek(mediaOffset.value); await player.value?.play(); }
async function playJunction() {
  const selected = activeRange.value;
  if (view.value !== 'base' && view.value !== 'after') { setView('after'); await nextTick(); player.value?.load(); }
  loop.value = { startFrame: Math.max(0, selected.startFrame - 24), endFrame: Math.min(totalFrames.value, selected.endFrame + 24) };
  pendingFrame = loop.value.startFrame;
  if (player.value?.readyState && player.value.readyState >= 2) await restartLoop();
}
function timeUpdate() { if (!player.value?.requestVideoFrameCallback && player.value) { currentFrame.value = mediaOffset.value + mediaTimeToFrame(player.value.currentTime, mediaRate.value, mediaFrames.value); if (loop.value && currentFrame.value >= loop.value.endFrame - 1) void restartLoop(); } }
async function applyToDraft() {
  if (!canApply.value || !trial.value || !draft.value || !head.value?.timelineHash || !repair.value) return;
  busy.value = true; const scope = `apply-draft:${draft.value.id}`; const fingerprint = `${head.value.id}:${trial.value.id}`;
  try { await api.saveVideoDraft(props.projectId, draft.value.id, { expectedEditVersionId: head.value.id, expectedTimelineHash: head.value.timelineHash, repairId: repair.value.id, previewJobId: trial.value.id, idempotencyKey: pendingIdempotencyKey(scope, fingerprint) }); settleIdempotencyKey(scope, fingerprint); await load(); emit('changed'); }
  catch (reason) { fail(reason, '修改结果未能应用到草稿'); } finally { busy.value = false; }
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
watch([() => issue.value.startFrame, () => issue.value.endFrame, instruction, endStatePolicy, desiredEndState, generationMode, audioMode, soundDescription, anchorStart, anchorEnd], () => {
  if (restoringInputs) return;
  inputRevision += 1;
  const hadReference = !!referenceJob.value || !!preview.value;
  preview.value = null; referenceJob.value = null;
  if (hadReference && route.query.referenceJobId) void router.replace({ query: { ...route.query, referenceJobId: undefined } });
}, { flush: 'sync' });
watch(() => props.workspace.eventCursor, () => void load()); watch(draftId, () => { restoredHead = ""; void load(); });
watch(() => displayedVideo.value?.id, () => { loop.value = null; currentFrame.value = pendingFrame ?? 0; if (callbackId !== undefined) callbackOwner?.cancelVideoFrameCallback?.(callbackId); callbackOwner = null; });
onMounted(async () => { unsubscribeJobs = subscribeJobs(() => ({ projectId: props.projectId }), load, () => jobs.value.some(job => job.execution?.waitingForProvider)); });
onBeforeUnmount(() => { disposed = true; unsubscribeJobs?.(); if (callbackId !== undefined) callbackOwner?.cancelVideoFrameCallback?.(callbackId); });
</script>

<template>
  <section class="card draft-editor">
    <header><div><h2>视频编辑</h2><small>选区 → 生成修改 → 查看效果 → 应用到草稿</small></div><span v-if="head">当前草稿 v{{ head.revision }}</span></header>
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
      <div class="candidate-workbench">
        <aside class="candidate-list" aria-label="候选素材列表">
          <h3>候选素材</h3><p v-if="!candidateGroups.length">生成结果会保留在这里。</p>
          <section v-for="group in candidateGroups" :key="group.id"><h4>父草稿版本 {{ group.revision ?? '历史' }} {{ group.id === head.id ? '· 当前' : '' }}</h4>
            <button v-for="item in group.items" :key="item.id" class="candidate-card" :class="{ selected: repair?.id === item.id }" :aria-pressed="repair?.id === item.id" @click="chooseCandidate(item)">
              <video v-if="item.candidateAssetId" :src="`/api/v1/assets/${item.candidateAssetId}/content#t=0.1`" muted preload="metadata" /><b>{{ item.instruction.slice(0, 44) }}{{ item.instruction.length > 44 ? '…' : '' }}</b><small>[{{ item.issueRange.startFrame }}, {{ item.issueRange.endFrame }}) · {{ (item.issueRange.startFrame / 24).toFixed(2) }}–{{ (item.issueRange.endFrame / 24).toFixed(2) }} 秒</small><span>{{ item.candidateAssetId ? '候选已返回' : item.status === 'generating' ? '正在生成' : '生成记录' }}</span>
              <small>{{ everUsed(item) ? '曾用于草稿' : '尚未用于草稿' }} · {{ inCurrentDraft(item) ? '当前草稿正在使用' : '当前草稿未使用' }}{{ usedAsStart(item) ? ' · 作为独立草稿起点' : '' }}</small><small v-if="item.preview.sourceResultJobId">{{ sourceCandidateName(item) }} → {{ item.id.slice(0, 8) }}</small>
            </button>
          </section>
        </aside>
        <div class="viewing-area">
          <div class="view-heading"><b>{{ view === 'base' ? startingLabel + ' · 选择修改区间' : view === 'after' ? '修改后完整效果' : '本次修改片段' }}</b><span v-if="view !== 'base'">父草稿 v{{ parentVersion?.revision }} · {{ (activeRange.startFrame / 24).toFixed(3) }}–{{ (activeRange.endFrame / 24).toFixed(3) }} 秒</span></div>
          <div v-if="view !== 'base'" class="view-tabs"><button class="secondary" :aria-pressed="view === 'result'" @click="setView('result')">单屏查看结果</button><button class="secondary" :aria-pressed="view === 'comparison'" @click="setView('comparison')">并排对比</button><button class="secondary" :aria-pressed="view === 'after'" @click="setView('after')">查看完整效果</button><button class="secondary" :disabled="locked" @click="startNextEdit">修改当前草稿其他区间</button><button class="primary" :disabled="!canContinue" @click="continueResult">{{ repair?.baseEditVersionId === head.id ? '继续修改此结果' : '从此结果创建独立编辑草稿并继续' }}</button></div>
          <p v-if="draft.sourceResultJobId" class="comparison-origin">独立编辑草稿 · 起点：来源结果 {{ draft.sourceResultJobId.slice(0, 8) }} 的完整效果。<button class="secondary" @click="openDraft(drafts.find(item => item.id === inputResult?.frozenInput?.editDraftId)!)" v-if="inputResult && drafts.some(item => item.id === inputResult?.frozenInput?.editDraftId)">返回来源草稿</button></p>
          <p v-if="inputResult && view === 'base'" class="comparison-origin">本次修改起点：{{ startingLabel }} · 候选有效范围 [{{ sourceRange?.startFrame }}, {{ sourceRange?.endFrame }}) · 最终应用基底：当前草稿 v{{ head.revision }}<button class="secondary" @click="startNextEdit">改为修改当前草稿其他区间</button></p>
          <button v-if="view === 'comparison' && repair?.preview.sourceResultJobId" class="secondary" @click="changeComparison">{{ compareOriginal ? '与上一结果比较' : '与开始修改前比较' }}</button>
          <LinkedVideoComparison v-if="view === 'comparison' && beforeSegment && afterSegment" ref="comparisonPlayer" :before="'/api/v1/assets/' + beforeSegment.id + '/content'" :after="'/api/v1/assets/' + afterSegment.id + '/content'" :total-frames="mediaFrames" :frame-offset="mediaOffset" :initial-frame="currentFrame - mediaOffset" :parent-revision="parentVersion?.revision" :before-label="beforeLabel" after-label="本次结果 · 完整有效片段" :stale="trialStale" @frame="currentFrame = $event + mediaOffset" />
          <div v-else-if="displayedVideo" class="player-stage"><video ref="player" :key="displayedVideo.id" :src="'/api/v1/assets/' + displayedVideo.id + '/content'" controls playsinline preload="auto" @loadeddata="loaded" @timeupdate="timeUpdate" @ended="restartLoop" /><small>草稿第 {{ currentFrame }} 帧 · {{ soundState(displayedVideo) }}</small></div>
          <p v-else class="notice" role="status">{{ resultError || (candidate && view !== 'base' ? '正在准备修改结果与完整效果（本地处理）' : pendingPaidJob ? '模型正在生成修改片段' : '正在准备完整草稿预览（本地处理）') }}</p>
          <div v-if="view === 'base' && !basePreview && baseJob && ['failed','cancelled'].includes(baseJob.status)" class="notice"><p>{{ baseJob.error?.message || '完整预览未准备完成' }}</p><button class="secondary" :disabled="busy" @click="renderPreview(true)">重试本地预览</button></div>
          <div class="transport"><button class="secondary" @click="seek(currentFrame - 1)">← 1 帧</button><button class="secondary" @click="seek(currentFrame + 1)">1 帧 →</button><button class="secondary" @click="playWhole">从头播放</button><button class="secondary" @click="playSelection">循环修改区间</button><button v-if="trialVideo || view === 'base'" class="secondary" @click="playJunction">查看接头</button><button class="secondary" @click="loop = null; comparisonPlayer?.stopLoop()">停止循环</button></div>
          <FrameTimeline :model-value="activeRange" @update:model-value="issue = $event" :total-frames="totalFrames" :current-frame="currentFrame" :disabled="locked || view !== 'base'" :min-duration-frames="96" @invalid="rangeInputInvalid = $event" :allowed-range="view === 'base' ? sourceRange : undefined" :context="view === 'base' ? sourceRange ?? preview?.generationRange : resultRange" :thumbnails="thumbnails" :segments="appliedSegments" compact label="草稿时间轴 · 本次修改" @seek="seek" @play="playSelection" />
          <p v-if="segmentView" class="range-mapping">候选完整有效范围 [{{ resultRange.startFrame }}, {{ resultRange.endFrame }}) · {{ mediaFrames }} 帧；本次内部修改 [{{ activeRange.startFrame }}, {{ activeRange.endFrame }}) · 查看期间范围固定</p>
          <template v-if="view !== 'base' && repair">
            <p class="sound-strip">{{ audioPolicy === 'preserve_current' ? '沿用本次修改起点的声音' : '使用修改片段的声音' }}</p>
            <p v-if="repair.baseEditVersionId !== head.id" class="notice">此结果来自旧父版本，可查看，不能应用到已变化的草稿。</p>
            <p v-if="resultRange.endFrame - resultRange.startFrame < 96" class="notice">此历史结果不足 4 秒，可查看，不能在内部继续生成；不会自动扩大范围。</p>
            <p v-if="trialStale && trial" class="notice">此预览对应上一份声音方案，当前结果尚未准备完成。</p>
            <div v-if="candidate && repair.preview.audioMode === 'generate_candidate' && !candidate.metadata.hasAudio" class="notice"><p>已请求声音，但候选没有返回音轨。原始素材仍保留。</p><button class="secondary" :disabled="resultBusy" @click="prepareResult(true)">保留原声并准备结果</button></div>
            <p v-if="resultError && displayedVideo" class="notice" role="alert">{{ resultError }}</p><button v-if="resultError && !resultBusy && (!trial || !['failed','cancelled'].includes(trial.status))" class="secondary" @click="prepareResult()">重新检查本地结果</button>
            <div v-if="trial && ['failed','cancelled'].includes(trial.status)" class="notice"><p>本地处理未完成：{{ trial.error?.message }}</p><button class="secondary" :disabled="resultBusy" @click="prepareResult(false, true)">重试本地处理</button></div>
            <div class="result-actions"><button v-if="view === 'after'" class="primary" :disabled="!canApply" @click="applyToDraft">应用此修改到草稿</button><small v-if="view !== 'after'">应用前请查看接回后的完整效果。</small></div>
            <details class="raw-inspection"><summary>结果详情与历史</summary><p>{{ repair.instruction }}</p><a v-if="candidate" :href="'/api/v1/assets/' + candidate.id + '/content'" target="_blank" rel="noopener">打开完整模型原始素材</a><p>历史取用 [{{ takeStart }}, {{ takeStart + takeLength }}) · 淡入 {{ fadeIn }} ms / 淡出 {{ fadeOut }} ms</p><label v-if="trials.length">历史结果<select :value="trial?.id" @change="chooseTrial(($event.target as HTMLSelectElement).value)"><option v-for="item in trials" :key="item.id" :value="item.id">{{ new Date(item.createdAt ?? '').toLocaleString('zh-CN') }} · {{ jobPresentation(item.status).label }}</option></select></label></details>
          </template>
        </div>
      </div>
      <div v-if="view === 'base'" class="form"><h3>描述本次修改</h3><details><summary>生成方式与起止状态</summary><label>生成路径<select v-model="generationMode" :disabled="locked"><option value="edit_existing">修改现有片段</option><option value="from_frame">从正确起点重新生成</option></select></label><p v-if="generationMode === 'edit_existing'">发送本次实际输入时间线的上下文及来源参考。继续修改时包含上一结果的画面。入点、出点图片通过提示词说明用途，属于普通参考，并非严格首尾帧约束；错误动作仍存在于参考视频中。</p><template v-else><p>仅发送明确选定的正确起始帧和可选结束帧，不发送原动作视频或五张身份参考。动作有更多重新生成空间，但原运动与独立身份约束会减少。</p><label>正确起始帧<input :value="anchorStart" @input="anchorStart = ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value)" type="number" min="0" :max="totalFrames - 1" :disabled="locked" /></label><button class="secondary" :disabled="locked || view !== 'base'" @click="anchorStart = currentFrame">使用正在查看的帧作为正确起点</button><label v-if="endStatePolicy !== 'replace'">确认正确的结束帧（可留空）<input :value="anchorEnd" type="number" min="0" :max="totalFrames - 1" :disabled="locked" @input="anchorEnd = ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value)" /></label></template><label>结束状态策略<select v-model="endStatePolicy" :disabled="locked"><option value="match_original">匹配原结束状态（原来的结尾正确）</option><option value="replace">替换结束状态（原来的结尾也需要修改）</option></select></label><label v-if="endStatePolicy === 'replace'">期望的结束状态<textarea v-model="desiredEndState" rows="2" :disabled="locked" /></label><p v-if="issue.endFrame === totalFrames">本次修改到片尾，出点接缝检查不适用。{{ endStatePolicy === 'replace' ? '不会强制匹配原来的错误尾帧。' : '' }}</p></details><label>本次声音生成<select v-model="audioMode" :disabled="locked"><option value="preserve_current">保留本次修改起点声音</option><option value="generate_candidate">生成并替换选区声音</option></select></label><label v-if="audioMode === 'generate_candidate'">修改后的声音描述<textarea v-model="soundDescription" rows="2" :disabled="locked" placeholder="补充与新动作对应的环境、物件、动作声音；有音乐或对白设计时照实填写。" /></label><p v-if="audioMode === 'generate_candidate'">请求原生混合音轨，不传原混合声音参考。实际未返回声音时保留素材，不自动重试；可明确选择保留父草稿声音后继续。</p><label>希望修改什么<textarea v-model="instruction" rows="4" :disabled="locked" placeholder="改变什么：…；保留什么：…；起始状态：…；结束状态：…。大幅改变动作时可选严格起始帧。" /></label><button class="secondary" :disabled="locked || rangeInputInvalid || !instruction.trim() || !isValidIssueRange(issue, totalFrames)" @click="prepare()">准备并检查实际参考（本地免费）</button></div>
      <section v-if="referenceJob && view === 'base'" class="prompt-preview" aria-label="实际参考准备"><h3>实际参考准备 · {{ jobPresentation(referenceJob.status).label }}</h3><p>只进行本地处理；刷新后可恢复，不调用模型。</p><p v-if="referenceJob.error">{{ referenceJob.error.message }}</p><button v-if="['failed','cancelled'].includes(referenceJob.status)" class="secondary" @click="prepare(true)">重试本地参考准备</button><template v-if="referenceVideo"><video class="reference-player" :src="'/api/v1/assets/' + referenceVideo.id + '/content'" controls preload="metadata" /><p>实际参考：{{ referenceVideo.metadata.width }}×{{ referenceVideo.metadata.height }} · {{ referenceVideo.metadata.durationFrames }} 帧 / {{ (Number(referenceVideo.metadata.durationFrames) / 24).toFixed(3) }} 秒 · 已移除声音 · 尾部补帧 {{ referenceVideo.metadata.paddedTailFrames }}</p><p>来源：{{ startingLabel }} · 输入范围 [{{ referenceVideo.metadata.sourceStartFrame }}, {{ referenceVideo.metadata.sourceEndFrame }})；模型输出规格维持 480p。</p></template></section>
      <section v-if="preview && view === 'base'" class="prompt-preview" ><h3>本次修改预览</h3><p>本次已准备、提交时发送的输入：{{ preview.generationMode === 'from_frame' ? '严格帧模式，不发送参考视频' : '本次输入结果上下文（无声）与普通图像参考' }} · {{ preview.audioMode === 'generate_candidate' ? '要求生成原生声音' : '不要求生成声音' }}</p><dl><dt>问题／替换区间</dt><dd>[{{ preview.issueRange.startFrame }}, {{ preview.issueRange.endFrame }}) · {{ (preview.issueRange.startFrame / 24).toFixed(3) }}–{{ (preview.issueRange.endFrame / 24).toFixed(3) }} 秒</dd><dt>模型生成上下文</dt><dd>[{{ preview.generationRange.startFrame }}, {{ preview.generationRange.endFrame }}) · 模型生成 {{ preview.providerDurationSeconds }} 秒</dd><dt>自动生成修改片段</dt><dd>[{{ preview.candidateCoreRange.startFrame }}, {{ preview.candidateCoreRange.endFrame }}) · 与替换区间等长</dd></dl><p>返回后按本次冻结范围自动准备结果。原片保持 {{ totalFrames }} 帧，未改动区间保持原时间线。</p><details><summary>完整修改指令</summary><pre>{{ preview.prompt }}</pre><p>需要避免的问题：{{ preview.negativePrompt }}</p></details><details><summary>本次将发送的实际图片</summary><div class="references"><figure v-for="reference in preview.imageReferences" :key="reference.role"><img v-if="reference.assetId" :src="`/api/v1/assets/${reference.assetId}/content`" :alt="reference.role" /><figcaption>{{ reference.role === 'first_frame' ? `严格首帧 · 第 ${reference.frameNumber} 帧` : reference.role === 'last_frame' ? `严格尾帧 · 第 ${reference.frameNumber} 帧` : reference.role === 'anchor_in' ? `入点参考 · 第 ${reference.frameNumber} 帧` : reference.role === 'anchor_out' ? `出点参考 · 第 ${reference.frameNumber} 帧` : ({ episode_child: '儿童', episode_cat: '猫咪', pair_scale: '人猫比例', environment: '环境', style_board: '画风' } as Record<string, string>)[reference.role] }}</figcaption></figure></div></details><details><summary>技术详情</summary><p>输入标识 {{ preview.inputHash }} · 模型 {{ preview.model }}</p><p>草稿版本 {{ preview.baseEditVersionId }} · 完整时间线 {{ preview.baseTimelineHash }}</p></details></section>
      <div v-if="view === 'base'" class="submit"><p>本次操作产生一次模型费用；不自动重试。<br /><span>{{ blockedReason }}</span></p><button class="primary" :disabled="Boolean(blockedReason) || busy" @click="generate">生成修改结果（付费）</button></div>
      <JobStatusCard v-if="repairJob" :job-id="repairJob.id" title="局部修改任务" @replacement="load" />
      <details class="acceptance-panel"><summary>验收与导出 · 当前完整草稿</summary><section v-if="basePreview" class="review"><h3>当前完整草稿的验收</h3><p>可以保存不通过项，继续修复；只有真正全部通过后才能正式选择。</p><div class="checks"><label v-for="(label, key) in labels" :key="key">{{ label }}<select v-model="checks[key]"><option value="">未判断</option><option value="pass">通过</option><option value="warning">需留意</option><option value="fail">不通过</option></select></label></div><div v-if="needsSoundReview" class="checks"><label v-for="(label, key) in audioLabels" :key="key">{{ label }}<select v-model="audioChecks[key]"><option value="">未判断</option><option value="pass">通过</option><option value="warning">需留意</option><option value="fail">不通过</option><option v-if="key !== 'soundIntent' && basePreview.metadata.hasAudio === false" value="not_applicable">无音轨，不适用</option></select></label></div><label>问题备注（关联当前所选区间）<textarea v-model="notes" rows="3" /></label><button class="secondary" :disabled="busy" @click="saveReview(false)">保存问题与验收记录</button><button class="primary" :disabled="busy || !allPass" @click="saveReview(true)">验收通过并正式选择完整视频</button></section>
      <details class="history"><summary>来源问题记录与不可变版本历史</summary><p v-for="review in reviews" :key="review.id">{{ review.notes }} <span v-for="item in review.issues" :key="item.note">[{{ item.range.startFrame }}, {{ item.range.endFrame }}) {{ item.note }}</span></p><p v-for="edit in edits.filter(item => item.editDraftId === draftId)" :key="edit.id">草稿版本 {{ edit.revision }} · {{ new Date(edit.createdAt).toLocaleString('zh-CN') }} · {{ edit.id === head.id ? '当前编辑' : '历史保留' }}</p></details>
    </details></template>
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
.candidate-card.selected { border-color: #b76b38; box-shadow: 0 0 0 1px #b76b38; }.candidate-card small { color: var(--muted); }.viewing-area { min-width: 0; }.view-heading,.view-tabs { display: flex; flex-wrap: wrap; gap: 10px; padding: 12px 18px; }.view-heading { justify-content: space-between; }.video-viewport { overflow: hidden; width: 100%; display: flex; justify-content: center; }.video-viewport video { max-width: 100%; }.raw-inspection { padding: 16px; }
@media (max-width: 760px) { .candidate-workbench { grid-template-columns: 1fr; }.candidate-list { max-height: 240px; border-right: 0; border-bottom: 1px solid var(--line); }.candidate-list section { display: flex; gap: 10px; align-items: center; }.candidate-card { min-width: 190px; max-width: 250px; }.player-stage { padding: 10px; }.view-heading { font-size: 13px; } }
</style>

<style scoped>
.candidate-list { max-height: 650px; padding: 12px; }.candidate-card { font-size: 12px; gap: 5px; padding: 9px; }.candidate-card video { width: 52px; height: 70px; object-fit: cover; float: left; }.candidate-card b { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }.candidate-list h4 { font-size: 12px; margin: 12px 0; }.view-heading,.view-tabs { padding: 8px 14px; }.view-tabs button,.transport button { font-size: 12px; padding: 7px 10px; }.comparison-origin,.range-mapping,.sound-strip { margin: 0; padding: 5px 16px; font-size: 12px; }.range-mapping { background: #f8e9df; }.sound-strip { background: #e9f1ed; }.transport { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 14px; }.player-stage { padding: 10px; gap: 4px; }.raw-inspection { margin: 0; padding: 8px 16px; font-size: 12px; }
@media (max-width: 760px) { .candidate-list { max-height: 120px; }.candidate-card { min-width: 175px; }.candidate-card video { display: none; }.candidate-list h3 { font-size: 13px; margin: 0; } }
</style>

<style scoped>.view-tabs button,.transport button { min-height: 30px; padding: 5px 10px; }.view-heading,.view-tabs { padding-block: 6px; }.comparison-origin,.range-mapping,.sound-strip { padding-block: 3px; font-size: 12px; line-height: 1.4; }.player-stage video { height: clamp(140px, calc(100dvh - 610px), 300px); }@media(max-width:760px) {.candidate-list { max-height: 100px; }.candidate-card { margin: 4px 0; gap: 2px; }.candidate-list section { overflow-x: auto; }.candidate-list h4 { min-width: 60px; }.candidate-card small { font-size: 10px; }.view-tabs { gap: 5px; }.view-heading { font-size: 12px; }}</style>
<style scoped>.draft-editor { margin-top:0; }.draft-editor > header { padding:12px 18px; align-items:center; }.draft-editor > header h2 { font-size:20px; margin:0; }.draft-editor > header small { color:var(--muted); }.acceptance-panel { border-top:1px solid var(--line); margin:0; padding:16px 20px; }.result-actions { padding:10px 16px; }.player-stage video { height:clamp(180px, calc(100dvh - 535px), 440px); width:100%; object-fit:contain; }.form { padding:16px 20px; gap:10px; }.form details { margin:0; }.form details label { margin-top:10px; }.view-heading span { font-size:12px; }.notice { overflow-wrap:anywhere; }@media(max-width:760px){.draft-editor>header { flex-wrap:wrap; }.player-stage video {height:240px;}}</style>

<style scoped>.reference-player { width:100%; max-height:320px; background:#252320; }.comparison-origin button { margin-left:8px; font-size:11px; padding:4px 8px; }</style>

<style scoped>.player-stage video { height:clamp(140px, calc(100dvh - 650px), 360px); }@media(max-width:760px){.player-stage video {height:190px;}.draft-editor>header {padding:10px 14px;}.candidate-list:has(>p) {padding:8px 12px;max-height:60px;}}</style>
