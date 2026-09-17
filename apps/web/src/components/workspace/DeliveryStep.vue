<!--
  DeliveryStep.vue —— 交付步骤:剪辑、导出与最终成片

  职责:
  1. 对已选视频候选做裁切(起点/终点毫秒)、转场与音频策略编辑
  2. 保存剪辑版本(EDL)→ 导出正式成片(后台渲染 job)→ 设为最终成片
  3. URL 带 ?draftId= 时隐藏剪辑/导出界面,只保留 VideoRepairWorkspace(局部修复草稿)

  核心数据流:
    User Action              Component State              API Call
    ────────────────────────────────────────────────────────────────
    进入页面             →   load()                    →   GET /projects/{id}/edits + /assets
    点"打开剪辑预览"     →   webavController           →   浏览器端 WebAV 实时裁切预览(免费,不走服务端)
    点"准备完整剪辑预览" →   prepareSavedPreview()     →   POST /projects/{id}/edit-previews(免费渲染)
    点"保存剪辑版本"     →   saving=true               →   本地 validateEditDecisionList → POST /projects/{id}/edits
    点"导出视频"         →   exportJob                 →   POST /projects/{id}/exports(幂等键 export:{projectId})
    SSE eventCursor 前进 →   syncFromWorkspaceEvent()  →   GET /jobs/{id} + load()
    点"设为最终成片"     →   approve()                 →   POST /projects/{id}/final-selection

  关键约束:
    - formatVersion 2/3 的视频含局部修改,不能用原片裁切覆盖,只能进编辑草稿续作
    - 输出规格固定:9:16 竖屏 / 720×1280 / mp4
    - 预览由浏览器完成,正式视频在后台渲染并保存(不以原片代替剪辑结果)

  关联组件:
    - VideoRepairWorkspace.vue:局部修复工作区(常驻挂载,draftId 路由下是唯一可见区块)
    - WorkspaceView.vue:父视图,changed 事件后重新加载 workspace
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { api } from "../../api/client";
import type { AssetDto, EditDecisionListDto, EditVersionDto, JobDto, WorkspaceDto } from "../../api/types";
import { validateEditDecisionList } from "../../editing";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { backgroundTaskBlockedReason, errorPresentation, jobPresentation, type PaidModelRuntime } from "../../presentation";
import { mountWebAvPreview, type WebAvPreviewController } from "../../webavPreview";
import VideoRepairWorkspace from "./VideoRepairWorkspace.vue";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto; runtime?: PaidModelRuntime | null }>();
const emit = defineEmits<{ changed: [] }>();
const route = useRoute();
// URL 带 ?draftId= → 局部修复草稿态:隐藏剪辑/导出两个 section,只显示 VideoRepairWorkspace
const editingDraft = computed(() => typeof route?.query.draftId === "string");
const edits = ref<EditVersionDto[]>([]); // 正式剪辑版本列表(不含草稿)
const finalAssets = ref<AssetDto[]>([]); // role="final" 的导出成片
const compositePreview = ref<AssetDto | null>(null); // 当前版本的服务端合成预览(edit_preview 资产)
const savedEdit = ref<EditVersionDto | null>(null); // 当前激活(或最新)的剪辑版本
const exportJob = ref<JobDto | null>(null); // 导出任务(后台渲染)
const saving = ref(false);
const error = ref("");
const errorDetail = ref("");
const webavHost = ref<HTMLElement | null>(null); // WebAV 预览的 DOM 挂载点
const webavController = ref<WebAvPreviewController | null>(null);
const webavReady = ref(false); // true = WebAV 就绪,false = 回落到原生 <video> 播放完整原片
const durationMs = computed(() => props.workspace.project.targetDurationSeconds * 1000);
// 剪辑参数:起止毫秒(左闭右开)、音频策略(默认原声淡入淡出)、转场(默认 fade 250ms)
const controls = reactive({ startMs: 0, endMs: durationMs.value, audioPolicy: "native_fades" as const, transition: "fade" as const, transitionMs: 250 });
const exportJobPresentation = computed(() => exportJob.value ? jobPresentation(exportJob.value.status) : null);
// 后台任务运行时未就绪(如对象存储不可用)时的导出禁用原因;空串 = 可导出
const exportBlockedReason = computed(() => (
  props.runtime === undefined ? "" : backgroundTaskBlockedReason(props.runtime)
));

