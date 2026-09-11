<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRaw, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import JobStatusCard from "../components/JobStatusCard.vue";
import { subscribeJobs } from "../jobUpdates";
import { api } from "../api/client";
import AssetImageViewer from "../components/workspace/AssetImageViewer.vue";
import type {
  AssetDto,
  EpisodeContinuityDto,
  EpisodeContinuityFramesDto,
  EpisodeContinuityStateDto,
  JobDto,
  RuntimeBootstrapDto,
  SeriesAssetBindingDto,
  SeriesEpisodeDto,
  SeriesPlanDraft,
  SeriesPlanPreviewDto,
  SeriesPlanSegmentPreviewDto,
  SeriesPlanSegmentVersionDto,
  SeriesPlanVersionDto,
  SeriesSourceBeatDto,
  StorySeriesDto,
} from "../api/types";

const route = useRoute();
const router = useRouter();
const seriesId = String(route.params.seriesId);
const series = ref<StorySeriesDto | null>(null);
const plans = ref<SeriesPlanVersionDto[]>([]);
const episodes = ref<SeriesEpisodeDto[]>([]);
const jobs = ref<JobDto[]>([]);
const preview = ref<SeriesPlanPreviewDto | null>(null);
const segmentPreview = ref<SeriesPlanSegmentPreviewDto | null>(null);
const segments = ref<SeriesPlanSegmentVersionDto[]>([]);
const runtime = ref<RuntimeBootstrapDto | null>(null);
const loading = ref(true);
const actionBusy = ref(false);
const error = ref("");
const goalTitle = ref("");
const goalCount = ref(3);
const goalDuration = ref(15);
const goalKeep = ref("");
async function saveProductionGoal() {
  if (!series.value || actionBusy.value || !goalTitle.value.trim()) return;
  actionBusy.value = true; error.value = "";
  try {
    await api.updateSeriesProductionTarget(seriesId, {
      title: goalTitle.value.trim(),
      ...(series.value.lengthMode === "fixed" ? { plannedEpisodeCount: goalCount.value } : {}),
      defaultEpisodeDurationSeconds: goalDuration.value,
      mustKeep: goalKeep.value.split("\n").map(item => item.trim()).filter(Boolean),
    });
    await load();
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "生产目标未保存。"; }
  finally { actionBusy.value = false; }
}
const treatmentLabels = { retained: "保留", merged: "合并", simplified: "简化", omitted: "省略" };

const selectedPlanId = ref("");
const openContinuity = ref<EpisodeContinuityDto | null>(null);
const continuityFrames = ref<EpisodeContinuityFramesDto | null>(null);
const selectedKeyframeIds = ref<string[]>([]);
type ContinuityDecision = "inherit" | "adjust" | "reset";
type ScalarContinuityField = Exclude<keyof EpisodeContinuityStateDto, "props" | "unfinishedActions">;
const continuityFields: Array<{ key: ScalarContinuityField; label: string }> = [
  { key: "wardrobe", label: "服装" },
  { key: "location", label: "地点" },
  { key: "weather", label: "天气" },
  { key: "timeOfDay", label: "时间" },
  { key: "lighting", label: "光线" },
  { key: "childState", label: "孩子状态" },
  { key: "catState", label: "猫咪状态" },
  { key: "spatialPositions", label: "空间位置" },
  { key: "endingImage", label: "开场画面" },
];
const continuityDecisions = ref<Record<string, ContinuityDecision>>({});
const continuityDraft = ref<EpisodeContinuityStateDto | null>(null);
const continuityActionsText = ref("");
const frameViewerOpen = ref(false);
const frameViewerAssets = ref<AssetDto[]>([]);
const frameViewerActiveId = ref<string | null>(null);
const assetBindings = ref<SeriesAssetBindingDto[]>([]);
const assetsLoading = ref(false);
const assetsError = ref("");
let assetsController: AbortController | undefined;
async function loadAssets() {
  assetsController?.abort();
  const controller = new AbortController(); assetsController = controller;
  assetsLoading.value = true; assetsError.value = "";
  const timeout = setTimeout(() => controller.abort(), 15000);
  try { assetBindings.value = await api.seriesAssets(seriesId, controller.signal); }
  catch (reason) {
    if (assetsController === controller) assetsError.value = reason instanceof Error ? "共享参考暂未加载完成，可单独重试。" : "共享参考读取失败。";
  } finally { clearTimeout(timeout); if (assetsController === controller) assetsLoading.value = false; }
}
const sourceBeats = ref<SeriesSourceBeatDto[]>([]);
const editingPlan = ref(false);
const editablePlan = ref<SeriesPlanDraft | null>(null);
const visibleRouteEpisodes = ref(10);
const visibleProductionEpisodes = ref(12);
let unsubscribeJobs: (() => void) | undefined;
let disposed = false;

const selectedPlan = computed(() => plans.value.find((item) => item.id === selectedPlanId.value) ?? plans.value[0] ?? null);
const activePlan = computed(() => plans.value.find((item) => item.active) ?? null);
const latestPlanJob = computed(() => jobs.value.find((item) => item.kind === "plan_series") ?? null);
const latestSegmentJob = computed(() => jobs.value.find((item) => item.kind === "plan_series_segment") ?? null);
const jobRunning = computed(() => latestPlanJob.value !== null && !["succeeded", "failed", "cancelled"].includes(latestPlanJob.value.status));
const segmentJobRunning = computed(() => latestSegmentJob.value !== null && !["succeeded", "failed", "cancelled"].includes(latestSegmentJob.value.status));
const latestActiveSegment = computed(() => segments.value
  .filter((item) => item.active)
  .sort((left, right) => right.startEpisodeOrder - left.startEpisodeOrder)[0] ?? null);
const canPlanAnotherSegment = computed(() => Boolean(
  series.value
  && activePlan.value
  && (series.value.lengthMode === "ongoing"
    || series.value.plannedEpisodeCount === null
    || series.value.plannedCount < series.value.plannedEpisodeCount),
));
const canGenerate = computed(() => Boolean(preview.value && runtime.value?.worker.ready && runtime.value.provider.paidCallsEnabled && runtime.value.provider.apiKeyConfigured && !jobRunning.value && !actionBusy.value));
const canGenerateSegment = computed(() => Boolean(
  segmentPreview.value
  && runtime.value?.worker.ready
  && runtime.value.provider.paidCallsEnabled
  && runtime.value.provider.apiKeyConfigured
  && !segmentJobRunning.value
  && !actionBusy.value,
));
const routeEpisodes = computed(() => selectedPlan.value?.plan.episodes.slice(0, visibleRouteEpisodes.value) ?? []);
const productionEpisodes = computed(() => episodes.value.slice(0, visibleProductionEpisodes.value));
const canSaveEditedPlan = computed(() => {
  const draft = editablePlan.value;
  const expectedCount = preview.value?.plannedEpisodeCount;
  if (!draft || !expectedCount || draft.episodes.length !== expectedCount) return false;
  const contentComplete = draft.episodes.every((item, index) => item.order === index + 1 && [item.title, item.premise, item.openingState, item.trigger, item.childIntent, item.childAction, item.catResponse, item.visibleChange, item.endingState].every((value) => value.trim().length > 0));
  if (!contentComplete || sourceBeats.value.length === 0) return contentComplete;
  if (series.value?.adaptationPolicy === "condense_mainline") return true;
  const knownOrdinals = new Set(sourceBeats.value.map((beat) => beat.bindingOrder));
  const coverages = draft.episodes.flatMap((episode) => episode.sourceCoverage);
  return coverages.every((coverage) => knownOrdinals.has(coverage.sourceUnitOrdinal) && coverage.coverageNote.trim().length > 0)
    && sourceBeats.value.every((beat) => coverages.some((coverage) => coverage.sourceUnitOrdinal === beat.bindingOrder));
});
const episodeOrdersNeedRepair = computed(() => Boolean(
  editablePlan.value
  && editablePlan.value.episodes.some((item, index) => item.order !== index + 1),
));

