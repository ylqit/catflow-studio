<script setup lang="ts">
import { ChatDotRound, Clock, Close, Document, MagicStick, Refresh, Warning } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { canvasApi } from "../../api/client";
import type { CreativeDocumentDto, ScriptWorkspaceDto, StoryWorkspaceDocumentDto } from "../../api/types";
import { useDirectorDockPreference } from "./directorDockPreference";
import type { DirectorDirtyRegistration } from "./directorDirtyState";

type WorkspaceState = "loading" | "ready" | "stale" | "error";

const props = withDefaults(defineProps<{ projectId: string; focusedItemId?: string; panel?: string }>(), { focusedItemId: "", panel: "main" });
const emit = defineEmits<{ "dirty-change": [registration?: DirectorDirtyRegistration] }>();
const router = useRouter();
const state = ref<WorkspaceState>("loading");
const error = ref("");
const workspace = ref<ScriptWorkspaceDto>();
const selectedId = ref("");
const saving = ref(false);
const generating = ref(false);
const saveState = ref<"saved" | "dirty" | "saving" | "conflict" | "error">("saved");
const draft = reactive({ title: "", summary: "", body: "" });
const { open: assistantOpen, stop: stopAssistantPreference } = useDirectorDockPreference(() => props.projectId, "script-assistant");
const mobilePanel = ref<"assistant" | "document" | "history">("document");
const assistantPanel = ref<HTMLElement>();
const documentPanel = ref<HTMLElement>();
const historyPanel = ref<HTMLElement>();
let requestSequence = 0;
let activeController: AbortController | undefined;
let saveAttempt: { fingerprint: string; idempotencyKey: string } | undefined;
let storyGenerationAttempt: { fingerprint: string; idempotencyKey: string } | undefined;
let pendingWorkspace: ScriptWorkspaceDto | undefined;

const documents = computed(() => [...(workspace.value?.documents ?? [])].sort((left, right) => right.revision - left.revision).slice(0, 5));
const selected = computed(() => documents.value.find((document) => document.id === selectedId.value));
const dirty = computed(() => Boolean(selected.value) && (draft.title !== selected.value!.title || draft.summary !== (selected.value!.summary ?? "") || draft.body !== selected.value!.body));
const historyOpen = computed(() => props.panel === "history");
const warnings = computed(() => selected.value?.warnings ?? []);
const blockingWarnings = computed(() => warnings.value.filter((item) => item.severity === "blocker"));
const briefText = computed(() => {
  const brief = workspace.value?.brief;
  if (!brief) return "当前项目尚未保存创作要求。";
  const body = brief.body ?? brief.theme ?? brief.summary;
  return typeof body === "string" && body.trim() ? body : "当前项目尚未保存创作要求。";
});
const assistantSuggestion = computed(() => {
  const first = warnings.value.find((item) => item.severity === "warning");
  return first ? `建议预览：${first.message}。它只是修改方向，不会自动覆盖正文。` : "优先检查动作节拍能否在目标时长内清楚可见，并保持安静、低对白的一人一猫关系。";
});

function syncDraft(document?: StoryWorkspaceDocumentDto) {
  draft.title = document?.title ?? "";
  draft.summary = document?.summary ?? "";
  draft.body = document?.body ?? "";
  saveState.value = "saved";
  saveAttempt = undefined;
}

function applyWorkspace(value: ScriptWorkspaceDto, preferredId = "") {
  workspace.value = value;
  const initial = documents.value.find((item) => item.id === preferredId)
    ?? documents.value.find((item) => item.id === props.focusedItemId)
    ?? documents.value.find((item) => item.id === selectedId.value)
    ?? documents.value.find((item) => item.id === value.currentStoryId)
    ?? documents.value[0];
  selectedId.value = initial?.id ?? "";
  syncDraft(initial);
}

