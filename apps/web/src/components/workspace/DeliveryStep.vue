<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { api } from "../../api/client";
import type { AssetDto, EditDecisionListDto, EditVersionDto, JobDto, WorkspaceDto } from "../../api/types";
import { validateEditDecisionList } from "../../editing";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { backgroundTaskBlockedReason, errorPresentation, jobPresentation, type PaidModelRuntime } from "../../presentation";
import { mountWebAvPreview, type WebAvPreviewController } from "../../webavPreview";
import VideoRepairWorkspace from "./VideoRepairWorkspace.vue";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto; runtime?: PaidModelRuntime | null }>();
const emit = defineEmits<{ changed: [] }>();
const edits = ref<EditVersionDto[]>([]);
const finalAssets = ref<AssetDto[]>([]);
const savedEdit = ref<EditVersionDto | null>(null);
const exportJob = ref<JobDto | null>(null);
const saving = ref(false);
const error = ref("");
const errorDetail = ref("");
const webavHost = ref<HTMLElement | null>(null);
const webavController = ref<WebAvPreviewController | null>(null);
const webavReady = ref(false);
const durationMs = computed(() => props.workspace.project.targetDurationSeconds * 1000);
const controls = reactive({ startMs: 0, endMs: durationMs.value, audioPolicy: "native_fades" as const, transition: "fade" as const, transitionMs: 250 });
const exportJobPresentation = computed(() => exportJob.value ? jobPresentation(exportJob.value.status) : null);
const exportBlockedReason = computed(() => (
  props.runtime === undefined ? "" : backgroundTaskBlockedReason(props.runtime)
));

function edl(): EditDecisionListDto | null {
  const video = props.workspace.selections.video;
  if (!video) return null;
  return {
    sourceVideoSelections: [{ assetId: video.id, sha256: video.sha256, startMs: controls.startMs, endMs: controls.endMs }],
    transitions: [{ afterClipIndex: 0, type: controls.transition, durationMs: controls.transitionMs }],
    audioPolicy: controls.audioPolicy,
    output: { aspectRatio: "9:16", width: 720, height: 1280, format: "mp4" },
  };
}

async function load() {
  edits.value = await api.edits(props.projectId);
  const assets = await api.assets(props.projectId);
  finalAssets.value = assets.filter((asset) => asset.role === "final");
  savedEdit.value = edits.value.find((edit) => edit.active) ?? edits.value[0] ?? null;
}

async function handleRepairChanged() {
  await load();
  emit("changed");
}

async function syncFromWorkspaceEvent() {
  const pendingJob = exportJob.value;
  const [refreshedJob] = await Promise.all([
    pendingJob ? api.job(pendingJob.id) : Promise.resolve(null),
    load(),
  ]);
  if (refreshedJob) exportJob.value = refreshedJob;
}

