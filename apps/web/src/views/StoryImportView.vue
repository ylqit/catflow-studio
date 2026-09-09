<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ProductionTargetFields from "../components/ProductionTargetFields.vue";
import JobStatusCard from "../components/JobStatusCard.vue";
import { subscribeJobs } from "../jobUpdates";
import { api } from "../api/client";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../idempotency";
import type { ProjectDto, StoryProductionTarget, StoryImportPreviewDto, StorySeriesDto, StorySourceDocumentDto } from "../api/types";

const route = useRoute();
const router = useRouter();
const rawText = ref("");
const sourceFormat = ref<"paste" | "txt" | "md">("paste");
const fileName = ref<string | null>(null);
const preview = ref<StoryImportPreviewDto | null>(null);
const document = ref<StorySourceDocumentDto | null>(null);
const series = ref<StorySeriesDto[]>([]);
const projects = ref<ProjectDto[]>([]);
const importHistory = ref<StorySourceDocumentDto[]>([]);
const busy = ref(false);
const error = ref("");
type ImportTarget = "new_series" | "append_series" | "independent" | "revision" | "reference";
const targetBySuggestion = ref<Record<string, ImportTarget>>({});
const targetSeriesBySuggestion = ref<Record<string, string>>({});
const targetProjectBySuggestion = ref<Record<string, string>>({});
const defaultTarget = ref<StoryProductionTarget>({ lengthMode: "fixed", plannedEpisodeCount: 3, defaultEpisodeDurationSeconds: 15, narrativeMode: "continuous", adaptationPolicy: "condense_mainline", mustKeep: [] });
const targets = ref<Record<string, StoryProductionTarget>>({});
const savedNotice = ref("");
const targetValid = (value: StoryProductionTarget) => Number.isInteger(value.defaultEpisodeDurationSeconds) && value.defaultEpisodeDurationSeconds >= 8 && value.defaultEpisodeDurationSeconds <= 15 && (value.lengthMode === "ongoing" || (Number.isInteger(value.plannedEpisodeCount) && (value.plannedEpisodeCount ?? 0) >= 2));
const allTargetsValid = computed(() => targetValid(defaultTarget.value) && Object.values(targets.value).every(targetValid));
async function saveTargets() {
  if (!document.value || !allTargetsValid.value) return;
  busy.value = true; error.value = ""; savedNotice.value = "";
  try {
    document.value = await api.updateStoryProductionTargets(document.value.id, {
      expectedUpdatedAt: document.value.updatedAt,
      productionTargets: { default: defaultTarget.value, ...targets.value },
    });
    savedNotice.value = "生产目标已保存；原文分析不变，没有调用模型。";
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "生产目标未保存，请刷新后重试。"; }
  finally { busy.value = false; }
}
let previewTimer: ReturnType<typeof setTimeout> | undefined;
let unsubscribeJobs: (() => void) | undefined;

const analysisInProgress = computed(() =>
  Boolean(document.value && ["analyzing", "pending"].includes(document.value.status)),
);
const canAnalyze = computed(() => Boolean(preview.value && allTargetsValid.value && !busy.value && !document.value && !analysisInProgress.value));
const canReanalyze = computed(() => Boolean(
  preview.value
  && document.value?.status === "analyzed"
  && document.value.relationSuggestions.every((item) => item.status === "suggested")
  && !busy.value,
));

function initialSuggestionTarget(suggestion: StorySourceDocumentDto["relationSuggestions"][number]): ImportTarget {
  if (
    suggestion.relationType === "append_series"
    && (!suggestion.suggestedSeriesId || !series.value.some((item) => item.id === suggestion.suggestedSeriesId))
  ) return "new_series";
  return suggestion.relationType;
}

function initializeSuggestion(suggestion: StorySourceDocumentDto["relationSuggestions"][number]) {
  if (!targetBySuggestion.value[suggestion.id]) {
    targetBySuggestion.value[suggestion.id] = initialSuggestionTarget(suggestion);
  }
  if (suggestion.suggestedSeriesId && !targetSeriesBySuggestion.value[suggestion.id]) {
    targetSeriesBySuggestion.value[suggestion.id] = suggestion.suggestedSeriesId;
  }
  if (!targets.value[suggestion.id]) {
    targets.value[suggestion.id] = document.value?.productionTargets?.[suggestion.id]
      ?? { ...defaultTarget.value, mustKeep: [...defaultTarget.value.mustKeep] };
  }
}