async function load(background = false) {
  activeController?.abort("script workspace superseded");
  const controller = new AbortController();
  activeController = controller;
  const sequence = ++requestSequence;
  state.value = background && workspace.value ? "ready" : "loading";
  error.value = "";
  try {
    const result = await canvasApi.scriptWorkspace(props.projectId, controller.signal);
    if (sequence !== requestSequence || controller.signal.aborted) return;
    if (dirty.value && workspace.value) {
      pendingWorkspace = result;
      state.value = "stale";
      error.value = "服务器数据已更新；当前未保存正文已保留。";
      return;
    }
    pendingWorkspace = undefined;
    applyWorkspace(result);
    state.value = "ready";
  } catch (reason) {
    if (sequence !== requestSequence || controller.signal.aborted) return;
    error.value = reason instanceof Error ? reason.message : String(reason);
    state.value = workspace.value ? "stale" : "error";
  } finally {
    if (sequence === requestSequence) activeController = undefined;
  }
}

async function resolveDirtySwitch() {
  if (!dirty.value) return true;
  try {
    await ElMessageBox.confirm("当前剧情还有未保存修改。保存会创建新 Revision；放弃只清除本地草稿。", "切换剧情候选", { confirmButtonText: "保存并切换", cancelButtonText: "放弃并切换", distinguishCancelAndClose: true, type: "warning" });
    return await saveRevision();
  } catch (reason) {
    if (reason === "cancel") {
      discardDraft();
      return true;
    }
    return false;
  }
}

async function chooseDocument(document: StoryWorkspaceDocumentDto, updateRoute = true) {
  if (selectedId.value === document.id) return;
  if (!await resolveDirtySwitch()) return;
  const current = documents.value.find((item) => item.id === document.id);
  if (!current) return;
  selectedId.value = current.id;
  syncDraft(current);
  if (updateRoute) void router.replace({ name: "project-script", params: { projectId: props.projectId }, query: { item: current.id, ...(props.panel !== "main" ? { panel: props.panel } : {}) } });
}

function asWorkspaceDocument(value: CreativeDocumentDto): StoryWorkspaceDocumentDto {
  return { id: value.id, title: value.title, body: value.body, summary: value.summary, revision: value.revision, status: value.status, source: value.source, warnings: value.warnings };
}

async function saveRevision(): Promise<boolean> {
  const document = selected.value;
  if (!document || !dirty.value || saving.value) return !dirty.value;
  if (!draft.title.trim() || !draft.body.trim()) {
    ElMessage.warning("剧情标题和正文不能为空");
    return false;
  }
  saving.value = true;
  saveState.value = "saving";
  const fingerprint = JSON.stringify([document.id, document.revision, draft.title.trim(), draft.summary.trim(), draft.body]);
  if (saveAttempt?.fingerprint !== fingerprint) saveAttempt = { fingerprint, idempotencyKey: crypto.randomUUID() };
  try {
    const saved = asWorkspaceDocument(await canvasApi.editStoryRevision(document.id, { title: draft.title.trim(), body: draft.body, summary: draft.summary.trim() || null, expectedRevision: document.revision, idempotencyKey: saveAttempt.idempotencyKey }));
    const base = pendingWorkspace ?? workspace.value ?? { brief: null, documents: [], currentStoryId: null };
    pendingWorkspace = undefined;
    applyWorkspace({ ...base, documents: [saved, ...base.documents.filter((item) => item.id !== saved.id)] }, saved.id);
    state.value = "ready";
    error.value = "";
    void router.replace({ name: "project-script", params: { projectId: props.projectId }, query: { item: saved.id, ...(props.panel !== "main" ? { panel: props.panel } : {}) } });
    ElMessage.success(`已保存剧情 Revision ${saved.revision}`);
    return true;
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : String(reason);
    error.value = message;
    saveState.value = /409|revision|版本|冲突/i.test(message) ? "conflict" : "error";
    return false;
  } finally {
    saving.value = false;
  }
}

function discardDraft() {
  if (pendingWorkspace) {
    const next = pendingWorkspace;
    pendingWorkspace = undefined;
    applyWorkspace(next);
    state.value = "ready";
    error.value = "";
    return;
  }
  syncDraft(selected.value);
}