function jobLabel(job: JobDto | null): string {
  if (!job) return "尚未生成整季方案";
  const labels: Record<JobDto["status"], string> = {
    queued: runtime.value?.worker.ready ? "等待后台任务领取" : "任务已保存，后台正在恢复",
    submitting: "正在生成整季方案",
    submitted: "模型正在处理",
    polling: "模型正在处理",
    storing: "正在校验并保存方案",
    succeeded: "整季方案已返回",
    failed: "本次没有生成可用方案",
    cancel_requested: "正在停止",
    cancelled: "已停止",
    submission_unknown: "提交状态需要确认，请勿重复生成",
  };
  return labels[job.status];
}

function segmentJobLabel(job: JobDto | null): string {
  if (!job) return "下一规划段尚未生成";
  const labels: Record<JobDto["status"], string> = {
    queued: runtime.value?.worker.ready ? "等待后台任务领取" : "任务已保存，后台正在恢复",
    submitting: "正在生成下一规划段",
    submitted: "模型正在处理规划段",
    polling: "模型正在处理规划段",
    storing: "正在校验并保存规划段",
    succeeded: "新规划段已返回，等待确认",
    failed: "本次没有生成可用规划段",
    cancel_requested: "正在停止",
    cancelled: "已停止",
    submission_unknown: "提交状态需要确认，请勿重复生成",
  };
  return labels[job.status];
}

function planStatus(plan: SeriesPlanVersionDto): string {
  if (plan.active) return "当前方案";
  if (plan.status === "rejected") return "未采用";
  if (plan.status === "superseded") return "已被新方案取代";
  if (plan.status === "candidate") {
    if (plan.promptRevision === "normalized-series-plan-v1") return plan.disposition === "needs_input" ? "规范化版本 · 待补充" : "规范化版本 · 待确认";
    return plan.disposition === "needs_input" ? "待补充" : "新方案 · 待确认";
  }
  return "历史方案";
}

function issueText(issue: SeriesPlanVersionDto["issues"][number]): string {
  if (issue.code !== "normalized_source_coverage") return `${issue.message}${issue.suggestedAction ? ` ${issue.suggestedAction}` : ""}`;
  const change = issue.beforeValue !== undefined || issue.afterValue !== undefined
    ? `${issue.beforeValue ?? "（空）"} → ${issue.afterValue ?? "（空）"}`
    : issue.message;
  return `${issue.path}：${change}${issue.message && issue.message !== change ? `；${issue.message}` : ""}`;
}

function episodeAction(episode: SeriesEpisodeDto): string {
  if (episode.status === "completed") return "查看成片";
  if (episode.status === "needs_attention") return "处理问题";
  if (!episode.projectId) return "开始制作";
  return episode.status === "story_review" ? "打开本集剧情" : "继续制作";
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    void loadAssets();
    const [detail, planList, segmentList, episodeList, jobList, run, beats] = await Promise.all([
      api.storySeriesDetail(seriesId), api.seriesPlans(seriesId), api.seriesPlanSegments(seriesId), api.seriesEpisodes(seriesId), api.seriesJobs(seriesId), api.runtime(), api.seriesSourceBeats(seriesId),
    ]);
    series.value = detail;
    goalTitle.value = detail.title;
    goalCount.value = detail.plannedEpisodeCount ?? 3;
    goalDuration.value = detail.defaultEpisodeDurationSeconds;
    goalKeep.value = detail.mustKeep.join("\n");
    plans.value = planList;
    segments.value = segmentList;
    episodes.value = episodeList;
    jobs.value = jobList;
    runtime.value = run;
    sourceBeats.value = beats;
    if (!selectedPlanId.value || !planList.some((item) => item.id === selectedPlanId.value)) selectedPlanId.value = planList[0]?.id ?? "";
    preview.value = await api.previewSeriesPlan(seriesId);
    await refreshSegmentPreview();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "系列暂时无法读取。";
  } finally {
    loading.value = false;
  }
}

async function refreshProgress() {
  try {
    const [jobList, planList, segmentList, episodeList, run] = await Promise.all([
      api.seriesJobs(seriesId), api.seriesPlans(seriesId), api.seriesPlanSegments(seriesId), api.seriesEpisodes(seriesId), api.runtime(),
    ]);
    jobs.value = jobList; plans.value = planList; segments.value = segmentList; episodes.value = episodeList; runtime.value = run;
    if (!selectedPlanId.value && planList[0]) selectedPlanId.value = planList[0].id;
  } catch { /* keep the last durable view while the local service recovers */ }
}

async function refreshSegmentPreview() {
  const currentSeries = series.value;
  const currentPlan = activePlan.value;
  if (!currentSeries || !currentPlan || !canPlanAnotherSegment.value) {
    segmentPreview.value = null;
    return;
  }
  const remaining = currentSeries.lengthMode === "fixed" && currentSeries.plannedEpisodeCount !== null
    ? currentSeries.plannedEpisodeCount - currentSeries.plannedCount
    : 12;
  segmentPreview.value = await api.previewSeriesPlanSegment(seriesId, {
    startEpisodeOrder: currentSeries.plannedCount + 1,
    requestedEpisodeCount: Math.min(30, remaining),
    expectedSeriesPlanVersionId: currentPlan.id,
    expectedPreviousSegmentVersionId: latestActiveSegment.value?.id ?? null,
  });
}

async function generateSegment() {
  if (!segmentPreview.value || !canGenerateSegment.value) return;
  actionBusy.value = true;
  error.value = "";
  try {
    const current = segmentPreview.value;
    const job = await api.generateSeriesPlanSegment(seriesId, {
      startEpisodeOrder: current.startEpisodeOrder,
      requestedEpisodeCount: current.requestedEpisodeCount,
      expectedSeriesPlanVersionId: current.expectedSeriesPlanVersionId,
      expectedPreviousSegmentVersionId: current.expectedPreviousSegmentVersionId ?? null,
      expectedInputHash: current.inputHash,
      idempotencyKey: crypto.randomUUID(),
    });
    jobs.value = [job, ...jobs.value.filter((item) => item.id !== job.id)];
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "下一规划段没有开始生成。";
  } finally {
    actionBusy.value = false;
  }
}

