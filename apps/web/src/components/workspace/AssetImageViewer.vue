<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { AssetDto } from "../../api/types";

const props = defineProps<{
  open: boolean;
  title: string;
  assets: AssetDto[];
  activeAssetId?: string | null;
  comparisons: Array<{ label: string; asset: AssetDto }>;
  prompt?: string | null;
  negativePrompt?: string | null;
  promptUnavailable?: boolean;
  qualityReport?: Record<string, unknown> | null;
}>();
const emit = defineEmits<{ close: []; assetChange: [asset: AssetDto] }>();

const dialog = ref<HTMLElement | null>(null);
const currentIndex = ref(0);
const view = ref<"image" | "comparison">("image");
const zoom = ref(100);
const fit = ref(true);
const imageError = ref(false);
const retryKey = ref(0);
const panX = ref(0);
const panY = ref(0);
const dragging = ref(false);
let pointerOrigin: { x: number; y: number; panX: number; panY: number } | null = null;
let previousFocus: HTMLElement | null = null;

const currentAsset = computed(() => props.assets[currentIndex.value] ?? null);
const currentSource = computed(() => {
  if (!currentAsset.value) return "";
  const base = `/api/v1/assets/${currentAsset.value.id}/content`;
  return retryKey.value ? `${base}?retry=${retryKey.value}` : base;
});
const imageTransform = computed(() => ({
  transform: `translate(${panX.value}px, ${panY.value}px) scale(${zoom.value / 100})`,
}));
const qualityChecks = computed(() => {
  const report = props.qualityReport;
  if (!report) return [];
  const labels: Record<string, string> = {
    intentMatch: "环境内容",
    characterFree: "空场景",
    styleMatch: "画风一致性",
    stagingSpace: "角色活动空间",
    technical: "图片文件",
    style: "画风一致性",
    anatomy: "画面结构",
  };
  return Object.entries(labels)
    .filter(([key]) => typeof report[key] === "string")
    .map(([key, label]) => {
      const status = String(report[key]);
      const statusLabel = status === "pass" ? "通过" : status === "fail" ? "需要处理" : "建议检查";
      const detail = key === "characterFree" && status !== "pass" ? "发现人物或动物" : statusLabel;
      return { key, label, detail, status };
    });
});
const qualityWarnings = computed(() => {
  const warnings = props.qualityReport?.warnings;
  if (!Array.isArray(warnings)) return [];
  return warnings
    .filter((item): item is { code?: string; message: string } => Boolean(item) && typeof item === "object" && typeof (item as { message?: unknown }).message === "string")
    .map((item) => item.message);
});

function resetImageView() {
  zoom.value = 100;
  fit.value = true;
  panX.value = 0;
  panY.value = 0;
  imageError.value = false;
}

function syncActiveAsset() {
  const index = props.assets.findIndex((asset) => asset.id === props.activeAssetId);
  currentIndex.value = index >= 0 ? index : 0;
  resetImageView();
}

async function focusDialog() {
  await nextTick();
  dialog.value?.focus();
}

function close() {
  emit("close");
  nextTick(() => previousFocus?.focus());
}

function moveCandidate(direction: number) {
  if (props.assets.length < 2) return;
  currentIndex.value = (currentIndex.value + direction + props.assets.length) % props.assets.length;
  resetImageView();
  const asset = currentAsset.value;
  if (asset) emit("assetChange", asset);
}

function changeZoom(amount: number) {
  fit.value = false;
  zoom.value = Math.min(400, Math.max(25, zoom.value + amount));
}

function showFit() {
  resetImageView();
}

function showOriginalSize() {
  fit.value = false;
  zoom.value = 100;
  panX.value = 0;
  panY.value = 0;
}

function retry() {
  imageError.value = false;
  retryKey.value += 1;
}

function startPan(event: PointerEvent) {
  if (fit.value || imageError.value) return;
  dragging.value = true;
  pointerOrigin = { x: event.clientX, y: event.clientY, panX: panX.value, panY: panY.value };
  (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
}

function movePan(event: PointerEvent) {
  if (!dragging.value || !pointerOrigin) return;
  panX.value = pointerOrigin.panX + event.clientX - pointerOrigin.x;
  panY.value = pointerOrigin.panY + event.clientY - pointerOrigin.y;
}

function stopPan() {
  dragging.value = false;
  pointerOrigin = null;
}

function keyboard(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    close();
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    moveCandidate(-1);
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    moveCandidate(1);
  } else if (event.key === "+" || event.key === "=") {
    event.preventDefault();
    changeZoom(25);
  } else if (event.key === "-") {
    event.preventDefault();
    changeZoom(-25);
  } else if (event.key === "Tab" && dialog.value) {
    const focusable = Array.from(
      dialog.value.querySelectorAll<HTMLElement>("button:not([disabled]), summary, [tabindex='0']"),
    );
    if (!focusable.length) return;
    const activeIndex = focusable.indexOf(document.activeElement as HTMLElement);
    const nextIndex = event.shiftKey
      ? (activeIndex <= 0 ? focusable.length - 1 : activeIndex - 1)
      : (activeIndex >= focusable.length - 1 ? 0 : activeIndex + 1);
    event.preventDefault();
    focusable[nextIndex]?.focus();
  }
}

