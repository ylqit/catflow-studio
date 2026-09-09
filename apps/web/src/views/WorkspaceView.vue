<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { subscribeJobs, jobsConnected } from "../jobUpdates";
import { api } from "../api/client";
import type { ProjectSeriesContextDto, RuntimeBootstrapDto, WorkspaceDto } from "../api/types";
import AssetsStep from "../components/workspace/AssetsStep.vue";
import DeliveryStep from "../components/workspace/DeliveryStep.vue";
import GenerationStep from "../components/workspace/GenerationStep.vue";
import PlannerStep from "../components/workspace/PlannerStep.vue";
import StoryboardStep from "../components/workspace/StoryboardStep.vue";
import { useUiStore } from "../stores/ui";

const props = defineProps<{ step: "planner" | "assets" | "storyboard" | "generation" | "delivery" }>();
const route = useRoute();
const router = useRouter();
const store = useUiStore();
const workspace = ref<WorkspaceDto | null>(null);
const runtime = ref<RuntimeBootstrapDto | null>(null);
const seriesContext = ref<ProjectSeriesContextDto | null>(null);
const loading = ref(true);
const error = ref("");
const projectId = computed(() => String(route.params.projectId));
let unsubscribeJobs: (() => void) | undefined;
let runtimeTimer: number | undefined;

const steps = [
  { id: "planner", number: "01", label: "生活灵感", hint: "一个微事件" },
  { id: "assets", number: "02", label: "角色与画风", hint: "五个固定槽位" },
  { id: "storyboard", number: "03", label: "分镜画布", hint: "1–4 个镜头" },
  { id: "generation", number: "04", label: "生成与选择", hint: "准备生成" },
  { id: "delivery", number: "05", label: "剪辑与导出", hint: "完成剪辑" },
] as const;

let workspaceRequest = 0;
async function loadWorkspace() {
  const sequence = ++workspaceRequest; const requestedProject = projectId.value;
  try {
    const [nextWorkspace, nextRuntime, nextSeriesContext] = await Promise.all([
      api.workspace(projectId.value),
      api.runtime().catch(() => null),
      api.projectSeriesContext(projectId.value),
    ]);
    if (sequence !== workspaceRequest || requestedProject !== projectId.value) return;
    workspace.value = nextWorkspace;
    runtime.value = nextRuntime;
    seriesContext.value = nextSeriesContext;
    store.lastEventId = Math.max(store.lastEventId, workspace.value.eventCursor);
    store.setProject(projectId.value);
    error.value = "";
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "工作区读取失败";
  } finally {
    if (sequence === workspaceRequest) loading.value = false;
  }
}

async function switchEpisode(event: Event) {
  const selectedProjectId = (event.target as HTMLSelectElement).value;
  if (selectedProjectId) await router.push(`/projects/${selectedProjectId}/${props.step}`);
}

async function refreshRuntime() {
  try {
    runtime.value = await api.runtime();
  } catch {
    // The workspace remains readable while runtime health is temporarily unavailable.
  }
}

function syncRuntimePolling() {
  if (runtimeTimer !== undefined) {
    window.clearInterval(runtimeTimer);
    runtimeTimer = undefined;
  }
  if (runtime.value?.worker.ready === false) {
    runtimeTimer = window.setInterval(() => { void refreshRuntime(); }, 5000);
  }
}

function refreshRuntimeWhenVisible() {
  if (document.visibilityState === "visible" && runtime.value?.worker.ready === false) {
    void refreshRuntime();
  }
}

function connectEvents() {
  unsubscribeJobs?.();
  unsubscribeJobs = subscribeJobs(() => ({ projectId: projectId.value }), loadWorkspace,
    () => Object.values(workspace.value ?? {}).some(value => value && typeof value === 'object' && 'execution' in value && (value.execution as { waitingForProvider?: boolean })?.waitingForProvider === true));
}
watch(jobsConnected, value => { store.sseConnected = value; });

onMounted(async () => {
  connectEvents();
  syncRuntimePolling();
  window.addEventListener("focus", refreshRuntimeWhenVisible);
  document.addEventListener("visibilitychange", refreshRuntimeWhenVisible);
});
watch(projectId, async () => { loading.value = true; await loadWorkspace(); connectEvents(); });
watch(() => [runtime.value?.worker.ready, runtime.value?.worker.state], syncRuntimePolling);
onBeforeUnmount(() => {
  unsubscribeJobs?.();
  if (runtimeTimer !== undefined) window.clearInterval(runtimeTimer);
  window.removeEventListener("focus", refreshRuntimeWhenVisible);
  document.removeEventListener("visibilitychange", refreshRuntimeWhenVisible);
  store.sseConnected = false;
});
</script>