async function adoptSegment(segment: SeriesPlanSegmentVersionDto) {
  if (!activePlan.value) return;
  actionBusy.value = true;
  error.value = "";
  try {
    await api.activateSeriesPlanSegment(seriesId, segment.id, {
      expectedSeriesPlanVersionId: activePlan.value.id,
      expectedPreviousSegmentVersionId: latestActiveSegment.value?.id ?? null,
      idempotencyKey: crypto.randomUUID(),
    });
    await load();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "规划段没有采用。";
  } finally {
    actionBusy.value = false;
  }
}

async function rejectSegment(segment: SeriesPlanSegmentVersionDto) {
  actionBusy.value = true;
  try {
    await api.rejectSeriesPlanSegment(seriesId, segment.id);
    await load();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "规划段状态没有更新。";
  } finally {
    actionBusy.value = false;
  }
}

async function generatePlan() {
  if (!preview.value || actionBusy.value) return;
  actionBusy.value = true; error.value = "";
  try {
    const job = await api.generateSeriesPlan(seriesId, { expectedInputHash: preview.value.inputHash, idempotencyKey: crypto.randomUUID() });
    jobs.value = [job, ...jobs.value.filter((item) => item.id !== job.id)];
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "整季方案没有开始生成。";
  } finally { actionBusy.value = false; }
}

async function adoptPlan(plan: SeriesPlanVersionDto) {
  actionBusy.value = true; error.value = "";
  try {
    await api.activateSeriesPlan(seriesId, plan.id, { expectedActivePlanVersionId: activePlan.value?.id ?? null, idempotencyKey: crypto.randomUUID() });
    await load();
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "方案没有采用。"; }
  finally { actionBusy.value = false; }
}

async function rejectPlan(plan: SeriesPlanVersionDto) {
  actionBusy.value = true;
  try { await api.rejectSeriesPlan(seriesId, plan.id); await load(); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : "方案状态没有更新。"; }
  finally { actionBusy.value = false; }
}

function startPlanEdit(plan: SeriesPlanVersionDto) {
  editablePlan.value = structuredClone(toRaw(plan.plan));
  if (series.value?.adaptationPolicy === "condense_mainline") {
    const draft = editablePlan.value;
    draft.sourceTreatments ??= [];
    draft.adaptationRisks ??= [];
    draft.preservedRequirements ??= [];
    for (const beat of sourceBeats.value) {
      if (!draft.sourceTreatments.some(item => item.sourceUnitOrdinal === beat.bindingOrder)) {
        draft.sourceTreatments.push({ sourceUnitOrdinal: beat.bindingOrder, treatment: "retained", episodeOrders: [], reason: "" });
      }
    }
    for (const requirement of series.value.mustKeep) {
      if (!draft.preservedRequirements.some(item => item.requirement === requirement)) {
        draft.preservedRequirements.push({ requirement, handling: "", episodeOrders: [] });
      }
    }
  }
  editingPlan.value = true;
}

function renumberEpisodes() {
  if (!editablePlan.value) return;
  editablePlan.value = {
    ...editablePlan.value,
    episodes: editablePlan.value.episodes.map((episode, index) => ({
      ...episode,
      order: index + 1,
    })),
  };
}

function toggleSourceBeat(
  episode: SeriesPlanDraft["episodes"][number],
  beat: SeriesSourceBeatDto,
  event: Event,
) {
  const checked = (event.target as HTMLInputElement).checked;
  if (checked) {
    if (episode.sourceCoverage.some((coverage) => coverage.sourceUnitOrdinal === beat.bindingOrder)) return;
    episode.sourceCoverage.push({
      sourceUnitOrdinal: beat.bindingOrder,
      coverage: "whole",
      coverageNote: beat.title,
    });
    episode.sourceCoverage.sort((left, right) => left.sourceUnitOrdinal - right.sourceUnitOrdinal);
    return;
  }
  episode.sourceCoverage = episode.sourceCoverage.filter(
    (coverage) => coverage.sourceUnitOrdinal !== beat.bindingOrder,
  );
}

function addEpisode() {
  const expectedCount = preview.value?.plannedEpisodeCount;
  if (!editablePlan.value || !series.value || !expectedCount || editablePlan.value.episodes.length >= expectedCount) return;
  const order = editablePlan.value.episodes.length + 1;
  editablePlan.value.episodes.push({
    order,
    title: "",
    targetDurationSeconds: series.value.defaultEpisodeDurationSeconds,
    premise: "",
    openingState: "",
    trigger: "",
    childIntent: "",
    childAction: "",
    catResponse: "",
    visibleChange: "",
    endingState: "",
    continuityCarryover: [],
    recurringLocationKeys: [],
    recurringPropKeys: [],
    productionWarnings: [],
    sourceCoverage: [],
  });
}

async function saveEditedPlan() {
  if (!selectedPlan.value || !editablePlan.value || !canSaveEditedPlan.value) return;
  actionBusy.value = true; error.value = "";
  try {
    const saved = await api.materializeSeriesPlan(seriesId, selectedPlan.value.id, {
      basePlanVersionId: selectedPlan.value.id,
      plan: editablePlan.value,
      idempotencyKey: crypto.randomUUID(),
    });
    editingPlan.value = false;
    await load();
    selectedPlanId.value = saved.id;
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "修改后的方案没有保存。"; }
  finally { actionBusy.value = false; }
}

async function normalizeSavedPlan(plan: SeriesPlanVersionDto) {
  if (actionBusy.value) return;
  actionBusy.value = true; error.value = "";
  try {
    const currentPreview = await api.previewSeriesPlan(seriesId);
    preview.value = currentPreview;
    const expectedSettingsHash = currentPreview.settingsInputHash;
    if (!expectedSettingsHash) throw new Error("当前设置缺少校验标识，请刷新后重试。");
    const saved = await api.materializeSeriesPlan(seriesId, plan.id, {
      source: "saved_result",
      basePlanVersionId: plan.id,
      expectedSettingsHash,
      idempotencyKey: `series-normalize:${plan.id}:${expectedSettingsHash.slice(0, 32)}`,
    });
    await load();
    selectedPlanId.value = saved.id;
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "方案没有完成规范化校验。"; }
  finally { actionBusy.value = false; }
}

async function openEpisode(episode: SeriesEpisodeDto) {
  if (actionBusy.value) return;
  actionBusy.value = true; error.value = "";
  try {
    const projectId = episode.projectId ?? (await api.materializeSeriesEpisode(seriesId, episode.id, "episode-project:" + episode.id)).id;
    await router.push("/projects/" + projectId + (episode.status === "completed" ? "/delivery" : "/planner"));
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "本集工作区没有打开。"; }
  finally { actionBusy.value = false; }
}