watch(
  () => props.open,
  (open, wasOpen) => {
    if (!open) return;
    if (!wasOpen) {
      previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    syncActiveAsset();
    view.value = "image";
    void focusDialog();
  },
  { immediate: true },
);
watch(
  () => [props.activeAssetId, props.assets.map((asset) => asset.id).join(":")],
  () => {
    if (props.open) syncActiveAsset();
  },
);

onMounted(() => {
  if (props.open) void focusDialog();
});
onBeforeUnmount(() => stopPan());
</script>

<template>
  <div v-if="open" class="viewer-backdrop" @mousedown.self="close">
    <section
      ref="dialog"
      class="image-viewer"
      role="dialog"
      aria-modal="true"
      :aria-label="`${title}图片查看器`"
      tabindex="-1"
      @keydown="keyboard"
    >
      <header class="viewer-header">
        <div>
          <h2>{{ title }}</h2>
          <p v-if="assets.length > 1">第 {{ currentIndex + 1 }} 张，共 {{ assets.length }} 张候选</p>
        </div>
        <button class="viewer-close" aria-label="关闭图片查看器" @click="close">关闭</button>
      </header>

      <nav class="viewer-tabs" aria-label="图片查看方式">
        <button data-view="image" :class="{ active: view === 'image' }" @click="view = 'image'">查看环境</button>
        <button
          v-if="comparisons.length"
          data-view="comparison"
          :class="{ active: view === 'comparison' }"
          @click="view = 'comparison'"
        >对照固定参考</button>
      </nav>

      <div class="viewer-body" :class="{ comparison: view === 'comparison' }">
        <div
          class="viewer-canvas"
          :class="{ draggable: !fit, dragging }"
          @pointerdown="startPan"
          @pointermove="movePan"
          @pointerup="stopPan"
          @pointercancel="stopPan"
        >
          <template v-if="currentAsset">
            <div v-if="imageError" class="viewer-load-error" role="status">
              <b>图片暂时无法读取</b>
              <span>请检查服务状态后重试。</span>
              <button data-action="retry" @click="retry">重试</button>
            </div>
            <img
              v-else
              :key="currentSource"
              class="viewer-main-image"
              :class="{ fit }"
              :src="currentSource"
              :alt="title"
              :style="imageTransform"
              draggable="false"
              @error="imageError = true"
              @dblclick="fit ? showOriginalSize() : showFit()"
            />
          </template>
          <p v-else>没有可查看的图片。</p>
          <button
            v-if="assets.length > 1"
            class="candidate-arrow previous"
            aria-label="上一张候选"
            @click.stop="moveCandidate(-1)"
          >上一张</button>
          <button
            v-if="assets.length > 1"
            class="candidate-arrow next"
            aria-label="下一张候选"
            @click.stop="moveCandidate(1)"
          >下一张</button>
        </div>

        <aside v-if="view === 'comparison'" class="comparison-list" aria-label="固定参考">
          <figure v-for="item in comparisons" :key="item.asset.id" class="comparison-item">
            <img :src="`/api/v1/assets/${item.asset.id}/content`" :alt="item.label" />
            <figcaption>{{ item.label }}</figcaption>
          </figure>
        </aside>
      </div>

      <footer class="viewer-toolbar">
        <button @click="showFit">适合窗口</button>
        <button @click="showOriginalSize">100% 原始比例</button>
        <button aria-label="缩小图片" @click="changeZoom(-25)">缩小</button>
        <span class="zoom-value" aria-live="polite">{{ zoom }}%</span>
        <button aria-label="放大图片" @click="changeZoom(25)">放大</button>
      </footer>

      <details v-if="prompt" class="viewer-prompt">
        <summary>查看该候选实际使用的生成指令</summary>
        <p>{{ prompt }}</p>
        <p v-if="negativePrompt"><b>需要避免的问题</b><br />{{ negativePrompt }}</p>
      </details>
      <p v-else-if="promptUnavailable" class="viewer-history-note">旧任务未记录完整生成指令。</p>

      <section v-if="qualityChecks.length || qualityWarnings.length" class="viewer-quality" aria-label="画面检查建议">
        <h3>画面检查建议</h3>
        <ul class="quality-checks">
          <li v-for="check in qualityChecks" :key="check.key" :class="`quality-${check.status}`">
            <span>{{ check.label }}</span><b>{{ check.detail }}</b>
          </li>
        </ul>
        <ul v-if="qualityWarnings.length" class="quality-warnings">
          <li v-for="warning in qualityWarnings" :key="warning">{{ warning }}</li>
        </ul>
      </section>
    </section>
  </div>
</template>

<style scoped>
.viewer-backdrop { position: fixed; inset: 0; z-index: 2000; display: grid; place-items: center; padding: 28px; background: rgba(32, 28, 24, .72); }
.image-viewer { width: min(1320px, 96vw); max-height: 94vh; overflow: auto; border-radius: 20px; background: #f8f3ed; color: var(--ink); box-shadow: 0 24px 80px rgba(20, 16, 12, .35); outline: none; }
.viewer-header { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 22px 12px; border-bottom: 1px solid var(--line); }
.viewer-header h2 { margin: 0; font: 600 22px Georgia, "Songti SC", serif; }.viewer-header p { margin: 5px 0 0; color: var(--muted); font-size: 12px; }
.viewer-close, .viewer-tabs button, .viewer-toolbar button, .viewer-load-error button { border: 1px solid var(--line); border-radius: 10px; padding: 8px 13px; background: white; color: var(--ink); cursor: pointer; }
.viewer-close:focus-visible, .viewer-tabs button:focus-visible, .viewer-toolbar button:focus-visible, .candidate-arrow:focus-visible { outline: 3px solid #c46d52; outline-offset: 2px; }
.viewer-tabs { display: flex; gap: 8px; padding: 12px 22px; }.viewer-tabs button.active { border-color: #b9634d; background: #f6dfd5; color: #9f4f3c; }
.viewer-body { display: grid; padding: 0 22px; }.viewer-body.comparison { grid-template-columns: minmax(0, 1fr) 270px; gap: 14px; }
.viewer-canvas { position: relative; display: grid; place-items: center; min-height: min(68vh, 760px); overflow: hidden; border-radius: 15px; background: #dcd6cf; touch-action: none; }.viewer-canvas.draggable { cursor: grab; }.viewer-canvas.dragging { cursor: grabbing; }
.viewer-main-image { max-width: none; max-height: none; transform-origin: center; user-select: none; transition: transform .12s ease; }.viewer-main-image.fit { width: 100%; height: min(68vh, 760px); object-fit: contain; }
.candidate-arrow { position: absolute; top: 50%; border: 0; border-radius: 999px; padding: 10px 12px; background: rgba(40, 35, 31, .72); color: white; cursor: pointer; transform: translateY(-50%); }.candidate-arrow.previous { left: 12px; }.candidate-arrow.next { right: 12px; }
.comparison-list { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; align-content: start; max-height: min(68vh, 760px); overflow: auto; }.comparison-item { margin: 0; padding: 8px; border: 1px solid var(--line); border-radius: 12px; background: white; }.comparison-item img { width: 100%; aspect-ratio: 3 / 4; object-fit: contain; border-radius: 8px; background: #e8e1d9; }.comparison-item figcaption { margin-top: 6px; text-align: center; font-size: 11px; color: var(--muted); }
.viewer-toolbar { display: flex; justify-content: center; align-items: center; gap: 8px; padding: 13px 22px; }.zoom-value { min-width: 52px; text-align: center; font-variant-numeric: tabular-nums; }
  .viewer-load-error { display: grid; justify-items: center; gap: 8px; color: var(--muted); }.viewer-load-error b { color: var(--ink); }.viewer-prompt, .viewer-quality, .viewer-history-note { margin: 0 22px 20px; padding: 12px 14px; border-radius: 12px; background: white; }.viewer-prompt summary { cursor: pointer; font-weight: 700; }.viewer-prompt p { line-height: 1.65; white-space: pre-wrap; }.viewer-history-note { color: var(--muted); }.viewer-quality h3 { margin: 0 0 10px; font-size: 14px; }.quality-checks, .quality-warnings { margin: 0; padding-left: 18px; }.quality-checks { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 7px 18px; list-style: none; padding: 0; }.quality-checks li { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }.quality-checks li span { color: var(--muted); }.quality-checks .quality-pass b { color: #55745d; }.quality-checks .quality-warning b, .quality-checks .quality-fail b { color: #a3533f; }.quality-warnings { margin-top: 10px; color: #8c513f; font-size: 12px; line-height: 1.6; }
@media (max-width: 820px) { .viewer-backdrop { padding: 8px; }.viewer-body.comparison { grid-template-columns: 1fr; }.comparison-list { grid-template-columns: repeat(4, minmax(120px, 1fr)); }.viewer-toolbar { flex-wrap: wrap; } }
</style>
