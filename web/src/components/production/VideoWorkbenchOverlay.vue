<script setup lang="ts">
import { Close, Refresh, VideoPlay } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { canvasApi } from "../../api/client";
import type { VideoWorkbenchDto, VideoWorkbenchTrackDto } from "../../api/types";
import DeliveryWorkbench from "../director/DeliveryWorkbench.vue";
import VideoGenerationWorkspace from "../director/VideoGenerationWorkspace.vue";

type WorkbenchTab = "preview" | "generate" | "edit";
const props = withDefaults(defineProps<{ projectId: string; tab?: WorkbenchTab; trackId?: string; shotId?: string }>(), { tab: "preview", trackId: "", shotId: "" });
const emit = defineEmits<{ close: []; tabChange: [tab: WorkbenchTab]; trackChange: [trackId: string] }>();
const state = ref<"loading" | "ready" | "stale" | "error">("loading");
const error = ref("");
const data = ref<VideoWorkbenchDto>();
const selectedTrackId = ref("");
let controller: AbortController | undefined;
let sequence = 0;

const selectedTrack = computed<VideoWorkbenchTrackDto | undefined>(() => data.value?.tracks.find((track) => track.id === selectedTrackId.value));
const selectedVersion = computed(() => selectedTrack.value?.versions.find((version) => version.selected) ?? selectedTrack.value?.versions[0]);
const visibleReferences = computed(() => (selectedTrack.value?.orderedReferences.length ? selectedTrack.value.orderedReferences : data.value?.approvedReferences ?? []).filter((item) => item.semanticRole !== "style_source"));

async function load(background = false) {
  if (props.tab !== "preview") return;
  controller?.abort("video workbench superseded");
  const request = new AbortController();
  controller = request;
  const requestSequence = ++sequence;
  state.value = background && data.value ? "ready" : "loading";
  error.value = "";
  try {
    const result = await canvasApi.videoWorkbench(props.projectId, request.signal);
    if (request.signal.aborted || requestSequence !== sequence) return;
    data.value = result;
    selectedTrackId.value = result.tracks.some((track) => track.id === props.trackId) ? props.trackId : result.activeTrackId ?? result.tracks[0]?.id ?? "";
    state.value = "ready";
  } catch (reason) {
    if (request.signal.aborted || requestSequence !== sequence) return;
    error.value = reason instanceof Error ? reason.message : String(reason);
    state.value = data.value ? "stale" : "error";
  }
}

function selectTrack(trackId: string) {
  selectedTrackId.value = trackId;
  emit("trackChange", trackId);
}

watch([() => props.projectId, () => props.tab], ([, tab]) => {
  if (tab !== "preview") return;
  if (!data.value) void load(false);
}, { immediate: true });
watch(() => props.trackId, (trackId) => { if (trackId && data.value?.tracks.some((track) => track.id === trackId)) selectedTrackId.value = trackId; });
onBeforeUnmount(() => { sequence += 1; controller?.abort("video workbench unmounted"); });
</script>

