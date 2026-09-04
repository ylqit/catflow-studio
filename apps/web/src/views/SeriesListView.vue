<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api } from "../api/client";
import type { StorySeriesDto } from "../api/types";

const series = ref<StorySeriesDto[]>([]);
const loading = ref(true);
const error = ref("");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    series.value = await api.storySeries();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "系列暂时无法读取。";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <main class="page series-list-page">
    <section class="page-heading">
      <div><h1>系列</h1><p class="subtitle">先规划整季路线，再按需制作每一集。</p></div>
      <RouterLink class="primary action-link" to="/series/new">新建系列</RouterLink>
    </section>
    <p v-if="error" class="notice error">{{ error }} <button @click="load">重新加载</button></p>
    <section v-if="loading" class="card empty">正在整理系列…</section>
    <section v-else-if="series.length === 0" class="card empty">
      <h2>还没有系列</h2><p>从一个主题、世界设定和集数开始。</p>
      <RouterLink class="primary action-link" to="/series/new">创建第一个系列</RouterLink>
    </section>
    <section v-else class="series-grid">
      <RouterLink v-for="item in series" :key="item.id" class="card series-card" :to="`/series/${item.id}`">
        <div class="series-card-head"><span>{{ item.narrativeMode === "continuous" ? "连续剧情" : item.narrativeMode === "lightly_serialized" ? "轻连续" : "单元故事" }}</span><b>{{ item.plannedEpisodeCount }} 集</b></div>
        <h2>{{ item.title }}</h2><p>{{ item.premise }}</p>
        <footer><span>已规划 {{ item.plannedCount }}</span><span>制作中 {{ item.materializedCount }}</span><span>已完成 {{ item.completedCount }}</span></footer>
      </RouterLink>
    </section>
  </main>
</template>

<style scoped>
.action-link { min-height: 40px; display: inline-flex; align-items: center; }.series-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }.series-card { min-height: 230px; padding: 24px; display: flex; flex-direction: column; }.series-card-head, .series-card footer { display: flex; justify-content: space-between; gap: 10px; color: var(--muted); font-size: 12px; }.series-card-head b { color: var(--accent-dark); }.series-card h2 { margin: 22px 0 10px; }.series-card p { color: var(--muted); line-height: 1.65; display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }.series-card footer { margin-top: auto; padding-top: 18px; border-top: 1px solid var(--line); }.notice button { border: 0; background: transparent; color: inherit; text-decoration: underline; cursor: pointer; }
</style>
