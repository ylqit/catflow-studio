<script setup lang="ts">
import { Download, EditPen, Refresh, VideoPlay } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { api, assetContentUrl } from "../../api/client";
import type {
  AssetDto, PersistentTaskDto, ProductionBoardDto, SequenceClipDto, SequenceDto,
  SequenceTransitionDto, ShotDto,
} from "../../api/types";
import { registerTask, useTaskCenter } from "../../tasks/taskCenter";
import VideoEditWorkspace from "../canvas/VideoEditWorkspace.vue";

type DeliveryLoadState = "initial_loading" | "initial_error" | "ready" | "refreshing" | "stale_success";

const props = withDefaults(defineProps<{
  projectId: string;
  focusedItemId?: string;
  panel: string;
}>(), { focusedItemId: "" });

const board = ref<ProductionBoardDto>();
const tasks = ref<PersistentTaskDto[]>([]);
const loadState = ref<DeliveryLoadState>("initial_loading");
const loadError = ref("");
const selectedShotId = ref("");
const selectedSequenceId = ref("");
const comparisonAssetId = ref("");
const localEditOpen = ref(false);
const localEditExpanded = ref(false);
const composeOpen = ref(false);
const composeTransitions = ref<Record<string, SequenceTransitionDto>>({});
const introFadeEnabled = ref(true);
const introFadeDurationMs = ref(400);
const outroFadeEnabled = ref(true);
const outroFadeDurationMs = ref(400);
const composing = ref(false);
const reviewingSequence = ref(false);
const taskCenter = useTaskCenter();
let requestSequence = 0;

const graph = computed(() => board.value?.projectGraph);
const allShots = computed(() => graph.value?.scenes.flatMap((scene) => scene.shots) ?? []);
const selectedSequence = computed<SequenceDto | undefined>(() => (
  graph.value?.sequences.find((sequence) => sequence.id === selectedSequenceId.value)
));
const selectedShot = computed<ShotDto | undefined>(() => (
  allShots.value.find((shot) => shot.id === selectedShotId.value)
));
const allAssets = computed(() => [
  ...(graph.value?.assets ?? []),
  ...allShots.value.flatMap((shot) => shot.assets),
]);
const assetById = computed(() => new Map(allAssets.value.map((asset) => [asset.id, asset])));
const selectedVideo = computed<AssetDto | undefined>(() => {
  const assetId = selectedShot.value?.selectedVideoAssetId;
  return assetId ? assetById.value.get(assetId) : undefined;
});
const selectedSequenceAsset = computed<AssetDto | undefined>(() => {
  const assetId = selectedSequence.value?.renderedAssetId;
  return assetId ? assetById.value.get(assetId) : undefined;
});
const displayedAsset = computed(() => selectedSequenceAsset.value ?? selectedVideo.value);
const downloadableAsset = computed(() => {
  if (selectedSequence.value?.status !== "approved"
    || selectedSequence.value.id !== graph.value?.project.selectedSequenceId
    || !selectedSequenceAsset.value?.contentReady) return undefined;
  return selectedSequenceAsset.value;
});
const comparisonAsset = computed(() => (
  comparisonAssetId.value ? assetById.value.get(comparisonAssetId.value) : undefined
));
const selectedVideoVersions = computed(() => (
  [...(selectedShot.value?.assets ?? [])]
    .filter((asset) => asset.mediaType === "video" && asset.role === "shot_video" && asset.contentReady)
    .sort((left, right) => String(right.createdAt ?? "").localeCompare(String(left.createdAt ?? "")))
));
const selectedVideoDurationMs = computed(() => {
  const qc = selectedVideo.value?.metadata.qc;
  if (qc && typeof qc === "object" && "durationMs" in qc && typeof qc.durationMs === "number") {
    return qc.durationMs;
  }
  return Math.max(500, Math.round((selectedShot.value?.durationSeconds ?? 8) * 1_000));
});
const localEditReferences = computed(() => allAssets.value.flatMap((asset) => {
  if (asset.mediaType !== "image" || !asset.contentReady || asset.role === "style_source") return [];
  const authority = asset.metadata.referenceAuthority;
  if (authority && typeof authority === "object" && "providerEligible" in authority
    && authority.providerEligible === false) return [];
  const semanticRole = authority && typeof authority === "object" && "role" in authority
    && typeof authority.role === "string" ? authority.role : asset.role;
  return [{
    id: asset.id,
    title: asset.displayName,
    thumbnailUrl: assetContentUrl(asset.id),
    semanticRole,
  }];
}));