// 构建 EDL(Edit Decision List):单源视频 + 裁切区间 + 转场 + 音频策略
// 输出规格固定 9:16 / 720×1280 / mp4;sha256 随源视频带上,供服务端校验源未变化
function edl(): EditDecisionListDto | null {
  const video = props.workspace.selections.video;
  if (!video) return null; // 未选视频候选 → null(模板已用空态引导兜底)
  return {
    sourceVideoSelections: [{ assetId: video.id, sha256: video.sha256, startMs: controls.startMs, endMs: controls.endMs }],
    transitions: [{ afterClipIndex: 0, type: controls.transition, durationMs: controls.transitionMs }],
    audioPolicy: controls.audioPolicy,
    output: { aspectRatio: "9:16", width: 720, height: 1280, format: "mp4" },
  };
}

// 加载正式剪辑版本与成片资产:
// - edits 过滤掉草稿(editDraftId 非空的属于 VideoRepairWorkspace 职责)
// - savedEdit 优先取 active 版本,否则取最新一个
// - compositePreview 只认"当前版本、非修复预览"的 edit_preview 资产
async function load() {
  edits.value = (await api.edits(props.projectId)).filter(edit => !edit.editDraftId);
  const assets = await api.assets(props.projectId);
  finalAssets.value = assets.filter((asset) => asset.role === "final");
  savedEdit.value = edits.value.find((edit) => edit.active) ?? edits.value[0] ?? null;
  compositePreview.value = assets.find(asset => asset.role === "edit_preview" && asset.metadata.editVersionId === savedEdit.value?.id && !asset.metadata.repairId) ?? null;
}

// 局部修复保存后的回调:重拉数据并通知父视图刷新 workspace
async function handleRepairChanged() {
  await load();
  emit("changed");
}

// SSE 事件到达(eventCursor 前进):并行刷新导出任务状态与列表数据
async function syncFromWorkspaceEvent() {
  const pendingJob = exportJob.value;
  const [refreshedJob] = await Promise.all([
    pendingJob ? api.job(pendingJob.id) : Promise.resolve(null),
    load(),
  ]);
  if (refreshedJob) exportJob.value = refreshedJob;
}