async function setCurrent() {
  const document = selected.value;
  if (!document || dirty.value) return;
  try {
    await ElMessageBox.confirm(`将“${document.title}”设为当前剧情。这个决定不会调用媒体 Provider。`, "选择当前剧情", { confirmButtonText: "设为当前剧情", cancelButtonText: "取消", type: "warning" });
    await canvasApi.approveStory(document.id);
    if (workspace.value) {
      workspace.value.currentStoryId = document.id;
      workspace.value.documents = workspace.value.documents.map((item) => ({ ...item, status: item.id === document.id ? "approved" : item.status === "approved" ? "superseded" : item.status }));
    }
    ElMessage.success("当前剧情已更新；下游状态将按版本规则重新计算");
  } catch (reason) {
    if (reason !== "cancel" && reason !== "close") ElMessage.error(reason instanceof Error ? reason.message : String(reason));
  }
}

async function generateCandidates() {
  const recipeInstanceId = workspace.value?.recipeInstanceId;
  if (!recipeInstanceId || generating.value) {
    if (!recipeInstanceId) ElMessage.warning("当前项目没有可执行的一人一猫 Recipe");
    return;
  }
  generating.value = true;
  try {
    const instance = await canvasApi.recipeInstance(recipeInstanceId);
    const estimatedCostMicros = instance.storyGenerationEstimatedCostMicros;
    if (estimatedCostMicros == null) {
      ElMessage.warning("Director 文本调用费用尚未计量，不能建立明确费用边界");
      return;
    }
    await ElMessageBox.confirm(
      `将调用 Director 文本模型 1 次，生成 1–5 个完整故事候选。预计费用 ${(estimatedCostMicros / 1_000_000).toFixed(4)}；创建任务前会固定当前创作要求。`,
      documents.value.length ? "补充故事候选" : "生成故事候选",
      { confirmButtonText: "确认并创建任务", cancelButtonText: "取消", type: "warning" },
    );
    const fingerprint = `${recipeInstanceId}:${instance.revision}:${estimatedCostMicros}`;
    if (storyGenerationAttempt?.fingerprint !== fingerprint) {
      storyGenerationAttempt = { fingerprint, idempotencyKey: crypto.randomUUID() };
    }
    const job = await canvasApi.runRecipeStory(
      recipeInstanceId,
      estimatedCostMicros,
      storyGenerationAttempt.idempotencyKey,
    );
    ElMessage.success(`故事生成任务已创建（${job.jobId}）；完成后刷新候选列表`);
  } catch (reason) {
    if (reason !== "cancel" && reason !== "close") {
      ElMessage.error(reason instanceof Error ? reason.message : String(reason));
    }
  } finally {
    generating.value = false;
  }
}

function setPanel(panel: "main" | "history" | "assistant") {
  void router.replace({ name: "project-script", params: { projectId: props.projectId }, query: { ...(selectedId.value ? { item: selectedId.value } : {}), ...(panel !== "main" ? { panel } : {}) } });
}

async function setMobilePanel(panel: "assistant" | "document" | "history") {
  mobilePanel.value = panel;
  setPanel(panel === "document" ? "main" : panel);
  await nextTick();
  ({ assistant: assistantPanel, document: documentPanel, history: historyPanel }[panel].value)?.focus();
}

watch(dirty, (value) => {
  saveState.value = value ? "dirty" : saveState.value === "dirty" ? "saved" : saveState.value;
  emit("dirty-change", value ? { scope: `script:${props.projectId}:${selectedId.value}`, label: "剧情正文", save: saveRevision, discard: discardDraft } : undefined);
}, { immediate: true });
watch(() => props.projectId, () => { workspace.value = undefined; pendingWorkspace = undefined; selectedId.value = ""; void load(false); }, { immediate: true });
watch(() => props.focusedItemId, () => { const target = documents.value.find((item) => item.id === props.focusedItemId); if (target && !dirty.value) void chooseDocument(target, false); });
watch(() => props.panel, (panel) => { mobilePanel.value = panel === "history" ? "history" : panel === "assistant" ? "assistant" : "document"; if (panel === "assistant") assistantOpen.value = true; }, { immediate: true });
onBeforeUnmount(() => { requestSequence += 1; activeController?.abort("script workspace unmounted"); stopAssistantPreference(); emit("dirty-change", undefined); });
</script>