const timelineClips = computed<SequenceClipDto[]>(() => {
  if (selectedSequence.value) return [...selectedSequence.value.plan.clips].sort((left, right) => left.order - right.order);
  let cursor = 0;
  return allShots.value.filter((shot) => Boolean(shot.selectedVideoAssetId)).map((shot, index) => {
    const durationMs = Math.round(shot.durationSeconds * 1_000);
    const clip: SequenceClipDto = {
      order: index + 1,
      shot_card_id: shot.id,
      source_asset_id: shot.selectedVideoAssetId!,
      source_start_ms: 0,
      source_end_ms: durationMs,
      timeline_start_ms: cursor,
      timeline_end_ms: cursor + durationMs,
      transitionFromPrevious: null,
    };
    cursor += durationMs;
    return clip;
  });
});
const timelineDurationMs = computed(() => selectedSequence.value?.plan.duration_ms
  ?? timelineClips.value.at(-1)?.timeline_end_ms
  ?? 0);
const deliveryTasks = computed(() => tasks.value.filter((task) => (
  task.kind === "build_sequence" || task.kind === "range_edit" || task.kind === "video_edit"
)));
const compositionShots = computed(() => [...allShots.value]
  .filter((shot) => {
    const assetId = shot.selectedVideoAssetId;
    const asset = assetId ? assetById.value.get(assetId) : undefined;
    return asset?.mediaType === "video" && asset.contentReady && asset.status === "approved";
  })
  .sort((left, right) => left.order - right.order));

function resolveSelection(result: ProductionBoardDto) {
  const resultGraph = result.projectGraph;
  const shots = resultGraph.scenes.flatMap((scene) => scene.shots);
  if (props.focusedItemId === "current-shot") {
    const adoptedSequence = resultGraph.sequences.find(
      (sequence) => sequence.id === resultGraph.project.selectedSequenceId,
    );
    const adoptedShotId = adoptedSequence?.plan.clips.find(
      (clip) => shots.some((shot) => shot.id === clip.shot_card_id),
    )?.shot_card_id;
    selectedShotId.value = adoptedShotId
      ?? shots.find((shot) => {
        const assetId = shot.selectedVideoAssetId;
        const asset = assetId ? shot.assets.find((candidate) => candidate.id === assetId) : undefined;
        return asset?.status === "approved";
      })?.id
      ?? shots[0]?.id
      ?? "";
    selectedSequenceId.value = "";
    return;
  }
  const focusedSequence = resultGraph.sequences.find((sequence) => sequence.id === props.focusedItemId);
  const focusedShot = shots.find((shot) => shot.id === props.focusedItemId
    || shot.assets.some((asset) => asset.id === props.focusedItemId));
  if (focusedSequence) {
    selectedSequenceId.value = focusedSequence.id;
    selectedShotId.value = "";
    return;
  }
  if (focusedShot) {
    selectedShotId.value = focusedShot.id;
    selectedSequenceId.value = "";
    return;
  }
  const currentSequence = resultGraph.sequences.find(
    (sequence) => sequence.id === resultGraph.project.selectedSequenceId,
  );
  if (currentSequence) {
    selectedSequenceId.value = currentSequence.id;
    selectedShotId.value = "";
    return;
  }
  selectedSequenceId.value = "";
  selectedShotId.value = shots.find((shot) => Boolean(shot.selectedVideoAssetId))?.id ?? shots[0]?.id ?? "";
}

