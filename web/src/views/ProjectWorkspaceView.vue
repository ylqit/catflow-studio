<script setup lang="ts">
import { ElMessageBox } from "element-plus";
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { RouteLocationNormalized } from "vue-router";

import { canvasApi } from "../api/client";
import type { ProjectWorkspaceShellDto, WorkspaceModuleId } from "../api/types";
import type { DirectorDirtyRegistration, DirectorDirtyResolution } from "../components/director/directorDirtyState";
import { DirectorDirtyCoordinator } from "../components/director/directorDirtyState";
import WorkspaceModuleNavigation from "../components/workspace/WorkspaceModuleNavigation.vue";
import WorkspaceProjectHeader from "../components/workspace/WorkspaceProjectHeader.vue";

const ScriptWorkspace = defineAsyncComponent(() => import("../components/director/ScriptWorkspace.vue"));
const AssetWorkspace = defineAsyncComponent(() => import("../components/director/AssetWorkspace.vue"));
const ProductionWorkspace = defineAsyncComponent(() => import("../components/production/ProductionWorkspace.vue"));

type LoadState = "loading" | "ready" | "refreshing" | "stale" | "error";

const route = useRoute();
const router = useRouter();
const shell = ref<ProjectWorkspaceShellDto>();
const loadState = ref<LoadState>("loading");
const loadError = ref("");
const dirtyCoordinator = new DirectorDirtyCoordinator();
let activeController: AbortController | undefined;
let requestSequence = 0;
let removeGuard: (() => void) | undefined;

const projectId = computed(() => String(route.params.projectId ?? ""));
const activeModuleId = computed<WorkspaceModuleId>(() => {
  const value = route.meta.workspaceModule;
  return value === "script" || value === "assets" || value === "production" ? value : "production";
});
const focusedItemId = computed(() => typeof route.query.item === "string" ? route.query.item : "");
const panel = computed(() => typeof route.query.panel === "string" ? route.query.panel : "main");
const modules = computed(() => [...(shell.value?.modules ?? [])].sort((left, right) => left.order - right.order));
const activeModule = computed(() => modules.value.find((module) => module.id === activeModuleId.value));
const activeWorkspaceComponent = computed(() => {
  if (activeModuleId.value === "script") return ScriptWorkspace;
  if (activeModuleId.value === "assets") return AssetWorkspace;
  return ProductionWorkspace;
});

async function loadShell(background = false) {
  if (!projectId.value) return;
  activeController?.abort("workspace shell superseded");
  const controller = new AbortController();
  activeController = controller;
  const sequence = ++requestSequence;
  const hasShell = shell.value?.project.id === projectId.value;
  loadState.value = background && hasShell ? "refreshing" : "loading";
  loadError.value = "";
  try {
    const result = await canvasApi.workspaceShell(projectId.value, controller.signal);
    if (sequence !== requestSequence || controller.signal.aborted) return;
    shell.value = result;
    loadState.value = "ready";
  } catch (error) {
    if (sequence !== requestSequence || controller.signal.aborted) return;
    loadError.value = error instanceof Error ? error.message : String(error);
    loadState.value = hasShell ? "stale" : "error";
  } finally {
    if (sequence === requestSequence) activeController = undefined;
  }
}

function moduleRoute(moduleId: WorkspaceModuleId, nextProjectId = projectId.value) {
  return { name: `project-${moduleId}`, params: { projectId: nextProjectId } };
}

function openModule(moduleId: WorkspaceModuleId) {
  void router.push(moduleRoute(moduleId));
}

function switchProject(nextProjectId: string) {
  void router.push(moduleRoute(activeModuleId.value, nextProjectId));
}

function openNext() {
  const next = activeModule.value?.nextAction;
  if (!next) return;
  void router.push(moduleRoute(next.moduleId));
}

async function chooseDirtyResolution(registration: DirectorDirtyRegistration): Promise<DirectorDirtyResolution> {
  try {
    await ElMessageBox.confirm(
      `${registration.label}还有未保存修改。保存会创建或更新正式版本；放弃不会改动当前业务版本。`,
      "离开前处理修改",
      { confirmButtonText: "保存修改", cancelButtonText: "放弃修改", distinguishCancelAndClose: true, closeOnClickModal: false, type: "warning" },
    );
    return "save";
  } catch (action) {
    return action === "cancel" ? "discard" : "continue";
  }
}

