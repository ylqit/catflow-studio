<script setup lang="ts">
import {
  Brush, Close, Crop, Delete, EditPen, Location, Position,
  RefreshLeft, RefreshRight, VideoPlay,
} from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { canvasApi } from "../../api/client";
import type {
  CapabilityCompilationPlan, VideoEditAnnotationInput, VideoEditRecipeDto,
  VideoEditTool, VideoFilmstripDto,
} from "../../api/types";
import CanvasReviewDialog from "./CanvasReviewDialog.vue";
import {
  clampVideoSelection,
  renderedVideoRect,
  type VideoEditConsoleDraft,
} from "./videoEditing";

interface EditorReference {
  id: string;
  title: string;
  thumbnailUrl: string;
  semanticRole?: string;
}

const props = withDefaults(defineProps<{
  projectId: string;
  sourceAssetId: string;
  videoUrl: string;
  posterUrl?: string;
  durationMs: number;
  initialStartMs?: number;
  initialEndMs?: number;
  references?: EditorReference[];
  embedded?: boolean;
  initialDraft?: VideoEditConsoleDraft;
  referenceAssetIds?: string[];
}>(), {
  references: () => [],
  embedded: false,
  initialDraft: undefined,
  referenceAssetIds: undefined,
});
const emit = defineEmits<{
  close: [];
  submitted: [recipeId: string];
  expand: [];
  "draft-change": [draft: VideoEditConsoleDraft];
  "select-references": [assetIds: string[]];
}>();

type EditorTool = VideoEditTool | "eraser";
const initialSelection = clampVideoSelection({
  startMs: props.initialDraft?.startMs ?? props.initialStartMs ?? 0,
  endMs: props.initialDraft?.endMs ?? props.initialEndMs ?? Math.min(props.durationMs, 8_000),
}, props.durationMs, "end");
const startMs = ref(initialSelection.startMs);
const endMs = ref(initialSelection.endMs);
const instruction = ref(props.initialDraft?.instruction ?? "");
const activeTool = ref<EditorTool>("rectangle");
const annotationMode = ref(false);
const annotationLabel = ref("");
const loopSelection = ref(true);
const selectedReferenceIds = ref<string[]>(
  props.referenceAssetIds
  ?? props.initialDraft?.referenceAssetIds
  ?? props.references.map((item) => item.id).slice(0, 6),
);
const annotations = ref<VideoEditAnnotationInput[]>(props.initialDraft?.annotations ?? []);
const redoStack = ref<VideoEditAnnotationInput[]>([]);
const recipe = ref<VideoEditRecipeDto | null>(null);
const plan = ref<CapabilityCompilationPlan | null>(null);
const compiling = ref(false);
const submitting = ref(false);
const costReviewOpen = ref(false);
const filmstrip = ref<VideoFilmstripDto | null>(null);
const filmstripError = ref("");
const filmstripLoading = ref(false);
const stageElement = ref<HTMLElement | null>(null);
const canvasElement = ref<HTMLCanvasElement | null>(null);
const videoElement = ref<HTMLVideoElement | null>(null);
const drawingPoints = ref<Array<{ x: number; y: number }>>([]);
const annotationSurface = ref({ left: 0, top: 0, width: 1, height: 1 });
let filmstripTimer: number | null = null;

const duration = computed(() => endMs.value - startMs.value);
const intervalValid = computed(() => duration.value >= 500 && duration.value <= 13_000);
const selectedReferences = computed(() => props.references.filter(
  (item) => selectedReferenceIds.value.includes(item.id),
));
const compiledReferenceById = computed(() => new Map(
  (plan.value?.actualReferences ?? []).map((item) => [item.assetId, item]),
));
const boundaryPreviews = computed(() => {
  const frames = filmstrip.value?.frames ?? [];
  const nearest = (timestampMs: number) => frames.reduce<(typeof frames)[number] | undefined>(
    (best, frame) => !best
      || Math.abs(frame.timestampMs - timestampMs) < Math.abs(best.timestampMs - timestampMs)
      ? frame : best,
    undefined,
  );
  return { start: nearest(startMs.value), end: nearest(endMs.value) };
});
const annotationSurfaceStyle = computed(() => ({
  left: `${annotationSurface.value.left}px`, top: `${annotationSurface.value.top}px`,
  width: `${annotationSurface.value.width}px`, height: `${annotationSurface.value.height}px`,
}));
const toolItems: Array<{ key: EditorTool; label: string; icon: unknown }> = [
  { key: "rectangle", label: "矩形", icon: Crop },
  { key: "brush", label: "画笔", icon: Brush },
  { key: "arrow", label: "箭头", icon: Position },
  { key: "text", label: "文字", icon: EditPen },
  { key: "marker", label: "时间点", icon: Location },
  { key: "eraser", label: "橡皮擦", icon: Delete },
];