async function updatePreview() {
  preview.value = null;
  if (!rawText.value.trim()) return;
  try {
    preview.value = await api.previewStoryImport({ rawText: rawText.value, sourceFormat: sourceFormat.value, fileName: fileName.value });
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "文本暂时无法预览。"; }
}

async function chooseFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  const extension = file.name.toLowerCase().endsWith(".md") ? "md" : "txt";
  sourceFormat.value = extension; fileName.value = file.name; rawText.value = await file.text();
}

function usePaste() { sourceFormat.value = "paste"; fileName.value = null; }

async function analyze() {
  if (!preview.value || busy.value) return;
  busy.value = true; error.value = "";
  const scope = "story-import:create";
  const fingerprint = `${preview.value.inputHash}:${sourceFormat.value}:${fileName.value ?? ""}`;
  try {
    const result = await api.createStoryImport({
      productionTargets: { default: defaultTarget.value },
      rawText: rawText.value,
      sourceFormat: sourceFormat.value,
      fileName: fileName.value,
      expectedInputHash: preview.value.inputHash,
      idempotencyKey: pendingIdempotencyKey(scope, fingerprint),
    });
    settleIdempotencyKey(scope, fingerprint);
    document.value = result.document;
    await router.replace(`/story-imports/${result.document.id}`);
    for (const suggestion of result.document.relationSuggestions) initializeSuggestion(suggestion);
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "故事分析没有开始。"; }
  finally { busy.value = false; }
}

async function refreshDocument() {
  if (!document.value) return;
  try {
    document.value = await api.storyImport(document.value.id);
    for (const suggestion of document.value.relationSuggestions) initializeSuggestion(suggestion);
  }
  catch { /* the persisted analysis remains recoverable */ }
}

async function loadDocument(documentId: unknown) {
  if (typeof documentId !== "string" || !documentId) {
    document.value = null;
    return;
  }
  try {
    const restored = await api.storyImport(documentId);
    document.value = restored;
    targets.value = {};
    if (restored.productionTargets?.default) defaultTarget.value = restored.productionTargets.default;
    rawText.value = restored.rawText;
    sourceFormat.value = restored.sourceFormat;
    fileName.value = restored.fileName ?? null;
    for (const suggestion of restored.relationSuggestions) initializeSuggestion(suggestion);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "导入记录暂时无法读取。";
  }
}

async function reanalyze() {
  if (!document.value) return;
  busy.value = true; error.value = "";
  try {
    const restoredPreview = await api.previewStoryImport({
      rawText: document.value.rawText,
      sourceFormat: document.value.sourceFormat,
      fileName: document.value.fileName,
    });
    const job = await api.reanalyzeStoryImport(document.value.id, {
      expectedInputHash: restoredPreview.inputHash,
      idempotencyKey: crypto.randomUUID(),
    });
    document.value = {
      ...document.value,
      status: "analyzing",
      analysisJobId: job.id,
    };
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "重新分析没有开始。";
  } finally {
    busy.value = false;
  }
}

function suggestionTarget(id: string): ImportTarget {
  return targetBySuggestion.value[id] ?? "new_series";
}

async function confirm(suggestionId: string) {
  const target = suggestionTarget(suggestionId);
  const goal = targets.value[suggestionId] ?? defaultTarget.value;
  if (!targetValid(goal)) { error.value = "固定系列至少 2 集，每集为 8–15 秒整数。"; return; }
  const seriesLengthMode = target === "new_series" ? goal.lengthMode : null;
  const scope = "story-import:confirm:" + document.value!.id + ":" + suggestionId;
  const fingerprint = JSON.stringify({ target, goal, series: targetSeriesBySuggestion.value[suggestionId], project: targetProjectBySuggestion.value[suggestionId] });
  busy.value = true; error.value = "";
  try {
    document.value = await api.updateStoryProductionTargets(document.value!.id, {
      expectedUpdatedAt: document.value!.updatedAt, productionTargets: { default: defaultTarget.value, ...targets.value },
    });
    const result = await api.confirmStoryImport(document.value!.id, {
      suggestionId,
      target,
      targetSeriesId: ["append_series", "revision", "reference"].includes(target) && !targetProjectBySuggestion.value[suggestionId] ? targetSeriesBySuggestion.value[suggestionId] || null : null,
      targetProjectId: ["revision", "reference"].includes(target) ? targetProjectBySuggestion.value[suggestionId] || null : null,
      seriesLengthMode,
      plannedEpisodeCount: seriesLengthMode === "fixed" ? goal.plannedEpisodeCount : null,
      defaultEpisodeDurationSeconds: goal.defaultEpisodeDurationSeconds,
      narrativeMode: goal.narrativeMode,
      adaptationPolicy: goal.adaptationPolicy,
      mustKeep: goal.mustKeep,
      idempotencyKey: pendingIdempotencyKey(scope, fingerprint),
    });
    settleIdempotencyKey(scope, fingerprint);
    if (result.series) await router.push(`/series/${result.series.id}`);
    else if (result.projects[0]) await router.push(`/projects/${result.projects[0].id}/planner`);
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "关系没有确认。"; }
  finally { busy.value = false; }
}

