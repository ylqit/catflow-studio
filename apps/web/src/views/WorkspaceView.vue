<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { api } from "../api/client";
import type { WorkspaceDto } from "../api/types";
import AssetsStep from "../components/workspace/AssetsStep.vue";
import DeliveryStep from "../components/workspace/DeliveryStep.vue";
import GenerationStep from "../components/workspace/GenerationStep.vue";
import PlannerStep from "../components/workspace/PlannerStep.vue";
import StoryboardStep from "../components/workspace/StoryboardStep.vue";
import { projectJobEvent } from "../projectJobEvents";
import { useUiStore } from "../stores/ui";

const props = defineProps<{ step: "planner" | "assets" | "storyboard" | "generation" | "delivery" }>();
const route = useRoute();
const store = useUiStore();
const workspace = ref<WorkspaceDto | null>(null);
const loading = ref(true);
const error = ref("");
const projectId = computed(() => String(route.params.projectId));
let eventSource: EventSource | null = null;

const steps = [
  { id: "planner", number: "01", label: "生活灵感", hint: "一个微事件" },
  { id: "assets", number: "02", label: "角色与画风", hint: "五个固定槽位" },
  { id: "storyboard", number: "03", label: "分镜画布", hint: "1–4 个镜头" },
  { id: "generation", number: "04", label: "生成与选择", hint: "冻结输入" },
  { id: "delivery", number: "05", label: "剪辑与导出", hint: "FFmpeg 成片" },
] as const;

async function loadWorkspace() {
  try {
    workspace.value = await api.workspace(projectId.value);
    store.lastEventId = Math.max(store.lastEventId, workspace.value.eventCursor);
    store.setProject(projectId.value);
    error.value = "";
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "工作区读取失败";
  } finally {
    loading.value = false;
  }
}

function connectEvents() {
  eventSource?.close();
  eventSource = new EventSource(api.eventsUrl(store.lastEventId));
  eventSource.onopen = () => { store.sseConnected = true; };
  eventSource.onerror = () => { store.sseConnected = false; };
  const refresh = (event: MessageEvent) => {
    if (event.lastEventId) store.lastEventId = Number(event.lastEventId);
    if (!projectJobEvent(event, projectId.value)) return;
    void loadWorkspace();
  };
  for (const type of ["job.queued", "job.submitting", "job.submitted", "job.polling", "job.succeeded", "job.failed", "job.cancelled", "planner.proposal.created"]) {
    eventSource.addEventListener(type, refresh as EventListener);
  }
}

onMounted(async () => { await loadWorkspace(); connectEvents(); });
watch(projectId, async () => { loading.value = true; await loadWorkspace(); connectEvents(); });
onBeforeUnmount(() => { eventSource?.close(); store.sseConnected = false; });
</script>

<template>
  <main class="workspace-page">
    <section v-if="loading" class="page"><div class="card empty">正在从 PostgreSQL 恢复工作区…</div></section>
    <section v-else-if="error || !workspace" class="page"><div class="card empty"><h2>工作区暂时不可用</h2><p class="notice error">{{ error }}</p><button class="secondary" @click="loadWorkspace">重新检查</button></div></section>
    <template v-else>
      <header class="workspace-heading">
        <div class="workspace-title"><RouterLink to="/projects">←</RouterLink><div><p class="eyebrow">{{ workspace.project.theme }}</p><h1>{{ workspace.project.title }}</h1></div></div>
        <div class="workspace-status"><span><i :class="{ live: store.sseConnected }" />{{ store.sseConnected ? "事件已连接" : "正在重连" }}</span><b>{{ workspace.project.targetDurationSeconds }}s</b><b>9:16</b></div>
      </header>
      <nav class="step-nav" aria-label="五步创作流程">
        <RouterLink v-for="item in steps" :key="item.id" :to="`/projects/${projectId}/${item.id}`" :class="{ current: step === item.id, ready: workspace.steps.find((state) => state.id === item.id)?.ready }">
          <span>{{ item.number }}</span><div><b>{{ item.label }}</b><small>{{ item.hint }}</small></div><i>✓</i>
        </RouterLink>
      </nav>
      <section class="workspace-content">
        <PlannerStep v-if="step === 'planner'" :project-id="projectId" @changed="loadWorkspace" />
        <AssetsStep v-else-if="step === 'assets'" :project-id="projectId" :workspace="workspace" @changed="loadWorkspace" />
        <StoryboardStep v-else-if="step === 'storyboard'" :project-id="projectId" :workspace="workspace" @changed="loadWorkspace" />
        <GenerationStep v-else-if="step === 'generation'" :project-id="projectId" :workspace="workspace" @changed="loadWorkspace" />
        <DeliveryStep v-else :project-id="projectId" :workspace="workspace" @changed="loadWorkspace" />
      </section>
    </template>
  </main>
</template>

<style scoped>
.workspace-page { min-height: calc(100vh - 70px); }
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
</style>