watch(
  () => props.referenceAssetIds,
  (value) => {
    if (value) selectedReferenceIds.value = [...value].slice(0, 6);
  },
);
watch([startMs, endMs, instruction, selectedReferenceIds, annotations], () => {
  plan.value = null;
  costReviewOpen.value = false;
  emit("draft-change", {
    startMs: startMs.value,
    endMs: endMs.value,
    instruction: instruction.value,
    referenceAssetIds: [...selectedReferenceIds.value],
    annotations: annotations.value.map((item) => ({
      ...item,
      points: item.points.map((point) => ({ ...point })),
    })),
  });
}, { deep: true });

function updateSelection(edge: "start" | "end", value: number) {
  const selection = clampVideoSelection({
    startMs: edge === "start" ? value : startMs.value,
    endMs: edge === "end" ? value : endMs.value,
  }, props.durationMs, edge);
  startMs.value = selection.startMs;
  endMs.value = selection.endMs;
  plan.value = null;
  seekTo(edge === "start" ? selection.startMs : selection.endMs);
}

function selectTool(tool: EditorTool) {
  activeTool.value = tool;
  annotationMode.value = true;
  const currentMs = (videoElement.value?.currentTime ?? 0) * 1_000;
  if (currentMs < startMs.value || currentMs > endMs.value) seekTo(startMs.value);
}

function normalizedPoint(event: PointerEvent) {
  const bounds = canvasElement.value?.getBoundingClientRect();
  if (!bounds || bounds.width <= 0 || bounds.height <= 0) return null;
  return {
    x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
    y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
  };
}

function annotationHit(point: { x: number; y: number }, annotation: VideoEditAnnotationInput) {
  if (annotation.tool === "rectangle" && annotation.points[1]) {
    const [first, second] = annotation.points;
    return point.x >= Math.min(first.x, second.x) - .025
      && point.x <= Math.max(first.x, second.x) + .025
      && point.y >= Math.min(first.y, second.y) - .025
      && point.y <= Math.max(first.y, second.y) + .025;
  }
  return annotation.points.some((item) => Math.hypot(item.x - point.x, item.y - point.y) < .045);
}

function beginAnnotation(event: PointerEvent) {
  if (!annotationMode.value) return;
  const point = normalizedPoint(event);
  if (!point) return;
  if (activeTool.value === "eraser") {
    const index = [...annotations.value].reverse().findIndex((item) => annotationHit(point, item));
    if (index >= 0) {
      const actualIndex = annotations.value.length - index - 1;
      const [removed] = annotations.value.splice(actualIndex, 1);
      redoStack.value.push(removed);
      drawAnnotations();
    }
    return;
  }
  drawingPoints.value = [point];
  canvasElement.value?.setPointerCapture?.(event.pointerId);
}

function extendAnnotation(event: PointerEvent) {
  if (!drawingPoints.value.length || activeTool.value !== "brush") return;
  const point = normalizedPoint(event);
  if (point) drawingPoints.value.push(point);
}

function finishAnnotation(event: PointerEvent) {
  if (!drawingPoints.value.length || activeTool.value === "eraser") return;
  const endPoint = normalizedPoint(event);
  if (!endPoint) return;
  const first = drawingPoints.value[0];
  const points = activeTool.value === "brush" ? [...drawingPoints.value, endPoint]
    : activeTool.value === "marker" || activeTool.value === "text" ? [first]
      : [first, endPoint];
  const currentFrameMs = Math.round(
    (videoElement.value?.currentTime ?? startMs.value / 1_000) * 1_000,
  );
  annotations.value.push({
    frameTimestampMs: Math.max(startMs.value, Math.min(endMs.value, currentFrameMs)),
    coordinateSpace: "source_normalized",
    tool: activeTool.value,
    points,
    label: activeTool.value === "text" ? annotationLabel.value.trim() || "修改此处" : "",
  });
  drawingPoints.value = [];
  redoStack.value = [];
  drawAnnotations();
}

function undo() {
  const item = annotations.value.pop();
  if (item) redoStack.value.push(item);
  drawAnnotations();
}

function redo() {
  const item = redoStack.value.pop();
  if (item) annotations.value.push(item);
  drawAnnotations();
}

