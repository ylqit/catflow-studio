<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";

import { api } from "../../api/client";
import type { AssetDto, EditDecisionListDto, EditVersionDto, JobDto, WorkspaceDto } from "../../api/types";
import { validateEditDecisionList } from "../../editing";
import { mountWebAvPreview, type WebAvPreviewController } from "../../webavPreview";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const edits = ref<EditVersionDto[]>([]);
const finalAssets = ref<AssetDto[]>([]);
const savedEdit = ref<EditVersionDto | null>(null);
const exportJob = ref<JobDto | null>(null);
const saving = ref(false);
const error = ref("");
const webavHost = ref<HTMLElement | null>(null);
const webavController = ref<WebAvPreviewController | null>(null);
const webavReady = ref(false);
const durationMs = computed(() => props.workspace.project.targetDurationSeconds * 1000);
const controls = reactive({ startMs: 0, endMs: durationMs.value, audioPolicy: "native_fades" as const, transition: "fade" as const, transitionMs: 250 });

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
  savedEdit.value = edits.value[0] ?? null;
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
    error.value = reason instanceof Error ? reason.message : "剪辑决策保存失败";
  } finally {
    saving.value = false;
  }
}

async function exportVideo() {
  if (!savedEdit.value) return;
  exportJob.value = await api.createExport(props.projectId, { editVersionId: savedEdit.value.id, idempotencyKey: crypto.randomUUID() });
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
    error.value = reason instanceof Error ? reason.message : "WebAV 预览不可用";
    webavReady.value = false;
  }
}

onMounted(load);
onBeforeUnmount(() => webavController.value?.destroy());
</script>

<template>
  <section v-if="!workspace.selections.video" class="card empty missing-video"><div>▶</div><h2>先选择一个视频版本</h2><p>正式剪辑只引用已选择视频的 Asset ID 与 SHA256。</p><RouterLink class="primary" :to="`/projects/${projectId}/generation`">前往生成与选择</RouterLink></section>
  <section v-else class="delivery-layout">
    <div class="editor card">
      <header><div><p class="eyebrow">WebAV decision preview</p><h2>裁切、顺序与简单转场</h2></div><span class="pill">720 × 1280 MP4</span></header>
      <div class="edit-stage"><div v-show="webavReady" ref="webavHost" class="webav-host" /><video v-show="!webavReady" controls :src="`/api/v1/assets/${workspace.selections.video.id}/content`" /><button class="webav-button" @click="startWebAv">{{ webavReady ? "重载 WebAV" : "启用 WebAV 预览" }}</button></div>
      <div class="timeline">
        <div class="timeline-head"><span>源视频 SHA256</span><code>{{ workspace.selections.video.sha256 }}</code></div>
        <div class="clip-track"><span class="clip-block">当前视频 · {{ ((controls.endMs - controls.startMs) / 1000).toFixed(1) }}s</span></div>
        <div class="trim-controls">
          <div class="field"><label>起点（毫秒）</label><input v-model.number="controls.startMs" type="number" min="0" :max="controls.endMs - 100" /></div>
          <div class="field"><label>终点（毫秒）</label><input v-model.number="controls.endMs" type="number" :min="controls.startMs + 100" :max="durationMs" /></div>
          <div class="field"><label>转场</label><select v-model="controls.transition"><option value="none">无</option><option value="fade">淡入淡出</option><option value="crossfade">交叉淡化</option></select></div>
          <div class="field"><label>音频</label><select v-model="controls.audioPolicy"><option value="native">原声</option><option value="mute">静音</option><option value="native_fades">原声淡入淡出</option></select></div>
        </div>
      </div>
      <p v-if="error" class="notice error">{{ error }}</p>
      <footer><span>WebAV 只做交互预览；正式媒体由 Worker 使用 FFmpeg 渲染。</span><button class="secondary" :disabled="saving" @click="saveEdit">{{ saving ? "保存中" : "保存 Edit Version" }}</button><button class="primary" :disabled="!savedEdit" @click="exportVideo">提交正式导出</button></footer>
    </div>
    <aside class="delivery-side">
      <div class="card version-card"><p class="eyebrow">Edit versions</p><h2>不可变剪辑版本</h2><div v-if="!edits.length" class="empty">保存后产生第一个版本。</div><article v-for="edit in edits" :key="edit.id"><b>Revision {{ edit.revision }}</b><span class="pill" :class="{ good: edit.status === 'approved' }">{{ edit.status }}</span><small>{{ new Date(edit.createdAt).toLocaleString("zh-CN") }}</small></article></div>
      <div class="card export-card"><p class="eyebrow">Final exports</p><h2>正式成片</h2><p v-if="exportJob" class="notice">导出任务：{{ exportJob.status }}。浏览器关闭后 Worker 仍会继续。</p><div v-if="!finalAssets.length" class="empty">还没有 FFmpeg 成片。</div><article v-for="asset in finalAssets" :key="asset.id"><video controls :src="`/api/v1/assets/${asset.id}/content`" /><button v-if="workspace.selections.final?.id !== asset.id" class="primary" @click="approve(asset.id)">批准为最终成片</button><span v-else class="pill good">已批准</span></article></div>
    </aside>
  </section>
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
</style>
