<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "../api/client";
import type { ProjectDto, ValidationRunDto, ValidationRunPreviewDto } from "../api/types";

const preview = ref<ValidationRunPreviewDto | null>(null);
const run = ref<ValidationRunDto | null>(null);
const projects = ref<ProjectDto[]>([]);
const authorizing = ref(false);
const error = ref("");
const usedTotal = computed(() => run.value
  && run.value.manifestHash === preview.value?.manifestHash
  ? Object.values(run.value.usage).reduce((sum, count) => sum + count, 0)
  : 0);
const currentRun = computed(() => run.value?.manifestHash === preview.value?.manifestHash ? run.value : null);
const canonLabels: Record<string, string> = {
  episode_child: "儿童身份",
  episode_cat: "猫咪身份",
  pair_scale: "人猫比例",
  style_board: "净化画风板",
};

async function load() {
  try {
    [preview.value, run.value, projects.value] = await Promise.all([
      api.previewValidationRun(),
      api.currentValidationRun(),
      api.projects(),
    ]);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "验收清单读取失败";
  }
}

async function authorize() {
  if (!preview.value) return;
  authorizing.value = true;
  try {
    run.value = await api.authorizeValidationRun(preview.value.manifestHash);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "首批运行授权失败";
  } finally {
    authorizing.value = false;
  }
}

async function pause() {
  if (run.value) run.value = await api.pauseValidationRun(run.value.id);
}

function projectFor(topic: string) {
  const canonProfileId = preview.value?.canon.profileId;
  return projects.value.find((project) => (
    project.theme === topic && project.canonProfileId === canonProfileId
  ));
}

onMounted(load);
</script>

<template>
  <main class="page validation-page">
    <section class="page-heading"><div><p class="eyebrow">Paid release gate</p><h1>首批 3 主题真实验收</h1><p class="subtitle">这里只冻结授权和显示额度；每一步仍需进入普通五步页面由用户点击完成。</p></div><RouterLink class="secondary" to="/settings">返回运行设置</RouterLink></section>
    <p v-if="error" class="notice error">{{ error }}</p>
    <section v-if="preview" class="manifest card" data-testid="validation-manifest">
      <header><div><p class="eyebrow">Frozen manifest</p><h2>一次性付费授权清单</h2></div><span class="pill" :class="{ good: currentRun?.status === 'authorized', warn: currentRun?.status === 'paused' }">{{ currentRun?.status ?? "未授权" }}</span></header>
      <p v-if="run && !currentRun" class="notice warn">检测到旧 Manifest 授权 {{ run.manifestHash.slice(0, 12) }}；本次 10/4 清单必须重新授权，旧额度不会复用。</p>
      <p v-for="reason in preview.blockingReasons" :key="reason" class="notice error">付费授权已阻断：{{ reason }}</p>
      <div class="manifest-facts"><div><small>主题</small><b>{{ preview.topics.length }} 个固定原创主题</b></div><div><small>规格</small><b>{{ preview.durationSeconds }} 秒 · {{ preview.resolution }} · {{ preview.aspectRatio }}</b></div><div><small>参考</small><b>完整五张，服务端固定顺序</b></div><div><small>预算参考</small><b>¥{{ preview.targetBudgetCny }} · 未计价付费调用</b></div><div><small>总调用上限</small><b>{{ preview.totalCallLimit }}</b></div><div><small>视频上限</small><b>{{ preview.maximumVideoCalls }}</b></div></div>
      <ul class="topics"><li v-for="topic in preview.topics" :key="topic">{{ topic }}</li></ul>
      <section class="repair-snapshot"><p class="eyebrow">Frozen repair</p><h3>{{ preview.repair.topic }} · [{{ preview.repair.issueRange.startFrame }}, {{ preview.repair.issueRange.endFrame }})</h3><p>{{ preview.repair.prompt }}</p></section>
      <section class="canon-snapshot" data-testid="validation-canon-snapshot">
        <header><div><p class="eyebrow">Frozen Canon</p><h3>6–7 岁 · 120 cm · Revision {{ preview.canon.version }}</h3></div><span class="pill good">4/4</span></header>
        <div class="hash"><span>Profile SHA256</span><code>{{ preview.canon.profileHash }}</code></div>
        <div class="canon-references"><div v-for="reference in preview.canon.references" :key="reference.role"><b>{{ canonLabels[reference.role] }}</b><code>{{ reference.sha256 }}</code></div></div>
      </section>
      <div class="models"><span v-for="(model, role) in preview.models" :key="role"><small>{{ role }}</small><code>{{ model }}</code></span></div>
      <table><thead><tr><th>调用类型</th><th>上限</th><th>已用</th></tr></thead><tbody><tr v-for="(limit, kind) in preview.callLimits" :key="kind"><td>{{ kind }}</td><td>{{ limit }}</td><td>{{ currentRun?.usage[kind] ?? 0 }}</td></tr></tbody><tfoot><tr><th>总计</th><th>{{ preview.totalCallLimit }}</th><th>{{ usedTotal }}</th></tr></tfoot></table>
      <div class="hash"><span>Manifest SHA256</span><code>{{ preview.manifestHash }}</code></div>
      <button v-if="!currentRun || ['cancelled', 'completed'].includes(currentRun.status)" class="primary authorize" :disabled="authorizing || !preview.authorizationReady" @click="authorize">{{ authorizing ? "授权中…" : "授权首批运行（最多 10 次 / 4 次视频，含 1 次片段修复）" }}</button>
      <button v-else-if="currentRun.status === 'authorized'" class="secondary authorize" @click="pause">暂停剩余付费调用</button>
    </section>
    <section v-if="preview" class="topic-grid">
      <article v-for="(topic, index) in preview.topics" :key="topic" class="card topic"><span>0{{ index + 1 }}</span><div><h2>{{ topic }}</h2><p>12 秒 · 3 个约 4 秒镜头 · 一次视频生成</p></div><RouterLink v-if="projectFor(topic)" class="secondary" :to="`/projects/${projectFor(topic)!.id}/planner`">继续普通五步流程</RouterLink><RouterLink v-else class="primary" :to="{ path: '/projects', query: { topic } }">去新建短片</RouterLink></article>
    </section>
  </main>
