<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';

const props = withDefaults(defineProps<{ before: string; after: string; totalFrames: number; parentRevision?: number; beforeLabel?: string; afterLabel?: string; stale: boolean; initialFrame?: number; frameOffset?: number }>(), { initialFrame: 0, frameOffset: 0 });
const emit = defineEmits<{ frame: [number] }>();
const videos = ref<HTMLVideoElement[]>([]);
const linked = ref(true); const audio = ref<'muted' | 'before' | 'after'>('muted');
const focus = ref('both'); const frames = ref([0, 0]); const commonFrame = ref(Math.max(0, Math.min(props.totalFrames - 1, props.initialFrame)));
const state = ref('等待两侧视频加载'); const playing = ref(false); const aligning = ref(false);
const capable = computed(() => videos.value.length === 2 && videos.value.every(v => typeof v.requestVideoFrameCallback === 'function'));
let range: { startFrame: number; endFrame: number } | null = null;
let positionReady = false;
let epoch = 0; let disposed = false; let pendingResume = false; let starting = false;
const callbacks = new Map<HTMLVideoElement, number>();
const seekCancellations = new Set<() => void>();
function stopPlayback() { pendingResume = false; playing.value = false; starting = false; videos.value.forEach(v => v.pause()); }
function pause() { ++epoch; seekCancellations.forEach(cancel => cancel()); aligning.value = false; stopPlayback(); state.value = '已暂停'; }
async function seek(frame: number, resume = false) {
  const token = ++epoch; seekCancellations.forEach(cancel => cancel()); positionReady = false; stopPlayback(); aligning.value = true; state.value = '正在对齐两侧画面';
  const target = Math.max(0, Math.min(props.totalFrames - 1, Math.trunc(frame)));
  commonFrame.value = target;
  const results = await Promise.all(videos.value.map((v, index) => new Promise<boolean>(resolve => {
    let frameCallback: number | undefined; let settled = false;
    const finish = (ok: boolean) => { if (settled) return; settled = true; seekCancellations.delete(cancel); clearTimeout(timeout); v.removeEventListener('seeked', onSeek); v.removeEventListener('canplay', onReady); if (frameCallback !== undefined) v.cancelVideoFrameCallback(frameCallback); resolve(ok); };
    const cancel = () => finish(false);
    seekCancellations.add(cancel);
    let requested = false; let presentedFrame = false;
    const onSeek = () => {
      if (token !== epoch || disposed) { finish(false); return; }
      if (!requested || Math.floor(v.currentTime * 24 + .01) !== target) { requestTarget(); return; }
      if (!capable.value || presentedFrame) finish(true);
    };
    const timeout = window.setTimeout(() => finish(false), 4000);
    const onReady = () => { if (!requested) requestTarget(); else if (presentedFrame && !v.seeking && Math.floor(v.currentTime * 24 + .01) === target) finish(true); };
    v.addEventListener('seeked', onSeek);
    v.addEventListener('canplay', onReady);
    const presentedTarget = (_now: number, metadata: VideoFrameCallbackMetadata) => {
      if (token !== epoch || disposed) { finish(false); return; }
      presentedFrame = Math.floor(metadata.mediaTime * 24 + .01) === target && Math.floor(v.currentTime * 24 + .01) === target;
      if (presentedFrame && !v.seeking) finish(true);
      else if (!settled && !presentedFrame) frameCallback = v.requestVideoFrameCallback(presentedTarget);
    };
    function requestTarget() {
      if (settled || token !== epoch || disposed || v.seeking || v.readyState < 2) return;
      if (!v.seeking && frames.value[index] === target && Math.floor(v.currentTime * 24 + .01) === target) { finish(true); return; }
      requested = true; presentedFrame = false;
      if (frameCallback !== undefined) v.cancelVideoFrameCallback(frameCallback);
      if (capable.value) frameCallback = v.requestVideoFrameCallback(presentedTarget);
      v.currentTime = (target + .001) / 24;
    }
    // Cancelling our listeners cannot cancel a decoder seek. Let it finish before
    // issuing the latest target, so an older seek cannot land after the new one.
    if (!v.seeking) requestTarget();
  })));
  if (disposed || token !== epoch) return;
  aligning.value = false;
  if (!results.every(Boolean) || results.length !== 2) { state.value = '定位尚未完成，请等待加载后重新定位'; return; }
  positionReady = true; commonFrame.value = target; emit('frame', target);
  state.value = capable.value ? '两侧定位就绪' : '浏览器不支持呈现帧反馈，定位同步精度受限';
  if (resume) await play();
}
async function play() {
  if (aligning.value) return;
  const token = epoch; starting = true;
  const outcomes = await Promise.allSettled(videos.value.map(v => v.play()));
  if (token !== epoch || disposed) return;
  starting = false;
  const rejected = outcomes.find(o => o.status === 'rejected');
  if (rejected?.status === 'rejected') { pause(); state.value = `播放未能开始：${rejected.reason instanceof Error ? rejected.reason.message : '请重新点击播放'}`; }
  else { playing.value = true; state.value = linked.value ? '联动播放中' : '独立查看中'; if (videos.value.some(v => v.readyState < 3)) stalled(); }
}
async function playRange(window: { startFrame: number; endFrame: number }) { range = window; await seek(window.startFrame, window.endFrame - window.startFrame > 1); if (window.endFrame - window.startFrame === 1) state.value = "单帧选区：两侧停留在所选帧"; }
async function playWhole() { range = null; await seek(0, true); }
function stopLoop() { range = null; }
function presented(v: HTMLVideoElement, index: number, metadata: VideoFrameCallbackMetadata) {
  frames.value[index] = Math.min(props.totalFrames - 1, Math.floor(metadata.mediaTime * 24 + .01));
  if (index === 0 && positionReady && !aligning.value && linked.value) { commonFrame.value = frames.value[0]!; emit('frame', commonFrame.value); }
  if (linked.value && playing.value && !aligning.value) {
    if (range && frames.value[index]! >= range.endFrame - 1) { void seek(range.startFrame, true); }
    else if (Math.abs(frames.value[0]! - frames.value[1]!) > 1) { state.value = '检测到超过一帧偏差，正在重新对齐'; void seek(commonFrame.value, true); }
  }
  if (!disposed) callbacks.set(v, v.requestVideoFrameCallback((_now, m) => presented(v, index, m)));
}
function loaded(index: number) {
  const v = videos.value[index]; if (!v) return;
  const previous = callbacks.get(v); if (previous !== undefined) v.cancelVideoFrameCallback(previous);
  if (v.requestVideoFrameCallback) callbacks.set(v, v.requestVideoFrameCallback((_now, m) => presented(v, index, m)));
  if (videos.value.length === 2 && videos.value.every(video => video.readyState >= 2)) void seek(commonFrame.value);
}
function stalled() {
  if (!linked.value || aligning.value || starting || !playing.value) return;
  pause(); pendingResume = true; state.value = '一侧正在缓冲，两侧已暂停';
}
function ready() { if (pendingResume && videos.value.every(v => v.readyState >= 3)) { pendingResume = false; void seek(commonFrame.value, true); } }
function failed() { pause(); state.value = '一侧视频加载失败，两侧已停止'; }
function ended() { if (!linked.value) return; if (range) void seek(range.startFrame, true); else { pause(); state.value = '完整播放结束'; } }
async function toggleLinked() { pause(); linked.value = !linked.value; range = null; if (linked.value) await seek(commonFrame.value); else state.value = '独立查看：各自使用播放与定位控件'; }
watch(audio, () => videos.value.forEach((v, i) => { v.muted = audio.value !== (i === 0 ? 'before' : 'after'); }));
watch(() => [props.before, props.after], async () => { ++epoch; pause(); callbacks.forEach((id, v) => v.cancelVideoFrameCallback(id)); callbacks.clear(); frames.value = [0, 0]; commonFrame.value = Math.min(props.totalFrames - 1, Math.max(0, props.initialFrame)); range = null; await nextTick(); });
onBeforeUnmount(() => { disposed = true; ++epoch; pause(); callbacks.forEach((id, v) => v.cancelVideoFrameCallback(id)); });
defineExpose({ seek, playRange, playWhole, stopLoop, pause });
</script>

