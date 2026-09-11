<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { FrameRangeDto } from "../../api/types";
import { clampIssueEnd, clampIssueStart, formatFrameTimecode } from "../../videoRepair";

const props = withDefaults(defineProps<{
  totalFrames: number; modelValue: FrameRangeDto; currentFrame: number;
  disabled?: boolean; context?: FrameRangeDto | null;
  thumbnails?: Array<{ frame: number; url: string }>;
  fixedLength?: boolean; compact?: boolean; label?: string; rate?: number;
  minDurationFrames?: number; allowedRange?: FrameRangeDto;
  segments?: Array<{ startFrame: number; endFrame: number; label: string }>;
}>(), { disabled: false, context: null, thumbnails: () => [], minDurationFrames: 1 });
const emit = defineEmits<{ "update:modelValue": [FrameRangeDto]; seek: [number]; play: []; invalid: [boolean] }>();
const track = ref<HTMLElement>();
const zoom = ref(1);
const rangeError = ref("");
const positionDraft = ref<string | null>(null);
const range = computed(() => props.modelValue);
const bounds = computed(() => props.allowedRange ?? { startFrame: 0, endFrame: props.totalFrames });
watch(() => [props.modelValue.startFrame, props.modelValue.endFrame], () => {
  rangeError.value = ""; emit("invalid", false);
});
const drag = ref<{ mode: "in" | "out" | "move"; start: number; range: FrameRangeDto }>();
function frameAt(clientX: number) {
  const box = track.value!.getBoundingClientRect();
  // The content rect includes zoom and scroll displacement. Handles never change this domain.
  return Math.max(0, Math.min(props.totalFrames, Math.round((clientX - box.left) / box.width * props.totalFrames)));
}
function update(mode: "in" | "out", value: number) {
  if (props.disabled || !Number.isFinite(value)) return;
  if (props.fixedLength) {
    const length = range.value.endFrame - range.value.startFrame;
    const first = Math.max(bounds.value.startFrame, Math.min(bounds.value.endFrame - length, Math.trunc(value) - (mode === 'out' ? length : 0)));
    emit('update:modelValue', { startFrame: first, endFrame: first + length });
    return;
  }
  const next = mode === "in"
    ? { ...range.value, startFrame: Math.max(bounds.value.startFrame, clampIssueStart(value, range.value.endFrame, bounds.value.endFrame, props.minDurationFrames)) }
    : { ...range.value, endFrame: Math.min(bounds.value.endFrame, clampIssueEnd(value, range.value.startFrame, bounds.value.endFrame, props.minDurationFrames)) };
  rangeError.value = ""; emit("invalid", false);
  emit("update:modelValue", next);
  emit("seek", mode === "in" ? next.startFrame : next.endFrame - 1);
}
function start(event: PointerEvent, mode: "in" | "out" | "move") {
  if (props.disabled) { emit("seek", Math.min(props.totalFrames - 1, frameAt(event.clientX))); return; }
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
    const first = Math.max(bounds.value.startFrame, Math.min(bounds.value.endFrame - length, drag.value.range.startFrame + frame - drag.value.start));
    emit("update:modelValue", { startFrame: first, endFrame: first + length });
    if (!props.fixedLength) emit("seek", first);
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
function seconds(mode: "in" | "out", input: HTMLInputElement) {
  if (props.disabled) return;
  const value = Math.round(Number(input.value) * (props.rate || 24));
  const next = { ...range.value, [mode === 'in' ? 'startFrame' : 'endFrame']: value };
  const length = next.endFrame - next.startFrame;
  if (!input.value.trim() || !Number.isFinite(value) || next.startFrame < bounds.value.startFrame || next.endFrame > bounds.value.endFrame || length < props.minDurationFrames || length > 360) {
    rangeError.value = `请选择有效范围内 ${props.minDurationFrames}–360 帧的区间。`;
    emit("invalid", true); input.setCustomValidity(rangeError.value); input.reportValidity(); return;
  }
  input.setCustomValidity(''); rangeError.value = ''; emit('invalid', false); emit('update:modelValue', next); emit('seek', mode === 'in' ? next.startFrame : next.endFrame - 1);
}
</script>

<template>
  <section class="frame-editor" :class="{ compact }" :aria-label="label || '精确帧时间轴'">
    <header><b>{{ label || '选区精确到帧' }} · {{ rate || 24 }} fps · [{{ range.startFrame }}, {{ range.endFrame }})</b><label>时间轴缩放 <select v-model.number="zoom"><option :value="1">100%</option><option :value="2">200%</option><option :value="4">400%</option></select></label></header>
    <div class="scroll-viewport">
      <div ref="track" class="frame-track" :style="{ width: `${zoom * 100}%` }" data-testid="frame-track" @pointerdown.self="emit('seek', Math.min(totalFrames - 1, frameAt($event.clientX)))">
        <div class="stills" @pointerdown="emit('seek', Math.min(totalFrames - 1, frameAt($event.clientX)))">
          <img v-for="image in thumbnails" :key="image.frame" :src="image.url" :alt="`第 ${image.frame} 帧`" :title="formatFrameTimecode(image.frame)" draggable="false" />
          <span v-if="!thumbnails.length">完整预览准备后显示对应帧的缩略图</span>
        </div>
        <div v-if="context" class="context-window" :style="style(context)" />
        <div v-for="segment in segments" :key="segment.startFrame" class="applied-window" :style="style(segment)" :title="segment.label"><span>{{ segment.label }}</span></div>
        <div class="selected-window" data-testid="selected-window" :style="style(range)" @pointerdown="start($event, 'move')" @pointermove="move" @pointerup="drag = undefined" @lostpointercapture="drag = undefined" />
        <button v-for="mode in (['in', 'out'] as const)" :key="mode" type="button" class="handle" :data-testid="`${mode}-handle`"
          role="slider" :aria-label="mode === 'in' ? '选区入点' : '选区出点'" :aria-disabled="disabled" :aria-valuemin="0" :aria-valuemax="totalFrames"
          :aria-valuenow="mode === 'in' ? range.startFrame : range.endFrame" :style="{ left: `${(mode === 'in' ? range.startFrame : range.endFrame) / totalFrames * 100}%` }"
          @pointerdown="start($event, mode)" @pointermove="move" @pointerup="drag = undefined" @lostpointercapture="drag = undefined" @keydown="key($event, mode)" />
        <i class="playhead" :style="{ left: `${currentFrame / totalFrames * 100}%` }" />
      </div>
    </div>
    <div class="inputs" v-if="!compact">
      <label>预览定位帧<input aria-label="预览定位帧" type="number" min="0" :max="totalFrames - 1" :value="positionDraft ?? currentFrame" @focus="positionDraft = String(currentFrame)" @input="positionDraft = ($event.target as HTMLInputElement).value" @change="emit('seek', Math.max(0, Math.min(totalFrames - 1, Math.trunc(Number(($event.target as HTMLInputElement).value) || 0))))" @blur="positionDraft = null" /></label>
      <label>入点时间码<input aria-label="入点时间码" :disabled="disabled" :value="formatFrameTimecode(range.startFrame)" @change="timecode('in', $event.target as HTMLInputElement)" /></label>
      <label>出点时间码<input aria-label="出点时间码" :disabled="disabled" :value="formatFrameTimecode(range.endFrame)" @change="timecode('out', $event.target as HTMLInputElement)" /></label>
      <label>入点帧（包含）<input aria-label="入点帧（包含）" type="number" min="0" :max="totalFrames - 1" :disabled="disabled" :value="range.startFrame" @change="update('in', Number(($event.target as HTMLInputElement).value))" /><code>{{ formatFrameTimecode(range.startFrame) }}</code></label>
      <label>出点帧（不包含）<input aria-label="出点帧（不包含）" type="number" min="1" :max="totalFrames" :disabled="disabled" :value="range.endFrame" @change="update('out', Number(($event.target as HTMLInputElement).value))" /><code>{{ formatFrameTimecode(range.endFrame) }}</code></label>
      <p aria-live="polite">{{ range.endFrame - range.startFrame }} 帧 · {{ ((range.endFrame - range.startFrame) / 24).toFixed(3) }} 秒<br />当前呈现帧 {{ currentFrame }} / {{ totalFrames - 1 }}</p>
      <button type="button" class="secondary" @click="emit('play')">播放选区</button>
    </div>
    <div v-else class="compact-inputs"><span>0 秒 / 0 帧</span><label v-if="fixedLength">取用起点<input aria-label="可视取用起点" type="number" min="0" :max="totalFrames - (range.endFrame - range.startFrame)" :value="range.startFrame" :disabled="disabled" @change="update('in', Number(($event.target as HTMLInputElement).value))" /></label><label v-else>定位帧<input aria-label="草稿定位帧" type="number" min="0" :max="totalFrames - 1" :value="positionDraft ?? currentFrame" @focus="positionDraft = String(currentFrame)" @input="positionDraft = ($event.target as HTMLInputElement).value" @change="emit('seek', Math.max(0, Math.min(totalFrames - 1, Math.trunc(Number(($event.target as HTMLInputElement).value) || 0))))" @blur="positionDraft = null" /></label><span>{{ (totalFrames / (rate || 24)).toFixed(3) }} 秒 / {{ totalFrames }} 帧</span></div>
    <div v-if="!disabled && !fixedLength" class="seconds-inputs">
      <label>开始（秒）<input aria-label="选区开始秒数" type="number" step="any" :value="(range.startFrame / (rate || 24)).toFixed(3)" @change="seconds('in', $event.target as HTMLInputElement)" /></label>
      <label>结束（秒）<input aria-label="选区结束秒数" type="number" step="any" :value="(range.endFrame / (rate || 24)).toFixed(3)" @change="seconds('out', $event.target as HTMLInputElement)" /></label>
      <template v-if="compact">
        <label>入点帧<input aria-label="选区入点帧" type="number" :min="bounds.startFrame" :max="range.endFrame - minDurationFrames" :value="range.startFrame" @change="update('in', Number(($event.target as HTMLInputElement).value))" /></label>
        <label>出点帧<input aria-label="选区出点帧" type="number" :min="range.startFrame + minDurationFrames" :max="bounds.endFrame" :value="range.endFrame" @change="update('out', Number(($event.target as HTMLInputElement).value))" /></label>
      </template>
      <b>{{ range.endFrame - range.startFrame }} 帧 · {{ ((range.endFrame - range.startFrame) / (rate || 24)).toFixed(3) }} 秒</b>
      <p v-if="rangeError" role="alert">{{ rangeError }}</p>
    </div>
    <small v-if="!compact">拖动两端调整，拖动色块整体移动；方向键逐帧调整，Shift 调整 10 帧。</small>
  </section>
</template>

<style scoped>
.frame-editor { padding: 20px 24px; min-width: 0; }
.seconds-inputs { display:flex; gap:12px; flex-wrap:wrap; align-items:center; font-size:12px; margin-top:6px; }.seconds-inputs label { display:flex; align-items:center; gap:6px; }.seconds-inputs input { width:90px; }.seconds-inputs p { color:#9b4028; margin:0; }
.frame-editor.compact { padding: 8px 16px; }.compact .frame-track { height: 46px; }.compact header { font-size: 12px; }.compact .scroll-viewport { padding-block: 5px; }.compact-inputs { display: flex; justify-content: space-between; align-items: center; font-size: 11px; gap: 8px; }.compact-inputs label { display: flex; align-items: center; gap: 6px; }.compact-inputs input { width: 80px; padding: 3px 6px; }.applied-window { position: absolute; bottom: 0; height: 16px; background: #26735fbb; color: white; font-size: 10px; pointer-events: none; overflow: hidden; }
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

<style scoped>.frame-editor.compact { padding: 6px 16px; }.compact .frame-track { height: 34px; }.compact .scroll-viewport { padding-block: 3px; margin-block: 2px; }.compact header { gap: 6px; min-height: 20px; }.compact header select { padding: 0 3px; height: 22px; }.compact-inputs input { height: 22px; }.compact .handle { width: 12px; border-width: 2px; }</style>
