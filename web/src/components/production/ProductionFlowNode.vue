<script setup lang="ts">
import { Handle, Position } from "@vue-flow/core";
import { Collection, Document, Film, List, MagicStick, VideoPlay } from "@element-plus/icons-vue";
import type { Component } from "vue";

import type { ProductionFlowNodeDto, ProductionFlowNodeKind } from "../../api/types";

const props = defineProps<{ artifact: ProductionFlowNodeDto; selected?: boolean; activate: (artifact: ProductionFlowNodeDto) => void }>();
const icons: Record<ProductionFlowNodeKind, Component> = {
  script: Document,
  director_plan: MagicStick,
  assets: Collection,
  storyboard_table: List,
  storyboard: Film,
  workbench: VideoPlay,
};
const kindLabels: Record<ProductionFlowNodeKind, string> = {
  script: "SCRIPT",
  director_plan: "DIRECTOR PLAN",
  assets: "ASSETS",
  storyboard_table: "STORYBOARD TABLE",
  storyboard: "STORYBOARD",
  workbench: "WORKBENCH",
};
</script>

<template>
  <article class="production-node" :class="{ selected }" :data-status="artifact.status">
    <Handle type="target" :position="Position.Left" />
    <header><span><component :is="icons[artifact.kind]" /></span><div><small>{{ kindLabels[artifact.kind] }}</small><h3>{{ artifact.title }}</h3></div><i /></header>
    <p>{{ artifact.subtitle }}</p>
    <div v-if="Array.isArray(artifact.data.previewUrls) && artifact.data.previewUrls.length" class="preview-strip">
      <img v-for="url in (artifact.data.previewUrls as string[]).slice(0, 3)" :key="url" :src="url" alt="" />
    </div>
    <button type="button" @click.stop="props.activate(artifact)">{{ artifact.kind === 'workbench' ? '打开视频工作台' : artifact.kind === 'storyboard_table' || artifact.kind === 'director_plan' ? '编辑分镜' : artifact.kind === 'script' ? '打开剧本' : artifact.kind === 'assets' ? '管理资产' : '查看画面' }}</button>
    <Handle type="source" :position="Position.Right" />
  </article>
</template>

<style scoped>
.production-node{box-sizing:border-box;width:300px;min-height:164px;padding:15px;display:flex;flex-direction:column;gap:11px;color:#dbe5f0;background:#171e26;border:1px solid #33404d;border-radius:14px;box-shadow:0 12px 30px rgb(0 0 0 / 18%)}.production-node.selected{border-color:#5c96c7;box-shadow:0 0 0 2px rgb(83 145 199 / 20%),0 16px 38px rgb(0 0 0 / 28%)}.production-node[data-status=blocked]{border-color:#73484a}.production-node[data-status=stale],.production-node[data-status=needs_review]{border-color:#715d39}.production-node header{display:grid;grid-template-columns:38px minmax(0,1fr) 8px;gap:10px;align-items:center}.production-node header>span{width:38px;height:38px;display:grid;place-items:center;color:#9bc8ee;background:#223347;border-radius:10px}.production-node header svg{width:19px}.production-node header div{min-width:0}.production-node small{display:block;color:#65788d;font-size:8px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.production-node h3{margin:3px 0 0;overflow:hidden;font-size:15px;text-overflow:ellipsis;white-space:nowrap}.production-node header i{width:8px;height:8px;background:#54aa80;border-radius:50%}.production-node[data-status=blocked] header i{background:#cc6f70}.production-node[data-status=stale] header i,.production-node[data-status=needs_review] header i{background:#d5a653}.production-node p{margin:0;color:#8998aa;font-size:11px;line-height:1.5}.preview-strip{height:48px;display:flex;gap:5px}.preview-strip img{width:48px;height:48px;object-fit:cover;background:#10151a;border:1px solid #303b46;border-radius:7px}.production-node button{min-height:44px;margin-top:auto;color:#d7e5f2;background:#212b35;border:1px solid #3a4a5b;border-radius:9px;cursor:pointer}.production-node button:hover,.production-node button:focus-visible{color:#fff;background:#29415a;border-color:#5183ad;outline:2px solid rgb(82 143 194 / 18%)}.production-node :deep(.vue-flow__handle){width:9px;height:9px;background:#6e879f;border:2px solid #151b22}
</style>