async function loadWorkspace(background = false) {
  const sequence = ++requestSequence;
  const hasCurrent = board.value?.projectId === props.projectId;
  loadState.value = background && hasCurrent ? "refreshing" : "initial_loading";
  loadError.value = "";
  try {
    const [nextBoard, nextTasks] = await Promise.all([
      api.productionBoard(props.projectId),
      api.projectTasks(props.projectId),
    ]);
    if (sequence !== requestSequence) return;
    board.value = nextBoard;
    tasks.value = nextTasks;
    resolveSelection(nextBoard);
    loadState.value = "ready";
  } catch (error) {
    if (sequence !== requestSequence) return;
    loadError.value = error instanceof Error ? error.message : String(error);
    loadState.value = hasCurrent ? "stale_success" : "initial_error";
  }
}

function showShot(shotId: string) {
  selectedShotId.value = shotId;
  selectedSequenceId.value = "";
  comparisonAssetId.value = "";
  localEditOpen.value = false;
  localEditExpanded.value = false;
}

function showSequence(sequenceId: string) {
  selectedSequenceId.value = sequenceId;
  selectedShotId.value = "";
  comparisonAssetId.value = "";
  localEditOpen.value = false;
  localEditExpanded.value = false;
}

function compareVersion(assetId: string) {
  comparisonAssetId.value = comparisonAssetId.value === assetId ? "" : assetId;
}

function openLocalEditor() {
  localEditExpanded.value = false;
  localEditOpen.value = true;
}

function closeLocalEditor() {
  localEditOpen.value = false;
  localEditExpanded.value = false;
}

function openComposition() {
  if (!compositionShots.value.length) {
    ElMessage.warning("请先为至少一个镜头选择已批准的视频版本");
    return;
  }
  composeTransitions.value = Object.fromEntries(compositionShots.value.slice(1).map((shot) => [
    shot.id,
    { type: "cut", durationMs: 0 } satisfies SequenceTransitionDto,
  ]));
  introFadeEnabled.value = true;
  introFadeDurationMs.value = 400;
  outroFadeEnabled.value = true;
  outroFadeDurationMs.value = 400;
  localEditOpen.value = false;
  localEditExpanded.value = false;
  composeOpen.value = true;
}

function setTransitionType(shotId: string, value: string) {
  const type = value as SequenceTransitionDto["type"];
  composeTransitions.value[shotId] = {
    type,
    durationMs: type === "cut" ? 0 : 300,
  };
}

async function buildSequence() {
  if (composing.value || !compositionShots.value.length) return;
  try {
    await ElMessageBox.confirm(
      "将使用已批准的视频版本在本地执行转场与合成，不会调用媒体 Provider。",
      "确认本地合成",
      { type: "info", confirmButtonText: "开始合成" },
    );
    composing.value = true;
    const result = await api.buildSequence(props.projectId, {
      transitions: compositionShots.value.slice(1).map((shot) => ({
        afterShotId: shot.id,
        transition: composeTransitions.value[shot.id] ?? { type: "cut", durationMs: 0 },
      })),
      introTransition: introFadeEnabled.value
        ? { type: "fade_black", durationMs: introFadeDurationMs.value }
        : null,
      outroTransition: outroFadeEnabled.value
        ? { type: "fade_black", durationMs: outroFadeDurationMs.value }
        : null,
    });
    registerTask(result.jobId, {
      kind: "build_sequence",
      label: "本地成片合成",
      operationKey: "sequence:build",
      projectId: props.projectId,
    });
    composeOpen.value = false;
    ElMessage.success("本地合成已提交到全局任务中心");
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error instanceof Error ? error.message : String(error));
    }
  } finally {
    composing.value = false;
  }
}

