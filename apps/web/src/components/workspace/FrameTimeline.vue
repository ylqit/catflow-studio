<script setup lang="ts">
import { computed, ref } from "vue";
import type { FrameRangeDto } from "../../api/types";
import { clampIssueEnd, clampIssueStart, formatFrameTimecode } from "../../videoRepair";

const props = withDefaults(defineProps<{
  totalFrames: number; modelValue: FrameRangeDto; currentFrame: number;
  disabled?: boolean; context?: FrameRangeDto | null;
  thumbnails?: Array<{ frame: number; url: string }>;
}>(), { disabled: false, context: null, thumbnails: () => [] });
const emit = defineEmits<{ "update:modelValue": [FrameRangeDto]; seek: [number]; play: [] }>();
const track = ref<HTMLElement>();
const zoom = ref(1);
const range = computed(() => props.modelValue);
const drag = ref<{ mode: "in" | "out" | "move"; start: number; range: FrameRangeDto }>();
function frameAt(clientX: number) {
  const box = track.value!.getBoundingClientRect();
  // The content rect includes zoom and scroll displacement. Handles never change this domain.
  return Math.max(0, Math.min(props.totalFrames, Math.round((clientX - box.left) / box.width * props.totalFrames)));
}
function update(mode: "in" | "out", value: number) {
  if (props.disabled || !Number.isFinite(value)) return;
  const next = mode === "in"
    ? { ...range.value, startFrame: clampIssueStart(value, range.value.endFrame, props.totalFrames) }
    : { ...range.value, endFrame: clampIssueEnd(value, range.value.startFrame, props.totalFrames) };
  emit("update:modelValue", next);
  emit("seek", mode === "in" ? next.startFrame : next.endFrame - 1);
}
function start(event: PointerEvent, mode: "in" | "out" | "move") {
  if (props.disabled) return;
  event.preventDefault(); event.stopPropagation();
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  drag.value = { mode, start: frameAt(event.clientX), range: { ...range.value } };
}
function move(event: PointerEvent) {
  if (!drag.value || props.disabled) return;
  const frame = frameAt(event.clientX);
  if (drag.value.mode !== "move") update(drag.value.mode, frame);
  else {
    const length = drag.value.range.endFrame - drag.value.range.startFrame;
    const first = Math.max(0, Math.min(props.totalFrames - length, drag.value.range.startFrame + frame - drag.value.start));
    emit("update:modelValue", { startFrame: first, endFrame: first + length });
    emit("seek", first);
  }
}
function key(event: KeyboardEvent, mode: "in" | "out") {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const value = mode === "in" ? range.value.startFrame : range.value.endFrame;
  update(mode, event.key === "Home" ? 0 : event.key === "End" ? props.totalFrames
    : value + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 10 : 1));
}
function style(window: FrameRangeDto) {
  return { left: `${window.startFrame / props.totalFrames * 100}%`, width: `${(window.endFrame - window.startFrame) / props.totalFrames * 100}%` };
}
function timecode(mode: "in" | "out", input: HTMLInputElement) {
  const match = /^(\d{2,}):(\d{2}):(\d{2}):(\d{2})$/.exec(input.value.trim());
  if (!match || Number(match[2]) >= 60 || Number(match[3]) >= 60 || Number(match[4]) >= 24) {
    input.setCustomValidity("请使用 时:分:秒:帧，帧数为 00–23。"); input.reportValidity(); return;
  }
  input.setCustomValidity("");
  update(mode, ((Number(match[1]) * 60 + Number(match[2])) * 60 + Number(match[3])) * 24 + Number(match[4]));
}
</script>

