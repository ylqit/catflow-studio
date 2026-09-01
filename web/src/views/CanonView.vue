<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api, assetContentUrl } from "../api/client";
import type { AssetDto } from "../api/types";

const assets = ref<AssetDto[]>([]);
const previewAsset = ref<AssetDto | null>(null);
const imageErrors = ref<Record<string, string>>({});

const displayNames: Record<string, string> = {
  "person:headshot": "人物大头照",
  "person:fullbody": "人物全身",
  "person:front": "人物正面",
  "person:side": "人物侧面",
  "person:back": "人物背面",
  "cat:front": "猫咪正面",
  "cat:side": "猫咪侧面",
  "cat:back": "猫咪背面",
  "style:line_texture": "线条材质",
  "style:outdoor": "室外画风",
  "style:indoor": "室内画风",
};

const groups = computed(() => [
  { key: "person", title: "人物", items: assets.value.filter((item) => item.semanticKey?.startsWith("person:")) },
  { key: "cat", title: "猫咪", items: assets.value.filter((item) => item.semanticKey?.startsWith("cat:")) },
  { key: "style", title: "画风", items: assets.value.filter((item) => item.semanticKey?.startsWith("style:")) },
]);

function assetName(asset: AssetDto): string {
  return displayNames[asset.semanticKey ?? ""] ?? asset.semanticKey ?? asset.role;
}

function closePreview(visible: boolean) {
  if (!visible) previewAsset.value = null;
}

async function recordImageFailure(asset: AssetDto) {
  try {
    const response = await fetch(assetContentUrl(asset.id), { method: "HEAD" });
    imageErrors.value[asset.id] = `HTTP ${response.status} · 资产 ${asset.id}`;
  } catch {
    imageErrors.value[asset.id] = `网络读取失败 · 资产 ${asset.id}`;
  }
}

onMounted(async () => {
  assets.value = await api.canon();
});
</script>

<template>
  <div class="page">
    <header>
      <div>
        <h1>全局 Canon 资产</h1>
        <p>这里只维护系列级人物、猫咪和画风基准，供所有视频项目引用。</p>
      </div>
    </header>

    <el-alert
      type="info"
      :closable="false"
      title="项目生成的场景视觉基准、锚点和视频帧不会进入 Canon；它们只属于生成它们的视频项目。项目视觉档案请在对应项目的“项目设置”中管理。"
    />

    <section v-for="group in groups" :key="group.key" class="canon-group">
      <h2>{{ group.title }} <small>{{ group.items.length }} 张</small></h2>
      <div class="grid">
        <article v-for="asset in group.items" :key="asset.id" :class="{ missing: !asset.contentReady }">
          <button class="preview" type="button" :disabled="!asset.contentReady" @click="previewAsset = asset">
            <img v-if="asset.contentReady && !imageErrors[asset.id]" :src="assetContentUrl(asset.id)" :alt="assetName(asset)" @error="recordImageFailure(asset)" />
            <div v-else class="missing-placeholder">{{ imageErrors[asset.id] || '文件缺失' }}<br /><small>请检查 storage_key 或运行 canon-repair 修复</small></div>
          </button>
          <div class="asset-caption">
            <b>{{ asset.displayName || assetName(asset) }}</b>
            <small>{{ asset.referencePurpose || '全局视觉参考' }}</small>
            <span>{{ asset.contentReady ? asset.sha256.slice(0, 12) : "内容不可用" }}</span>
          </div>
        </article>
      </div>
    </section>

    <el-dialog
      :model-value="Boolean(previewAsset)"
      :title="previewAsset ? assetName(previewAsset) : 'Canon 预览'"
      width="min(860px, 92vw)"
      @update:model-value="closePreview"
    >
      <img v-if="previewAsset?.contentReady && !imageErrors[previewAsset.id]" class="large-preview" :src="assetContentUrl(previewAsset.id)" :alt="assetName(previewAsset)" @error="recordImageFailure(previewAsset)" />
      <el-alert v-else-if="previewAsset" type="error" :closable="false" :title="imageErrors[previewAsset.id] || `资产 ${previewAsset.id} 内容缺失，请执行 Canon repair`" />
    </el-dialog>
  </div>
</template>

<style scoped>
.page { padding: 28px; color: #e8ebf2; }
header { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 18px; }
.page p { color: #929aaa; }
.canon-group { margin-top: 26px; }
.canon-group h2 { font-size: 18px; }
.canon-group small { color: #818a9a; font-weight: 400; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 14px; }
.grid article { background: #161b23; border: 1px solid #2b323f; border-radius: 10px; padding: 10px; display: grid; gap: 8px; }
.grid article.missing { border-color: #8b5e2b; }
.preview { border: 0; padding: 0; background: #0a0d12; cursor: zoom-in; width: 100%; }
.preview:disabled { cursor: default; }
.preview img, .missing-placeholder { width: 100%; aspect-ratio: 1; object-fit: contain; }
.missing-placeholder { display: grid; place-content: center; color: #d6a15e; line-height: 1.6; }
.asset-caption { display: grid; gap: 3px; }
.asset-caption span { color: #818a9a; font-size: 11px; }
.large-preview { display: block; max-width: 100%; max-height: 72vh; margin: 0 auto; object-fit: contain; }
@media (max-width: 800px) { header { flex-direction: column; } }
</style>
