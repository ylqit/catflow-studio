<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

import { api } from "../../api/client";
import type { AssetDto, GenerationPreviewDto, JobDto, WorkspaceDto } from "../../api/types";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const preview = ref<GenerationPreviewDto | null>(null);
const currentJob = ref<JobDto | null>(null);
const videos = ref<AssetDto[]>([]);
const loadingPreview = ref(false);
const submitting = ref(false);
const error = ref("");
let timer: number | null = null;

async function loadVideos() {
  videos.value = (await api.assets(props.projectId)).filter((asset) => asset.mediaType === "video");
}

async function prepare() {
  loadingPreview.value = true;
  error.value = "";
  try {
    preview.value = await api.previewVideo(props.projectId, 4);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "生成预览不可用";
  } finally {
    loadingPreview.value = false;
  }
}

async function submit() {
  if (!preview.value) return;
  submitting.value = true;
  try {
    currentJob.value = await api.createVideoJob(props.projectId, {
      expectedInputHash: preview.value.inputHash,
      expectedCostMicros: preview.value.expectedCostMicros,
      idempotencyKey: crypto.randomUUID(),
    });
    startPolling();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "视频任务提交失败";
  } finally {
    submitting.value = false;
  }
}

function startPolling() {
  if (timer !== null) window.clearInterval(timer);
  timer = window.setInterval(async () => {
    if (!currentJob.value) return;
    currentJob.value = await api.job(currentJob.value.id);
    if (["succeeded", "failed", "cancelled"].includes(currentJob.value.status)) {
      if (timer !== null) window.clearInterval(timer);
      timer = null;
      await loadVideos();
    }
  }, 1500);
}

async function chooseVideo(assetId: string) {
  await api.selectAsset(props.projectId, "video", assetId);
  emit("changed");
}

onMounted(loadVideos);
onBeforeUnmount(() => { if (timer !== null) window.clearInterval(timer); });
</script>

<template>
  <section class="generation-layout">
    <div class="generation-main">
      <div class="preview-card card">
        <header><div><p class="eyebrow">Frozen generation input</p><h2>付费前先看清输入</h2></div><button class="secondary" :disabled="loadingPreview" @click="prepare">{{ loadingPreview ? "编译中…" : "生成预览" }}</button></header>
        <p v-if="error" class="notice error">{{ error }}</p>
        <div v-if="!preview" class="empty preview-empty"><div>▦</div><p>服务端会根据当前 Story、Shot Plan 和五个资产槽位编译冻结输入。</p></div>
        <template v-else>
          <div class="model-strip"><span><small>Provider</small><b>{{ preview.provider }}</b></span><span><small>Model</small><b>{{ preview.model }}</b></span><span><small>预计费用</small><b>¥ {{ (preview.expectedCostMicros / 1_000_000).toFixed(4) }}</b></span></div>
          <div class="prompt-block"><label>正式 Prompt</label><p>{{ preview.prompt }}</p></div>
          <div class="reference-list">
            <div v-for="reference in preview.references" :key="reference.role" :class="{ omitted: !reference.included }"><span class="priority">{{ reference.priority }}</span><b>{{ reference.role }}</b><span>{{ reference.included ? "已冻结" : `省略：${reference.omittedReason}` }}</span></div>
          </div>
          <div class="hash-row"><span>Input hash</span><code>{{ preview.inputHash }}</code></div>
          <button class="primary submit-generation" :disabled="submitting" @click="submit"><span v-if="submitting" class="spinner" />确认输入并提交视频任务</button>
        </template>
      </div>

      <div class="candidates-card card">
        <header><div><p class="eyebrow">Video candidates</p><h2>候选与当前选择</h2></div><span v-if="currentJob" class="pill" :class="{ good: currentJob.status === 'succeeded', warn: currentJob.status === 'failed' }">{{ currentJob.status }}</span></header>
        <div v-if="!videos.length" class="empty">{{ currentJob ? "Worker 正在继续原任务；浏览器可以安全关闭。" : "尚无视频候选。" }}</div>
        <div v-else class="video-grid">
          <article v-for="asset in videos" :key="asset.id" :class="{ chosen: workspace.selections.video?.id === asset.id }">
            <video controls preload="metadata" :src="`/api/v1/assets/${asset.id}/content`" />
            <footer><span>{{ asset.sha256.slice(0, 10) }}</span><button v-if="workspace.selections.video?.id !== asset.id" class="secondary" @click="chooseVideo(asset.id)">选择此版本</button><span v-else class="pill good">当前视频</span></footer>
          </article>
        </div>
      </div>
    </div>
    <aside class="safety-card card">
      <p class="eyebrow">Generation safety</p><h2>不会悄悄重提</h2>
      <ol><li><b>1</b><span>预览确认输入哈希和预计费用</span></li><li><b>2</b><span>相同幂等键只产生一个 Job</span></li><li><b>3</b><span>Provider task ID 获得后立即写入 PostgreSQL</span></li><li><b>4</b><span>重启后只轮询、下载、取消或对账</span></li></ol>
      <p class="notice">参考固定顺序：儿童 → 猫咪 → 同框比例 → 环境 → 净化画风板。前端不能重排，style_source 永远排除。</p>
    </aside>
  </section>