</template>

<style scoped>
.manifest { padding: 26px; }
.manifest header, .topic { display: flex; justify-content: space-between; align-items: center; gap: 18px; }
.manifest-facts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 20px 0; }
.manifest-facts div, .models span { padding: 12px; border-radius: 11px; background: #f4eee7; }
.manifest-facts small, .manifest-facts b, .models small, .models code { display: block; }
.manifest-facts small, .models small { color: var(--muted); font-size: 9px; }
.manifest-facts b { margin-top: 5px; font-size: 12px; }
.topics { display: flex; gap: 8px; padding: 0; list-style: none; }
.topics li { padding: 7px 10px; border-radius: 999px; background: var(--sage-soft); font-size: 11px; }
.canon-snapshot { margin: 16px 0; padding: 16px; border: 1px solid var(--line); border-radius: 13px; background: #fff; }
.canon-snapshot header { margin-bottom: 12px; }
.canon-snapshot h3 { margin: 3px 0 0; font-size: 15px; }
.canon-references { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.canon-references div { display: grid; gap: 5px; padding: 9px; border-radius: 9px; background: #f4eee7; }
.canon-references b { font-size: 11px; }
.canon-references code { overflow: hidden; text-overflow: ellipsis; font-size: 9px; }
.models { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.models code { margin-top: 5px; overflow: hidden; text-overflow: ellipsis; font-size: 9px; }
table { width: 100%; margin: 18px 0; border-collapse: collapse; font-size: 12px; }
th, td { padding: 9px; border-bottom: 1px solid var(--line); text-align: left; }
.hash { display: grid; grid-template-columns: 130px 1fr; gap: 10px; color: var(--muted); font-size: 10px; }
.hash code { overflow: hidden; text-overflow: ellipsis; }
.authorize { width: 100%; margin-top: 16px; }
.topic-grid { display: grid; gap: 12px; margin-top: 20px; }
.topic { padding: 18px 22px; }
.topic > span { color: var(--accent); font: 600 22px Georgia, serif; }
.topic > div { flex: 1; }
.topic h2 { margin: 0 0 4px; font-size: 18px; }
.topic p { margin: 0; color: var(--muted); font-size: 11px; }
</style>