<template>
  <section class="video-workbench" aria-label="视频工作台">
    <header class="workbench-header">
      <div><span>VIDEO WORKBENCH</span><h2>视频生产与交付</h2></div>
      <nav aria-label="视频工作台标签"><button v-for="item in ([['preview','预览'],['generate','视频生成'],['edit','剪辑与交付']] as const)" :key="item[0]" type="button" :class="{ active: tab === item[0] }" @click="$emit('tabChange',item[0])">{{ item[1] }}</button></nav>
      <button class="refresh" type="button" aria-label="刷新视频工作台" @click="load(true)"><Refresh /></button>
      <button class="close" type="button" aria-label="关闭视频工作台" @click="$emit('close')"><Close /></button>
    </header>

    <VideoGenerationWorkspace v-if="tab === 'generate'" :project-id="projectId" :focused-item-id="shotId || trackId" :panel="'main'" />
    <DeliveryWorkbench v-else-if="tab === 'edit'" :project-id="projectId" :focused-item-id="shotId || trackId" panel="main" />
    <div v-else-if="state === 'loading'" class="workbench-state" aria-busy="true">正在读取参考、Prompt、任务与版本…</div>
    <div v-else-if="state === 'error'" class="workbench-state error" role="alert"><b>视频工作台加载失败</b><p>{{ error }}</p><button type="button" @click="load(false)">重新加载</button></div>
    <template v-else-if="tab === 'preview'">
      <div v-if="state === 'stale'" class="stale-banner">数据可能过期：{{ error }}</div>
      <section v-if="tab === 'preview'" class="preview-workspace">
        <aside><span>TRACKS</span><button v-for="track in data?.tracks" :key="track.id" type="button" :class="{ active: selectedTrackId === track.id }" @click="selectTrack(track.id)"><b>{{ track.title }}</b><small>{{ track.durationSeconds }}s · {{ track.versions.length }} 个版本</small></button><p v-if="!data?.tracks.length">尚无视频 Track。</p></aside>
        <main><div v-if="selectedVersion?.contentUrl" class="player"><video :src="selectedVersion.contentUrl" controls preload="metadata" /></div><div v-else class="empty-player"><VideoPlay /><b>尚无可预览视频</b><p>在“视频生成”标签核对冻结输入和 Provider 参数。</p></div><section v-if="selectedTrack" class="preview-meta"><h3>{{ selectedTrack.title }}</h3><p>{{ selectedTrack.shotIds.length }} 个镜头 · {{ selectedTrack.durationSeconds }} 秒</p><mark v-if="selectedVersion">{{ selectedVersion.status }}{{ selectedVersion.selected ? ' · 当前采用' : '' }}</mark></section></main>
        <aside class="versions"><span>VERSION HISTORY</span><article v-for="(version,index) in selectedTrack?.versions" :key="version.assetId"><b>V{{ index + 1 }}</b><small>{{ version.status }} · {{ new Date(version.createdAt).toLocaleString() }}</small><mark v-if="version.selected">当前采用</mark></article><p v-if="!selectedTrack?.versions.length">还没有视频版本。</p></aside>
      </section>

      <section v-else-if="tab === 'generate'" class="generate-workspace">
        <header class="reference-strip"><span>实际 Provider 参考</span><article v-for="reference in visibleReferences" :key="reference.assetId" :class="{ disabled: !reference.providerEligible }"><img v-if="reference.contentUrl" :src="reference.contentUrl" alt="" /><div><b>{{ reference.title }}</b><small>#{{ reference.ordinal }} · {{ reference.semanticRole }}</small></div><mark>{{ reference.providerEligible ? '可提交' : '不可提交' }}</mark></article><p v-if="!visibleReferences.length">当前没有已批准参考。</p></header>
        <aside class="prompt-panel"><span>PROVIDER PROMPT</span><h3>专业自然语言 Prompt</h3><textarea :value="selectedTrack?.prompt || ''" readonly aria-label="Provider 实际 Prompt" /><details><summary>审计信息</summary><dl><div><dt>Track</dt><dd>{{ selectedTrack?.id || '—' }}</dd></div><div><dt>Shot</dt><dd>{{ selectedTrack?.shotIds.join(' · ') || '—' }}</dd></div></dl></details></aside>
        <main class="generation-result"><div v-if="selectedVersion?.contentUrl" class="player"><video :src="selectedVersion.contentUrl" controls preload="metadata" /></div><div v-else class="empty-player"><VideoPlay /><b>等待视频结果</b><p>生成提交仍会经过费用确认、输入哈希冻结和现有任务安全边界。</p></div><section class="task-summary"><b>任务状态</b><pre>{{ selectedTrack?.task ? JSON.stringify(selectedTrack.task, null, 2) : '当前没有运行中的视频任务' }}</pre></section></main>
        <aside class="provider-panel"><span>EXECUTION</span><h3>生成设置</h3><dl><div v-for="(value,key) in (selectedTrack?.providerConfig ?? {})" :key="key"><dt>{{ key }}</dt><dd>{{ String(value) }}</dd></div></dl><p>参考顺序、Prompt、模型和输入哈希只在费用确认后冻结；本页加载和切换不会创建任务。</p></aside>
        <footer class="track-strip"><button v-for="track in data?.tracks" :key="track.id" type="button" :class="{ active: track.id === selectedTrackId }" @click="selectTrack(track.id)"><b>{{ track.title }}</b><small>{{ track.durationSeconds }}s · {{ track.versions.length }} 版本</small></button></footer>
      </section>
    </template>
  </section>