// 从 asset.metadata 安全取数值字段(缺失/非有限数 → null),供"技术信息"面板显示
function metadataNumber(asset: AssetDto, key: string): number | null {
  const value = asset.metadata[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

// 保存剪辑版本:先本地校验 EDL(区间合法、sha256 一致),再提交服务端
// formatVersion 2/3 = 视频含局部修改,拒绝用原片裁切覆盖(必须走编辑草稿续作)
async function saveEdit() {
  if ((savedEdit.value?.formatVersion === 2 || savedEdit.value?.formatVersion === 3)) {
    error.value = "当前视频包含局部修改。请进入编辑草稿保存后续版本，不能用原片裁切覆盖。";
    return;
  }
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

// 导出正式成片:创建后台渲染 job;幂等键(scope = export:{projectId})防止重复提交
async function exportVideo() {
  if (!savedEdit.value || exportBlockedReason.value) return;
  const scope = `export:${props.projectId}`;
  exportJob.value = await api.createExport(props.projectId, {
    editVersionId: savedEdit.value.id,
    idempotencyKey: pendingIdempotencyKey(scope, savedEdit.value.id),
  });
  settleIdempotencyKey(scope, savedEdit.value.id);
}

// 把某个导出成片设为项目最终成片(写入 workspace.selections.final)
async function approve(assetId: string) {
  await api.approveFinal(props.projectId, assetId);
  emit("changed");
}

// 打开浏览器端 WebAV 剪辑预览:直接拉源视频流,按 startMs/endMs 实时裁切播放
// 免费、不产生服务端渲染;失败时 webavReady=false 回落到原生 <video> 播放完整原片
async function startWebAv() {
  if (!webavHost.value || !props.workspace.selections.video) return;
  webavController.value?.destroy(); // 重挂载前先销毁旧实例,避免双份解码器
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
// 请求服务端渲染当前版本的完整合成预览(edit_preview 资产,免费);完成后经 SSE 触发 load() 刷新
async function prepareSavedPreview() {
  if (!savedEdit.value || saving.value) return;
  saving.value = true;
  try { await api.renderEditPreview(props.projectId, savedEdit.value.id, `edit-preview:${savedEdit.value.id}`); }
  catch (reason) { error.value = errorPresentation(reason, "完整剪辑预览暂时无法准备").message; }
  finally { saving.value = false; }
}

onMounted(load);
// 订阅 workspace SSE 游标:任何后台事件(导出完成、预览就绪等)都触发数据同步
watch(() => props.workspace.eventCursor, () => void syncFromWorkspaceEvent());
onBeforeUnmount(() => webavController.value?.destroy()); // 组件卸载时释放 WebAV 解码资源
</script>

<template>
  <!-- 空态:未选视频且非草稿 → 引导去视频候选页(问题视频可直接进编辑草稿,无需虚假勾选全部通过) -->
  <section v-if="!workspace.selections.video && !editingDraft" class="card empty missing-video"><div>▶</div><h2>打开一个视频开始编辑</h2><p>问题视频可以直接进入编辑草稿，无需虚假勾选全部通过。</p><RouterLink class="primary" :to="`/projects/${projectId}/generation`">前往视频候选</RouterLink></section>
  <section v-else-if="workspace.selections.video && !editingDraft" class="delivery-layout">
    <div class="editor card">
      <header><div><p class="eyebrow">剪辑</p><h2>裁切与转场</h2></div><span class="pill">720 × 1280</span></header>
      <div v-if="savedEdit" class="edit-stage">
        <video v-if="compositePreview" controls :src="`/api/v1/assets/${compositePreview.id}/content`" />
        <p v-else class="notice">当前已有剪辑版本。请准备完整合成预览，不以原片代替剪辑结果。</p>
        <button class="webav-button" :disabled="saving" @click="prepareSavedPreview">准备完整剪辑预览（免费）</button>
      </div>
            <div v-else class="edit-stage"><div v-show="webavReady" ref="webavHost" class="webav-host" /><video v-show="!webavReady" controls :src="`/api/v1/assets/${workspace.selections.video.id}/content`" /><button class="webav-button" @click="startWebAv">{{ webavReady ? "重新加载预览" : "打开剪辑预览" }}</button></div>
      <div v-if="savedEdit?.formatVersion !== 2" class="timeline">
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
    <!-- 右侧栏:剪辑版本记录 + 导出结果(成片播放 / 技术信息 / 设为最终成片) -->
    <aside class="delivery-side">
      <div class="card version-card"><p class="eyebrow">视频版本</p><h2>剪辑记录</h2><div v-if="!edits.length" class="empty">保存后会产生第一个版本。</div><article v-for="edit in edits" :key="edit.id"><b>版本 {{ edit.revision }}</b><span class="pill" :class="{ good: edit.status === 'approved' }">{{ edit.status === "approved" ? "已采用" : edit.status === "rendered" ? "已导出" : "草稿" }}</span><small>{{ new Date(edit.createdAt).toLocaleString("zh-CN") }}</small></article></div>
      <div class="card export-card"><p class="eyebrow">导出结果</p><h2>正式成片</h2><p v-if="exportJob && exportJobPresentation" class="notice" :class="{ error: ['warn', 'danger'].includes(exportJobPresentation.tone) }">导出进度：{{ exportJobPresentation.label }}。{{ exportJob.error?.message || exportJobPresentation.description }}</p><div v-if="!finalAssets.length" class="empty">还没有导出成片。</div><article v-for="asset in finalAssets" :key="asset.id"><video controls :src="`/api/v1/assets/${asset.id}/content`" /><details><summary>查看技术信息</summary><dl class="technical-proof"><div><dt>文件校验值</dt><dd><code>{{ asset.sha256 }}</code></dd></div><div><dt>画幅</dt><dd>{{ metadataNumber(asset, "width") }} × {{ metadataNumber(asset, "height") }}</dd></div><div><dt>帧与时长</dt><dd>{{ metadataNumber(asset, "durationFrames") }} 帧 · {{ ((metadataNumber(asset, "durationMs") ?? 0) / 1000).toFixed(3) }} 秒</dd></div><div><dt>视频编码</dt><dd>{{ asset.metadata.codec ?? "未知" }}</dd></div><div><dt>音轨</dt><dd>{{ asset.metadata.audioPolicy === "preserve_original" && asset.metadata.candidateAudioUsed === false ? "根视频原音轨" : "按剪辑设置输出" }}<span v-if="asset.metadata.audioCodec"> · {{ asset.metadata.audioCodec }}</span></dd></div></dl></details><button v-if="workspace.selections.final?.id !== asset.id" class="primary" @click="approve(asset.id)">设为最终成片</button><span v-else class="pill good">最终成片</span></article></div>
    </aside>
  </section>
  <!-- 局部修复工作区:常驻挂载;editingDraft(?draftId=)时上方两个 section 均不渲染,它是唯一可见区块 -->
  <VideoRepairWorkspace :project-id="projectId" :workspace="workspace" @changed="handleRepairChanged" />
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