async function showContinuity(episode: SeriesEpisodeDto) {
  try {
    openContinuity.value = await api.seriesEpisodeContinuity(seriesId, episode.id);
    continuityFrames.value = openContinuity.value.previousEpisodeId
      ? await api.seriesEpisodeContinuityFrames(seriesId, openContinuity.value.previousEpisodeId)
      : null;
    selectedKeyframeIds.value = continuityFrames.value?.selectedKeyframes.map((asset) => asset.id) ?? [];
    const snapshot = openContinuity.value.incoming;
    continuityDraft.value = snapshot ? structuredClone(toRaw(snapshot.state)) : null;
    continuityActionsText.value = snapshot?.state.unfinishedActions.join("\n") ?? "";
    continuityDecisions.value = Object.fromEntries(
      [...continuityFields.map((field) => field.key), "props", "unfinishedActions"].map((key) => [
        key,
        snapshot?.decisions[key] ?? "inherit",
      ]),
    );
  }
  catch (reason) { error.value = reason instanceof Error ? reason.message : "连续性状态无法读取。"; }
}

function toggleKeyframe(assetId: string) {
  const current = selectedKeyframeIds.value;
  if (current.includes(assetId)) {
    selectedKeyframeIds.value = current.filter((id) => id !== assetId);
  } else if (current.length < 2) {
    selectedKeyframeIds.value = [...current, assetId];
  }
}

async function saveKeyframes() {
  const previousEpisodeId = openContinuity.value?.previousEpisodeId;
  if (!previousEpisodeId) return;
  actionBusy.value = true;
  try {
    await api.selectSeriesEpisodeContinuityKeyframes(
      seriesId,
      previousEpisodeId,
      selectedKeyframeIds.value,
    );
    continuityFrames.value = await api.seriesEpisodeContinuityFrames(
      seriesId,
      previousEpisodeId,
    );
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "连续性关键帧没有保存。";
  } finally { actionBusy.value = false; }
}

function viewFrames(assets: AssetDto[], active: AssetDto) {
  frameViewerAssets.value = assets;
  frameViewerActiveId.value = active.id;
  frameViewerOpen.value = true;
}

async function confirmContinuity() {
  const snapshot = openContinuity.value?.incoming;
  if (!snapshot || !continuityDraft.value || actionBusy.value) return;
  actionBusy.value = true;
  error.value = "";
  try {
    continuityDraft.value.unfinishedActions = continuityActionsText.value
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    await api.confirmSeriesEpisodeContinuity(seriesId, snapshot.episodeId, {
      direction: "incoming",
      state: continuityDraft.value,
      decisions: continuityDecisions.value,
      expectedSnapshotId: snapshot.id,
      idempotencyKey: crypto.randomUUID(),
    });
    openContinuity.value = await api.seriesEpisodeContinuity(seriesId, snapshot.episodeId);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "连续性没有确认，请检查需要调整的内容。";
  } finally {
    actionBusy.value = false;
  }
}

onMounted(async () => { unsubscribeJobs = subscribeJobs(() => ({ seriesId }), refreshProgress, () => jobs.value.some(job => job.execution?.waitingForProvider)); await load(); });
onBeforeUnmount(() => { disposed = true; assetsController?.abort(); assetsController = undefined; unsubscribeJobs?.(); });
watch(selectedPlanId, () => { editingPlan.value = false; editablePlan.value = null; visibleRouteEpisodes.value = 10; });
</script>

