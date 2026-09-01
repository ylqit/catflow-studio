<script setup lang="ts">
import {
  FolderOpened,
  Operation,
  Setting,
} from "@element-plus/icons-vue";
import { computed, inject } from "vue";
import { useRouter } from "vue-router";

const props = withDefaults(defineProps<{
  taskCount?: number;
}>(), {
  taskCount: 0,
});
const router = useRouter();
const openGlobalTasks = inject<() => void>("openGlobalTasks", () => undefined);

const taskLabel = computed(() => props.taskCount > 0
  ? `全局任务，${props.taskCount} 个运行或待处理任务`
  : "全局任务");
</script>

<template>
  <nav class="project-rail" aria-label="应用导航">
    <button class="rail-brand" type="button" aria-label="一人一猫项目" title="一人一猫项目" @click="router.push('/projects')"><FolderOpened /></button>
    <div class="rail-primary">
      <button type="button" aria-label="项目列表" title="项目列表" @click="router.push('/projects')"><FolderOpened /></button>
      <button type="button" class="rail-task" :aria-label="taskLabel" :title="taskLabel" @click="openGlobalTasks"><Operation /><span v-if="taskCount" class="rail-task-badge">{{ taskCount > 99 ? '99+' : taskCount }}</span></button>
    </div>
    <div class="rail-secondary">
      <button type="button" aria-label="设置" title="设置" @click="router.push('/settings')"><Setting /></button>
    </div>
  </nav>
</template>

<style scoped>
.project-rail { grid-row: 1 / -1; width: 64px; min-width: 64px; padding: 8px; display: flex; flex-direction: column; align-items: center; gap: 12px; background: #0c0f14; border-right: 1px solid #252b35; }
.rail-primary, .rail-secondary { width: 100%; display: grid; gap: 8px; }.rail-primary { flex: 1; align-content: start; }.rail-secondary { align-content: end; }
button { width: 48px; min-width: 44px; height: 44px; padding: 11px; display: grid; place-items: center; color: #8490a2; background: transparent; border: 1px solid transparent; border-radius: 10px; cursor: pointer; transition: color .16s ease, background .16s ease, border-color .16s ease; }
button:hover, button:focus-visible { color: #e6edf8; background: #19212c; border-color: #334052; outline: none; }.rail-brand { color: #d9ebff; background: #19324d; border-color: #315c86; }.rail-brand :deep(svg) { width: 23px; height: 23px; } button:disabled { opacity: .38; cursor: not-allowed; }
button :deep(svg) { width: 20px; height: 20px; }
.rail-task { position: relative; }.rail-task-badge { position: absolute; top: 2px; right: 1px; min-width: 17px; height: 17px; padding: 0 4px; display: grid; place-items: center; color: #fff; background: #cf5f62; border: 2px solid #0c0f14; border-radius: 9px; font-size: 9px; font-weight: 800; line-height: 1; }
@media (prefers-reduced-motion: reduce) { button { transition: none; } }
@media (max-width: 720px) { .project-rail { position: fixed; z-index: 1400; right: 0; bottom: 0; left: 0; width: auto; min-width: 0; height: 58px; padding: 6px 8px; flex-direction: row; border-top: 1px solid #2d3643; border-right: 0; }.rail-brand { display: none; }.rail-primary, .rail-secondary { width: auto; display: flex; gap: 4px; }.rail-primary { flex: 1; }.rail-secondary { margin-left: auto; } button { width: 44px; } }
</style>