watch(rawText, () => { if (previewTimer) clearTimeout(previewTimer); previewTimer = setTimeout(updatePreview, 400); });
watch([sourceFormat, fileName], updatePreview);
watch(() => route.params.documentId, async (documentId, previousDocumentId) => {
  if (documentId === previousDocumentId) return;
  await loadDocument(documentId);
});
watch([rawText, sourceFormat, fileName, defaultTarget], () => {
  if (!document.value && route.params.documentId === undefined && targetValid(defaultTarget.value)) {
    localStorage.setItem("catflow:story-import-draft", JSON.stringify({ rawText: rawText.value, sourceFormat: sourceFormat.value, fileName: fileName.value, target: defaultTarget.value }));
  }
}, { deep: true });
onMounted(async () => {
  if (!route.params.documentId) {
    try {
      const saved = JSON.parse(localStorage.getItem("catflow:story-import-draft") ?? "null");
      if (saved?.target && targetValid(saved.target) && Array.isArray(saved.target.mustKeep)) {
        defaultTarget.value = saved.target;
        rawText.value = saved.rawText ?? "";
        sourceFormat.value = saved.sourceFormat ?? "paste";
        fileName.value = saved.fileName ?? null;
      }
    } catch { /* An unreadable browser draft cannot change a saved source. */ }
  }
  [series.value, projects.value, importHistory.value] = await Promise.all([
    api.storySeries().catch(() => []),
    api.projects().catch(() => []),
    api.storyImports().catch(() => []),
  ]);
  await loadDocument(route.params.documentId);
  unsubscribeJobs = subscribeJobs(() => ({ storySourceDocumentId: document.value?.id }), refreshDocument, () => analysisInProgress.value);
});
onBeforeUnmount(() => { if (previewTimer) clearTimeout(previewTimer); unsubscribeJobs?.(); });
</script>

