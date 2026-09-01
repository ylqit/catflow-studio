<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const isWorkspace = computed(() => typeof route.params.projectId === "string");
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink class="brand" to="/projects" aria-label="返回项目列表">
        <span class="brand-mark">CF</span>
        <span>
          <strong>CatFlow</strong>
          <small>一人一猫生活短片工作室</small>
        </span>
      </RouterLink>
      <div class="topbar-center" :class="{ visible: isWorkspace }">
        原创日常 · 9:16 · 8–15 秒
      </div>
      <nav class="topbar-actions" aria-label="全局导航">
        <RouterLink to="/projects">项目</RouterLink>
        <RouterLink to="/settings">运行设置</RouterLink>
        <span class="local-badge"><i /> 本机模式</span>
      </nav>
    </header>
    <RouterView />
  </div>
</template>