</template>

<style scoped>
.generation-layout { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 20px; align-items: start; }
.generation-main { display: grid; gap: 20px; }
.preview-card, .candidates-card, .safety-card { padding: 23px; }
.preview-card > header, .candidates-card > header { display: flex; justify-content: space-between; align-items: start; }
header h2 { margin-bottom: 0; font-size: 20px; }
.preview-empty { padding: 45px; }
.preview-empty > div { font-size: 34px; color: #c47a61; }
.model-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 20px 0; }
.model-strip span { padding: 12px; border-radius: 12px; background: #f4eee7; }
.model-strip small, .model-strip b { display: block; }
.model-strip small { margin-bottom: 4px; color: var(--muted); font-size: 9px; text-transform: uppercase; letter-spacing: .1em; }
.model-strip b { font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
.prompt-block { padding: 15px; border: 1px solid var(--line); border-radius: 13px; background: #fff; }
.prompt-block label { color: #b35f49; font-size: 10px; font-weight: 800; }
.prompt-block p { margin: 8px 0 0; color: #615a54; font-size: 12px; line-height: 1.7; }
.reference-list { display: grid; gap: 7px; margin: 15px 0; }
.reference-list > div { display: grid; grid-template-columns: 28px 145px 1fr; align-items: center; padding: 9px; border-radius: 9px; background: var(--sage-soft); color: #58705c; font-size: 11px; }
.reference-list > div.omitted { background: #f3ece4; color: #8a8178; text-decoration: none; opacity: .75; }
.priority { width: 20px; height: 20px; display: grid; place-items: center; border-radius: 50%; background: #ffffffaa; font-size: 9px; }
.hash-row { display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: center; color: var(--muted); font-size: 10px; }
.hash-row code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.submit-generation { width: 100%; margin-top: 17px; }
.video-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
.video-grid article { padding: 7px; border: 1px solid var(--line); border-radius: 13px; background: #f4eee7; }
.video-grid article.chosen { border: 2px solid #79957d; }
.video-grid video { width: 100%; aspect-ratio: 9 / 16; border-radius: 9px; background: #27231f; }
.video-grid footer { display: flex; justify-content: space-between; align-items: center; gap: 6px; padding: 7px 3px 1px; color: var(--muted); font-size: 9px; }
.video-grid button { min-height: 29px; padding: 0 8px; font-size: 9px; }
.safety-card { position: sticky; top: 96px; }
.safety-card ol { list-style: none; padding: 0; margin: 20px 0; display: grid; gap: 13px; }
.safety-card li { display: grid; grid-template-columns: 27px 1fr; align-items: center; gap: 9px; color: var(--muted); font-size: 11px; line-height: 1.4; }
.safety-card li b { width: 25px; height: 25px; display: grid; place-items: center; border-radius: 8px; color: #b75e47; background: #f7e6de; }
</style>