<template>
  <main class="page import-page">
    <JobStatusCard v-if="document?.analysisJobId" :job-id="document.analysisJobId" title="原文来源分析" @replacement="refreshDocument" />
    <header class="page-heading"><div><h1>导入故事</h1><p class="subtitle">可以一次粘贴一个故事、多个主题，也可以分多次补充。关系建议由你确认。</p></div><RouterLink class="ghost back-link" to="/projects">返回项目库</RouterLink></header>
    <p v-if="error" :class="['notice', { error: !error.includes('没有产生新费用') }]">{{ error }}</p>
    <section v-if="!document && importHistory.length" class="card import-history">
      <header><div><h2>已有来源记录</h2><p>打开已保存的分析结果不会调用模型，也不会产生费用。</p></div></header>
      <div><RouterLink v-for="item in importHistory" :key="item.id" :to="`/story-imports/${item.id}`"><b>{{ item.relationSuggestions[0]?.title ?? item.fileName ?? item.rawText.split('\n')[0] ?? '未命名来源' }}</b><span>{{ item.units.length }} 个剧情节拍 · {{ item.status === 'analyzed' || item.status === 'confirmed' ? '分析已保存' : item.status === 'failed' ? '需要检查' : '分析中' }}</span><time>{{ new Date(item.createdAt).toLocaleString('zh-CN') }}</time></RouterLink></div>
    </section>
    <div class="import-layout">
      <section class="card import-editor">
        <header><h2>来源文本</h2><div><button :class="sourceFormat === 'paste' ? 'secondary' : 'ghost'" @click="usePaste">粘贴文字</button><label class="ghost file-button">上传 TXT / MD<input type="file" accept=".txt,.md,text/plain,text/markdown" @change="chooseFile" /></label></div></header>
        <p v-if="fileName" class="file-name">{{ fileName }}</p>
        <textarea v-model="rawText" :readonly="!!document" aria-label="故事来源文本" placeholder="粘贴单个故事、系列剧本、主题合集或后续修订稿…" />
        <ProductionTargetFields v-model="defaultTarget" label="新系列生产目标" />
        <p v-if="!allTargetsValid" class="notice error">固定系列至少 2 集，每集时长为 8–15 秒整数。</p>
        <button v-if="document" class="secondary" :disabled="busy || !allTargetsValid" @click="saveTargets">保存生产目标（不调用模型）</button>
        <p v-if="savedNotice" class="notice">{{ savedNotice }}</p>
        <div v-if="preview && !document" class="preview-summary"><span>{{ preview.characterCount }} 个字符</span><span>每次导入都会新建来源记录，并产生一次故事分析费用</span></div>
        <details v-if="preview"><summary>查看本次分析内容</summary><pre>{{ preview.prompt }}</pre></details>
        <button v-if="!document" class="primary analyze-button" :disabled="!canAnalyze" @click="analyze">{{ busy ? "正在提交" : analysisInProgress ? "分析任务进行中" : "导入并分析（付费）" }}</button>
      </section>

      <section class="card import-result">
        <template v-if="!document"><h2>分析结果</h2><p class="empty">导入后会保留完整原文，并给出语义单元和关系建议。</p></template>
        <template v-else-if="['analyzing', 'pending'].includes(document.status)"><h2>正在理解故事结构</h2><p class="notice">原文已经保存。可以离开页面，完成后会恢复同一份结果。</p></template>
        <template v-else-if="document.status === 'failed'">
          <h2>本次分析没有完成</h2>
          <p class="notice error">原文已经安全保存。重新分析会创建一次新的付费任务，不会重复保存这份文档。</p>
          <button class="primary" :disabled="busy" @click="reanalyze">{{ busy ? "正在提交" : "重新分析（付费）" }}</button>
        </template>
        <template v-else>
          <header><div><h2>识别到 {{ document.units.length }} 个剧情节拍</h2><p>剧情节拍忠实记录原文事件，不直接等同于最终剧集。</p></div><button class="secondary reanalyze-button" :disabled="!canReanalyze" @click="reanalyze">{{ busy ? "正在提交" : "重新分析并拆分（付费）" }}</button></header>
          <details v-for="unit in document.units" :key="unit.id" class="source-unit"><summary>{{ unit.ordinal }} · {{ unit.title }} <small>{{ unit.theme }}</small></summary><p>{{ unit.rawText }}</p></details>
          <article v-for="suggestion in document.relationSuggestions" :key="suggestion.id" class="relation-card">
            <div><span class="pill">关系建议</span><h3>{{ suggestion.title }}</h3><p>{{ suggestion.rationale }}</p></div>
            <div class="relation-action"><select v-model="targetBySuggestion[suggestion.id]" :aria-label="`${suggestion.title}的处理方式`"><option value="new_series">创建新系列</option><option value="append_series">追加到现有系列</option><option value="independent">创建独立短片</option><option value="revision">作为修订稿</option><option value="reference">作为参考资料</option></select><section v-if="['new_series', 'independent'].includes(suggestionTarget(suggestion.id))" class="series-length">
              <ProductionTargetFields v-if="suggestionTarget(suggestion.id) === 'new_series' && targets[suggestion.id]" v-model="targets[suggestion.id]" :label="suggestion.title + '生产目标'" />
              <label v-else>短片时长（秒）<input v-if="targets[suggestion.id]" v-model.number="targets[suggestion.id].defaultEpisodeDurationSeconds" type="number" min="8" max="15" /></label>
              <p>{{ suggestion.unitIds.length }} 个来源事件，按本组目标确认缩编方案。不同故事组独立设置。</p>
              <small>故事分析中的集数建议未考虑当前时长；以这里的生产目标为准。创建后只准备文字方案，逐集确认后制作。</small>
              <button class="ghost" :disabled="busy || !allTargetsValid" @click="saveTargets">保存各组目标</button>
            </section><select v-if="suggestionTarget(suggestion.id) === 'append_series'" v-model="targetSeriesBySuggestion[suggestion.id]" aria-label="选择目标系列"><option value="">选择系列</option><option v-for="item in series" :key="item.id" :value="item.id">{{ item.title }}</option></select><template v-if="['revision', 'reference'].includes(suggestionTarget(suggestion.id))"><select v-model="targetProjectBySuggestion[suggestion.id]" aria-label="选择目标短片"><option value="">关联到短片（可选）</option><option v-for="item in projects" :key="item.id" :value="item.id">{{ item.title }}</option></select><select v-model="targetSeriesBySuggestion[suggestion.id]" aria-label="选择目标系列"><option value="">关联到系列（可选）</option><option v-for="item in series" :key="item.id" :value="item.id">{{ item.title }}</option></select></template><button class="secondary confirm-relation" :disabled="busy || (suggestion.status !== 'suggested' && suggestionTarget(suggestion.id) !== 'new_series') || (['new_series', 'independent'].includes(suggestionTarget(suggestion.id)) && !targetValid(targets[suggestion.id] ?? defaultTarget)) || (suggestionTarget(suggestion.id) === 'append_series' && !targetSeriesBySuggestion[suggestion.id]) || (['revision', 'reference'].includes(suggestionTarget(suggestion.id)) && !targetSeriesBySuggestion[suggestion.id] && !targetProjectBySuggestion[suggestion.id])" @click="confirm(suggestion.id)">{{ suggestion.status === "suggested" ? "确认关系" : suggestionTarget(suggestion.id) === "new_series" ? "创建另一个新系列" : "已确认" }}</button></div>
          </article>
        </template>
      </section>
    </div>
  </main>