async function decideSequence(approve: boolean) {
  if (!selectedSequence.value || reviewingSequence.value) return;
  try {
    await ElMessageBox.confirm(
      approve
        ? "批准后，这个不可变总片版本将成为当前成片，可用于下载和交付。"
        : "拒绝后会保留该总片及审计记录，但不会作为当前成片。",
      approve ? "批准当前总片" : "拒绝当前总片",
      { type: approve ? "success" : "warning", confirmButtonText: approve ? "批准" : "拒绝" },
    );
    reviewingSequence.value = true;
    await api.selectSequence(props.projectId, selectedSequence.value.id, approve);
    ElMessage.success(approve ? "总片已批准并设为当前成片" : "总片已拒绝");
    await loadWorkspace(true);
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error instanceof Error ? error.message : String(error));
    }
  } finally {
    reviewingSequence.value = false;
  }
}

function downloadCurrent() {
  if (!downloadableAsset.value) return;
  const link = document.createElement("a");
  link.href = assetContentUrl(downloadableAsset.value.id);
  link.download = "final-video.mp4";
  link.click();
}

function transitionLabel(clip: SequenceClipDto) {
  const transition = clip.transitionFromPrevious;
  if (!transition || transition.type === "cut") return "硬切";
  return `${transition.type === "cross_dissolve" ? "叠化" : "黑场"} ${(transition.durationMs / 1_000).toFixed(1)}s`;
}

watch(() => props.projectId, () => {
  board.value = undefined;
  tasks.value = [];
  void loadWorkspace(false);
}, { immediate: true });
watch(() => props.focusedItemId, () => {
  if (board.value) resolveSelection(board.value);
});
watch(() => taskCenter.projectSignals.value[props.projectId]?.revision ?? 0, () => {
  if (board.value) void loadWorkspace(true);
});
watch(() => taskCenter.workspaceRefreshRequest.value?.revision ?? 0, () => {
  if (taskCenter.workspaceRefreshRequest.value?.projectId === props.projectId) void loadWorkspace(true);
});
onBeforeUnmount(() => { requestSequence += 1; });
</script>

