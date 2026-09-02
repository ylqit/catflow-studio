<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { api } from "../../api/client";
import type { AssetDto, AssetGenerationKind, AssetGenerationPreviewDto, AssetSlot, JobDto, RuntimeBootstrapDto, WorkspaceDto } from "../../api/types";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { projectJobEvent } from "../../projectJobEvents";
import { useUiStore } from "../../stores/ui";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const store = useUiStore();
const assets = ref<AssetDto[]>([]);
const busySlot = ref<AssetSlot | null>(null);
const error = ref("");
const runtime = ref<RuntimeBootstrapDto | null>(null);
const generationPreview = ref<AssetGenerationPreviewDto | null>(null);
const currentJob = ref<JobDto | null>(null);
let events: EventSource | null = null;

const slots: Array<{ id: AssetGenerationKind; order: string; title: string; responsibility: string }> = [
  { id: "episode_child", order: "01", title: "本集儿童设计", responsibility: "锁定 6–7 岁、约 1.2 米、约 4.5–5 头身、齐下颌短发与脸型" },
  { id: "episode_cat", order: "02", title: "本集猫咪设计", responsibility: "锁定灰白分区、虎斑、眼鼻口、环纹尾巴与四足" },
  { id: "pair_scale", order: "03", title: "人猫同框比例", responsibility: "只负责可信的人猫尺寸与站位关系" },
  { id: "environment", order: "04", title: "当前环境参考", responsibility: "只控制空间与光线，不改变角色身份" },
  { id: "style_board", order: "05", title: "Canon v4 净化画风板", responsibility: "只控制线条、材质、色彩与柔和暖光" },
];

const grouped = computed(() => Object.fromEntries(slots.map((slot) => [slot.id, assets.value.filter((asset) => asset.role === slot.id)])) as Record<AssetSlot, AssetDto[]>);
const currentJobLabel = computed(() => currentJob.value?.kind === "diagnose_image" ? "图片质量诊断" : "共享环境生成");

async function load() {
  try {
    [assets.value, runtime.value] = await Promise.all([
      api.assets(props.projectId),
      api.runtime(),
    ]);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "资产读取失败";
  }
}

async function upload(slot: AssetSlot, event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  busySlot.value = slot;
  error.value = "";
  try {
    await api.uploadAsset(props.projectId, slot, file);
    await load();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "上传失败";
  } finally {
    busySlot.value = null;
    input.value = "";
  }
}

async function select(slot: AssetSlot, assetId: string) {
  busySlot.value = slot;
  try {
    await api.selectAsset(props.projectId, slot, assetId);
    emit("changed");
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "选择失败";
  } finally {
    busySlot.value = null;
  }
}

async function generateEnvironment() {
  if (busySlot.value) return;
  busySlot.value = "environment";
  error.value = "";
  try {
    const prepared = await api.previewAssetGeneration(props.projectId, "environment");
    generationPreview.value = prepared;
    const scope = `asset-generation:${props.projectId}:environment`;
    currentJob.value = await api.createAssetGeneration(props.projectId, {
      kind: "environment",
      expectedInputHash: prepared.inputHash,
      idempotencyKey: pendingIdempotencyKey(scope, prepared.inputHash),
    });
    settleIdempotencyKey(scope, prepared.inputHash);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "候选生成失败";
  } finally {
    busySlot.value = null;
  }
}

async function diagnose(asset: AssetDto) {
  if (busySlot.value) return;
  error.value = "";
  busySlot.value = asset.role as AssetGenerationKind;
  const scope = `asset-diagnosis:${props.projectId}:${asset.id}`;
  try {
    currentJob.value = await api.diagnoseAsset(
      props.projectId,
      asset.id,
      pendingIdempotencyKey(scope, asset.sha256),
    );
    settleIdempotencyKey(scope, asset.sha256);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "诊断失败";
  } finally {
    busySlot.value = null;
  }
}

function reportStatus(asset: AssetDto): string | null {
  const report = asset.metadata.qualityReport as { style?: string } | undefined;
  return report?.style ?? null;
}

function connectEvents() {
  events = new EventSource(api.eventsUrl(store.lastEventId));
  const refresh = async (event: Event) => {
    const message = event as MessageEvent;
    if (message.lastEventId) store.lastEventId = Number(message.lastEventId);
    const jobEvent = projectJobEvent(message, props.projectId);
    if (!jobEvent) return;
    if (currentJob.value?.id === jobEvent.jobId) currentJob.value = await api.job(jobEvent.jobId);
    if (jobEvent.eventType === "job.succeeded") await load();
  };
  for (const type of ["job.succeeded", "job.failed", "job.submission_unknown"]) {
    events.addEventListener(type, (event) => { void refresh(event); });
  }
}