function drawArrow(context: CanvasRenderingContext2D, points: Array<{ x: number; y: number }>) {
  if (!points[1]) return;
  context.beginPath();
  context.moveTo(points[0].x, points[0].y);
  context.lineTo(points[1].x, points[1].y);
  const angle = Math.atan2(points[1].y - points[0].y, points[1].x - points[0].x);
  for (const offset of [-Math.PI / 7, Math.PI / 7]) {
    context.lineTo(points[1].x - 24 * Math.cos(angle + offset), points[1].y - 24 * Math.sin(angle + offset));
    context.moveTo(points[1].x, points[1].y);
  }
  context.stroke();
}

function drawAnnotations() {
  const canvas = canvasElement.value;
  if (!canvas || typeof CanvasRenderingContext2D === "undefined") return;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "#ff5f57";
  context.fillStyle = "#ff5f57";
  context.lineWidth = Math.max(3, canvas.width / 300);
  context.font = `${Math.max(18, canvas.width / 45)}px sans-serif`;
  for (const annotation of annotations.value) {
    const points = annotation.points.map((point) => ({ x: point.x * canvas.width, y: point.y * canvas.height }));
    if (annotation.tool === "rectangle" && points[1]) {
      context.strokeRect(points[0].x, points[0].y, points[1].x - points[0].x, points[1].y - points[0].y);
    } else if (annotation.tool === "arrow") drawArrow(context, points);
    else if (annotation.tool === "brush") {
      context.beginPath(); context.moveTo(points[0].x, points[0].y);
      points.slice(1).forEach((point) => context.lineTo(point.x, point.y)); context.stroke();
    } else if (annotation.tool === "text") context.fillText(annotation.label, points[0].x, points[0].y);
    else { context.beginPath(); context.arc(points[0].x, points[0].y, 8, 0, Math.PI * 2); context.fill(); }
  }
}

function updateAnnotationSurface() {
  const stage = stageElement.value;
  const video = videoElement.value;
  const canvas = canvasElement.value;
  if (!stage || !video || !canvas) return;
  const source = { width: video.videoWidth || 1280, height: video.videoHeight || 720 };
  const rect = renderedVideoRect(
    { left: 0, top: 0, width: stage.clientWidth, height: stage.clientHeight },
    source,
  );
  annotationSurface.value = rect;
  canvas.width = source.width;
  canvas.height = source.height;
  drawAnnotations();
}

function seekTo(value: number) {
  if (videoElement.value) videoElement.value.currentTime = value / 1_000;
}

function onTimeUpdate() {
  const video = videoElement.value;
  if (!video || !loopSelection.value || video.paused) return;
  const currentMs = video.currentTime * 1_000;
  if (currentMs < startMs.value || currentMs >= endMs.value) video.currentTime = startMs.value / 1_000;
}

function seekFromFilmstrip(event: MouseEvent) {
  const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect();
  seekTo(Math.round((event.clientX - bounds.left) / Math.max(bounds.width, 1) * props.durationMs));
}

async function refreshFilmstrip(queueWhenMissing = true) {
  filmstripLoading.value = true;
  filmstripError.value = "";
  try {
    let result = await canvasApi.videoFilmstrip(props.sourceAssetId, 12);
    if (result.status === "not_requested" && queueWhenMissing) {
      result = await canvasApi.createVideoFilmstrip(props.sourceAssetId, 12);
    }
    filmstrip.value = result;
    if (result.status === "failed") {
      filmstripError.value = result.error?.message ?? "FFmpeg 抽帧失败，可检查服务配置后重试";
    } else if (result.frames.length < result.frameCount) {
      filmstripTimer = window.setTimeout(() => void refreshFilmstrip(false), 1_500);
    }
  } catch (error) {
    filmstripError.value = error instanceof Error ? error.message : String(error);
  } finally {
    filmstripLoading.value = false;
  }
}

async function compileRecipe() {
  if (!intervalValid.value) { ElMessage.error("单次重编区间必须在 0.5–13 秒之间"); return; }
  if (!instruction.value.trim()) { ElMessage.error("请描述需要修改的动作或画面"); return; }
  compiling.value = true;
  try {
    if (!recipe.value) {
      recipe.value = await canvasApi.createVideoEditRecipe({
        projectId: props.projectId, sourceAssetId: props.sourceAssetId,
        startMs: startMs.value, endMs: endMs.value,
        instruction: instruction.value.trim(), referenceAssetIds: selectedReferenceIds.value,
        annotations: annotations.value,
      });
    } else {
      recipe.value = await canvasApi.updateVideoEditRecipe(recipe.value.id, recipe.value.revision, {
        startMs: startMs.value, endMs: endMs.value,
        instruction: instruction.value.trim(), referenceAssetIds: selectedReferenceIds.value,
      });
      recipe.value = await canvasApi.replaceVideoEditAnnotations(
        recipe.value.id, recipe.value.revision, annotations.value,
      );
    }
    plan.value = await canvasApi.compileVideoEditRecipe(recipe.value.id);
  } finally { compiling.value = false; }
}

