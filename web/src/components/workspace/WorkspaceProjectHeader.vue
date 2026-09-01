<script setup lang="ts">
import { ArrowDown, ArrowLeft, Refresh, Warning } from "@element-plus/icons-vue";
import { computed, ref } from "vue";

import { api } from "../../api/client";
import type { ProjectSummary, ProjectWorkspaceShellDto, WorkspaceModuleDto } from "../../api/types";

const props = defineProps<{
  shell?: ProjectWorkspaceShellDto;
  activeModule?: WorkspaceModuleDto;
  loadState: "loading" | "ready" | "refreshing" | "stale" | "error";
  loadError?: string;
}>();
const emit = defineEmits<{
  refresh: [];
  projects: [];
  switchProject: [projectId: string];
  next: [];
}>();

const menuOpen = ref(false);
const projects = ref<ProjectSummary[]>([]);
const projectsLoaded = ref(false);
const projectsLoading = ref(false);
const projectError = ref("");
const busy = computed(() => ["loading", "refreshing"].includes(props.loadState));
const syncLabel = computed(() => ({
  loading: "正在载入",
  ready: "已同步",
  refreshing: "同步中",
  stale: "数据可能过期",
  error: "状态不可用",
})[props.loadState]);

async function toggleProjects() {
  menuOpen.value = !menuOpen.value;
  if (!menuOpen.value || projectsLoaded.value || projectsLoading.value) return;
  projectsLoading.value = true;
  projectError.value = "";
  try {
    projects.value = await api.projects();
    projectsLoaded.value = true;
  } catch (error) {
    projectError.value = error instanceof Error ? error.message : String(error);
  } finally {
    projectsLoading.value = false;
  }
}

function selectProject(projectId: string) {
  menuOpen.value = false;
  if (projectId && projectId !== props.shell?.project.id) emit("switchProject", projectId);
}
</script>

<template>
  <header class="workspace-header">
    <button class="icon-action" type="button" aria-label="返回项目列表" title="返回项目列表" @click="$emit('projects')">
      <ArrowLeft />
    </button>
    <div class="project-switcher-wrap">
      <button class="project-switcher" type="button" aria-haspopup="listbox" :aria-expanded="menuOpen" @click="toggleProjects">
        <span><small>ONE CHILD · ONE CAT</small><strong>{{ shell?.project.title ?? "正在载入项目…" }}</strong></span>
        <ArrowDown />
      </button>
      <div v-if="menuOpen" class="project-menu" role="listbox" aria-label="切换项目">
        <span v-if="projectsLoading" class="menu-state">正在加载项目…</span>
        <span v-else-if="projectError" class="menu-state error">{{ projectError }}</span>
        <template v-else>
          <button v-for="project in projects" :key="project.id" type="button" role="option" :aria-selected="project.id === shell?.project.id" @click="selectProject(project.id)">
            <strong>{{ project.title }}</strong><small>{{ project.contentDate }} · {{ project.status }}</small>
          </button>
          <span v-if="!projects.length" class="menu-state">没有可切换的项目</span>
        </template>
      </div>
    </div>
    <div class="sync-state" :class="{ problem: loadState === 'stale' || loadState === 'error' }" :title="loadError || syncLabel">
      <Warning v-if="loadState === 'stale' || loadState === 'error'" /><i v-else />
      <span>{{ syncLabel }}</span>
    </div>
    <div class="header-actions">
      <span v-if="shell?.activeTaskSummary" class="task-summary">{{ shell.activeTaskSummary.activeCount }} 运行 · {{ shell.activeTaskSummary.attentionCount }} 待处理</span>
      <button v-if="activeModule?.nextAction" class="next-action" type="button" @click="$emit('next')">{{ activeModule.nextAction.label }}</button>
      <button class="icon-action refresh" type="button" :disabled="busy" aria-label="刷新项目状态" title="刷新项目状态" @click="$emit('refresh')"><Refresh /></button>
    </div>
  </header>
</template>

<style scoped>
.workspace-header{position:relative;z-index:30;height:56px;padding:0 14px;display:flex;align-items:center;gap:10px;color:#e8eef7;background:#11161d;border-bottom:1px solid #252d37}.icon-action,.project-switcher,.next-action{min-height:44px;border-radius:10px;cursor:pointer}.icon-action{width:44px;padding:11px;color:#91a0b3;background:transparent;border:1px solid transparent}.icon-action:hover,.icon-action:focus-visible,.project-switcher:hover,.project-switcher:focus-visible{color:#fff;background:#1b242e;border-color:#344252;outline:none}.icon-action:disabled{opacity:.45;cursor:wait}.project-switcher-wrap{position:relative;min-width:0}.project-switcher{width:min(380px,36vw);padding:4px 10px;display:flex;align-items:center;gap:8px;color:#e7edf6;background:transparent;border:1px solid transparent;text-align:left}.project-switcher>span{min-width:0;display:grid;flex:1}.project-switcher small{color:#65758a;font-size:9px;font-weight:800;letter-spacing:.15em}.project-switcher strong{overflow:hidden;font-size:14px;text-overflow:ellipsis;white-space:nowrap}.project-switcher :deep(svg){width:14px}.project-menu{position:absolute;top:calc(100% + 5px);left:0;width:min(380px,calc(100vw - 90px));max-height:360px;padding:7px;display:grid;gap:4px;overflow:auto;background:#151c24;border:1px solid #344151;border-radius:12px;box-shadow:0 18px 40px rgb(0 0 0 / 38%)}.project-menu button{min-height:50px;padding:8px 10px;display:grid;gap:3px;color:#c9d4e0;text-align:left;background:transparent;border:1px solid transparent;border-radius:9px;cursor:pointer}.project-menu button:hover,.project-menu button:focus-visible,.project-menu button[aria-selected=true]{color:#fff;background:#202b37;border-color:#425872;outline:none}.project-menu small,.menu-state{color:#758398;font-size:11px}.menu-state{padding:13px}.menu-state.error{color:#e6aaa3}.sync-state{display:flex;align-items:center;gap:6px;color:#789b8c;font-size:11px}.sync-state i{width:7px;height:7px;background:#4aaa7d;border-radius:50%}.sync-state.problem{color:#dda866}.sync-state :deep(svg){width:14px}.header-actions{min-width:0;margin-left:auto;display:flex;align-items:center;gap:8px}.task-summary{color:#7c899b;font-size:11px}.next-action{padding:0 15px;color:#ecf6ff;background:#255c89;border:1px solid #3b79aa;font-weight:700}.next-action:hover,.next-action:focus-visible{background:#2d6c9f;outline:2px solid rgb(91 161 218 / 22%)}.refresh :deep(svg){width:17px}@media(max-width:900px){.sync-state,.task-summary{display:none}.project-switcher{width:min(300px,48vw)}.next-action{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}@media(max-width:620px){.workspace-header{padding:0 8px}.workspace-header>.icon-action:first-child{display:none}.project-switcher{width:calc(100vw - 180px)}.next-action{max-width:120px;padding:0 10px;font-size:12px}}
</style>
