<script setup lang="ts">
import ProviderPrompt from "../ProviderPrompt.vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { api, ApiError } from '../../api/client';
import type { AssetDto, ShotMediaPreviewDto, ShotProductionContextDto } from '../../api/types';
import { billingPresentation, jobPresentation, paidModelBlockedReason, type PaidModelRuntime } from '../../presentation';
import { pendingIdempotencyKey, settleIdempotencyKey } from '../../idempotency';
import FrameTimeline from './FrameTimeline.vue';

const props = defineProps<{ projectId: string; planId: string; shotId: string; videoMode?: boolean; disabled?: boolean; runtime?: PaidModelRuntime | null }>();
const emit = defineEmits<{ changed: []; take: [{ shotId: string; assetId: string; sourceInFrame: number } | null] }>();
const context = ref<ShotProductionContextDto>(); const assets = ref<AssetDto[]>([]); const preview = ref<ShotMediaPreviewDto>();
const selectedImage = ref(''); const sourceVideo = ref(''); const sourceFrame = ref(0); const viewedFrame = ref(0);
const sourceReady = ref(false);
const sourcePlayer = ref<HTMLVideoElement>(); const selectedVideo = ref(''); const takeStart = ref(0);
const checks = ref<string[]>([]); const busy = ref(false); const error = ref(''); const open = ref(false); const unknown = ref(false);
let timer: ReturnType<typeof setInterval> | undefined; let disposed = false; let loading = false; let frameCallback: number | undefined; let frameOwner: HTMLVideoElement | undefined;
const labels = { identity_scale: '儿童、猫咪身份与比例正确', placement_state: '位置、视线与道具符合起点', movement_space: '动作有可见运动空间', action_start: '目标动作尚未提前完成', continuity: '与相邻镜头状态可衔接' };
const imageAssets = computed(() => assets.value.filter(a => a.mediaType === 'image' && !['edit_thumbnail', 'project_poster'].includes(a.role)));
const videoAssets = computed(() => assets.value.filter(a => a.mediaType === 'video' && a.role !== 'edit_comparison'));
const shotVideos = computed(() => videoAssets.value.filter(a => a.role === 'shot_video' && a.metadata.targetShotId === props.shotId));
const candidate = computed(() => assets.value.find(a => a.id === selectedVideo.value));
const source = computed(() => assets.value.find(a => a.id === sourceVideo.value));
const running = computed(() => context.value?.jobs.some(j => !['succeeded', 'failed', 'cancelled'].includes(j.status)) ?? false);
const paidBlock = computed(() => paidModelBlockedReason(props.runtime));
const stale = computed(() => !!context.value?.shot.confirmedFrame && !context.value.frameCurrent);
const takeError = computed(() => !candidate.value ? '请选择当前镜头的真实视频。' : candidate.value.metadata.shotDesignHash !== context.value?.designHash ? '素材已不对应当前镜头设计。' : !Number.isInteger(takeStart.value) || takeStart.value < 0 || takeStart.value + (context.value?.targetDurationFrames ?? 0) > Number(candidate.value.metadata.durationFrames) ? '素材不足，不能形成等长取用。' : '');
async function load() {
  if (loading || disposed || !open.value || props.disabled) return;
  loading = true; const plan = props.planId;
  try {
    const [loaded, items] = await Promise.all([api.shotProductionContext(props.projectId, plan, props.shotId), api.assets(props.projectId)]);
    if (disposed || plan !== props.planId) return;
    context.value = loaded; assets.value = items;
    if (!selectedImage.value) selectedImage.value = loaded.shot.confirmedFrame?.assetId ?? '';
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '镜头记录读取失败'; }
  finally { loading = false; }
}
async function prepare(purpose: 'shot_frame' | 'shot_video') {
  busy.value = true; error.value = '';
  try { preview.value = await api.previewShotMedia(props.projectId, props.planId, props.shotId, purpose); }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '准备失败'; }
  finally { busy.value = false; }
}
async function generate() {
  if (!preview.value || running.value || unknown.value || paidBlock.value) return;
  const frozen = preview.value; const scope = `shot-media:${props.projectId}:${props.shotId}:${frozen.purpose}`;
  busy.value = true; error.value = '';
  try {
    await api.generateShotMedia(props.projectId, { shotPlanVersionId: props.planId, shotId: props.shotId, purpose: frozen.purpose, expectedInputHash: frozen.inputHash, idempotencyKey: pendingIdempotencyKey(scope, frozen.inputHash) });
    settleIdempotencyKey(scope, frozen.inputHash); await load();
  } catch (reason) {
    unknown.value = !(reason instanceof ApiError && [400, 401, 403, 404, 409, 422, 503].includes(reason.status));
    error.value = unknown.value ? '提交结果未知，请检查任务记录，禁止重复提交。' : String(reason);
  } finally { busy.value = false; }
}
async function confirm() {
  if (!context.value || checks.value.length !== 5 || !selectedImage.value) return;
  busy.value = true; error.value = '';
  try { await api.confirmShotFrame(props.projectId, { shotPlanVersionId: props.planId, shotId: props.shotId, assetId: selectedImage.value, expectedDesignHash: context.value.designHash, checks: checks.value }); preview.value = undefined; emit('changed'); }
  catch (reason) { error.value = String(reason); } finally { busy.value = false; }
}
function observeFrame() {
  const video = sourcePlayer.value; if (!video) return;
  sourceReady.value = video.readyState >= 2;
  if (frameCallback !== undefined) frameOwner?.cancelVideoFrameCallback?.(frameCallback);
  frameOwner = video;
  const rate = Number(source.value?.metadata.frameRateNumerator ?? 24) / Number(source.value?.metadata.frameRateDenominator ?? 1);
  const update = (_now: number, meta: VideoFrameCallbackMetadata) => { viewedFrame.value = Math.floor(meta.mediaTime * rate + .01); if (!disposed && sourcePlayer.value === video) frameCallback = video.requestVideoFrameCallback(update); };
  if (video.requestVideoFrameCallback) frameCallback = video.requestVideoFrameCallback(update);
}
function locateSource() { if (sourcePlayer.value && source.value) { sourcePlayer.value.pause(); sourcePlayer.value.currentTime = (sourceFrame.value + .001) * Number(source.value.metadata.frameRateDenominator ?? 1) / Number(source.value.metadata.frameRateNumerator ?? 24); } }
async function extract() {
  if (!sourceVideo.value) return; busy.value = true; error.value = '';
  const fingerprint = `${props.planId}:${sourceVideo.value}:${sourceFrame.value}`; const scope = `shot-extract:${props.projectId}:${props.shotId}`;
  try { await api.extractShotFrame(props.projectId, { shotPlanVersionId: props.planId, shotId: props.shotId, sourceVideoAssetId: sourceVideo.value, frame: sourceFrame.value, idempotencyKey: pendingIdempotencyKey(scope, fingerprint) }); settleIdempotencyKey(scope, fingerprint); await load(); }
  catch (reason) { error.value = String(reason); } finally { busy.value = false; }
}
watch([selectedVideo, takeStart, takeError], () => {
  const choice = !takeError.value ? { shotId: props.shotId, assetId: selectedVideo.value, sourceInFrame: takeStart.value } : null;
  emit('take', choice);
  if (open.value) sessionStorage.setItem(`shot-take:${props.projectId}:${props.shotId}`, JSON.stringify({ assetId: selectedVideo.value, sourceInFrame: takeStart.value }));
});
watch(sourceVideo, () => { sourceReady.value = false; sourceFrame.value = 0; viewedFrame.value = 0; });
watch(selectedImage, () => { checks.value = []; });
watch(() => [props.planId, props.disabled], () => { preview.value = undefined; context.value = undefined; emit('take', null); void load(); });
watch(open, () => { if (open.value) void load(); });
onMounted(() => { open.value = !!props.videoMode; try { const saved = JSON.parse(sessionStorage.getItem(`shot-take:${props.projectId}:${props.shotId}`) ?? 'null'); if (saved) { selectedVideo.value = saved.assetId; takeStart.value = saved.sourceInFrame; } } catch { /* Discard an invalid local viewing preference. */ } timer = setInterval(() => void load(), 5000); });
onBeforeUnmount(() => { disposed = true; clearInterval(timer); if (frameCallback !== undefined) frameOwner?.cancelVideoFrameCallback?.(frameCallback); });
</script>