<template>
  <section class="frame-editor" aria-label="精确帧时间轴">
    <header><b>选区精确到帧 · 24 fps</b><label>时间轴缩放 <select v-model.number="zoom"><option :value="1">100%</option><option :value="2">200%</option><option :value="4">400%</option></select></label></header>
    <div class="scroll-viewport">
      <div ref="track" class="frame-track" :style="{ width: `${zoom * 100}%` }" data-testid="frame-track" @pointerdown.self="emit('seek', Math.min(totalFrames - 1, frameAt($event.clientX)))">
        <div class="stills" @pointerdown="emit('seek', Math.min(totalFrames - 1, frameAt($event.clientX)))">
          <img v-for="image in thumbnails" :key="image.frame" :src="image.url" :alt="`第 ${image.frame} 帧`" :title="formatFrameTimecode(image.frame)" draggable="false" />
          <span v-if="!thumbnails.length">完整预览准备后显示对应帧的缩略图</span>
        </div>
        <div v-if="context" class="context-window" :style="style(context)" />
        <div class="selected-window" data-testid="selected-window" :style="style(range)" @pointerdown="start($event, 'move')" @pointermove="move" @pointerup="drag = undefined" @lostpointercapture="drag = undefined" />
        <button v-for="mode in (['in', 'out'] as const)" :key="mode" type="button" class="handle" :data-testid="`${mode}-handle`"
          role="slider" :aria-label="mode === 'in' ? '选区入点' : '选区出点'" :aria-disabled="disabled" :aria-valuemin="0" :aria-valuemax="totalFrames"
          :aria-valuenow="mode === 'in' ? range.startFrame : range.endFrame" :style="{ left: `${(mode === 'in' ? range.startFrame : range.endFrame) / totalFrames * 100}%` }"
          @pointerdown="start($event, mode)" @pointermove="move" @pointerup="drag = undefined" @lostpointercapture="drag = undefined" @keydown="key($event, mode)" />
        <i class="playhead" :style="{ left: `${currentFrame / totalFrames * 100}%` }" />
      </div>
    </div>
    <div class="inputs">
      <label>预览定位帧<input aria-label="预览定位帧" type="number" min="0" :max="totalFrames - 1" :value="currentFrame" @change="emit('seek', Number(($event.target as HTMLInputElement).value))" /></label>
      <label>入点时间码<input aria-label="入点时间码" :disabled="disabled" :value="formatFrameTimecode(range.startFrame)" @change="timecode('in', $event.target as HTMLInputElement)" /></label>
      <label>出点时间码<input aria-label="出点时间码" :disabled="disabled" :value="formatFrameTimecode(range.endFrame)" @change="timecode('out', $event.target as HTMLInputElement)" /></label>
      <label>入点帧（包含）<input aria-label="入点帧（包含）" type="number" min="0" :max="totalFrames - 1" :disabled="disabled" :value="range.startFrame" @change="update('in', Number(($event.target as HTMLInputElement).value))" /><code>{{ formatFrameTimecode(range.startFrame) }}</code></label>
      <label>出点帧（不包含）<input aria-label="出点帧（不包含）" type="number" min="1" :max="totalFrames" :disabled="disabled" :value="range.endFrame" @change="update('out', Number(($event.target as HTMLInputElement).value))" /><code>{{ formatFrameTimecode(range.endFrame) }}</code></label>
      <p aria-live="polite">{{ range.endFrame - range.startFrame }} 帧 · {{ ((range.endFrame - range.startFrame) / 24).toFixed(3) }} 秒<br />当前呈现帧 {{ currentFrame }} / {{ totalFrames - 1 }}</p>
      <button type="button" class="secondary" @click="emit('play')">播放选区</button>
    </div>
    <small>拖动两端调整，拖动色块整体移动；手柄获得焦点后，方向键逐帧调整，Shift 调整 10 帧。选区允许 1 帧，不代表模型能保证一帧级语义修复。</small>
  </section>
</template>

<style scoped>
.frame-editor { padding: 20px 24px; min-width: 0; }
header,.inputs { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; justify-content: space-between; }
header label { display: flex; gap: 8px; align-items: center; } select { width: 95px; }
.scroll-viewport { overflow-x: auto; padding: 10px 14px; margin: 4px -14px; }
.frame-track { position: relative; height: 100px; background: #36312b; touch-action: none; }
.stills { display: flex; height: 100%; overflow: hidden; color: #fff; align-items: center; }
.stills img { height: 100%; flex: 1; min-width: 0; object-fit: cover; user-select: none; }
.context-window,.selected-window { position: absolute; inset-block: 0; box-sizing: border-box; }
.context-window { background: #dbc46a44; border-block: 3px solid #bf9f34; pointer-events: none; }
.selected-window { box-shadow: inset 0 0 0 3px #df745c; background: #e2765d22; cursor: grab; touch-action: none; }
.handle { position: absolute; top: 0; height: 100%; width: 18px; padding: 0; margin: 0; transform: translateX(-50%); box-sizing: border-box; border: 3px solid #fff; border-radius: 7px; background: #d87560; cursor: ew-resize; z-index: 3; touch-action: none; }
.handle:focus-visible { outline: 3px solid #235a9a; outline-offset: 2px; }
.handle[aria-disabled="true"] { cursor: default; background: #8b7771; }
.playhead { position: absolute; top: 0; bottom: 0; width: 2px; background: #fff; transform: translateX(-50%); pointer-events: none; z-index: 4; }
.inputs label { display: grid; gap: 4px; } input { width: 120px; } small { color: var(--muted); display: block; margin-top: 8px; line-height: 1.5; }
</style>