<template>
  <main class="page series-workspace">
    <section v-if="loading" class="card empty">正在打开系列…</section>
    <template v-else-if="series">
      <header class="series-header">
        <div><RouterLink to="/series">← 系列</RouterLink><h1>{{ series.title }}</h1><p>{{ series.premise }}</p></div>
        <div class="series-count"><b>{{ series.lengthMode === "ongoing" ? `持续连载 · 已规划 ${series.plannedCount} 集` : `固定系列 · 计划 ${series.plannedEpisodeCount} 集` }}</b><span>已开始 {{ series.materializedCount }} · 已完成 {{ series.completedCount }}</span></div>
      </header>
      <p v-if="error" class="notice error">{{ error }}</p>

      <nav class="series-tabs" aria-label="系列创作台"><a href="#setting">系列设定</a><a href="#route">整季路线</a><a href="#episodes">剧集列表</a><a href="#assets">共享资产</a><a href="#continuity">连续性</a></nav>

      <section id="setting" class="card studio-section">
        <header><div><h2>系列设定</h2><p>{{ series.narrativeMode === "continuous" ? "连续剧情" : series.narrativeMode === "lightly_serialized" ? "轻连续" : "单元故事" }} · 每集 {{ series.defaultEpisodeDurationSeconds }} 秒</p></div></header>
        <details v-if="series.adaptationPolicy === 'condense_mainline' && !activePlan" class="goal-editor">
          <summary>调整生产目标（不重新分析原文）</summary>
          <label>系列名称<input v-model="goalTitle" maxlength="160" required /></label>
          <label v-if="series.lengthMode === 'fixed'">计划集数<input v-model.number="goalCount" type="number" min="2" /></label>
          <label>每集时长（秒）<input v-model.number="goalDuration" type="number" min="8" max="15" /></label>
          <label>必须保留（每行一项）<textarea v-model="goalKeep" /></label>
          <p v-if="series.lengthMode === 'fixed'">{{ goalCount }} × {{ goalDuration }} 秒 = {{ goalCount * goalDuration }} 秒</p>
          <button class="secondary" :disabled="actionBusy || jobRunning || segmentJobRunning || !goalTitle.trim() || goalCount < 2 || goalDuration < 8 || goalDuration > 15" @click="saveProductionGoal">保存生产目标</button>
        </details>
        <div class="setting-grid"><dl><dt>世界与环境</dt><dd>{{ series.worldSetting }}</dd></dl><dl><dt>情绪方向</dt><dd>{{ series.emotionalDirection }}</dd></dl><dl><dt>必须保留</dt><dd>{{ series.mustKeep.join("、") || "固定儿童、猫咪和画风" }}</dd></dl><dl><dt>必须避免</dt><dd>{{ series.mustAvoid.join("、") || "危险动作与身份变化" }}</dd></dl></div>
      </section>

      <section v-if="sourceBeats.length" class="card studio-section source-coverage">
        <header><div><h2>来源剧情节拍</h2><p>{{ sourceBeats.length }} 个来源事件{{ series.adaptationPolicy === "condense_mainline" ? "；保留、合并、简化和省略必须明确说明。" : "；保留原有覆盖规则与分集路线。" }}</p></div></header>
        <div class="beat-grid"><article v-for="beat in sourceBeats" :key="beat.id"><b>{{ beat.bindingOrder }}</b><span>{{ beat.title }}</span><small>被方案引用 {{ selectedPlan?.plan.episodes.filter((episode) => episode.sourceCoverage.some((coverage) => coverage.sourceUnitOrdinal === beat.bindingOrder)).length ?? 0 }} 次</small></article></div>
      </section>

      <section class="card planning-section">
        <header><div><h2>本次系列规划</h2><p v-if="preview">将生成 {{ preview.plannedEpisodeCount }} 集简纲；不会同时生成剧本、图片、分镜或视频。</p></div><button class="primary" :disabled="!canGenerate" @click="generatePlan">{{ jobRunning ? "规划进行中" : activePlan ? "重新规划整季" : "生成系列规划（付费）" }}</button></header>
        <p v-if="!runtime?.worker.ready" class="notice">后台任务暂时不可用，系统正在自动恢复。</p>
        <p v-else-if="!runtime?.provider.apiKeyConfigured || !runtime?.provider.paidCallsEnabled" class="notice">模型服务尚未开放新的付费调用，请先检查运行设置。</p>
        <JobStatusCard v-if="latestPlanJob" :job-id="latestPlanJob.id" title="系列规划任务" @replacement="refreshProgress" />
        <div class="progress-line" aria-live="polite"><b>{{ jobLabel(latestPlanJob) }}</b><span v-if="jobRunning">可以离开页面，任务会继续并保存。</span></div>
        <details v-if="preview"><summary>查看完整规划指令</summary><pre>{{ preview.prompt }}</pre></details>
      </section>

      <section v-if="activePlan && (canPlanAnotherSegment || segments.length)" class="card studio-section segment-planning">
        <header>
          <div>
            <h2>规划段</h2>
            <p v-if="segmentPreview">下一次只规划第 {{ segmentPreview.startEpisodeOrder }}–{{ segmentPreview.startEpisodeOrder + segmentPreview.requestedEpisodeCount - 1 }} 集，不会自动继续后续段。</p>
            <p v-else>已采用的规划段都会保留；每一段都需要单独确认。</p>
          </div>
          <button v-if="segmentPreview" class="primary" :disabled="!canGenerateSegment" @click="generateSegment">{{ segmentJobRunning ? "规划段生成中" : "规划下一段（付费）" }}</button>
        </header>
        <JobStatusCard v-if="latestSegmentJob" :job-id="latestSegmentJob.id" title="分段规划任务" @replacement="refreshProgress" />
        <div v-if="segmentPreview" class="progress-line" aria-live="polite"><b>{{ segmentJobLabel(latestSegmentJob) }}</b><span>本段 {{ segmentPreview.requestedEpisodeCount }} 集 · 单次最多 30 集</span></div>
        <details v-if="segmentPreview"><summary>查看本段完整规划指令</summary><pre>{{ segmentPreview.prompt }}</pre></details>
        <div v-if="segments.length" class="segment-list">
          <article v-for="segment in segments" :key="segment.id">
            <div><b>第 {{ segment.startEpisodeOrder }}–{{ segment.startEpisodeOrder + segment.requestedEpisodeCount - 1 }} 集</b><span>{{ segment.active ? "已采用" : segment.status === "candidate" ? "新规划段 · 待确认" : segment.status === "rejected" ? "未采用" : "已被取代" }}</span></div>
            <p v-if="segment.issues.length">{{ segment.issues.length }} 项需要查看的内容</p>
            <div v-if="segment.status === 'candidate'" class="candidate-actions"><button class="ghost" :disabled="actionBusy" @click="rejectSegment(segment)">不采用</button><button class="primary" :disabled="segment.disposition !== 'candidate_ready' || actionBusy" @click="adoptSegment(segment)">采用本段</button></div>
          </article>
        </div>
      </section>

      <section v-if="plans.length" class="plan-versions">
        <button v-for="plan in plans" :key="plan.id" :class="['plan-version', { active: selectedPlan?.id === plan.id }]" @click="selectedPlanId = plan.id"><b>方案 {{ plan.revision }}</b><span>{{ planStatus(plan) }}</span><span v-if="plan.promptRevision === 'normalized-series-plan-v1' && plan.basePlanVersionId">来源方案 {{ plans.find((item) => item.id === plan.basePlanVersionId)?.revision ?? plan.basePlanVersionId }}</span></button>
      </section>

      <section v-if="selectedPlan" id="route" class="card studio-section">
        <header><div><h2>整季路线</h2><p>{{ selectedPlan.plan.seriesBible.logline }}</p></div><div v-if="selectedPlan.status === 'candidate'" class="candidate-actions"><button class="secondary normalize-plan" :disabled="actionBusy" @click="normalizeSavedPlan(selectedPlan)">规范化并重新校验</button><button class="ghost" @click="startPlanEdit(selectedPlan)">{{ selectedPlan.disposition === "needs_input" ? "补充方案" : "编辑方案" }}</button><button class="ghost" @click="rejectPlan(selectedPlan)">不采用</button><button class="primary" :disabled="selectedPlan.disposition !== 'candidate_ready' || actionBusy" @click="adoptPlan(selectedPlan)">采用整季方案</button></div></header>
        <p v-if="selectedPlan.issues.length" class="notice">方案还包含 {{ selectedPlan.issues.length }} 项需要查看的内容。{{ selectedPlan.disposition === "needs_input" ? "补充后才能采用，但不需要重新调用模型。" : "不影响查看。" }}</p>
        <ul v-if="selectedPlan.issues.length" class="notice"><li v-for="(issue, index) in selectedPlan.issues" :key="index">{{ issueText(issue) }}</li></ul>
        <section v-if="!editingPlan && series.adaptationPolicy === 'condense_mainline'" class="adaptation-review">
          <h3>原文如何缩编</h3>
          <article v-for="item in selectedPlan.plan.sourceTreatments" :key="item.sourceUnitOrdinal"><b>来源 {{ item.sourceUnitOrdinal }} · {{ treatmentLabels[item.treatment] }}</b><p>{{ item.reason }}</p><small>{{ item.treatment === 'omitted' ? '已省略，不计入覆盖' : '用于第 ' + item.episodeOrders.join('、') + ' 集' }}</small></article>
          <h3>必须保留内容</h3><p v-for="item in selectedPlan.plan.preservedRequirements" :key="item.requirement">{{ item.requirement }}：{{ item.handling }}（第 {{ item.episodeOrders.join('、') }} 集）</p>
          <p v-for="(risk, index) in selectedPlan.plan.adaptationRisks" :key="index" :class="['notice', { error: risk.blocking }]">{{ risk.blocking ? '待调整' : '规划提醒' }}：{{ risk.message }}</p>
        </section>
        <div v-if="editingPlan && editablePlan" class="plan-editor">
          <label>整季一句话<input v-model="editablePlan.seriesBible.logline" /></label>
          <details>
            <summary>共享道具与连续性</summary>
            <p>修改名称与规则会随新方案保存；原道具引用标识保持不变。</p>
            <fieldset v-for="prop in editablePlan.seriesBible.recurringProps" :key="prop.key">
              <legend>{{ prop.name }}</legend>
              <label>道具名称<input v-model="prop.name" :aria-label="`共享道具 ${prop.key} 名称`" /></label>
              <label>道具连续性规则<textarea v-model="prop.continuityRule" :aria-label="`共享道具 ${prop.key} 连续性规则`" /></label>
            </fieldset>
            <label>整季连续性规则（每行一项）<textarea :value="editablePlan.seriesBible.continuityRules.join('\n')" rows="6" @change="editablePlan.seriesBible.continuityRules = ($event.target as HTMLTextAreaElement).value.split('\n').map(value => value.trim()).filter(Boolean)" /></label>
          </details>
          <section v-if="series.adaptationPolicy === 'condense_mainline'" class="adaptation-editor">
            <h3>来源处理说明</h3>
            <article v-for="item in editablePlan.sourceTreatments" :key="item.sourceUnitOrdinal"><b>来源 {{ item.sourceUnitOrdinal }}</b>
              <select v-model="item.treatment"><option v-for="(label, value) in treatmentLabels" :key="value" :value="value">{{ label }}</option></select>
              <label v-for="episode in editablePlan.episodes" :key="episode.order"><input v-model="item.episodeOrders" type="checkbox" :value="episode.order" />第 {{ episode.order }} 集</label>
              <label>保留内容与删改原因<textarea v-model="item.reason" /></label>
            </article>
            <h3>必须保留的内容如何实现</h3>
            <article v-for="item in editablePlan.preservedRequirements" :key="item.requirement"><b>{{ item.requirement }}</b><textarea v-model="item.handling" />
              <label v-for="episode in editablePlan.episodes" :key="episode.order"><input v-model="item.episodeOrders" type="checkbox" :value="episode.order" />第 {{ episode.order }} 集</label>
            </article>
            <article v-for="(risk, index) in editablePlan.adaptationRisks" :key="index"><label>风险与调整说明<textarea v-model="risk.message" /></label><label><input v-model="risk.blocking" type="checkbox" />尚未解决，阻止采用</label></article>
            <p>修改来源处理后，请同步下方每集的实际引用。省略事件应取消引用；精简事件使用“部分覆盖”。</p>
          </section>
          <div v-if="episodeOrdersNeedRepair" class="order-repair"><p>集数没有从 1 连续编号。可以按当前显示顺序修正，不会调用模型。</p><button class="secondary renumber-episodes" @click="renumberEpisodes">按当前顺序编号为 1–{{ editablePlan.episodes.length }}</button></div>
          <article v-for="(episode, episodeIndex) in editablePlan.episodes" :key="episodeIndex" class="episode-editor"><b>第 {{ episode.order }} 集</b><button class="ghost" @click="editablePlan.episodes.splice(episodeIndex, 1)">移除此集</button><label>目标时长（秒）<input v-model.number="episode.targetDurationSeconds" type="number" min="8" max="15" /></label><label>标题<input v-model="episode.title" /></label><label>本集事件<textarea v-model="episode.premise" /></label><label>开场状态<textarea v-model="episode.openingState" /></label><label>触发<textarea v-model="episode.trigger" /></label><label>儿童目标<textarea v-model="episode.childIntent" /></label><label>儿童动作<textarea v-model="episode.childAction" /></label><label>猫咪回应<textarea v-model="episode.catResponse" /></label><label>可见变化<textarea v-model="episode.visibleChange" /></label><label>结尾状态<textarea v-model="episode.endingState" /></label><label>跨集承接（每行一项）<textarea :value="episode.continuityCarryover.join('\n')" :aria-label="`第 ${episode.order} 集跨集承接`" @change="episode.continuityCarryover = ($event.target as HTMLTextAreaElement).value.split('\n').map(value => value.trim()).filter(Boolean)" /></label><label>制作提示（每行一项）<textarea :value="episode.productionWarnings.join('\n')" :aria-label="`第 ${episode.order} 集制作提示`" @change="episode.productionWarnings = ($event.target as HTMLTextAreaElement).value.split('\n').map(value => value.trim()).filter(Boolean)" /></label><fieldset v-if="sourceBeats.length" class="episode-source-coverage"><legend>来源剧情节拍</legend><div class="beat-options"><label v-for="beat in sourceBeats" :key="beat.id"><input type="checkbox" :checked="episode.sourceCoverage.some((coverage) => coverage.sourceUnitOrdinal === beat.bindingOrder)" :aria-label="`第 ${episode.order} 集使用剧情节拍 ${beat.bindingOrder}`" @change="toggleSourceBeat(episode, beat, $event)" />{{ beat.bindingOrder }} · {{ beat.title }}</label></div><div v-for="coverage in episode.sourceCoverage" :key="coverage.sourceUnitOrdinal" class="coverage-detail"><b>节拍 {{ coverage.sourceUnitOrdinal }}</b><select v-model="coverage.coverage" :aria-label="`第 ${episode.order} 集剧情节拍 ${coverage.sourceUnitOrdinal}覆盖方式`"><option value="whole">完整覆盖</option><option value="partial">部分覆盖</option><option value="continuation">延续覆盖</option></select><label>覆盖说明<input v-model="coverage.coverageNote" :aria-label="`第 ${episode.order} 集剧情节拍 ${coverage.sourceUnitOrdinal}覆盖说明`" /></label></div></fieldset></article>
          <button v-if="preview && editablePlan.episodes.length < preview.plannedEpisodeCount" class="secondary" @click="addEpisode">补充第 {{ editablePlan.episodes.length + 1 }} 集</button>
          <div class="editor-actions"><button class="ghost" @click="editingPlan = false">取消</button><button class="primary" :disabled="!canSaveEditedPlan || actionBusy" @click="saveEditedPlan">保存为新候选</button></div>
          <p v-if="!canSaveEditedPlan" class="field-hint">需要补齐本次规划的 {{ preview?.plannedEpisodeCount }} 集、重要内容；来源处理与时长由保存后的校验明确指出；不会重新调用模型。</p>
        </div>
        <div v-else class="episode-rail">
          <article v-for="episode in routeEpisodes" :key="episode.order"><b>第 {{ episode.order }} 集</b><h3>{{ episode.title }}</h3><p>{{ episode.premise }}</p><small>目标 {{ episode.targetDurationSeconds }} 秒</small><dl><dt>开场</dt><dd>{{ episode.openingState }}</dd><dt>主要动作</dt><dd>{{ episode.childAction }} {{ episode.catResponse }}</dd><dt>可见变化</dt><dd>{{ episode.visibleChange }}</dd><dt>结尾承接</dt><dd>{{ episode.endingState }}</dd></dl></article>
        </div>
        <button v-if="!editingPlan && selectedPlan.plan.episodes.length > routeEpisodes.length" class="ghost load-more" @click="visibleRouteEpisodes += 10">继续查看</button>
      </section>

      <section id="episodes" class="card studio-section">
        <header><div><h2>剧集列表</h2><p>点击“开始制作”时才创建这一集的短片项目。</p></div></header>
        <div v-if="episodes.length" class="episode-list"><article v-for="episode in productionEpisodes" :key="episode.id"><span class="episode-order">{{ episode.order }}</span><div><h3>{{ episode.title }}</h3><p>{{ episode.outline.visibleChange }}</p><small>{{ episode.outline.openingState }} → {{ episode.outline.endingState }}</small></div><button v-if="episode.order > 1" class="ghost" @click="showContinuity(episode)">连续性</button><button class="secondary" :disabled="actionBusy" @click="openEpisode(episode)">{{ episodeAction(episode) }}</button></article><button v-if="episodes.length > productionEpisodes.length" class="ghost load-more" @click="visibleProductionEpisodes += 12">加载更多剧集</button></div>
        <p v-else class="empty">采用整季方案后，这里会出现稳定的剧集条目。</p>
      </section>

      <section id="assets" class="card studio-section"><header><div><h2>共享资产</h2><p>先确定复用关系；每张新图片仍需由你明确生成。</p></div></header><p v-if="assetsLoading" role="status">共享参考正在加载，剧情与剧集列表可继续查看。</p><div v-if="assetsError" class="notice"><p>{{ assetsError }}</p><button class="secondary" @click="loadAssets">重新读取共享参考</button></div><div v-if="assetBindings.length" class="bound-assets"><span v-for="binding in assetBindings" :key="binding.id"><b>{{ binding.bindingKey }}</b> · {{ binding.role }}</span></div><div class="asset-needs"><span v-for="location in activePlan?.plan.seriesBible.recurringLocations ?? []" :key="location.key">环境 · {{ location.name }}</span><span v-for="prop in activePlan?.plan.seriesBible.recurringProps ?? []" :key="prop.key">道具 · {{ prop.name }}</span><span v-for="rule in activePlan?.plan.seriesBible.wardrobeRules ?? []" :key="rule">服装 · {{ rule }}</span></div></section>

      <section id="continuity" class="card studio-section">
        <header><div><h2>连续性</h2><p>第 2 集起，生成视频前需要确认从上一集继承、调整或重置的状态。</p></div></header>
        <p v-if="!openContinuity" class="empty">在剧集列表中选择一集查看相邻状态。</p>
        <template v-else>
          <div class="continuity-panel"><div><b>本集孩子状态</b><p>{{ openContinuity.incoming?.state.childState }}</p></div><span>→</span><div><b>本集开场</b><p>{{ openContinuity.incoming?.state.endingImage }}</p></div><span v-if="openContinuity.incoming?.confirmed" class="pill good">已确认</span></div>
          <div v-if="openContinuity.incoming && !openContinuity.incoming.confirmed && continuityDraft" class="continuity-editor">
            <p>逐项确认下一集如何承接。选择“调整”或“重置”后，可直接修改右侧状态。</p>
            <label v-for="field in continuityFields" :key="field.key" class="continuity-field">
              <span>{{ field.label }}</span>
              <select v-model="continuityDecisions[field.key]" :aria-label="`${field.label}的连续性处理`"><option value="inherit">继承</option><option value="adjust">调整</option><option value="reset">重置</option></select>
              <textarea v-model="continuityDraft[field.key]" :readonly="continuityDecisions[field.key] === 'inherit'" :aria-label="`${field.label}状态`" />
            </label>
            <div class="continuity-field continuity-props"><span>道具</span><select v-model="continuityDecisions.props" aria-label="道具的连续性处理"><option value="inherit">继承</option><option value="adjust">调整</option><option value="reset">重置</option></select><div><p v-if="!continuityDraft.props.length">本集没有需要承接的道具。</p><label v-for="prop in continuityDraft.props" :key="prop.key"><b>{{ prop.name }}</b><input v-model="prop.state" :readonly="continuityDecisions.props === 'inherit'" :aria-label="`${prop.name}状态`" /></label></div></div>
            <label class="continuity-field"><span>未完成动作</span><select v-model="continuityDecisions.unfinishedActions" aria-label="未完成动作的连续性处理"><option value="inherit">继承</option><option value="adjust">调整</option><option value="reset">重置</option></select><textarea v-model="continuityActionsText" :readonly="continuityDecisions.unfinishedActions === 'inherit'" aria-label="未完成动作，每行一项" /></label>
            <button class="primary continuity-confirm" :disabled="actionBusy" @click="confirmContinuity">确认本集连续性</button>
          </div>
          <div v-if="continuityFrames?.lastFrame" class="continuity-images">
            <div><b>上一集尾帧</b><button class="frame-card" @click="viewFrames([continuityFrames!.lastFrame!, ...continuityFrames!.candidates], continuityFrames!.lastFrame!)"><img :src="`/api/v1/assets/${continuityFrames.lastFrame.id}/content`" alt="上一集最终画面" /><span>查看大图</span></button></div>
            <div><b>连续性关键帧（最多两张）</b><div class="keyframe-grid"><article v-for="asset in continuityFrames.candidates" :key="asset.id"><button class="frame-card" @click="viewFrames(continuityFrames!.candidates, asset)"><img :src="`/api/v1/assets/${asset.id}/content`" alt="连续性关键帧候选" /><span>查看大图</span></button><label><input type="checkbox" :checked="selectedKeyframeIds.includes(asset.id)" :disabled="!selectedKeyframeIds.includes(asset.id) && selectedKeyframeIds.length >= 2" @change="toggleKeyframe(asset.id)" />用于下一集</label></article></div><button class="secondary" :disabled="actionBusy" @click="saveKeyframes">保存关键帧选择</button></div>
          </div>
          <p v-else class="field-hint">上一集选定最终成片后，会在本机提取尾帧与关键帧候选，不调用模型。</p>
        </template>
      </section>
      <AssetImageViewer :open="frameViewerOpen" title="连续性参考画面" :assets="frameViewerAssets" :active-asset-id="frameViewerActiveId" :comparisons="[]" @asset-change="(asset) => frameViewerActiveId = asset.id" @close="frameViewerOpen = false" />
    </template>
  </main>