<template>
  <main class="workspace-page" :class="{ 'editing-focus': step === 'delivery', 'storyboard-workspace': step === 'storyboard' }">
    <section v-if="loading" class="page"><div class="card empty">正在加载工作区…</div></section>
    <section v-else-if="error || !workspace" class="page"><div class="card empty"><h2>工作区暂时不可用</h2><p class="notice error">{{ error }}</p><button class="secondary" @click="loadWorkspace">重新检查</button></div></section>
    <template v-else>
      <header class="workspace-heading">
        <div class="workspace-title"><RouterLink :to="seriesContext ? `/series/${seriesContext.series.id}` : '/projects'">←</RouterLink><div><p v-if="seriesContext" class="series-breadcrumb"><RouterLink :to="`/series/${seriesContext.series.id}`">{{ seriesContext.series.title }}</RouterLink> · 第 {{ seriesContext.episode.order }} 集</p><p v-else class="eyebrow">{{ workspace.project.theme }}</p><h1>{{ workspace.project.title }}</h1></div></div>
        <div class="workspace-status"><span v-if="!store.sseConnected" class="connection-warning"><i />正在恢复连接</span><b>{{ workspace.project.targetDurationSeconds }}s</b><b>9:16</b></div>
      </header>
      <div v-if="seriesContext" class="episode-switcher"><label>切换剧集<select :value="projectId" @change="switchEpisode"><option v-for="episode in seriesContext.episodes" :key="episode.id" :value="episode.projectId ?? ''" :disabled="!episode.projectId">第 {{ episode.order }} 集 · {{ episode.title }}{{ episode.projectId ? '' : '（未开始）' }}</option></select></label></div>
      <nav class="step-nav" aria-label="五步创作流程">
        <RouterLink v-for="item in steps" :key="item.id" :to="`/projects/${projectId}/${item.id}`" :class="{ current: step === item.id, ready: workspace.steps.find((state) => state.id === item.id)?.ready }">
          <span>{{ item.number }}</span><div><b>{{ item.id === 'planner' && seriesContext ? '本集剧情' : item.label }}</b><small>{{ item.id === 'planner' && seriesContext ? '本集任务与前后承接' : item.hint }}</small></div><i>✓</i>
        </RouterLink>
      </nav>
      <p v-if="runtime?.worker.ready === false" class="worker-warning" role="status">
        后台任务暂时不可用。已经保存的任务不会丢失，{{ runtime.worker.retryingAutomatically ? "系统正在尝试恢复；" : "请到运行设置检查；" }}恢复前不能开始新的生成。
      </p>
      <section class="workspace-content">
        <PlannerStep v-if="step === 'planner'" :key="projectId" :project-id="projectId" :series-context="seriesContext" :runtime="runtime" @changed="loadWorkspace" />
        <AssetsStep v-else-if="step === 'assets'" :project-id="projectId" :workspace="workspace" :runtime="runtime" @changed="loadWorkspace" />
        <StoryboardStep v-else-if="step === 'storyboard'" :project-id="projectId" :workspace="workspace" :runtime="runtime" @changed="loadWorkspace" />
        <GenerationStep v-else-if="step === 'generation'" :project-id="projectId" :workspace="workspace" :runtime="runtime" @changed="loadWorkspace" />
        <DeliveryStep v-else :project-id="projectId" :workspace="workspace" :runtime="runtime" @changed="loadWorkspace" />
      </section>
    </template>
  </main>
</template>

<style scoped>
.workspace-page { min-height: calc(100vh - 70px); }
.editing-focus .workspace-heading { height:54px; }.editing-focus .workspace-title h1 { font-size:20px; }.editing-focus .step-nav { height:42px; }.editing-focus .step-nav small { display:none; }.editing-focus .episode-switcher { margin-bottom:4px; }.editing-focus .workspace-content { padding-top:10px; }
.workspace-heading { width: min(1480px, calc(100% - 56px)); height: 84px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; }
.workspace-title { display: flex; align-items: center; gap: 15px; }
.workspace-title > a { width: 34px; height: 34px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 11px; background: var(--paper); color: var(--muted); }
.workspace-title .eyebrow { max-width: 520px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-bottom: 2px; }
.workspace-title h1 { margin: 0; font-size: 25px; }
.workspace-status { display: flex; align-items: center; gap: 8px; }
.workspace-status > * { padding: 6px 9px; border-radius: 8px; color: #786f67; background: #ede6de; font-size: 10px; }
.workspace-status span i { display: inline-block; width: 6px; height: 6px; margin-right: 5px; border-radius: 50%; background: #b8906f; }
.workspace-status span i.live { background: #67a172; }
.step-nav { height: 72px; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); padding: 0 max(28px, calc((100% - 1480px) / 2)); border-block: 1px solid var(--line); background: rgb(255 253 249 / 78%); }
.step-nav a { position: relative; display: flex; align-items: center; gap: 10px; padding: 0 20px; border-right: 1px solid var(--line); color: #8a8179; }
.step-nav a:first-child { border-left: 1px solid var(--line); }
.step-nav a > span { font: 500 18px Georgia, serif; color: #c8bcb0; }
.step-nav a div { display: grid; gap: 3px; }
.step-nav a b { color: #655d56; font-size: 12px; }
.step-nav a small { font-size: 9px; }
.step-nav a > i { display: none; margin-left: auto; color: #6f9576; font-style: normal; }
.step-nav a.ready > i { display: block; }
.step-nav a.current { background: #f7ebe3; }
.step-nav a.current::after { content: ""; position: absolute; left: 18px; right: 18px; bottom: -1px; height: 3px; border-radius: 3px 3px 0 0; background: var(--accent); }
.step-nav a.current > span, .step-nav a.current b { color: var(--accent-dark); }
.workspace-content { width: min(1480px, calc(100% - 56px)); margin: 0 auto; padding: 20px 0 55px; }
.worker-warning { width: min(1480px, calc(100% - 56px)); box-sizing: border-box; margin: 14px auto 0; padding: 11px 14px; border: 1px solid #d8b28d; border-radius: 11px; color: #815741; background: #fff4e8; font-size: 11px; }
.series-breadcrumb { margin: 0 0 3px; color: var(--muted); font-size: 11px; }.series-breadcrumb a { color: var(--accent-dark); }.episode-switcher { width: min(1480px, calc(100% - 56px)); margin: 0 auto 10px; display: flex; justify-content: flex-end; }.episode-switcher label { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 11px; }.episode-switcher select { min-width: 220px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface); }
</style>