<template>
  <section class="script-workspace" :class="{ collapsed: !assistantOpen }" :data-mobile-panel="mobilePanel" aria-label="剧本工作区">
    <nav class="mobile-tabs" role="tablist"><button type="button" @click="setMobilePanel('assistant')">导演助手</button><button type="button" @click="setMobilePanel('document')">剧情正文</button><button type="button" @click="setMobilePanel('history')">版本历史</button></nav>
    <aside ref="assistantPanel" class="assistant-panel" :class="{ hidden: !assistantOpen }" tabindex="-1">
      <header><ChatDotRound /><div><span>DIRECTOR</span><b>导演助手</b></div><button type="button" aria-label="收起导演助手" @click="assistantOpen = false"><Close /></button></header>
      <section><span>创作要求</span><p>{{ briefText }}</p></section>
      <section class="suggestion"><span>修改建议预览</span><p>{{ assistantSuggestion }}</p><small>预览不会保存 Revision，也不会调用 Provider。</small></section>
      <section><span>下一步</span><p>{{ blockingWarnings.length ? "先处理执行阻塞。" : "保存正文后，明确选择一个当前剧情版本。" }}</p></section>
    </aside>
    <main ref="documentPanel" class="document-panel" tabindex="-1">
      <button v-if="!assistantOpen" class="open-assistant" type="button" @click="assistantOpen = true"><ChatDotRound />导演助手</button>
      <div v-if="state === 'loading'" class="state" aria-busy="true">正在读取故事候选与正文…</div>
      <div v-else-if="state === 'error'" class="state error" role="alert"><b>剧情工作区加载失败</b><p>{{ error }}</p><button type="button" @click="load(false)"><Refresh />重新加载</button></div>
      <template v-else>
        <div v-if="state === 'stale'" class="stale">数据可能过期：{{ error }} <button type="button" @click="load(true)">重新加载</button></div>
        <header class="document-toolbar">
          <nav aria-label="故事候选"><button v-for="(document,index) in documents" :key="document.id" type="button" :class="{ active: selectedId === document.id }" @click="chooseDocument(document)"><span>候选 {{ index + 1 }}</span><b>{{ document.title }}</b><em>{{ workspace?.currentStoryId === document.id ? "当前剧情" : `R${document.revision}` }}</em></button></nav>
          <button class="generate-candidates" type="button" :disabled="generating || !workspace?.recipeInstanceId" @click="generateCandidates"><MagicStick />{{ documents.length ? '补充候选' : '生成候选' }}</button><button type="button" aria-label="刷新剧情工作区" @click="load(true)"><Refresh />刷新</button><button type="button" @click="setPanel(historyOpen ? 'main' : 'history')"><Clock />历史</button>
        </header>
        <div v-if="!documents.length" class="state"><Document /><b>还没有故事候选</b><p>从当前创作要求生成 1–5 个完整长文本候选；费用确认前不会创建任务。</p><button type="button" :disabled="generating || !workspace?.recipeInstanceId" @click="generateCandidates"><MagicStick />{{ generating ? '正在创建任务…' : '生成故事候选' }}</button></div>
        <form v-else class="document-editor" @submit.prevent="saveRevision">
          <div class="revision-row"><span>Revision {{ selected?.revision }}</span><span>{{ selected?.source === 'ai' ? 'AI 草案' : '人工版本' }}</span><span :data-state="saveState">{{ { saved:'已保存',dirty:'未保存',saving:'保存中',conflict:'版本冲突',error:'保存失败' }[saveState] }}</span></div>
          <label><span>剧情标题</span><input v-model="draft.title" aria-label="剧情标题" /></label>
          <label><span>可选摘要</span><textarea v-model="draft.summary" rows="2" aria-label="剧情摘要" /></label>
          <label class="body-field"><span>完整故事正文 <small>支持 Markdown 长文本</small></span><textarea v-model="draft.body" aria-label="完整故事正文" /></label>
          <div v-if="warnings.length" class="warnings"><article v-for="warning in warnings" :key="warning.code" :data-severity="warning.severity"><Warning /><div><b>{{ warning.severity === 'blocker' ? '执行阻塞' : '创作提示' }}</b><p>{{ warning.message }}</p></div></article></div>
          <footer><small>艺术提示不禁用保存；正文为空或版本冲突才会阻止提交。</small><button type="button" :disabled="!dirty" @click="discardDraft">放弃修改</button><button class="save" type="submit" :disabled="saving || !dirty">保存新 Revision</button><button class="current" type="button" :disabled="saving || dirty || workspace?.currentStoryId === selected?.id" @click="setCurrent">设为当前剧情</button></footer>
        </form>
      </template>
    </main>
    <aside v-if="historyOpen || mobilePanel === 'history'" ref="historyPanel" class="history-panel" tabindex="-1"><header><div><span>REVISION HISTORY</span><b>历史版本</b></div><button type="button" @click="setMobilePanel('document')"><Close /></button></header><button v-for="document in documents" :key="document.id" type="button" :class="{ active: selectedId === document.id }" @click="chooseDocument(document)"><span>Revision {{ document.revision }}</span><b>{{ document.title }}</b><small>{{ document.source }} · {{ document.status }}</small></button></aside>
  </section>