async function submitRecipe() {
  if (!recipe.value || !plan.value) return;
  submitting.value = true;
  try {
    await canvasApi.submitVideoEditRecipe(recipe.value.id, crypto.randomUUID(), plan.value.estimatedCostMicros);
    costReviewOpen.value = false;
    ElMessage.success("视频局部重编已进入持久任务队列，原资产不会被覆盖");
    emit("submitted", recipe.value.id);
  } finally { submitting.value = false; }
}

onMounted(async () => {
  await nextTick();
  updateAnnotationSurface();
  window.addEventListener("resize", updateAnnotationSurface);
  void refreshFilmstrip();
});
onBeforeUnmount(() => {
  if (filmstripTimer !== null) window.clearTimeout(filmstripTimer);
  window.removeEventListener("resize", updateAnnotationSurface);
});
</script>

<template>
  <section class="video-editor" :class="{ embedded }" :role="embedded ? 'region' : 'dialog'" :aria-modal="embedded ? undefined : 'true'" aria-label="视频局部重编器">
    <header class="editor-header">
      <div><span>VIDEO EDIT RECIPE · REVISION {{ recipe?.revision ?? 1 }}</span><h1>{{ embedded ? '片段重拍' : '视频局部重编' }}</h1></div>
      <div class="header-actions"><button v-if="embedded" type="button" @click="emit('expand')">展开高级编辑</button><button v-if="!embedded" class="icon-button" type="button" aria-label="关闭视频编辑器" @click="emit('close')"><Close /></button></div>
    </header>

    <main class="editor-layout">
      <section class="preview-column">
        <div ref="stageElement" class="video-stage">
          <video ref="videoElement" :src="videoUrl" :poster="posterUrl" controls playsinline @loadedmetadata="updateAnnotationSurface(); seekTo(startMs)" @timeupdate="onTimeUpdate" />
          <canvas
            ref="canvasElement" :class="{ active: annotationMode }" :style="annotationSurfaceStyle"
            :aria-hidden="!annotationMode" aria-label="视频标注层"
            @pointerdown="beginAnnotation" @pointermove="extendAnnotation" @pointerup="finishAnnotation"
          />
          <button v-if="annotationMode" class="exit-annotation" type="button" @click="annotationMode = false">退出标注，恢复播放控制</button>
        </div>

        <nav class="annotation-tools" aria-label="视频标注工具">
          <button v-for="tool in toolItems" :key="tool.key" type="button" :data-tool="tool.key" :class="{ active: annotationMode && activeTool === tool.key }" :aria-label="tool.label" :title="tool.label" @click="selectTool(tool.key)"><component :is="tool.icon" /><span>{{ tool.label }}</span></button>
          <input v-if="annotationMode && activeTool === 'text'" v-model="annotationLabel" aria-label="标注文字" placeholder="输入标注文字" />
          <i /><button type="button" aria-label="撤销标注" :disabled="!annotations.length" @click="undo"><RefreshLeft /></button><button type="button" aria-label="重做标注" :disabled="!redoStack.length" @click="redo"><RefreshRight /></button>
        </nav>

        <section class="range-panel">
          <div class="filmstrip" aria-label="视频帧带" @click="seekFromFilmstrip">
            <img v-for="frame in filmstrip?.frames ?? []" :key="frame.assetId" data-filmstrip-frame :src="frame.contentUrl" :alt="`${(frame.timestampMs / 1000).toFixed(1)} 秒帧`" />
            <div v-if="filmstripLoading && !filmstrip?.frames.length" class="filmstrip-state">正在生成真实帧带…</div>
            <div v-else-if="filmstripError" class="filmstrip-state error">{{ filmstripError }} <button type="button" @click.stop="refreshFilmstrip()">重试</button></div>
            <span class="range-selection" :style="{ left: `${startMs / durationMs * 100}%`, width: `${duration / durationMs * 100}%` }"><b>{{ (duration / 1000).toFixed(1) }}s</b></span>
            <input data-range-handle="start" aria-label="重编选区起点" class="range-handle start" type="range" min="0" :max="Math.max(0, durationMs - 500)" step="100" :value="startMs" @input="updateSelection('start', Number(($event.target as HTMLInputElement).value))" />
            <input data-range-handle="end" aria-label="重编选区终点" class="range-handle end" type="range" min="500" :max="durationMs" step="100" :value="endMs" @input="updateSelection('end', Number(($event.target as HTMLInputElement).value))" />
          </div>
          <div class="range-inputs">
            <label>起点 <input aria-label="重编起点毫秒" type="number" :value="startMs" min="0" :max="durationMs - 500" step="100" @change="updateSelection('start', Number(($event.target as HTMLInputElement).value))" /></label><span>—</span>
            <label>终点 <input aria-label="重编终点毫秒" type="number" :value="endMs" min="500" :max="durationMs" step="100" @change="updateSelection('end', Number(($event.target as HTMLInputElement).value))" /></label>
            <label class="loop-toggle"><input v-model="loopSelection" type="checkbox" />选区循环</label><small :class="{ error: !intervalValid }">单区间 0.5–13 秒 · 区间外画面与原音轨保持不变</small>
          </div>
          <div class="boundary-previews" aria-label="编辑区间真实边界帧">
            <figure>
              <img v-if="boundaryPreviews.start" :src="boundaryPreviews.start.contentUrl" alt="编辑区间入口帧" />
              <span v-else>等待帧带</span>
              <figcaption><b>编辑区间入口帧</b><small>{{ (startMs / 1000).toFixed(1) }}s · 提交时从源视频精确抽取</small></figcaption>
            </figure>
            <figure>
              <img v-if="boundaryPreviews.end" :src="boundaryPreviews.end.contentUrl" alt="编辑区间出口帧" />
              <span v-else>等待帧带</span>
              <figcaption><b>编辑区间出口帧</b><small>{{ (endMs / 1000).toFixed(1) }}s · 提交时从源视频精确抽取</small></figcaption>
            </figure>
          </div>
        </section>
      </section>

      <aside class="recipe-panel">
        <section><span class="section-label">编辑目标</span><textarea v-model="instruction" rows="5" placeholder="描述需要调整的视频动作、主体、产品或镜头" /></section>
        <section><div class="section-title"><span class="section-label">主体 / 产品 / 风格参考</span><span class="reference-title-actions"><b>{{ selectedReferences.length }} / 6</b><button type="button" @click="emit('select-references', selectedReferenceIds)">＋ 从画布添加</button></span></div>
          <label v-for="reference in references" :key="reference.id" class="reference-row"><input v-model="selectedReferenceIds" type="checkbox" :value="reference.id" :disabled="!selectedReferenceIds.includes(reference.id) && selectedReferenceIds.length >= 6" /><img :src="reference.thumbnailUrl" :alt="reference.title" /><span><strong>{{ reference.title }}</strong><small>{{ reference.semanticRole ?? '语义参考' }}</small></span><em v-if="compiledReferenceById.get(reference.id)?.providerIncluded">进入 {{ compiledReferenceById.get(reference.id)?.providerSlot ?? '供应商请求' }}</em><em v-else-if="compiledReferenceById.has(reference.id)" class="omitted">省略：{{ compiledReferenceById.get(reference.id)?.omissionReason }}</em><em v-else>预计会进入供应商请求，编译后确认</em></label>
          <p v-if="!references.length" class="empty-copy">未添加额外参考；仍会使用源视频与区间边界帧。</p>
        </section>
        <section v-if="plan" class="compile-plan"><div class="section-title"><span class="section-label">能力编译计划</span><b>{{ plan.mode === 'two_stage' ? '两阶段' : '直接提交' }}</b></div><div class="cost-grid"><span><strong>{{ plan.imageCallCount }}</strong> 次图片调用</span><span><strong>{{ plan.videoCallCount }}</strong> 次视频调用</span><span><strong>{{ plan.estimatedCostMicros ? `¥${(plan.estimatedCostMicros / 1_000_000).toFixed(3)}` : '待配置' }}</strong> 预计费用</span></div><p v-for="warning in plan.warnings" :key="warning">{{ warning }}</p></section>
        <footer><button type="button" data-action="compile" :disabled="compiling" @click="compileRecipe"><VideoPlay />{{ compiling ? '编译中…' : '生成能力计划' }}</button><button type="button" class="primary" data-action="submit" :disabled="!plan || submitting" @click="costReviewOpen = true">{{ submitting ? '提交中…' : '打开费用确认' }}</button><small>每次提交创建新 Recipe Revision 与新资产，绝不覆盖原视频。</small></footer>
      </aside>
    </main>
    <Teleport to="body">
      <CanvasReviewDialog
        v-if="costReviewOpen && plan && recipe"
        title="视频局部编辑费用确认"
        @close="costReviewOpen = false"
      >
        <section class="cost-review">
          <h2>本次只调用视频模型，不生成控制锚点</h2>
          <p>边界帧由服务端从源视频的人工选区免费抽取；确认前不会创建任务或发起 Provider 请求。</p>
          <dl>
            <div><dt>源视频</dt><dd>{{ sourceAssetId }}</dd></div>
            <div><dt>编辑区间</dt><dd>{{ startMs }}ms → {{ endMs }}ms</dd></div>
            <div><dt>Provider / 模型</dt><dd>{{ plan.provider }} · {{ plan.model }}</dd></div>
            <div><dt>调用数量</dt><dd>{{ plan.imageCallCount }} 次图片 · {{ plan.videoCallCount }} 次视频</dd></div>
            <div><dt>参考素材</dt><dd>{{ plan.actualReferences?.filter((item) => item.providerIncluded).length ?? 0 }} 项</dd></div>
            <div><dt>编译输入哈希</dt><dd><code>{{ plan.inputHash || '当前服务端未返回' }}</code></dd></div>
            <div><dt>预计费用</dt><dd>{{ plan.estimatedCostMicros ? `¥${(plan.estimatedCostMicros / 1_000_000).toFixed(3)}` : '费用尚未配置，不能据此推断免费' }}</dd></div>
          </dl>
          <div class="cost-boundaries">
            <figure><img v-if="boundaryPreviews.start" :src="boundaryPreviews.start.contentUrl" alt="费用确认入口帧" /><figcaption>入口帧 · {{ startMs }}ms</figcaption></figure>
            <figure><img v-if="boundaryPreviews.end" :src="boundaryPreviews.end.contentUrl" alt="费用确认出口帧" /><figcaption>出口帧 · {{ endMs }}ms</figcaption></figure>
          </div>
        </section>
        <template #actions>
          <button class="cost-cancel" type="button" data-action="cancel-cost" @click="costReviewOpen = false">取消，不创建任务</button>
          <button class="cost-confirm" type="button" data-action="confirm-cost" :disabled="submitting" @click="submitRecipe">{{ submitting ? '提交中…' : '确认费用并生成新版本' }}</button>
        </template>
      </CanvasReviewDialog>
    </Teleport>
  </section>