</template>

<style scoped>
.back-link { min-height: 40px; display: inline-flex; align-items: center; }.import-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(420px, .8fr); gap: 16px; align-items: start; }.import-editor, .import-result { padding: 24px; }.import-editor header { display: flex; justify-content: space-between; gap: 14px; }.import-editor header div { display: flex; gap: 7px; }.import-editor textarea { width: 100%; min-height: 430px; padding: 15px; border: 1px solid var(--line); border-radius: 13px; resize: vertical; line-height: 1.7; }.file-button { min-height: 40px; display: inline-flex; align-items: center; cursor: pointer; }.file-button input { position: absolute; width: 1px; height: 1px; opacity: 0; }.file-name { color: var(--muted); font-size: 12px; }.preview-summary { padding: 12px 0; display: flex; gap: 10px; color: var(--muted); font-size: 12px; }.import-editor pre { max-height: 240px; overflow: auto; white-space: pre-wrap; padding: 12px; border-radius: 10px; background: #f7f2eb; }.analyze-button { width: 100%; margin-top: 14px; }.source-unit { margin-bottom: 8px; padding: 12px; border: 1px solid var(--line); border-radius: 11px; }.source-unit summary { cursor: pointer; font-weight: 700; }.source-unit small { margin-left: 8px; color: var(--muted); }.source-unit p { margin: 12px 0 0; color: #5f5750; line-height: 1.7; white-space: pre-wrap; }.relation-card { margin-top: 14px; padding: 15px; display: grid; grid-template-columns: 1fr auto; gap: 15px; border-radius: 13px; background: #f8f3ec; }.relation-card h3 { margin: 8px 0 5px; }.relation-card p { margin: 0; color: var(--muted); line-height: 1.55; }.relation-action { min-width: 185px; display: grid; gap: 7px; }.relation-action select { padding: 8px; border: 1px solid var(--line); border-radius: 9px; background: white; }
.series-length { padding: 10px; display: grid; gap: 7px; border: 1px solid var(--line); border-radius: 10px; background: #fffaf4; }.series-length label { display: flex; align-items: center; gap: 6px; }.series-length input[type="number"] { min-width: 0; width: 86px; padding: 7px; }.series-length small { color: #8a6338; line-height: 1.4; }
.import-history { margin-bottom: 16px; padding: 20px 24px; }.import-history header { display: flex; justify-content: space-between; }.import-history header h2, .import-history header p { margin: 0; }.import-history header p { color: var(--muted); font-size: 12px; }.import-history > div { margin-top: 12px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }.import-history a { min-width: 0; padding: 12px; display: grid; gap: 5px; border: 1px solid var(--line); border-radius: 10px; color: var(--ink); text-decoration: none; }.import-history a:hover, .import-history a:focus-visible { border-color: var(--accent); }.import-history span, .import-history time { color: var(--muted); font-size: 11px; }.import-history b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.relation-card { grid-template-columns:1fr; }.relation-action { min-width:0; }.import-editor textarea[aria-label="故事来源文本"] { min-height:240px; }@media(max-width:900px) { .import-layout { grid-template-columns:1fr; }.import-history > div { grid-template-columns:1fr; } }
</style>