</template>

<style scoped>
.series-header, .studio-section > header, .planning-section > header { display: flex; justify-content: space-between; align-items: start; gap: 24px; }.series-header { margin-bottom: 18px; }.series-header h1 { margin: 8px 0; }.series-header p { color: var(--muted); max-width: 820px; }.series-count { display: grid; text-align: right; gap: 5px; }.series-count b { font-size: 25px; }.series-count span { color: var(--muted); font-size: 12px; }.series-tabs { position: sticky; top: 70px; z-index: 10; margin-bottom: 14px; padding: 10px 16px; display: flex; gap: 22px; border: 1px solid var(--line); border-radius: 14px; background: #fffcf7ee; backdrop-filter: blur(12px); font-size: 12px; }.studio-section, .planning-section { margin-bottom: 16px; padding: 25px; }.studio-section header p, .planning-section header p { color: var(--muted); }.setting-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }.setting-grid dl { margin: 0; padding: 14px; border-radius: 12px; background: #f8f3ec; }.setting-grid dt { color: var(--accent-dark); font-size: 11px; font-weight: 700; }.setting-grid dd { margin: 7px 0 0; line-height: 1.65; }.progress-line { margin-top: 18px; display: flex; justify-content: space-between; color: #647565; }.progress-line span { color: var(--muted); font-size: 12px; }.planning-section details { margin-top: 13px; }.planning-section pre { max-height: 280px; overflow: auto; white-space: pre-wrap; padding: 14px; background: #f7f2eb; border-radius: 10px; }.plan-versions { margin-bottom: 14px; display: flex; gap: 8px; overflow-x: auto; }.plan-version { min-width: 160px; padding: 11px 13px; display: grid; gap: 4px; text-align: left; border: 1px solid var(--line); border-radius: 12px; background: white; color: var(--ink); cursor: pointer; }.plan-version span { color: var(--muted); font-size: 11px; }.plan-version.active { border-color: var(--accent); box-shadow: 0 0 0 2px #db7a5d1e; }.candidate-actions { display: flex; gap: 8px; }.episode-rail { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(220px, 1fr); gap: 10px; overflow-x: auto; }.episode-rail article { min-height: 180px; padding: 16px; border-radius: 13px; background: #f8f2ea; }.episode-rail h3 { margin: 10px 0 7px; }.episode-rail p { color: #655c54; line-height: 1.55; }.episode-rail small { color: var(--muted); }.episode-list { display: grid; gap: 8px; }.episode-list article { padding: 13px; display: grid; grid-template-columns: 38px minmax(0, 1fr) auto auto; gap: 12px; align-items: center; border: 1px solid var(--line); border-radius: 12px; }.episode-list h3, .episode-list p { margin: 0 0 4px; }.episode-list p, .episode-list small { color: var(--muted); }.episode-order { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 10px; background: #f5e5dc; color: var(--accent-dark); font-weight: 800; }.asset-needs { display: flex; flex-wrap: wrap; gap: 8px; }.asset-needs span { padding: 8px 10px; border-radius: 9px; background: var(--sage-soft); color: #536b58; font-size: 12px; }.continuity-panel { display: grid; grid-template-columns: 1fr auto 1fr auto; align-items: center; gap: 15px; }.continuity-panel > div { padding: 15px; background: #f8f3ec; border-radius: 12px; }.continuity-panel p { margin: 7px 0 0; color: var(--muted); line-height: 1.55; }
.continuity-editor { margin-top: 14px; padding: 16px; display: grid; gap: 9px; border-radius: 13px; background: #f8f3ec; }.continuity-editor > p { margin: 0 0 4px; color: var(--muted); font-size: 12px; }.continuity-field { display: grid; grid-template-columns: 90px 92px minmax(0, 1fr); gap: 9px; align-items: center; }.continuity-field > span { color: #6f6259; font-size: 12px; font-weight: 700; }.continuity-field select, .continuity-field textarea, .continuity-field input { width: 100%; padding: 8px 9px; border: 1px solid var(--line); border-radius: 8px; background: white; }.continuity-field textarea { min-height: 52px; resize: vertical; }.continuity-field textarea[readonly], .continuity-field input[readonly] { color: var(--muted); background: #f1ede7; }.continuity-props > div { display: grid; gap: 7px; }.continuity-props > div > p { margin: 0; color: var(--muted); }.continuity-props label { display: grid; grid-template-columns: 120px 1fr; gap: 8px; align-items: center; }.continuity-confirm { justify-self: end; margin-top: 4px; }
.plan-editor { display: grid; gap: 14px; }.plan-editor > label, .episode-editor label { display: grid; gap: 5px; color: var(--muted); font-size: 11px; }.plan-editor input, .plan-editor textarea { width: 100%; padding: 9px 11px; border: 1px solid var(--line); border-radius: 9px; background: white; }.episode-editor { padding: 14px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; border-radius: 12px; background: #f8f3ec; }.episode-editor > b { grid-column: 1 / -1; }.episode-editor textarea { min-height: 64px; resize: vertical; }.editor-actions { display: flex; justify-content: flex-end; gap: 8px; }.field-hint { margin: 0; color: #9b5b49; }.load-more { margin-top: 12px; }
.episode-source-coverage { grid-column: 1 / -1; margin: 4px 0 0; padding: 12px; display: grid; gap: 10px; border: 1px solid var(--line); border-radius: 10px; background: #fffaf4; }.episode-source-coverage legend { padding: 0 6px; color: var(--accent-dark); font-size: 12px; font-weight: 700; }.beat-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 10px; }.beat-options label { display: flex; flex-direction: row; align-items: center; gap: 6px; color: #655c54; }.beat-options input { width: auto; }.coverage-detail { padding-top: 9px; display: grid; grid-template-columns: 72px 110px minmax(0, 1fr); gap: 9px; align-items: end; border-top: 1px dashed var(--line); }.coverage-detail select { min-height: 36px; padding: 7px; border: 1px solid var(--line); border-radius: 8px; background: white; }
.order-repair { padding: 12px 14px; display: flex; justify-content: space-between; align-items: center; gap: 14px; border-radius: 11px; background: #fff3dc; }.order-repair p { margin: 0; color: #725f42; }
.bound-assets { margin-bottom: 10px; display: flex; flex-wrap: wrap; gap: 8px; }.bound-assets span { padding: 9px 11px; border: 1px solid #cadae0; border-radius: 9px; background: #f4f9fa; color: #506a72; font-size: 12px; }

.continuity-images { margin-top: 15px; display: grid; grid-template-columns: minmax(160px, 240px) 1fr; gap: 16px; }.continuity-images > div { display: grid; align-content: start; gap: 9px; }.keyframe-grid { display: flex; flex-wrap: wrap; gap: 10px; }.keyframe-grid article { display: grid; gap: 6px; }.keyframe-grid label { font-size: 12px; color: var(--muted); }.frame-card { width: 132px; padding: 7px; display: grid; gap: 5px; border: 1px solid var(--line); border-radius: 10px; background: #f6f2eb; color: var(--ink); cursor: pointer; }.frame-card img { width: 100%; aspect-ratio: 9 / 16; object-fit: contain; background: #e9e4dc; border-radius: 7px; }.frame-card span { font-size: 11px; }
.segment-planning details { margin-top: 13px; }.segment-planning pre { max-height: 260px; overflow: auto; white-space: pre-wrap; padding: 14px; border-radius: 10px; background: #f7f2eb; }.segment-list { margin-top: 14px; display: grid; gap: 8px; }.segment-list article { padding: 13px 14px; display: flex; justify-content: space-between; align-items: center; gap: 12px; border: 1px solid var(--line); border-radius: 11px; }.segment-list article > div:first-child { display: grid; gap: 4px; }.segment-list span, .segment-list p { color: var(--muted); font-size: 12px; }
.goal-editor,.adaptation-review,.adaptation-editor { padding:16px; border:1px solid var(--line); border-radius:10px; margin:12px 0; }.goal-editor label,.adaptation-editor article { display:grid; gap:8px; margin:10px 0; }.adaptation-review article { padding:12px; border-bottom:1px solid var(--line); }.adaptation-editor textarea,.goal-editor textarea { min-height:64px; }.episode-rail dd { margin:4px 0 10px; }
</style>