function registerDirtyState(registration?: DirectorDirtyRegistration) {
  dirtyCoordinator.register(registration);
}

function leavesCurrentWorkspace(to: RouteLocationNormalized, from: RouteLocationNormalized) {
  if (String(to.params.projectId ?? "") !== String(from.params.projectId ?? "")) return true;
  return to.name !== from.name;
}

watch(projectId, () => {
  shell.value = undefined;
  loadState.value = "loading";
  loadError.value = "";
  void loadShell(false);
}, { immediate: true });

onMounted(() => {
  removeGuard = router.beforeEach(async (to, from) => {
    if (!dirtyCoordinator.active || !leavesCurrentWorkspace(to, from)) return true;
    return dirtyCoordinator.resolve(chooseDirtyResolution);
  });
});
onBeforeUnmount(() => {
  requestSequence += 1;
  activeController?.abort("workspace view unmounted");
  removeGuard?.();
});
</script>

<template>
  <div class="project-workspace">
    <WorkspaceProjectHeader
      :shell="shell"
      :active-module="activeModule"
      :load-state="loadState"
      :load-error="loadError"
      @projects="router.push({ name: 'projects' })"
      @refresh="loadShell(Boolean(shell))"
      @switch-project="switchProject"
      @next="openNext"
    />
    <WorkspaceModuleNavigation v-if="shell" :modules="modules" :active-module-id="activeModuleId" @select="openModule" />
    <div v-else class="navigation-skeleton" aria-busy="true"><i v-for="index in 3" :key="index" /></div>
    <main class="workspace-content">
      <div v-if="loadState === 'error'" class="shell-error" role="alert"><b>项目工作区状态加载失败</b><p>{{ loadError }}</p><button type="button" @click="loadShell(false)">重新加载</button></div>
      <Suspense v-else :timeout="0">
        <component
          :is="activeWorkspaceComponent"
          :project-id="projectId"
          :focused-item-id="focusedItemId"
          :panel="activeModuleId === 'production' ? undefined : panel"
          @dirty-change="registerDirtyState"
        />
        <template #fallback><div class="module-loader" aria-busy="true"><i /><b>正在打开{{ activeModule?.title ?? "项目工作区" }}…</b><span>项目外壳与其他入口仍可使用</span></div></template>
      </Suspense>
      <div v-if="loadState === 'stale'" class="stale-warning" role="status">数据可能过期：{{ loadError }}</div>
    </main>
  </div>
</template>

<style scoped>
.project-workspace{width:100%;height:100%;display:grid;grid-template-rows:56px 56px minmax(0,1fr);overflow:hidden;color:#e8eef7;background:#0b0f14}.workspace-content{position:relative;min-width:0;min-height:0;margin:12px;overflow:hidden;background:#10151c;border:1px solid #252e39;border-radius:15px}.navigation-skeleton{height:56px;padding:6px 12px;display:grid;grid-template-columns:repeat(3,minmax(0,240px));gap:7px;background:#0e1319;border-bottom:1px solid #252d37}.navigation-skeleton i{border-radius:10px;background:#1a222c;animation:pulse 1.2s ease-in-out infinite alternate}.module-loader{height:100%;display:grid;place-content:center;justify-items:center;gap:8px;color:#7f8e9f}.module-loader i{width:42px;height:42px;border:3px solid #263544;border-top-color:#5f91bf;border-radius:50%;animation:spin .9s linear infinite}.module-loader b{color:#bac7d4;font-size:14px}.module-loader span{font-size:10px}.shell-error{width:min(620px,calc(100% - 40px));margin:50px auto;padding:22px;color:#efcdc7;background:#281b1d;border:1px solid #684044;border-radius:14px}.shell-error p{color:#c99b9b;white-space:pre-wrap}.shell-error button{min-height:44px;padding:0 15px;color:#fff;background:#72444a;border:1px solid #985b63;border-radius:9px;cursor:pointer}.stale-warning{position:absolute;z-index:50;top:10px;right:12px;max-width:min(620px,calc(100% - 24px));padding:9px 12px;color:#e1bd83;background:rgb(49 38 24 / 95%);border:1px solid #735b37;border-radius:9px;font-size:11px}@keyframes pulse{to{opacity:.55}}@keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.navigation-skeleton i,.module-loader i{animation:none}}@media(max-width:720px){.project-workspace{grid-template-rows:56px 56px minmax(0,1fr)}.workspace-content{margin:8px 8px 66px}}
</style>