</template>

<style scoped>
.video-workbench{position:absolute;z-index:140;inset:10px;display:grid;grid-template-rows:62px minmax(0,1fr);overflow:hidden;color:#e8eef7;background:#10161d;border:1px solid #354250;border-radius:16px;box-shadow:0 28px 80px rgb(0 0 0 / 52%)}.workbench-header{padding:8px 12px;display:flex;align-items:center;gap:12px;background:#151c24;border-bottom:1px solid #2d3743}.workbench-header>div{display:grid;gap:2px}.workbench-header span,.preview-workspace aside>span,.generate-workspace span{color:#6e8eae;font-size:9px;font-weight:800;letter-spacing:.13em}.workbench-header h2{margin:0;font-size:17px}.workbench-header nav{margin-left:auto;display:flex;gap:5px}.workbench-header nav button,.refresh,.close{min-height:44px;padding:0 14px;color:#9eacbd;background:transparent;border:1px solid transparent;border-radius:9px;cursor:pointer}.workbench-header nav button.active{color:#eff7ff;background:#24364a;border-color:#456c91}.refresh,.close{width:44px;padding:12px;background:#1d252e;border-color:#354250}.refresh svg,.close svg{width:17px}.workbench-state{height:100%;display:grid;place-items:center;align-content:center;gap:9px;color:#7d8b9d}.workbench-state.error{color:#e0aaa3}.workbench-state p{max-width:520px;margin:0}.workbench-state button{min-height:44px;padding:0 14px;color:#fff;background:#72444a;border:1px solid #965860;border-radius:9px}.stale-banner{position:absolute;z-index:10;top:72px;right:16px;padding:9px 12px;color:#ddba80;background:#2c2419;border:1px solid #705a38;border-radius:9px}.preview-workspace{min-height:0;display:grid;grid-template-columns:260px minmax(0,1fr) 300px;overflow:hidden}.preview-workspace>aside{padding:14px;display:grid;align-content:start;gap:7px;overflow:auto;background:#141b23}.preview-workspace>aside:first-child{border-right:1px solid #2b3540}.preview-workspace>aside:last-child{border-left:1px solid #2b3540}.preview-workspace aside button,.versions article{min-height:56px;padding:10px;display:grid;gap:4px;color:#cad5e1;text-align:left;background:#1a222b;border:1px solid #2f3a47;border-radius:9px}.preview-workspace aside button{cursor:pointer}.preview-workspace aside button.active{background:#203247;border-color:#4f7eaa}.preview-workspace small,.preview-workspace p{color:#77869a}.preview-workspace>main{min-width:0;padding:18px;display:flex;flex-direction:column;gap:12px;overflow:auto}.player{min-height:0;flex:1;display:grid;place-items:center;background:#090d12;border-radius:12px;overflow:hidden}.player video{width:100%;height:100%;max-height:100%;object-fit:contain}.empty-player{min-height:360px;display:grid;place-items:center;align-content:center;gap:9px;color:#718094;background:#0c1117;border:1px dashed #334150;border-radius:12px}.empty-player svg{width:42px}.empty-player p{max-width:420px;margin:0;text-align:center}.preview-meta{display:flex;align-items:center;gap:12px}.preview-meta h3{margin:0}.preview-meta p{margin:0;color:#7e8b9c}.preview-meta mark,.versions mark{margin-left:auto;padding:4px 8px;color:#9fd0b5;background:#1d3828;border-radius:999px;font-size:9px}.generate-workspace{min-height:0;display:grid;grid-template-columns:390px minmax(0,1fr) 320px;grid-template-rows:110px minmax(0,1fr) 96px;overflow:hidden}.reference-strip{grid-column:1/-1;padding:10px 14px;display:flex;align-items:center;gap:8px;overflow-x:auto;background:#141b23;border-bottom:1px solid #2d3742}.reference-strip>span{flex:0 0 90px}.reference-strip article{flex:0 0 220px;height:70px;padding:6px;display:grid;grid-template-columns:58px minmax(0,1fr);grid-template-rows:1fr auto;gap:3px 8px;background:#192129;border:1px solid #303c49;border-radius:9px}.reference-strip article.disabled{opacity:.55}.reference-strip img{grid-row:1/3;width:58px;height:58px;object-fit:cover;border-radius:7px}.reference-strip article div{min-width:0;display:grid;align-content:center;gap:3px}.reference-strip article b,.reference-strip article small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.reference-strip article small{color:#77869a}.reference-strip mark{justify-self:start;color:#8dc6a6;background:transparent;font-size:9px}.prompt-panel,.provider-panel{padding:16px;overflow:auto;background:#141b23}.prompt-panel{border-right:1px solid #2d3742}.provider-panel{border-left:1px solid #2d3742}.prompt-panel h3,.provider-panel h3{margin:5px 0 12px}.prompt-panel textarea{box-sizing:border-box;width:100%;height:calc(100% - 100px);min-height:300px;padding:12px;color:#dce5ee;background:#0e141a;border:1px solid #33414e;border-radius:9px;line-height:1.65;resize:none}.prompt-panel summary{min-height:44px;display:flex;align-items:center;cursor:pointer}.prompt-panel dl,.provider-panel dl{display:grid;gap:6px}.prompt-panel dl div,.provider-panel dl div{display:grid;grid-template-columns:100px minmax(0,1fr);gap:8px;padding:7px 0;border-top:1px solid #2c3641}.prompt-panel dt,.provider-panel dt{color:#76869a}.prompt-panel dd,.provider-panel dd{margin:0;overflow-wrap:anywhere}.provider-panel p{color:#7d8b9d;line-height:1.6}.generation-result{min-width:0;padding:16px;display:flex;flex-direction:column;gap:10px;overflow:auto}.generation-result .player{min-height:300px}.task-summary{padding:10px;background:#171f27;border:1px solid #2f3b48;border-radius:9px}.task-summary pre{max-height:140px;margin:7px 0 0;overflow:auto;color:#8292a4;font-size:10px;white-space:pre-wrap}.track-strip{grid-column:1/-1;padding:9px 12px;display:flex;gap:7px;overflow-x:auto;background:#121820;border-top:1px solid #2d3742}.track-strip button{flex:0 0 190px;min-height:66px;padding:9px;display:grid;gap:4px;color:#cbd6e2;text-align:left;background:#1a222b;border:1px solid #303b47;border-radius:9px;cursor:pointer}.track-strip button.active{background:#223449;border-color:#4f80ad}.track-strip small{color:#78879a}@media(max-width:1180px){.preview-workspace{grid-template-columns:220px minmax(0,1fr)}.preview-workspace>.versions{display:none}.generate-workspace{grid-template-columns:340px minmax(0,1fr);grid-template-rows:100px minmax(0,1fr) 96px}.provider-panel{display:none}}@media(max-width:850px){.video-workbench{inset:4px}.workbench-header>div{display:none}.generate-workspace{display:flex;flex-direction:column;overflow:auto}.reference-strip{min-height:100px}.prompt-panel textarea{height:300px}.generation-result .player{min-height:360px}.track-strip{min-height:90px}.preview-workspace{grid-template-columns:1fr}.preview-workspace>aside{display:none}}
.preview-workspace>main{min-height:0;overflow:hidden}
.player video{display:block;min-width:0;min-height:0;max-width:100%;max-height:100%}
</style>