</template>

<style scoped>
.script-workspace{height:100%;min-height:0;display:grid;grid-template-columns:350px minmax(0,1fr);overflow:hidden;color:#e8eef7;background:#0e1319}.script-workspace.collapsed{grid-template-columns:minmax(0,1fr)}.assistant-panel{min-width:0;padding:18px;display:flex;flex-direction:column;gap:12px;overflow:auto;background:#131920;border-right:1px solid #28323d}.assistant-panel.hidden{display:none}.assistant-panel>header{min-height:48px;display:flex;align-items:center;gap:11px}.assistant-panel>header>svg{width:23px;color:#79acd8}.assistant-panel>header div{display:grid}.assistant-panel span,.history-panel span{color:#7088a2;font-size:10px;font-weight:800;letter-spacing:.12em}.assistant-panel>header button,.history-panel header button{width:44px;min-height:44px;margin-left:auto;padding:12px;color:#a9b5c3;background:transparent;border:1px solid #34404d;border-radius:9px;cursor:pointer}.assistant-panel section{padding:14px;display:grid;gap:6px;background:#181f27;border:1px solid #2d3845;border-radius:12px}.assistant-panel p{margin:0;color:#aab5c2;line-height:1.65}.assistant-panel small{color:#718093}.assistant-panel .suggestion{margin-top:auto}.document-panel{position:relative;min-width:0;min-height:0;display:flex;flex-direction:column;overflow:hidden}.open-assistant{position:absolute;z-index:4;top:78px;left:12px;min-height:44px;padding:0 12px;display:flex;align-items:center;gap:7px;color:#cbd8e5;background:#1d2833;border:1px solid #384958;border-radius:9px;cursor:pointer}.open-assistant svg{width:16px}.document-toolbar{min-height:70px;padding:10px 18px;display:flex;align-items:center;gap:9px;border-bottom:1px solid #29323d}.document-toolbar nav{min-width:0;display:flex;flex:1;gap:7px;overflow:auto}.document-toolbar button{min-height:44px;padding:0 12px;display:inline-flex;align-items:center;gap:6px;color:#b8c4d2;background:#1a222b;border:1px solid #33404e;border-radius:9px;cursor:pointer}.document-toolbar nav button{min-width:160px;padding:7px 10px;display:grid;grid-template-columns:1fr auto;gap:2px;text-align:left}.document-toolbar nav button span{grid-column:1/3;font-size:9px}.document-toolbar nav button b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.document-toolbar nav button em{color:#7891aa;font-size:9px;font-style:normal}.document-toolbar nav button.active{background:#1d2b3a;border-color:#4a789f}.document-editor{width:min(940px,calc(100% - 48px));min-height:0;margin:0 auto;padding:24px 0 88px;display:flex;flex:1;flex-direction:column;gap:14px;overflow:auto}.revision-row{display:flex;gap:12px;color:#748397;font-size:10px}.revision-row span:last-child{margin-left:auto}.revision-row [data-state=dirty],.revision-row [data-state=conflict],.revision-row [data-state=error]{color:#dba568}.document-editor label{display:grid;gap:6px;color:#91a0b2;font-size:11px}.document-editor input,.document-editor textarea{box-sizing:border-box;width:100%;padding:11px 13px;color:#eef4fa;background:#151c24;border:1px solid #33404d;border-radius:9px;font:inherit;line-height:1.65;resize:vertical}.document-editor input:focus,.document-editor textarea:focus{border-color:#5e94c5;outline:2px solid rgb(86 143 196 / 18%)}.body-field{min-height:350px;flex:1}.body-field textarea{min-height:330px;flex:1;font-size:15px}.body-field small{color:#68778a}.warnings{display:grid;gap:7px}.warnings article{padding:10px 12px;display:flex;gap:9px;color:#d6b579;background:#292318;border:1px solid #5f4d2d;border-radius:9px}.warnings article[data-severity=blocker]{color:#e1a09b;background:#301d20;border-color:#704247}.warnings svg{width:16px}.warnings p{margin:2px 0 0}.document-editor footer{position:sticky;bottom:-88px;margin-top:auto;padding:12px;display:flex;align-items:center;justify-content:flex-end;gap:8px;background:rgb(20 27 35 / 96%);border:1px solid #303c49;border-radius:12px}.document-editor footer small{margin-right:auto;color:#748296}.document-editor footer button{min-height:44px;padding:0 14px;color:#c9d4e0;background:#242e39;border:1px solid #3b4958;border-radius:9px;cursor:pointer}.document-editor footer .save{color:#ebf6ff;background:#28628f;border-color:#397fb5}.document-editor footer .current{color:#15251d;background:#8bc8a5;border-color:#a4d8b8;font-weight:800}.document-editor footer button:disabled{opacity:.4;cursor:not-allowed}.state{height:100%;display:grid;place-items:center;align-content:center;gap:9px;color:#7b899b}.state svg{width:26px}.state p{max-width:500px;margin:0;text-align:center}.state.error{color:#dfaaa5}.state button,.stale button{min-height:44px;padding:0 12px;display:flex;align-items:center;gap:6px;color:#d8e2ed;background:#202b35;border:1px solid #405263;border-radius:9px;cursor:pointer}.state button svg{width:16px}.stale{padding:8px 15px;color:#dfba7e;background:#2b2318;border-bottom:1px solid #5c492c}.history-panel{width:330px;padding:16px;display:grid;align-content:start;gap:8px;overflow:auto;background:#131920;border-left:1px solid #2c3642}.history-panel header{display:flex;align-items:center}.history-panel header div{display:grid}.history-panel>button{min-height:58px;padding:11px;display:grid;gap:3px;color:#d4deea;text-align:left;background:#19212a;border:1px solid #2f3a47;border-radius:9px;cursor:pointer}.history-panel>button.active{border-color:#4e7da7}.history-panel small{color:#758397}.mobile-tabs{display:none}@media(min-width:1440px){.script-workspace:has(.history-panel){grid-template-columns:350px minmax(0,1fr) 330px}.script-workspace.collapsed:has(.history-panel){grid-template-columns:minmax(0,1fr) 330px}}@media(max-width:1439px){.history-panel{position:absolute;z-index:8;inset:0 0 0 auto;box-shadow:-14px 0 36px rgb(0 0 0 / 36%)}}@media(max-width:1023px){.script-workspace{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}.mobile-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;padding:6px;background:#131920}.mobile-tabs button{min-height:44px;color:#91a0b2;background:#182029;border:1px solid #2e3a47;border-radius:8px}.assistant-panel,.document-panel,.history-panel{grid-row:2;grid-column:1}.history-panel{position:relative;inset:auto;width:auto;box-shadow:none}.script-workspace[data-mobile-panel=assistant] .document-panel,.script-workspace[data-mobile-panel=assistant] .history-panel,.script-workspace[data-mobile-panel=document] .assistant-panel,.script-workspace[data-mobile-panel=document] .history-panel,.script-workspace[data-mobile-panel=history] .assistant-panel,.script-workspace[data-mobile-panel=history] .document-panel{display:none}.script-workspace[data-mobile-panel=assistant] .assistant-panel.hidden{display:flex}.document-editor{width:calc(100% - 28px)}.document-editor footer{flex-wrap:wrap}.document-editor footer small{width:100%}}
.document-toolbar>button{flex:0 0 auto;height:44px;white-space:nowrap}
</style>