function metadataNumber(asset: AssetDto, key: string): number | null {
  const value = asset.metadata[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

async function saveEdit() {
  const decision = edl();
  if (!decision) return;
  saving.value = true;
  try {
    validateEditDecisionList(decision, { [decision.sourceVideoSelections[0].assetId]: decision.sourceVideoSelections[0].sha256 });
    savedEdit.value = await api.createEdit(props.projectId, decision);
    await load();
  } catch (reason) {
    const failure = errorPresentation(reason, "剪辑版本没有成功保存");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    saving.value = false;
  }
}

async function exportVideo() {
  if (!savedEdit.value || exportBlockedReason.value) return;
  const scope = `export:${props.projectId}`;
  exportJob.value = await api.createExport(props.projectId, {
    editVersionId: savedEdit.value.id,
    idempotencyKey: pendingIdempotencyKey(scope, savedEdit.value.id),
  });
  settleIdempotencyKey(scope, savedEdit.value.id);
}

async function approve(assetId: string) {
  await api.approveFinal(props.projectId, assetId);
  emit("changed");
}

async function startWebAv() {
  if (!webavHost.value || !props.workspace.selections.video) return;
  webavController.value?.destroy();
  try {
    webavController.value = await mountWebAvPreview(
      webavHost.value,
      `/api/v1/assets/${props.workspace.selections.video.id}/content`,
      { startMs: controls.startMs, endMs: controls.endMs },
    );
    webavReady.value = true;
    webavController.value.play();
  } catch (reason) {
    const failure = errorPresentation(reason, "剪辑预览暂时不可用");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
    webavReady.value = false;
  }
}

onMounted(load);
watch(() => props.workspace.eventCursor, () => void syncFromWorkspaceEvent());
onBeforeUnmount(() => webavController.value?.destroy());
</script>

<template>
  <section v-if="!workspace.selections.video" class="card empty missing-video"><div>▶</div><h2>先选择一个视频</h2><p>选择后即可裁切、预览并导出成片。</p><RouterLink class="primary" :to="`/projects/${projectId}/generation`">前往选择视频</RouterLink></section>
  <section v-else class="delivery-layout">
    <div class="editor card">
      <header><div><p class="eyebrow">剪辑</p><h2>裁切与转场</h2></div><span class="pill">720 × 1280</span></header>
      <div class="edit-stage"><div v-show="webavReady" ref="webavHost" class="webav-host" /><video v-show="!webavReady" controls :src="`/api/v1/assets/${workspace.selections.video.id}/content`" /><button class="webav-button" @click="startWebAv">{{ webavReady ? "重新加载预览" : "打开剪辑预览" }}</button></div>
      <div class="timeline">
        <div class="clip-track"><span class="clip-block">当前视频 · {{ ((controls.endMs - controls.startMs) / 1000).toFixed(1) }}s</span></div>
        <div class="trim-controls">
          <div class="field"><label>起点（毫秒）</label><input v-model.number="controls.startMs" type="number" min="0" :max="controls.endMs - 100" /></div>
          <div class="field"><label>终点（毫秒）</label><input v-model.number="controls.endMs" type="number" :min="controls.startMs + 100" :max="durationMs" /></div>
          <div class="field"><label>转场</label><select v-model="controls.transition"><option value="none">无</option><option value="fade">淡入淡出</option><option value="crossfade">交叉淡化</option></select></div>
          <div class="field"><label>音频</label><select v-model="controls.audioPolicy"><option value="native">原声</option><option value="mute">静音</option><option value="native_fades">原声淡入淡出</option></select></div>
        </div>
        <details class="editor-technical"><summary>技术详情</summary><p>源视频校验值</p><code>{{ workspace.selections.video.sha256 }}</code><p>预览由浏览器完成，正式视频在后台渲染并保存。</p></details>
      </div>
      <div v-if="error" class="notice error creator-error"><p>{{ error }}</p><details v-if="errorDetail && errorDetail !== error"><summary>技术详情</summary><code>{{ errorDetail }}</code></details></div>
      <footer><span>{{ exportBlockedReason || "保存后会保留当前版本，导出不会覆盖原视频。" }}</span><button class="secondary" :disabled="saving" @click="saveEdit">{{ saving ? "保存中" : "保存剪辑版本" }}</button><button class="primary" :disabled="!savedEdit || Boolean(exportBlockedReason)" @click="exportVideo">导出视频</button></footer>
    </div>
    <aside class="delivery-side">
      <div class="card version-card"><p class="eyebrow">视频版本</p><h2>剪辑记录</h2><div v-if="!edits.length" class="empty">保存后会产生第一个版本。</div><article v-for="edit in edits" :key="edit.id"><b>版本 {{ edit.revision }}</b><span class="pill" :class="{ good: edit.status === 'approved' }">{{ edit.status === "approved" ? "已采用" : edit.status === "rendered" ? "已导出" : "草稿" }}</span><small>{{ new Date(edit.createdAt).toLocaleString("zh-CN") }}</small></article></div>
      <div class="card export-card"><p class="eyebrow">导出结果</p><h2>正式成片</h2><p v-if="exportJob && exportJobPresentation" class="notice" :class="{ error: ['warn', 'danger'].includes(exportJobPresentation.tone) }">导出进度：{{ exportJobPresentation.label }}。{{ exportJob.error?.message || exportJobPresentation.description }}</p><div v-if="!finalAssets.length" class="empty">还没有导出成片。</div><article v-for="asset in finalAssets" :key="asset.id"><video controls :src="`/api/v1/assets/${asset.id}/content`" /><details><summary>查看技术信息</summary><dl class="technical-proof"><div><dt>文件校验值</dt><dd><code>{{ asset.sha256 }}</code></dd></div><div><dt>画幅</dt><dd>{{ metadataNumber(asset, "width") }} × {{ metadataNumber(asset, "height") }}</dd></div><div><dt>帧与时长</dt><dd>{{ metadataNumber(asset, "durationFrames") }} 帧 · {{ ((metadataNumber(asset, "durationMs") ?? 0) / 1000).toFixed(3) }} 秒</dd></div><div><dt>视频编码</dt><dd>{{ asset.metadata.codec ?? "未知" }}</dd></div><div><dt>音轨</dt><dd>{{ asset.metadata.audioPolicy === "preserve_original" && asset.metadata.candidateAudioUsed === false ? "根视频原音轨" : "按剪辑设置输出" }}<span v-if="asset.metadata.audioCodec"> · {{ asset.metadata.audioCodec }}</span></dd></div></dl></details><button v-if="workspace.selections.final?.id !== asset.id" class="primary" @click="approve(asset.id)">设为最终成片</button><span v-else class="pill good">最终成片</span></article></div>
    </aside>
  </section>
  <VideoRepairWorkspace v-if="workspace.selections.video" :project-id="projectId" :workspace="workspace" @changed="handleRepairChanged" />
</template>

<style scoped>
.missing-video { padding: 90px; }
.missing-video > div { font-size: 35px; color: var(--accent); }
.missing-video p { color: var(--muted); }
.missing-video .primary { display: inline-flex; align-items: center; }
.delivery-layout { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 20px; align-items: start; }
.editor { overflow: hidden; }
.editor > header { display: flex; justify-content: space-between; align-items: start; padding: 22px 24px; border-bottom: 1px solid var(--line); }
.editor h2 { margin: 0; font-size: 20px; }
.edit-stage { position: relative; display: grid; place-items: center; min-height: 430px; padding: 22px; background: #292622; }
.edit-stage video { height: 390px; aspect-ratio: 9 / 16; background: #111; box-shadow: 0 18px 50px #0007; }
.webav-host { width: 219px; height: 390px; overflow: hidden; background: #111; }
.webav-host :deep(canvas) { width: 219px !important; height: 390px !important; }
.webav-button { position: absolute; right: 16px; top: 16px; padding: 7px 10px; border: 1px solid #ffffff44; border-radius: 9px; color: white; background: #2d2926aa; cursor: pointer; font-size: 10px; }
.timeline { padding: 20px 24px; background: #f7f1e9; border-top: 1px solid #4a423c; }
.timeline-head { display: flex; gap: 10px; color: var(--muted); font-size: 9px; }
.timeline-head code { overflow: hidden; text-overflow: ellipsis; }
.clip-track { height: 50px; margin: 12px 0 16px; padding: 6px; border-radius: 10px; background: #e8dfd4; }
.clip-block { width: 100%; height: 100%; display: flex; align-items: center; padding: 0 13px; border-radius: 7px; color: #fff; background: linear-gradient(90deg, #c5755d, #d99172); font-size: 11px; }
.trim-controls { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.trim-controls input, .trim-controls select { padding: 8px; font-size: 11px; }
.editor-technical { margin-top: 14px; color: var(--muted); font-size: 10px; }.editor-technical summary, .export-card details summary { cursor: pointer; font-weight: 700; }.editor-technical p { margin: 7px 0 3px; }.editor-technical code { overflow-wrap: anywhere; }
.editor > .notice { margin: 0 24px 15px; }
.editor > footer { display: flex; align-items: center; justify-content: flex-end; gap: 9px; padding: 15px 24px; border-top: 1px solid var(--line); }
.editor > footer > span { margin-right: auto; color: var(--muted); font-size: 10px; }
.delivery-side { display: grid; gap: 20px; }
.version-card, .export-card { padding: 21px; }
.version-card h2, .export-card h2 { font-size: 19px; }
.version-card article { display: grid; grid-template-columns: 1fr auto; gap: 6px; padding: 11px 0; border-top: 1px solid var(--line); font-size: 11px; }
.version-card article small { grid-column: span 2; color: var(--muted); }
.export-card article { display: grid; gap: 10px; margin-top: 13px; }
.export-card video { width: 100%; max-height: 310px; background: #222; }
.technical-proof { display: grid; gap: 6px; margin: 0; }.technical-proof div { display: grid; grid-template-columns: 75px minmax(0, 1fr); gap: 8px; font-size: 9px; }.technical-proof dt { color: var(--muted); }.technical-proof dd { margin: 0; overflow-wrap: anywhere; }
</style>