onMounted(async () => { await load(); connectEvents(); });
onBeforeUnmount(() => events?.close());
</script>

<template>
  <section class="assets-layout">
    <div class="asset-intro card">
      <p class="eyebrow">Canon v4</p>
      <h2>身份优先，画风分责</h2>
      <p>四个 Canon 槽位全局只读继承；项目只生成、诊断并选择当前环境参考。</p>
      <div class="canon-portrait"><span class="child">⌒◡⌒</span><span class="cat">= ᵔᴗᵔ =</span></div>
      <ul>
        <li><b>儿童</b><span>同一位 6–7 岁、约 1.2 米短发儿童</span></li>
        <li><b>猫咪</b><span>同一只灰白虎斑猫</span></li>
        <li><b>画风</b><span>二维柔和数字插画</span></li>
        <li><b>隔离</b><span>style_source 永不进入 Provider</span></li>
      </ul>
      <p class="notice">AI 身份、画风和结构诊断只提供建议；只有损坏、无法解码或格式错误会阻止选择。</p>
    </div>

    <div class="slot-list">
      <p v-if="error" class="notice error">{{ error }}</p>
      <p v-if="currentJob" class="notice" :class="{ error: ['failed', 'submission_unknown'].includes(currentJob.status) }">
        {{ currentJobLabel }} · {{ currentJob.status }}（状态由 SSE 恢复）
        <span v-if="currentJob.error"> · {{ currentJob.error.code }}：{{ currentJob.error.message }}</span>
        <span v-if="currentJob.actualUsage"> · 实际 usage {{ JSON.stringify(currentJob.actualUsage) }}</span>
        <span v-if="currentJob.billingStatus === 'unpriced'"> · 费用待核价</span>
        <span v-else-if="currentJob.actualCostMicros != null"> · ¥{{ (currentJob.actualCostMicros / 1_000_000).toFixed(4) }}</span>
      </p>
      <article v-for="slot in slots" :key="slot.id" class="asset-slot card">
        <div class="slot-order">{{ slot.order }}</div>
        <div class="slot-main">
          <header><div><h3>{{ slot.title }}</h3><p>{{ slot.responsibility }}</p></div><span class="pill" :class="{ good: workspace.selections[slot.id] }">{{ workspace.selections[slot.id] ? "已选择" : "待选择" }}</span></header>
          <div v-if="slot.id !== 'environment'" class="candidates inherited">
            <div v-if="workspace.selections[slot.id]" class="candidate selected">
              <img :src="`/api/v1/assets/${workspace.selections[slot.id]!.id}/content`" :alt="`${slot.title}全局继承`" />
              <span class="current-badge">全局 Canon · 只读继承</span>
            </div>
            <p v-else class="notice warn">请先到“模型与运行设置”上传并发布 Canon v4。</p>
          </div>
          <div v-else class="candidates">
            <div v-if="workspace.selections[slot.id]" class="candidate selected">
              <img :src="`/api/v1/assets/${workspace.selections[slot.id]!.id}/content`" :alt="`${slot.title}当前选择`" />
              <span class="current-badge">共享环境 · 全局继承</span>
            </div>
            <div v-for="asset in grouped[slot.id].filter((candidate) => candidate.id !== workspace.selections[slot.id]?.id)" :key="asset.id" class="candidate">
              <img :src="`/api/v1/assets/${asset.id}/content`" :alt="slot.title" />
              <span v-if="reportStatus(asset)" class="quality-badge">诊断 {{ reportStatus(asset) }}</span>
              <button class="diagnose-button" :disabled="busySlot === slot.id" @click="diagnose(asset)">诊断</button>
              <button v-if="workspace.selections[slot.id]?.id !== asset.id" class="secondary" :disabled="busySlot === slot.id" @click="select(slot.id, asset.id)">选择</button>
            </div>
            <label class="upload-candidate" :class="{ busy: busySlot === slot.id }">
              <input type="file" accept="image/png,image/jpeg,image/webp" @change="upload(slot.id, $event)" />
              <b>{{ busySlot === slot.id ? "处理中…" : "＋" }}</b><span>上传候选</span>
            </label>
            <button class="generate-candidate" :disabled="busySlot === slot.id" @click="generateEnvironment"><b>✦</b><span>生成新的共享环境候选</span></button>
          </div>
          <section v-if="slot.id === 'environment'" class="paid-model-note">
            <b>{{ runtime?.provider.name ?? "Ark" }} · {{ runtime?.provider.imageModel ?? "图片模型" }} · Ark 付费模型</b>
            <span>点击生成或诊断会直接创建一个任务；完成后显示实际 usage，未配置费率时标记为待核价。</span>
            <details v-if="generationPreview"><summary>查看最近一次冻结输入</summary><p>{{ generationPreview.prompt }}</p><code>{{ generationPreview.inputHash }}</code></details>
          </section>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.assets-layout { display: grid; grid-template-columns: 310px minmax(0, 1fr); gap: 20px; align-items: start; }