<template>
  <section class="delivery-workbench" aria-label="成片交付工作台">
    <div v-if="loadState === 'initial_loading'" class="delivery-state" aria-busy="true">
      <i /><span>正在加载视频版本、时间线和导出历史…</span>
    </div>
    <div v-else-if="loadState === 'initial_error'" class="delivery-state error" role="alert">
      <b>成片交付暂时无法载入</b><p>{{ loadError }}</p><button type="button" @click="loadWorkspace(false)">重新加载</button>
    </div>
    <template v-else-if="graph">
      <header class="delivery-header">
        <div><span>DELIVERY</span><h1>成片交付</h1><p>{{ graph.project.title }} · 视频版本、局部编辑、时间线和导出共用同一工作区</p></div>
        <div class="delivery-actions">
          <button type="button" :disabled="loadState === 'refreshing'" @click="loadWorkspace(true)"><Refresh />刷新</button>
          <button data-action="download-current" type="button" :disabled="!downloadableAsset" @click="downloadCurrent"><Download />下载当前成片</button>
        </div>
      </header>

      <div v-if="loadState === 'stale_success'" class="delivery-stale" role="status">数据可能过期：{{ loadError }}</div>

      <main class="delivery-main">
        <VideoEditWorkspace
          v-if="localEditOpen && selectedShot && selectedVideo"
          class="delivery-local-editor"
          :project-id="projectId"
          :source-asset-id="selectedVideo.id"
          :video-url="assetContentUrl(selectedVideo.id)"
          :duration-ms="selectedVideoDurationMs"
          :references="localEditReferences"
          :embedded="!localEditExpanded"
          @expand="localEditExpanded = true"
          @close="closeLocalEditor"
          @submitted="closeLocalEditor(); loadWorkspace(true)"
        />
        <section v-else class="delivery-player">
          <div class="player-heading">
            <div><span>{{ selectedSequence ? 'MASTER REVISION' : 'SHOT VERSION' }}</span><h2>{{ selectedSequence ? `总片 Revision ${selectedSequence.revision}` : selectedShot?.title || '选择一个镜头' }}</h2></div>
            <button v-if="selectedShot && selectedVideo" data-action="open-local-edit" type="button" @click="openLocalEditor"><EditPen />局部编辑</button>
          </div>
          <div v-if="displayedAsset && comparisonAsset" class="delivery-comparison">
            <figure><video controls playsinline :src="assetContentUrl(displayedAsset.id)" /><figcaption>当前采用版本</figcaption></figure>
            <figure><video controls playsinline :src="assetContentUrl(comparisonAsset.id)" /><figcaption>{{ comparisonAsset.displayName }}</figcaption></figure>
          </div>
          <video v-else-if="displayedAsset" controls playsinline :src="assetContentUrl(displayedAsset.id)" />
          <div v-else class="player-empty"><VideoPlay /><b>还没有可播放的批准版本</b><span>先在视频生成模块审核并选择一个版本。</span></div>
        </section>

        <aside v-if="!localEditOpen" class="delivery-inspector">
          <section v-if="composeOpen" class="delivery-compose-panel" aria-label="成片编排">
            <div class="inspector-title"><span>本地编排与转场</span><b>{{ compositionShots.length }} 段</b></div>
            <p>这里只组合已批准的视频版本；黑场和叠化由本地时间线完成，不会提交媒体 Provider。</p>
            <label class="delivery-compose-boundary">
              <span><strong>开场</strong><small>从黑场渐入</small></span>
              <input v-model="introFadeEnabled" type="checkbox" aria-label="启用开场黑场渐入" />
              <input v-if="introFadeEnabled" v-model.number="introFadeDurationMs" type="number" min="150" max="1500" step="50" aria-label="开场渐入时长（毫秒）" />
            </label>
            <article v-for="(shot, index) in compositionShots" :key="shot.id" class="delivery-compose-shot">
              <div><strong>{{ index + 1 }}. {{ shot.title }}</strong><small>{{ shot.durationSeconds.toFixed(1) }}s · {{ shot.selectedVideoAssetId }}</small></div>
              <span v-if="index === 0">成片起点</span>
              <template v-else>
                <select :data-transition-shot="shot.id" :value="composeTransitions[shot.id]?.type ?? 'cut'" @change="setTransitionType(shot.id, ($event.target as HTMLSelectElement).value)">
                  <option value="cut">硬切</option>
                  <option value="fade_black">黑场衔接</option>
                  <option value="cross_dissolve">短叠化</option>
                </select>
                <input v-if="composeTransitions[shot.id]?.type !== 'cut'" v-model.number="composeTransitions[shot.id]!.durationMs" type="number" min="150" max="1000" step="50" :aria-label="`${shot.title}转场时长（毫秒）`" />
              </template>
            </article>
            <label class="delivery-compose-boundary">
              <span><strong>结尾</strong><small>淡出到黑场</small></span>
              <input v-model="outroFadeEnabled" type="checkbox" aria-label="启用结尾黑场淡出" />
              <input v-if="outroFadeEnabled" v-model.number="outroFadeDurationMs" type="number" min="150" max="1500" step="50" aria-label="结尾淡出时长（毫秒）" />
            </label>
            <footer><button type="button" @click="composeOpen = false">取消</button><button data-action="build-sequence" type="button" :disabled="composing" @click="buildSequence">{{ composing ? '正在提交…' : '开始本地合成' }}</button></footer>
          </section>
          <section v-if="selectedSequence?.status === 'content_review' || selectedSequence?.status === 'awaiting_review'" class="delivery-sequence-review">
            <div class="inspector-title"><span>等待成片审核</span><b>人工决定</b></div>
            <p>本地合成已经完成，但不会自动成为当前成片。请先播放检查画幅、时长、转场和声音。</p>
            <footer><button data-action="reject-sequence" type="button" :disabled="reviewingSequence" @click="decideSequence(false)">拒绝</button><button data-action="approve-sequence" type="button" :disabled="reviewingSequence" @click="decideSequence(true)">批准为当前成片</button></footer>
          </section>
          <section v-if="selectedShot" class="delivery-video-history">
            <div class="inspector-title"><span>镜头版本</span><b>{{ selectedVideoVersions.length }}</b></div>
            <article v-for="asset in selectedVideoVersions" :key="asset.id" :class="{ active: asset.id === selectedVideo?.id }">
              <div><strong>{{ asset.displayName }}</strong><small>{{ asset.status }} · {{ asset.createdAt || '创建时间未记录' }}</small></div>
              <button v-if="asset.id !== selectedVideo?.id" type="button" @click="compareVersion(asset.id)">{{ comparisonAssetId === asset.id ? '关闭对比' : '对比' }}</button>
              <span v-else>当前采用</span>
            </article>
            <p v-if="!selectedVideoVersions.length">这个镜头还没有可播放的视频版本。</p>
          </section>
          <section class="delivery-sequence-history">
            <div class="inspector-title"><span>总片版本</span><b>{{ graph.sequences.length }}</b></div>
            <article v-for="sequence in [...graph.sequences].reverse()" :key="sequence.id" :class="{ active: sequence.id === selectedSequence?.id }">
              <button type="button" @click="showSequence(sequence.id)"><strong>Revision {{ sequence.revision }}</strong><small>{{ (sequence.plan.duration_ms / 1000).toFixed(2) }}s · {{ sequence.status }}</small></button>
            </article>
            <p v-if="!graph.sequences.length">尚未合成总片。</p>
          </section>
          <section>
            <div class="inspector-title"><span>交付任务</span><b>{{ deliveryTasks.length }}</b></div>
            <article v-for="task in deliveryTasks" :key="task.stepId" class="delivery-task"><strong>{{ task.kind }}</strong><small>{{ task.status }} · {{ task.providerTaskId || '本地任务' }}</small></article>
            <p v-if="!deliveryTasks.length">没有正在执行的合成或局部编辑任务。</p>
          </section>
        </aside>
      </main>

      <section class="delivery-timeline" aria-label="成片时间线">
        <header><div><span>TIMELINE</span><h2>{{ (timelineDurationMs / 1000).toFixed(2) }} 秒</h2></div><button data-action="open-compose" type="button" @click="openComposition">编排并合成</button></header>
        <div class="timeline-ruler"><span>视频轨 V1</span><i /></div>
        <div class="timeline-track">
          <article v-for="clip in timelineClips" :key="`${clip.order}-${clip.shot_card_id}`" class="delivery-timeline-clip" :class="{ active: clip.shot_card_id === selectedShot?.id }" @click="showShot(clip.shot_card_id)">
            <span>{{ clip.order.toString().padStart(2, '0') }}</span><strong>{{ allShots.find(shot => shot.id === clip.shot_card_id)?.title || clip.shot_card_id }}</strong><small>{{ ((clip.timeline_end_ms - clip.timeline_start_ms) / 1000).toFixed(1) }}s</small>
            <em v-if="clip.order > 1">{{ transitionLabel(clip) }}</em>
          </article>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.delivery-workbench { width: 100%; height: 100%; min-width: 0; min-height: 0; display: grid; grid-template-rows: auto minmax(0, 1fr) 184px; overflow: hidden; color: #e7edf6; background: #0c1015; }.delivery-state { height: 100%; display: grid; place-content: center; justify-items: center; gap: 10px; color: #7f8b9c; }.delivery-state i { width: 34px; height: 34px; border: 2px solid #263442; border-top-color: #6fa4d2; border-radius: 50%; animation: delivery-spin .8s linear infinite; }.delivery-state.error { color: #d9aaa3; }.delivery-state.error button,.delivery-actions button,.delivery-player button,.delivery-timeline header button { min-height: 44px; padding: 0 14px; display: inline-flex; align-items: center; justify-content: center; gap: 7px; color: #dbe5ef; background: #202832; border: 1px solid #374453; border-radius: 9px; cursor: pointer; }.delivery-state.error p { max-width: 560px; }.delivery-header { min-height: 76px; padding: 13px 18px; display: flex; align-items: center; justify-content: space-between; gap: 18px; background: #11161d; border-bottom: 1px solid #29323d; }.delivery-header span,.player-heading span,.delivery-timeline header span { color: #698199; font-size: 9px; font-weight: 800; letter-spacing: .15em; }.delivery-header h1 { margin: 3px 0; font-size: 19px; }.delivery-header p { margin: 0; color: #778496; font-size: 11px; }.delivery-actions { display: flex; gap: 8px; }.delivery-actions svg,.delivery-player button svg { width: 16px; }.delivery-actions button:disabled { opacity: .38; cursor: not-allowed; }.delivery-stale { position: absolute; z-index: 5; top: 86px; right: 18px; padding: 8px 11px; color: #dfbd88; background: #2b2116; border: 1px solid #69502f; border-radius: 8px; font-size: 11px; }.delivery-main { min-width: 0; min-height: 0; padding: 14px 16px; display: grid; grid-template-columns: minmax(0, 1fr) 310px; gap: 12px; overflow: hidden; }.delivery-player { min-width: 0; min-height: 0; padding: 12px; display: grid; grid-template-rows: auto minmax(0, 1fr); gap: 10px; background: #11171e; border: 1px solid #2c3642; border-radius: 13px; }.player-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.player-heading h2 { margin: 3px 0 0; font-size: 15px; }.delivery-player video { width: 100%; height: 100%; min-height: 0; object-fit: contain; background: #07090c; border-radius: 9px; }.player-empty { display: grid; place-content: center; justify-items: center; gap: 8px; color: #687588; background: #080b0f; border-radius: 9px; }.player-empty svg { width: 34px; }.player-empty span { font-size: 11px; }.delivery-inspector { min-height: 0; overflow: auto; display: grid; align-content: start; gap: 10px; }.delivery-inspector section { padding: 12px; display: grid; gap: 8px; background: #121820; border: 1px solid #2d3743; border-radius: 12px; }.inspector-title { display: flex; justify-content: space-between; color: #788699; font-size: 10px; font-weight: 800; letter-spacing: .08em; }.delivery-sequence-history article,.delivery-task { padding: 8px; background: #191f28; border: 1px solid #303b48; border-radius: 8px; }.delivery-sequence-history article.active { border-color: #5f8eb7; background: #1b2a37; }.delivery-sequence-history article button { width: 100%; padding: 0; display: grid; gap: 4px; color: #dbe4ee; background: transparent; border: 0; text-align: left; cursor: pointer; }.delivery-sequence-history small,.delivery-task small,.delivery-inspector p { color: #758195; font-size: 10px; }.delivery-task { display: grid; gap: 4px; }.delivery-timeline { min-width: 0; padding: 10px 16px 14px; overflow: hidden; background: #11161d; border-top: 1px solid #2a333e; }.delivery-timeline header { height: 44px; display: flex; align-items: center; justify-content: space-between; }.delivery-timeline header h2 { margin: 2px 0 0; font-size: 14px; }.delivery-timeline header button { min-height: 36px; background: #284e70; border-color: #3d6b91; }.timeline-ruler { height: 20px; display: grid; grid-template-columns: 88px 1fr; align-items: center; gap: 8px; color: #667487; font-size: 9px; }.timeline-ruler i { height: 1px; background: #2d3743; }.timeline-track { min-width: 0; height: 84px; margin-left: 96px; display: flex; gap: 5px; overflow-x: auto; }.delivery-timeline-clip { position: relative; min-width: 180px; padding: 10px; display: grid; grid-template-columns: auto 1fr auto; align-content: center; gap: 5px 8px; color: #cdd7e2; background: #1b232d; border: 1px solid #354250; border-radius: 9px; cursor: pointer; }.delivery-timeline-clip.active { border-color: #68a1d1; background: #1b3143; }.delivery-timeline-clip span { color: #7391ab; font-size: 9px; }.delivery-timeline-clip strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }.delivery-timeline-clip small { color: #8592a2; font-size: 9px; }.delivery-timeline-clip em { position: absolute; left: -8px; top: 4px; padding: 2px 5px; color: #d6b681; background: #302719; border: 1px solid #654e2e; border-radius: 5px; font-size: 8px; font-style: normal; }
.delivery-comparison { min-width: 0; min-height: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.delivery-comparison figure { min-width: 0; min-height: 0; margin: 0; display: grid; grid-template-rows: minmax(0, 1fr) auto; gap: 6px; }
.delivery-comparison figcaption { color: #8491a3; font-size: 10px; text-align: center; }
.delivery-local-editor { grid-column: 1 / -1; min-width: 0; min-height: 0; overflow: hidden; background: #11171e; border: 1px solid #2c3642; border-radius: 13px; }
.delivery-video-history article { min-height: 52px; padding: 8px; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; background: #191f28; border: 1px solid #303b48; border-radius: 8px; }
.delivery-video-history article.active { border-color: #5f8eb7; background: #1b2a37; }
.delivery-video-history article > div { min-width: 0; display: grid; gap: 4px; }
.delivery-video-history article button { min-width: 62px; min-height: 44px; padding: 0 10px; color: #dbe5ef; background: #202832; border: 1px solid #3a4858; border-radius: 8px; cursor: pointer; }
.delivery-video-history article > span { color: #79b9a0; font-size: 10px; }
.delivery-video-history small { overflow: hidden; color: #758195; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.delivery-compose-panel { max-height: 100%; overflow: auto; }
.delivery-compose-boundary,.delivery-compose-shot { min-width: 0; padding: 9px; display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 8px; background: #191f28; border: 1px solid #303b48; border-radius: 8px; }
.delivery-compose-boundary > span,.delivery-compose-shot > div { min-width: 0; display: grid; gap: 3px; }
.delivery-compose-panel small { overflow: hidden; color: #758195; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.delivery-compose-panel select,.delivery-compose-panel input[type="number"] { min-height: 38px; padding: 0 8px; color: #dbe5ef; background: #0f141a; border: 1px solid #3a4654; border-radius: 7px; }
.delivery-compose-panel input[type="checkbox"] { width: 20px; height: 20px; accent-color: #5f91bd; }
.delivery-compose-shot > span { color: #7ca8cb; font-size: 10px; }
.delivery-compose-panel footer { display: grid; grid-template-columns: 1fr 1.4fr; gap: 8px; }
.delivery-compose-panel footer button { min-height: 44px; color: #dbe5ef; background: #202832; border: 1px solid #3a4858; border-radius: 8px; cursor: pointer; }
.delivery-compose-panel footer button:last-child { background: #284e70; border-color: #3d6b91; }
.delivery-compose-panel footer button:disabled { opacity: .45; cursor: wait; }
.delivery-sequence-review { border-color: #705b32 !important; background: #201c15 !important; }
.delivery-sequence-review footer { display: grid; grid-template-columns: 1fr 1.5fr; gap: 8px; }
.delivery-sequence-review footer button { min-height: 44px; color: #dbe5ef; background: #25201a; border: 1px solid #5b4930; border-radius: 8px; cursor: pointer; }
.delivery-sequence-review footer button:last-child { color: #e6f4ec; background: #234538; border-color: #3d755f; }
.delivery-sequence-review footer button:disabled { opacity: .45; cursor: wait; }
@keyframes delivery-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .delivery-state i { animation: none; } }
@media (max-width: 1020px) { .delivery-main { grid-template-columns: 1fr; overflow: auto; }.delivery-inspector { grid-template-columns: 1fr 1fr; overflow: visible; }.delivery-player { min-height: 480px; } }
@media (max-width: 720px) { .delivery-workbench { grid-template-rows: auto minmax(0, 1fr) 168px; }.delivery-header { align-items: flex-start; flex-direction: column; }.delivery-main { padding: 10px; }.delivery-inspector { grid-template-columns: 1fr; }.timeline-track { margin-left: 0; }.timeline-ruler { display: none; } }
</style>
