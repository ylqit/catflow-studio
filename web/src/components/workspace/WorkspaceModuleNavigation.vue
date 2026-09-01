<script setup lang="ts">
import { Collection, Document, VideoPlay } from "@element-plus/icons-vue";
import type { Component } from "vue";

import type { WorkspaceModuleDto, WorkspaceModuleId } from "../../api/types";

defineProps<{ modules: WorkspaceModuleDto[]; activeModuleId: WorkspaceModuleId }>();
defineEmits<{ select: [moduleId: WorkspaceModuleId] }>();

const icons: Record<WorkspaceModuleId, Component> = {
  script: Document,
  assets: Collection,
  production: VideoPlay,
};
</script>

<template>
  <nav class="module-navigation" aria-label="项目工作区">
    <button v-for="module in modules" :key="module.id" type="button" :class="{ active: module.id === activeModuleId }" :data-status="module.status" @click="$emit('select', module.id)">
      <component :is="icons[module.id]" />
      <span><b>{{ module.title }}</b><small>{{ module.blocker || module.nextAction?.label || `${module.progress ?? 0}% 完成` }}</small></span>
      <em v-if="module.attentionCount">{{ module.attentionCount }}</em>
    </button>
  </nav>
</template>

<style scoped>
.module-navigation{height:56px;padding:6px 12px;display:flex;align-items:center;gap:6px;background:#0e1319;border-bottom:1px solid #252d37}.module-navigation button{position:relative;min-width:190px;max-width:280px;min-height:44px;padding:6px 11px;display:grid;grid-template-columns:22px minmax(0,1fr) auto;align-items:center;gap:9px;color:#8290a2;text-align:left;background:transparent;border:1px solid transparent;border-radius:10px;cursor:pointer}.module-navigation button>svg{width:19px}.module-navigation span{min-width:0;display:grid;gap:1px}.module-navigation b{color:#abb7c6;font-size:12px}.module-navigation small{overflow:hidden;color:#667487;font-size:9px;text-overflow:ellipsis;white-space:nowrap}.module-navigation em{min-width:19px;height:19px;padding:0 5px;display:grid;place-items:center;color:#fff;background:#c06162;border-radius:10px;font-size:9px;font-style:normal}.module-navigation button:hover,.module-navigation button:focus-visible{color:#d8e3ef;background:#19212b;border-color:#344352;outline:none}.module-navigation button.active{color:#8fc5f3;background:#1a2b3d;border-color:#365e84}.module-navigation button.active b{color:#eef7ff}.module-navigation button[data-status=blocked] small{color:#cf887e}.module-navigation button[data-status=needs_review] small,.module-navigation button[data-status=stale] small{color:#d2a665}@media(max-width:850px){.module-navigation button{min-width:44px;max-width:none;flex:1;grid-template-columns:22px minmax(0,1fr);padding:6px 9px}.module-navigation small{display:none}.module-navigation em{position:absolute;top:2px;right:2px}}@media(max-width:560px){.module-navigation b{font-size:11px}.module-navigation button{gap:5px}}
</style>
