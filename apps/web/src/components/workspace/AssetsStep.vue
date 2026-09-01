<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "../../api/client";
import type { AssetDto, AssetGenerationKind, AssetSlot, WorkspaceDto } from "../../api/types";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const assets = ref<AssetDto[]>([]);
const busySlot = ref<AssetSlot | null>(null);
const error = ref("");

const slots: Array<{ id: AssetGenerationKind; order: string; title: string; responsibility: string }> = [
  { id: "episode_child", order: "01", title: "本集儿童设计", responsibility: "锁定 8–9 岁、齐下颌短发、脸型与身体比例" },
  { id: "episode_cat", order: "02", title: "本集猫咪设计", responsibility: "锁定灰白分区、虎斑、眼鼻口、环纹尾巴与四足" },
  { id: "pair_scale", order: "03", title: "人猫同框比例", responsibility: "只负责可信的人猫尺寸与站位关系" },
  { id: "environment", order: "04", title: "当前环境参考", responsibility: "只控制空间与光线，不改变角色身份" },
  { id: "style_board", order: "05", title: "Canon v4 净化画风板", responsibility: "只控制线条、材质、色彩与柔和暖光" },
];

const grouped = computed(() => Object.fromEntries(slots.map((slot) => [slot.id, assets.value.filter((asset) => asset.role === slot.id)])) as Record<AssetSlot, AssetDto[]>);

async function load() {
  try {
    assets.value = await api.assets(props.projectId);
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

async function generate(slot: AssetGenerationKind) {
  busySlot.value = slot;
  error.value = "";
  try {
    const preview = await api.previewAssetGeneration(props.projectId, slot);
    let job = await api.createAssetGeneration(props.projectId, {
      kind: slot,
      expectedInputHash: preview.inputHash,
      expectedCostMicros: preview.expectedCostMicros,
      idempotencyKey: crypto.randomUUID(),
    });
    for (let attempt = 0; attempt < 30; attempt += 1) {
      if (["succeeded", "failed", "cancelled"].includes(job.status)) break;
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      job = await api.job(job.id);
    }
    if (job.status === "failed") throw new Error(job.error?.message || "生成失败");
    await load();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "候选生成失败";
  } finally {
    busySlot.value = null;
  }
}

async function diagnose(asset: AssetDto) {
  busySlot.value = asset.role as AssetGenerationKind;
  try {
    let job = await api.diagnoseAsset(props.projectId, asset.id);
    for (let attempt = 0; attempt < 30; attempt += 1) {
      if (["succeeded", "failed", "cancelled"].includes(job.status)) break;
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      job = await api.job(job.id);
    }
    await load();
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

onMounted(load);
</script>

<template>
  <section class="assets-layout">
    <div class="asset-intro card">
      <p class="eyebrow">Canon v4</p>
      <h2>身份优先，画风分责</h2>
      <p>五个槽位有固定职责和 Provider 优先级。上传只创建不可变候选，点击选择才会产生新的项目选择记录。</p>
      <div class="canon-portrait"><span class="child">⌒◡⌒</span><span class="cat">= ᵔᴗᵔ =</span></div>
      <ul>
        <li><b>儿童</b><span>同一位 8–9 岁短发儿童</span></li>
        <li><b>猫咪</b><span>同一只灰白虎斑猫</span></li>
        <li><b>画风</b><span>二维柔和数字插画</span></li>
        <li><b>隔离</b><span>style_source 永不进入 Provider</span></li>
      </ul>
      <p class="notice">AI 身份、画风和结构诊断只提供建议；只有损坏、无法解码或格式错误会阻止选择。</p>
    </div>

    <div class="slot-list">
      <p v-if="error" class="notice error">{{ error }}</p>
      <article v-for="slot in slots" :key="slot.id" class="asset-slot card">
        <div class="slot-order">{{ slot.order }}</div>
        <div class="slot-main">
          <header><div><h3>{{ slot.title }}</h3><p>{{ slot.responsibility }}</p></div><span class="pill" :class="{ good: workspace.selections[slot.id] }">{{ workspace.selections[slot.id] ? "已选择" : "待选择" }}</span></header>
          <div class="candidates">
            <div v-if="workspace.selections[slot.id]" class="candidate selected">
              <img :src="`/api/v1/assets/${workspace.selections[slot.id]!.id}/content`" :alt="`${slot.title}当前选择`" />
              <span class="current-badge">当前选择</span>
            </div>
            <div v-for="asset in grouped[slot.id]" :key="asset.id" class="candidate" :class="{ selected: workspace.selections[slot.id]?.id === asset.id }">
              <img :src="`/api/v1/assets/${asset.id}/content`" :alt="slot.title" />
              <span v-if="reportStatus(asset)" class="quality-badge">诊断 {{ reportStatus(asset) }}</span>
              <button class="diagnose-button" :disabled="busySlot === slot.id" @click="diagnose(asset)">诊断</button>
              <button v-if="workspace.selections[slot.id]?.id !== asset.id" class="secondary" :disabled="busySlot === slot.id" @click="select(slot.id, asset.id)">选择</button>
            </div>
            <label class="upload-candidate" :class="{ busy: busySlot === slot.id }">
              <input type="file" accept="image/png,image/jpeg,image/webp" @change="upload(slot.id, $event)" />
              <b>{{ busySlot === slot.id ? "处理中…" : "＋" }}</b><span>上传候选</span>
            </label>
            <button class="generate-candidate" :disabled="busySlot === slot.id" @click="generate(slot.id)"><b>✦</b><span>生成 fake 候选</span></button>
          </div>
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
</style>