<template>
  <section class="linked-comparison" aria-label="双播放器真实试装对比">
    <div class="screens" :class="`focus-${focus}`">
      <figure v-for="(src, index) in [before, after]" v-show="focus === 'both' || focus === String(index)" :key="index">
        <figcaption>{{ index === 0 ? (beforeLabel || `修改前 · 父草稿 v${parentRevision ?? '?'}`) : (afterLabel || '修改后 · 本次修改片段') }}<b v-if="index === 1 && stale"> · 上一份试装</b></figcaption>
        <video :ref="el => { if (el) videos[index] = el as HTMLVideoElement; }" :src="src" :aria-label="index === 0 ? '修改前视频' : '修改后视频'" :controls="!linked" :muted="audio !== (index === 0 ? 'before' : 'after')" playsinline preload="auto" @loadeddata="loaded(index)" @waiting="stalled" @canplay="ready" @error="failed" @ended="ended" />
        <small>{{ index === 0 ? 'A' : 'B' }} · 草稿第 {{ (frames[index] ?? 0) + frameOffset }} 帧 · 片段 {{ frames[index] }} / {{ totalFrames - 1 }} 帧</small>
      </figure>
    </div>
    <div class="controls">
      <button class="secondary" :aria-pressed="linked" @click="toggleLinked">{{ linked ? '解除联动' : '恢复联动' }}</button>
      <button class="secondary" :disabled="!linked || aligning" @click="playing ? pause() : play()">{{ playing ? '暂停两侧' : '联动播放' }}</button>
      <label>试听<select v-model="audio" aria-label="对比声音"><option value="muted">静音对比</option><option value="before">修改前声音 A</option><option value="after">修改后声音 B</option></select></label>
      <label>放大<select v-model="focus" aria-label="放大查看"><option value="both">并排</option><option value="0">仅看左侧 A</option><option value="1">仅看右侧 B</option></select></label>
    </div>
    <p role="status">{{ state }}<span v-if="!capable"> · 当前环境不支持可靠呈现帧反馈</span></p>
  </section>
</template>

<style scoped>
.linked-comparison { background: #262523; color: #fff; padding: 10px 14px; }.screens { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.screens:not(.focus-both) { grid-template-columns: 1fr; }figure { margin: 0; min-width: 0; display: grid; justify-items: center; gap: 4px; }video { width: 100%; height: clamp(140px, calc(100dvh - 610px), 300px); object-fit: contain; background: #151515; }figcaption,small { font-size: 12px; }b { color: #ffcf89; }.controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: center; margin-top: 8px; }label { display: flex; gap: 4px; align-items: center; font-size: 12px; }select { width: auto; }p { margin: 6px 0 0; font-size: 12px; text-align: center; }@media (max-width: 600px) { .screens { gap: 6px; }video { height: 190px; }figcaption { font-size: 11px; } }
</style>

<style scoped>.controls button { min-height: 30px; padding: 5px 10px; font-size: 12px; }.controls { gap: 6px; }video { border-radius: 4px; }</style>