.asset-intro { position: sticky; top: 96px; padding: 24px; }
.asset-intro > p:not(.eyebrow, .notice) { color: var(--muted); line-height: 1.65; font-size: 13px; }
.canon-portrait { height: 180px; margin: 20px 0; border-radius: 16px; display: flex; align-items: end; justify-content: center; gap: 22px; padding-bottom: 38px; color: #fff; background: radial-gradient(circle at 60% 20%, #f8e8cd, transparent 30%), linear-gradient(145deg, #b8c6b5, #788e7c); font: 600 20px Georgia, serif; }
.canon-portrait .cat { font-size: 17px; }
.asset-intro ul { list-style: none; padding: 0; margin: 0 0 18px; display: grid; gap: 9px; }
.asset-intro li { display: grid; grid-template-columns: 42px 1fr; font-size: 12px; color: var(--muted); }
.asset-intro li b { color: #b55f49; }
.slot-list { display: grid; gap: 14px; }
.asset-slot { display: grid; grid-template-columns: 64px 1fr; padding: 20px; }
.slot-order { font: 500 28px Georgia, serif; color: #d3c7ba; }
.slot-main header { display: flex; justify-content: space-between; gap: 15px; }
.slot-main h3 { margin: 0 0 5px; font: 600 17px Georgia, "Songti SC", serif; }
.slot-main header p { margin: 0; color: var(--muted); font-size: 11px; }
.candidates { display: flex; gap: 12px; margin-top: 16px; overflow-x: auto; padding-bottom: 3px; }
.candidate, .upload-candidate, .generate-candidate { flex: 0 0 126px; height: 134px; border: 1px solid var(--line); border-radius: 13px; overflow: hidden; position: relative; background: var(--paper-2); }
.candidate img { width: 100%; height: 100%; object-fit: cover; display: block; }
.candidate button { position: absolute; bottom: 7px; left: 7px; right: 7px; min-height: 30px; padding: 0; font-size: 11px; }
.candidate .diagnose-button { left: auto; right: 7px; top: 7px; bottom: auto; width: 42px; min-height: 24px; border: 0; border-radius: 7px; color: white; background: #4e4740bb; cursor: pointer; font-size: 9px; }
.quality-badge { position: absolute; left: 7px; top: 7px; padding: 5px 7px; border-radius: 7px; color: white; background: #66826bcc; font-size: 9px; }
.candidate .current-badge { position: absolute; left: 7px; bottom: 7px; padding: 5px 8px; border-radius: 7px; color: white; background: #5e775fcc; font-size: 10px; }
.candidate.selected { border: 2px solid #76947b; }
.upload-candidate { display: grid; place-content: center; justify-items: center; gap: 4px; border-style: dashed; color: #8b8177; cursor: pointer; }
.upload-candidate input { display: none; }
.upload-candidate b { font-size: 24px; font-weight: 300; }
.upload-candidate span { font-size: 11px; }
.upload-candidate:hover { border-color: #d47a60; color: #b95e48; }
.upload-candidate.busy { opacity: .5; pointer-events: none; }
.generate-candidate { display: grid; place-content: center; justify-items: center; gap: 5px; color: #a25c48; background: #f9eae3; cursor: pointer; }
.generate-candidate b { font-size: 22px; }
.generate-candidate span { font-size: 10px; }
.candidates.inherited { overflow: visible; }
.paid-model-note { display: grid; gap: 5px; margin-top: 12px; padding: 12px 14px; border-radius: 12px; background: #fff7ef; color: var(--muted); font-size: 10px; }
.paid-model-note b { color: var(--ink); }.paid-model-note details { margin-top: 5px; }.paid-model-note p { line-height: 1.6; }.paid-model-note code { overflow-wrap: anywhere; }
</style>