<template>
  <section class="shot-production">
    <button v-if="!videoMode" class="secondary" :disabled="disabled" :aria-expanded="open" @click="open = !open">准备镜头起始画面（按需）</button>
    <small v-if="disabled">先保存并使用此分镜版本，再准备镜头画面。</small>
    <div v-if="open">
      <p v-if="context">镜头 {{ context.shot.order }} · 目标 {{ context.targetDurationFrames }} 帧 · {{ context.frameCurrent ? '起始画面已确认' : stale ? '起始画面已过期，保留历史供查看' : '尚未确认起始画面' }}</p>
      <p v-if="stale" role="status">动作或场景参考已变化，旧画面不再对应当前设计。</p>
      <template v-if="!videoMode">
        <label>使用已有真实图片<select v-model="selectedImage"><option value="">请选择图片</option><option v-for="asset in imageAssets" :key="asset.id" :value="asset.id">{{ asset.role }} · {{ asset.id.slice(0, 8) }}</option></select></label>
        <img v-if="selectedImage" class="start-image" :src="`/api/v1/assets/${selectedImage}/content`" alt="待确认的镜头起始画面" />
        <div v-if="selectedImage" class="frame-checks"><label v-for="(label, key) in labels" :key="key"><input v-model="checks" type="checkbox" :value="key" />{{ label }}</label><button class="primary" :disabled="busy || checks.length !== 5" @click="confirm">确认画面并保存新分镜版本</button></div>
        <details><summary>从已有视频保存真实帧（本地免费）</summary><label>来源视频<select v-model="sourceVideo"><option value="">请选择来源</option><option v-for="asset in videoAssets" :key="asset.id" :value="asset.id">{{ asset.role }} · {{ asset.id.slice(0, 8) }}</option></select></label><video v-if="sourceVideo" ref="sourcePlayer" controls :src="`/api/v1/assets/${sourceVideo}/content`" @loadeddata="observeFrame" /><label>保存帧号<input v-model.number="sourceFrame" type="number" min="0" :max="Number(source?.metadata.durationFrames ?? 1) - 1" @change="locateSource" /></label><p>实际呈现帧 {{ viewedFrame }}；定位后检查画面，再保存。</p><button class="secondary" :disabled="busy || running || !sourceReady || viewedFrame !== sourceFrame" @click="extract">保存所选真实帧（免费）</button></details>
        <button class="secondary" :disabled="busy || running" @click="prepare('shot_frame')">检查镜头图片生成输入（免费）</button>
      </template>
      <template v-else>
        <img v-if="context?.shot.confirmedFrame" class="start-image" :src="`/api/v1/assets/${context.shot.confirmedFrame.assetId}/content`" alt="本镜头严格起始帧" />
        <p>每个镜头单独调用当前模型；仅发送已确认严格首帧。模型最小生成 4 秒，短镜头返回后等长取用。</p>
        <button class="secondary" :disabled="busy || running || !context?.frameCurrent" @click="prepare('shot_video')">检查本镜头视频输入（免费）</button>
        <label>本镜头候选<select v-model="selectedVideo"><option value="">请选择候选</option><option v-for="asset in shotVideos" :key="asset.id" :value="asset.id">{{ asset.id.slice(0, 8) }} · {{ asset.metadata.durationFrames }} 帧{{ asset.metadata.shotDesignHash !== context?.designHash ? ' · 旧设计' : '' }}</option></select></label>
        <template v-if="candidate && context"><video controls :src="`/api/v1/assets/${candidate.id}/content`" /><p>{{ candidate.metadata.audioRequestMissing ? '请求声音但未返回音轨' : candidate.metadata.hasAudio ? '包含原生混合音轨' : '无音轨' }} · {{ candidate.metadata.durationFrames }} 帧</p><FrameTimeline compact fixed-length label="镜头素材取用" :model-value="{ startFrame: takeStart, endFrame: takeStart + context.targetDurationFrames }" :total-frames="Number(candidate.metadata.durationFrames)" :current-frame="takeStart" @update:model-value="takeStart = $event.startFrame" /></template>
        <p v-if="takeError">{{ takeError }}</p>
      </template>
      <section v-if="preview" class="generation-input"><h4>本次实际输入 · {{ preview.purpose === 'shot_frame' ? '镜头起始图片' : '单镜头视频' }}</h4><div class="references"><figure v-for="reference in preview.references" :key="reference.role"><img :src="`/api/v1/assets/${reference.assetId}/content`" :alt="reference.role" /><figcaption>{{ reference.role }}</figcaption></figure></div><p>{{ preview.generationMode === 'from_frame' ? '严格首帧，无其他图片或视频参考' : '角色、比例、场景、画风普通参考' }} · {{ preview.generateAudio ? '要求生成已有分镜设计的声音' : '图片无声音' }}</p><ProviderPrompt :compiled-provider-prompt="preview.compiledProviderPrompt" :prompt="preview.prompt" :negative-prompt="preview.negativePrompt" :warnings="preview.warnings" /><details><summary>模型与费用</summary><p>{{ preview.model }} · 费用待核价 · 单次付费生成，不自动重试</p></details><button class="primary" :disabled="busy || running || unknown || !!paidBlock" @click="generate">{{ preview.purpose === 'shot_frame' ? '生成本镜头起始画面（付费 1 次）' : '生成本镜头视频（付费 1 次）' }}</button><p v-if="paidBlock">{{ paidBlock }}</p></section>
      <details v-if="context?.jobs.length"><summary>镜头任务与费用记录（{{ context.jobs.length }}）</summary><p v-for="job in context.jobs" :key="job.id">{{ job.kind }} · {{ jobPresentation(job.status).label }} · {{ job.provider === 'local_ffmpeg' ? '本地免费（不调用模型）' : billingPresentation(job.billingStatus, job.actualCostMicros, job.provider).detail }}<br />{{ job.id }}<br />{{ job.error?.message }}<br />{{ job.actualUsage }}</p></details>
      <button class="quiet" @click="load">刷新镜头记录</button><p v-if="error" role="alert">{{ error }}</p>
    </div>
  </section>
</template>

<style scoped>
.shot-production { padding: 12px 16px; border-top: 1px solid var(--line); font-size: 13px; }label { display: grid; gap: 6px; margin: 10px 0; }.start-image,video { display: block; max-width: 100%; height: 280px; object-fit: contain; margin: 10px 0; }.frame-checks label { display: flex; align-items: center; }.frame-checks input { width: auto; }button { margin: 6px 8px 6px 0; }.references { display: flex; flex-wrap: wrap; gap: 12px; }figure { margin: 0; }figure img { width: 90px; height: 100px; object-fit: contain; }figcaption { font-size: 11px; }.generation-input { padding: 12px; background: #f5efe5; }details p { overflow-wrap: anywhere; white-space: pre-wrap; line-height: 1.6; }
</style>