</template>

<style scoped>
.video-editor { position: fixed; inset: 0; z-index: 3000; display: grid; grid-template-rows: 72px 1fr; color: #edf1f6; background: #101216; }
.video-editor.embedded { position: relative; inset: auto; z-index: auto; width: 100%; height: 100%; grid-template-rows: 48px minmax(0, 1fr); background: #171a20; border: 0; border-radius: 0; box-shadow: none; }
.editor-header { padding: 0 24px; display: flex; align-items: center; justify-content: space-between; background: #15181d; border-bottom: 1px solid #2b3038; }.editor-header span,.section-label { color: #758197; font-size: 10px; font-weight: 800; letter-spacing: .13em; }.editor-header h1 { margin: 3px 0 0; font-size: 17px; }.header-actions { display: flex; gap: 8px; }.header-actions button { padding: 8px 10px; color: #c9d1dd; background: #242932; border: 1px solid #39414d; border-radius: 9px; cursor: pointer; }.icon-button { width: 38px; height: 38px; padding: 10px !important; }
.editor-layout { min-height: 0; display: grid; grid-template-columns: minmax(0,1fr) 390px; }.preview-column { min-width: 0; min-height: 0; padding: 18px; display: grid; grid-template-rows: minmax(260px,1fr) auto auto; gap: 12px; }.video-stage { position: relative; min-height: 0; overflow: hidden; display: grid; place-items: center; background: #090a0c; border: 1px solid #2e333b; border-radius: 14px; }.video-stage video { width: 100%; height: 100%; object-fit: contain; }.video-stage canvas { position: absolute; pointer-events: none; }.video-stage canvas.active { cursor: crosshair; pointer-events: auto; }.exit-annotation { position: absolute; right: 12px; top: 12px; z-index: 2; padding: 7px 9px; color: #fff; background: rgb(26 30 36 / 88%); border: 1px solid #596472; border-radius: 8px; cursor: pointer; }
.annotation-tools { padding: 7px; display: flex; align-items: center; gap: 4px; background: #20242b; border: 1px solid #353b45; border-radius: 12px; }.annotation-tools button { min-width: 42px; height: 40px; padding: 7px 10px; display: flex; align-items: center; gap: 6px; color: #aeb8c7; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }.annotation-tools button :deep(svg) { width: 17px; }.annotation-tools button.active,.annotation-tools button:hover { color: #fff; background: #38404b; }.annotation-tools i { width: 1px; height: 24px; margin: 0 5px; background: #3b424d; }.annotation-tools input { min-width: 140px; padding: 7px; color: #e5ebf2; background: #111419; border: 1px solid #3e4652; border-radius: 7px; }
.range-panel { padding: 12px; background: #181c22; border: 1px solid #303640; border-radius: 12px; }.filmstrip { position: relative; height: 76px; overflow: hidden; display: flex; background: #0f1216; border-radius: 8px; cursor: pointer; }.filmstrip img { min-width: 0; flex: 1; height: 76px; object-fit: cover; filter: brightness(.72); }.filmstrip-state { width: 100%; display: grid; place-items: center; color: #8793a3; font-size: 12px; }.filmstrip-state.error { color: #e79999; }.filmstrip-state button { margin-left: 8px; }.range-selection { position: absolute; top: 0; bottom: 0; min-width: 8px; border: 3px solid #75a9f8; border-radius: 8px; box-shadow: 0 0 0 999px rgb(4 6 8 / 46%); pointer-events: none; }.range-selection b { position: absolute; right: 5px; top: 5px; padding: 3px 5px; background: #101722; border-radius: 5px; font-size: 10px; }.range-handle { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; opacity: .01; pointer-events: none; }.range-handle::-webkit-slider-thumb { width: 22px; height: 76px; pointer-events: auto; cursor: ew-resize; }.range-handle::-moz-range-thumb { width: 22px; height: 76px; pointer-events: auto; cursor: ew-resize; }.range-inputs { margin-top: 10px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }.range-inputs label { display: flex; align-items: center; gap: 6px; color: #808b9c; font-size: 11px; }.range-inputs input[type=number] { width: 92px; padding: 7px; color: #d8e0eb; background: #111419; border: 1px solid #343b46; border-radius: 7px; }.range-inputs small { margin-left: auto; color: #7f8999; }.range-inputs small.error { color: #ef8e8e; }
.boundary-previews { margin-top: 12px; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }.boundary-previews figure { min-width: 0; margin: 0; padding: 8px; display: grid; grid-template-columns: 74px 1fr; align-items: center; gap: 9px; background: #11151a; border: 1px solid #303844; border-radius: 9px; }.boundary-previews img,.boundary-previews figure>span { width: 74px; height: 52px; object-fit: cover; display: grid; place-items: center; color: #697587; background: #090b0e; border-radius: 6px; font-size: 10px; }.boundary-previews figcaption { min-width: 0; display: grid; gap: 4px; }.boundary-previews b { color: #dce5f0; font-size: 11px; }.boundary-previews small { color: #748094; font-size: 9px; line-height: 1.4; }
.recipe-panel { min-height: 0; padding: 20px; overflow: auto; background: #171a20; border-left: 1px solid #2b3038; }.recipe-panel>section { padding-bottom: 20px; margin-bottom: 20px; border-bottom: 1px solid #2c323b; }textarea { box-sizing: border-box; width: 100%; margin-top: 10px; padding: 12px; resize: vertical; color: #e8edf4; background: #101318; border: 1px solid #343c48; border-radius: 10px; font: inherit; line-height: 1.6; }.section-title { display: flex; align-items: center; justify-content: space-between; }.section-title b { color: #aab6c7; font-size: 11px; }.reference-row { margin-top: 10px; padding: 8px; display: grid; grid-template-columns: auto 48px 1fr auto; align-items: center; gap: 9px; background: #20242b; border: 1px solid #323943; border-radius: 9px; cursor: pointer; }.reference-row img { width: 48px; height: 48px; object-fit: cover; border-radius: 7px; }.reference-row span { display: grid; }.reference-row small { color: #727e91; }.reference-row em { color: #74c79e; font-size: 9px; font-style: normal; }.reference-row em.omitted { color: #e5a083; }.empty-copy { color: #727e8f; font-size: 12px; }.compile-plan { padding: 14px !important; background: #1d2429; border: 1px solid #395144 !important; border-radius: 11px; }.cost-grid { margin: 12px 0; display: grid; grid-template-columns: repeat(3,1fr); gap: 6px; }.cost-grid span { padding: 8px; display: grid; color: #808c9d; background: #14181d; border-radius: 7px; font-size: 9px; }.cost-grid strong { color: #e4eaf2; font-size: 15px; }.compile-plan p { color: #8fb7a3; font-size: 11px; }
.reference-title-actions { display: flex; align-items: center; gap: 8px; }.reference-title-actions button { min-height: 34px; padding: 6px 9px; color: #cfe6ff; background: #244e7c; border: 1px solid #3b6e9f; border-radius: 8px; cursor: pointer; }
footer { display: grid; gap: 9px; }footer button { min-height: 42px; padding: 9px 12px; display: flex; align-items: center; justify-content: center; gap: 7px; color: inherit; background: #29303a; border: 1px solid #414c5a; border-radius: 9px; cursor: pointer; }footer button :deep(svg) { width: 16px; }footer button.primary { color: #10141a; background: #e7edf6; border-color: #e7edf6; font-weight: 800; }footer button:disabled { opacity: .38; cursor: not-allowed; }footer small { color: #6e798a; text-align: center; line-height: 1.5; }
.cost-review { padding: 24px; }.cost-review h2 { margin: 0 0 8px; font-size: 19px; }.cost-review>p { margin: 0 0 20px; color: #96a2b2; }.cost-review dl { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; overflow: hidden; background: #343b45; border: 1px solid #343b45; border-radius: 12px; }.cost-review dl div { min-width: 0; padding: 12px; display: grid; gap: 5px; background: #1a1e24; }.cost-review dt { color: #7f8b9b; font-size: 10px; }.cost-review dd { min-width: 0; margin: 0; overflow-wrap: anywhere; color: #e0e7ef; }.cost-review code { color: #a9c9ee; font-size: 11px; }.cost-boundaries { margin-top: 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.cost-boundaries figure { margin: 0; overflow: hidden; background: #111419; border: 1px solid #333b46; border-radius: 10px; }.cost-boundaries img { width: 100%; height: 160px; display: block; object-fit: contain; background: #080a0d; }.cost-boundaries figcaption { padding: 9px; color: #aab5c4; font-size: 11px; }.cost-cancel,.cost-confirm { width: auto !important; min-width: 170px; padding: 0 16px; }.cost-confirm { color: #0e141b !important; background: #dbe9f8 !important; }.cost-cancel { color: #cad3de !important; background: #313741 !important; }
.embedded .editor-header { padding: 0 18px; }.embedded .editor-header h1 { font-size: 14px; }.embedded .editor-layout { grid-template-columns: minmax(360px, .9fr) minmax(520px, 1.35fr); overflow: hidden; }.embedded .preview-column { padding: 14px 18px; display: block; overflow: auto; }.embedded .video-stage,.embedded .annotation-tools { display: none; }.embedded .range-panel { min-height: 170px; display: grid; align-content: center; }.embedded .filmstrip { height: 104px; }.embedded .filmstrip img { height: 104px; }.embedded .range-handle::-webkit-slider-thumb { height: 104px; }.embedded .range-handle::-moz-range-thumb { height: 104px; }.embedded .recipe-panel { padding: 14px 18px; display: grid; grid-template-columns: minmax(220px, .85fr) minmax(300px, 1.15fr); align-content: start; gap: 10px; border-left: 1px solid #2b3038; }.embedded .recipe-panel > section { min-width: 0; max-height: 200px; margin: 0; padding: 12px; overflow: auto; background: #1d2128; border: 1px solid #303742; border-radius: 10px; }.embedded .recipe-panel > section.compile-plan,.embedded .recipe-panel > footer { grid-column: 1 / -1; }.embedded .recipe-panel textarea { min-height: 88px; margin-top: 8px; }.embedded .reference-row { grid-template-columns: auto 42px 1fr; }.embedded .reference-row img { width: 42px; height: 42px; }.embedded .reference-row em { display: none; }.embedded .recipe-panel footer { grid-template-columns: 1fr 1fr; align-items: center; }.embedded .recipe-panel footer small { grid-column: 1 / -1; }
@media (max-width: 980px) { .editor-layout { grid-template-columns: 1fr; overflow: auto; }.preview-column { min-height: 650px; }.recipe-panel { border: 1px solid #2b3038; }.annotation-tools span { display: none; } }
@media (max-width: 1100px) { .embedded .editor-layout { grid-template-columns: 1fr; overflow: auto; }.embedded .preview-column { min-height: 190px; }.embedded .recipe-panel { grid-template-columns: 1fr; border-left: 0; }.embedded .recipe-panel > section.compile-plan,.embedded .recipe-panel > footer { grid-column: auto; } }
</style>
