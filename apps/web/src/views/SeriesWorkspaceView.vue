<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRaw, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

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
  SeriesEpisodeStoryPreviewDto,
  SeriesPlanDraft,
  SeriesPlanPreviewDto,
  SeriesPlanVersionDto,
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
const runtime = ref<RuntimeBootstrapDto | null>(null);
const loading = ref(true);
const actionBusy = ref(false);
const error = ref("");
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
const editingPlan = ref(false);
const editablePlan = ref<SeriesPlanDraft | null>(null);
const visibleRouteEpisodes = ref(10);
const visibleProductionEpisodes = ref(12);
const selectedEpisode = ref<SeriesEpisodeDto | null>(null);
const storyPreview = ref<SeriesEpisodeStoryPreviewDto | null>(null);
const storyNotes = ref("");
const storyPreviewLoading = ref(false);
let pollTimer: ReturnType<typeof setInterval> | undefined;
let storyPreviewTimer: ReturnType<typeof setTimeout> | undefined;

const selectedPlan = computed(() => plans.value.find((item) => item.id === selectedPlanId.value) ?? plans.value[0] ?? null);
const activePlan = computed(() => plans.value.find((item) => item.active) ?? null);
const latestPlanJob = computed(() => jobs.value.find((item) => item.kind === "plan_series") ?? null);
const jobRunning = computed(() => latestPlanJob.value !== null && !["succeeded", "failed", "cancelled"].includes(latestPlanJob.value.status));
const selectedEpisodeStoryJob = computed(() => {
  const projectId = selectedEpisode.value?.projectId;
  if (!projectId) return null;
  return jobs.value.find((item) => item.kind === "plan_series_episode" && item.projectId === projectId) ?? null;
});
const episodeStoryJobRunning = computed(() => Boolean(
  selectedEpisodeStoryJob.value
  && !["succeeded", "failed", "cancelled"].includes(selectedEpisodeStoryJob.value.status),
));
const canGenerate = computed(() => Boolean(preview.value && runtime.value?.worker.ready && runtime.value.provider.paidCallsEnabled && runtime.value.provider.apiKeyConfigured && !jobRunning.value && !actionBusy.value));
const canGenerateEpisodeStory = computed(() => Boolean(
  storyPreview.value
  && runtime.value?.worker.ready
  && runtime.value.provider.paidCallsEnabled
  && runtime.value.provider.apiKeyConfigured
  && !episodeStoryJobRunning.value
  && !actionBusy.value,
));
const routeEpisodes = computed(() => selectedPlan.value?.plan.episodes.slice(0, visibleRouteEpisodes.value) ?? []);
const productionEpisodes = computed(() => episodes.value.slice(0, visibleProductionEpisodes.value));
const canSaveEditedPlan = computed(() => {
  const draft = editablePlan.value;
  if (!draft || !series.value || draft.episodes.length !== series.value.plannedEpisodeCount) return false;
  return draft.episodes.every((item, index) => item.order === index + 1 && [item.title, item.premise, item.openingState, item.trigger, item.childIntent, item.childAction, item.catResponse, item.visibleChange, item.endingState].every((value) => value.trim().length > 0));
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

function episodeStoryJobLabel(job: JobDto): string {
  const labels: Record<JobDto["status"], string> = {
    queued: runtime.value?.worker.ready ? "等待后台任务领取" : "任务已保存，后台正在恢复",
    submitting: "正在生成本集故事",
    submitted: "模型正在处理本集故事",
    polling: "模型正在处理本集故事",
    storing: "正在校验并保存故事候选",
    succeeded: "本集故事候选已生成",
    failed: "本集故事没有生成成功",
    cancel_requested: "正在停止",
    cancelled: "已停止",
    submission_unknown: "提交状态需要确认，请勿重复生成",
  };
  return labels[job.status];
}

function planStatus(plan: SeriesPlanVersionDto): string {
  if (plan.active) return "当前方案";
  if (plan.status === "candidate") return plan.disposition === "needs_input" ? "待补充" : "新方案 · 待确认";
  if (plan.status === "rejected") return "未采用";
  if (plan.status === "superseded") return "已被新方案取代";
  return "历史方案";
}

function episodeAction(episode: SeriesEpisodeDto): string {
  if (episode.status === "completed") return "查看成片";
  if (episode.status === "needs_attention") return "处理问题";
  if (!episode.projectId) return "开始制作";
  return episode.status === "story_review" ? "准备本集剧情" : "继续制作";
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [detail, planList, episodeList, jobList, run, bindings] = await Promise.all([
      api.storySeriesDetail(seriesId), api.seriesPlans(seriesId), api.seriesEpisodes(seriesId), api.seriesJobs(seriesId), api.runtime(), api.seriesAssets(seriesId),
    ]);
    series.value = detail;
    plans.value = planList;
    episodes.value = episodeList;
    jobs.value = jobList;
    runtime.value = run;
    assetBindings.value = bindings;
    if (!selectedPlanId.value || !planList.some((item) => item.id === selectedPlanId.value)) selectedPlanId.value = planList[0]?.id ?? "";
    preview.value = await api.previewSeriesPlan(seriesId);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "系列暂时无法读取。";
  } finally {
    loading.value = false;
  }
}

async function refreshProgress() {
  try {
    const [jobList, planList, episodeList, run] = await Promise.all([
      api.seriesJobs(seriesId), api.seriesPlans(seriesId), api.seriesEpisodes(seriesId), api.runtime(),
    ]);
    jobs.value = jobList; plans.value = planList; episodes.value = episodeList; runtime.value = run;
    if (planList[0] && latestPlanJob.value?.status === "succeeded") selectedPlanId.value = planList[0].id;
  } catch { /* keep the last durable view while the local service recovers */ }
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

function addEpisode() {
  if (!editablePlan.value || !series.value || editablePlan.value.episodes.length >= series.value.plannedEpisodeCount) return;
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

async function openEpisode(episode: SeriesEpisodeDto) {
  if (episode.projectId && episode.status !== "story_review") { await router.push(`/projects/${episode.projectId}/planner`); return; }
  actionBusy.value = true;
  try {
    if (!episode.projectId) await api.materializeSeriesEpisode(seriesId, episode.id, crypto.randomUUID());
    episodes.value = await api.seriesEpisodes(seriesId);
    selectedEpisode.value = episodes.value.find((item) => item.id === episode.id) ?? null;
    await refreshStoryPreview();
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "本集工作区没有创建。"; }
  finally { actionBusy.value = false; }
}

async function refreshStoryPreview() {
  if (!selectedEpisode.value?.projectId) return;
  storyPreviewLoading.value = true;
  try {
    storyPreview.value = await api.previewSeriesEpisodeStory(seriesId, selectedEpisode.value.id, storyNotes.value || null);
  } catch (reason) {
    storyPreview.value = null;
    error.value = reason instanceof Error ? reason.message : "本集故事内容暂时无法预览。";
  } finally { storyPreviewLoading.value = false; }
}

async function generateEpisodeStory() {
  if (!selectedEpisode.value?.projectId || !storyPreview.value || !canGenerateEpisodeStory.value) return;
  actionBusy.value = true; error.value = "";
  try {
    const job = await api.generateSeriesEpisodeStory(seriesId, selectedEpisode.value.id, {
      expectedInputHash: storyPreview.value.inputHash,
      additionalNotes: storyNotes.value || null,
      idempotencyKey: crypto.randomUUID(),
    });
    jobs.value = [job, ...jobs.value.filter((item) => item.id !== job.id)];
    await router.push(`/projects/${selectedEpisode.value.projectId}/planner`);
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "本集故事没有开始生成。"; }
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

onMounted(async () => { await load(); pollTimer = setInterval(refreshProgress, 3000); });
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer); if (storyPreviewTimer) clearTimeout(storyPreviewTimer); });
watch(selectedPlanId, () => { editingPlan.value = false; editablePlan.value = null; visibleRouteEpisodes.value = 10; });
watch(storyNotes, () => { if (storyPreviewTimer) clearTimeout(storyPreviewTimer); storyPreviewTimer = setTimeout(refreshStoryPreview, 400); });
</script>

<template>
  <main class="page series-workspace">
    <section v-if="loading" class="card empty">正在打开系列…</section>
    <template v-else-if="series">
      <header class="series-header">
        <div><RouterLink to="/series">← 系列</RouterLink><h1>{{ series.title }}</h1><p>{{ series.premise }}</p></div>
        <div class="series-count"><b>{{ series.plannedEpisodeCount }} 集</b><span>已开始 {{ series.materializedCount }} · 已完成 {{ series.completedCount }}</span></div>
      </header>
      <p v-if="error" class="notice error">{{ error }}</p>

      <nav class="series-tabs" aria-label="系列创作台"><a href="#setting">系列设定</a><a href="#route">整季路线</a><a href="#episodes">剧集列表</a><a href="#assets">共享资产</a><a href="#continuity">连续性</a></nav>

      <section id="setting" class="card studio-section">
        <header><div><h2>系列设定</h2><p>{{ series.narrativeMode === "continuous" ? "连续剧情" : series.narrativeMode === "lightly_serialized" ? "轻连续" : "单元故事" }} · 每集约 {{ series.defaultEpisodeDurationSeconds }} 秒</p></div></header>
        <div class="setting-grid"><dl><dt>世界与环境</dt><dd>{{ series.worldSetting }}</dd></dl><dl><dt>情绪方向</dt><dd>{{ series.emotionalDirection }}</dd></dl><dl><dt>必须保留</dt><dd>{{ series.mustKeep.join("、") || "固定儿童、猫咪和画风" }}</dd></dl><dl><dt>必须避免</dt><dd>{{ series.mustAvoid.join("、") || "危险动作与身份变化" }}</dd></dl></div>
      </section>

      <section class="card planning-section">
        <header><div><h2>本次系列规划</h2><p v-if="preview">将生成 {{ preview.plannedEpisodeCount }} 集简纲；不会同时生成剧本、图片、分镜或视频。</p></div><button class="primary" :disabled="!canGenerate" @click="generatePlan">{{ jobRunning ? "规划进行中" : activePlan ? "重新规划整季" : "生成系列规划（付费）" }}</button></header>
        <p v-if="!runtime?.worker.ready" class="notice">后台任务暂时不可用，系统正在自动恢复。</p>
        <p v-else-if="!runtime?.provider.apiKeyConfigured || !runtime?.provider.paidCallsEnabled" class="notice">模型服务尚未开放新的付费调用，请先检查运行设置。</p>
        <div class="progress-line" aria-live="polite"><b>{{ jobLabel(latestPlanJob) }}</b><span v-if="jobRunning">可以离开页面，任务会继续并保存。</span></div>
        <details v-if="preview"><summary>查看完整规划指令</summary><pre>{{ preview.prompt }}</pre></details>
      </section>

      <section v-if="plans.length" class="plan-versions">
        <button v-for="plan in plans" :key="plan.id" :class="['plan-version', { active: selectedPlan?.id === plan.id }]" @click="selectedPlanId = plan.id"><b>方案 {{ plan.revision }}</b><span>{{ planStatus(plan) }}</span></button>
      </section>

      <section v-if="selectedPlan" id="route" class="card studio-section">
        <header><div><h2>整季路线</h2><p>{{ selectedPlan.plan.seriesBible.logline }}</p></div><div v-if="selectedPlan.status === 'candidate'" class="candidate-actions"><button class="ghost" @click="startPlanEdit(selectedPlan)">{{ selectedPlan.disposition === "needs_input" ? "补充方案" : "编辑方案" }}</button><button class="ghost" @click="rejectPlan(selectedPlan)">不采用</button><button class="primary" :disabled="selectedPlan.disposition !== 'candidate_ready' || actionBusy" @click="adoptPlan(selectedPlan)">采用整季方案</button></div></header>
        <p v-if="selectedPlan.issues.length" class="notice">方案还包含 {{ selectedPlan.issues.length }} 项需要查看的内容。{{ selectedPlan.disposition === "needs_input" ? "补充后才能采用，但不需要重新调用模型。" : "不影响查看。" }}</p>
        <div v-if="editingPlan && editablePlan" class="plan-editor">
          <label>整季一句话<input v-model="editablePlan.seriesBible.logline" /></label>
          <div v-if="episodeOrdersNeedRepair" class="order-repair"><p>集数没有从 1 连续编号。可以按当前显示顺序修正，不会调用模型。</p><button class="secondary renumber-episodes" @click="renumberEpisodes">按当前顺序编号为 1–{{ editablePlan.episodes.length }}</button></div>
          <article v-for="episode in editablePlan.episodes" :key="episode.order" class="episode-editor"><b>第 {{ episode.order }} 集</b><label>标题<input v-model="episode.title" /></label><label>本集事件<textarea v-model="episode.premise" /></label><label>开场状态<textarea v-model="episode.openingState" /></label><label>触发<textarea v-model="episode.trigger" /></label><label>儿童目标<textarea v-model="episode.childIntent" /></label><label>儿童动作<textarea v-model="episode.childAction" /></label><label>猫咪回应<textarea v-model="episode.catResponse" /></label><label>可见变化<textarea v-model="episode.visibleChange" /></label><label>结尾状态<textarea v-model="episode.endingState" /></label></article>
          <button v-if="editablePlan.episodes.length < series.plannedEpisodeCount" class="secondary" @click="addEpisode">补充第 {{ editablePlan.episodes.length + 1 }} 集</button>
          <div class="editor-actions"><button class="ghost" @click="editingPlan = false">取消</button><button class="primary" :disabled="!canSaveEditedPlan || actionBusy" @click="saveEditedPlan">保存为新候选</button></div>
          <p v-if="!canSaveEditedPlan" class="field-hint">需要补齐 {{ series.plannedEpisodeCount }} 集，并填写每集的开场、事件、人物动作、可见变化和结尾。</p>
        </div>
        <div v-else class="episode-rail">
          <article v-for="episode in routeEpisodes" :key="episode.order"><b>第 {{ episode.order }} 集</b><h3>{{ episode.title }}</h3><p>{{ episode.premise }}</p><small>{{ episode.openingState }} → {{ episode.endingState }}</small></article>
        </div>
        <button v-if="!editingPlan && selectedPlan.plan.episodes.length > routeEpisodes.length" class="ghost load-more" @click="visibleRouteEpisodes += 10">继续查看</button>
      </section>

      <section id="episodes" class="card studio-section">
        <header><div><h2>剧集列表</h2><p>点击“开始制作”时才创建这一集的短片项目。</p></div></header>
        <div v-if="episodes.length" class="episode-list"><article v-for="episode in productionEpisodes" :key="episode.id"><span class="episode-order">{{ episode.order }}</span><div><h3>{{ episode.title }}</h3><p>{{ episode.outline.visibleChange }}</p><small>{{ episode.outline.openingState }} → {{ episode.outline.endingState }}</small></div><button v-if="episode.order > 1" class="ghost" @click="showContinuity(episode)">连续性</button><button class="secondary" :disabled="actionBusy" @click="openEpisode(episode)">{{ episodeAction(episode) }}</button></article><button v-if="episodes.length > productionEpisodes.length" class="ghost load-more" @click="visibleProductionEpisodes += 12">加载更多剧集</button></div>
        <p v-else class="empty">采用整季方案后，这里会出现稳定的剧集条目。</p>
        <div v-if="selectedEpisode" class="episode-story-panel"><div><b>第 {{ selectedEpisode.order }} 集 · 准备剧情</b><p>只扩写这一集的故事候选，不会生成其他集、分镜、图片或视频。</p></div><div v-if="selectedEpisodeStoryJob" class="progress-line episode-story-progress" aria-live="polite"><b>{{ episodeStoryJobLabel(selectedEpisodeStoryJob) }}</b><span v-if="episodeStoryJobRunning">当前任务完成前不会创建第二条任务。</span></div><label>本集补充说明<textarea v-model="storyNotes" placeholder="可选：补充这一集需要强调的动作、道具或情绪变化" /></label><p v-if="storyPreviewLoading" class="field-hint">正在更新本集内容…</p><template v-else-if="storyPreview"><p class="story-summary">{{ selectedEpisode.outline.premise }} · {{ selectedEpisode.targetDurationSeconds }} 秒</p><details><summary>查看完整生成指令</summary><pre>{{ storyPreview.prompt }}</pre></details><button class="primary" :disabled="!canGenerateEpisodeStory" @click="generateEpisodeStory">{{ episodeStoryJobRunning ? "本集故事正在生成" : "生成本集故事候选（付费）" }}</button></template></div>
      </section>

      <section id="assets" class="card studio-section"><header><div><h2>共享资产</h2><p>先确定复用关系；每张新图片仍需由你明确生成。</p></div></header><div v-if="assetBindings.length" class="bound-assets"><span v-for="binding in assetBindings" :key="binding.id"><b>{{ binding.bindingKey }}</b> · {{ binding.role }}</span></div><div class="asset-needs"><span v-for="location in activePlan?.plan.seriesBible.recurringLocations ?? []" :key="location.key">环境 · {{ location.name }}</span><span v-for="prop in activePlan?.plan.seriesBible.recurringProps ?? []" :key="prop.key">道具 · {{ prop.name }}</span><span v-for="rule in activePlan?.plan.seriesBible.wardrobeRules ?? []" :key="rule">服装 · {{ rule }}</span></div></section>

      <section id="continuity" class="card studio-section">
        <header><div><h2>连续性</h2><p>第 2 集起，生成视频前需要确认从上一集继承、调整或重置的状态。</p></div></header>
        <p v-if="!openContinuity" class="empty">在剧集列表中选择一集查看相邻状态。</p>
        <template v-else>
          <div class="continuity-panel"><div><b>上一集结尾</b><p>{{ openContinuity.incoming?.state.childState }}</p></div><span>→</span><div><b>本集开场</b><p>{{ openContinuity.incoming?.state.endingImage }}</p></div><span v-if="openContinuity.incoming?.confirmed" class="pill good">已确认</span></div>
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
.order-repair { padding: 12px 14px; display: flex; justify-content: space-between; align-items: center; gap: 14px; border-radius: 11px; background: #fff3dc; }.order-repair p { margin: 0; color: #725f42; }
.bound-assets { margin-bottom: 10px; display: flex; flex-wrap: wrap; gap: 8px; }.bound-assets span { padding: 9px 11px; border: 1px solid #cadae0; border-radius: 9px; background: #f4f9fa; color: #506a72; font-size: 12px; }
.episode-story-panel { margin-top: 15px; padding: 17px; display: grid; gap: 12px; border-radius: 13px; background: #f7f1e9; }.episode-story-panel p { margin: 4px 0 0; color: var(--muted); }.episode-story-panel label { display: grid; gap: 5px; color: var(--muted); font-size: 11px; }.episode-story-panel textarea { min-height: 76px; padding: 10px; border: 1px solid var(--line); border-radius: 10px; resize: vertical; }.episode-story-panel pre { max-height: 260px; overflow: auto; white-space: pre-wrap; }.episode-story-panel .primary { justify-self: end; }
.continuity-images { margin-top: 15px; display: grid; grid-template-columns: minmax(160px, 240px) 1fr; gap: 16px; }.continuity-images > div { display: grid; align-content: start; gap: 9px; }.keyframe-grid { display: flex; flex-wrap: wrap; gap: 10px; }.keyframe-grid article { display: grid; gap: 6px; }.keyframe-grid label { font-size: 12px; color: var(--muted); }.frame-card { width: 132px; padding: 7px; display: grid; gap: 5px; border: 1px solid var(--line); border-radius: 10px; background: #f6f2eb; color: var(--ink); cursor: pointer; }.frame-card img { width: 100%; aspect-ratio: 9 / 16; object-fit: contain; background: #e9e4dc; border-radius: 7px; }.frame-card span { font-size: 11px; }
</style>
