<script setup lang="ts">
import { Background } from "@vue-flow/background";
import { VueFlow, useVueFlow, type Edge, type Node, type NodeMouseEvent } from "@vue-flow/core";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";
import { ChatDotRound, Close, Refresh } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { canvasApi } from "../../api/client";
import type { ProductionFlowDto, ProductionFlowNodeDto } from "../../api/types";
import type { DirectorDirtyRegistration } from "../director/directorDirtyState";
import ProductionFlowNode from "./ProductionFlowNode.vue";
import StoryboardEditorOverlay from "./StoryboardEditorOverlay.vue";
import VideoWorkbenchOverlay from "./VideoWorkbenchOverlay.vue";

type LoadState = "loading" | "ready" | "stale" | "error";
type WorkbenchTab = "preview" | "generate" | "edit";
const props = withDefaults(defineProps<{ projectId: string; focusedItemId?: string }>(), { focusedItemId: "" });
const emit = defineEmits<{ "dirty-change": [registration?: DirectorDirtyRegistration] }>();
const route = useRoute();
const router = useRouter();
const { fitView, getViewport } = useVueFlow("toonflow-production");
const state = ref<LoadState>("loading");
const error = ref("");
const flow = ref<ProductionFlowDto>();
const nodes = shallowRef<Node[]>([]);
const edges = shallowRef<Edge[]>([]);
const selectedId = ref("");
const assistantOpen = ref(typeof window === "undefined" || window.innerWidth >= 1600);
const storyboardOpen = ref(false);
const saveStatus = ref<"saved" | "saving" | "conflict" | "error">("saved");
let activeController: AbortController | undefined;
let requestSequence = 0;
let restoringViewport = false;
let vueFlowReady = false;
const pendingLayoutOperations = new Set<"move_node" | "viewport">();

const selectedArtifact = computed(() => flow.value?.nodes.find((node) => node.id === selectedId.value));
const selectedArtifactDetails = computed(() => {
  const artifact = selectedArtifact.value;
  if (!artifact) return [];
  const data = artifact.data;
  switch (artifact.kind) {
    case "script":
      return [
        { label: "当前版本", value: data.revision ? `R${String(data.revision)}` : "尚未设定" },
        { label: "剧情摘要", value: String(data.summary || "等待选定正式剧情") },
      ];
    case "director_plan":
      return [
        { label: "镜头数量", value: `${String(data.shotCount || 0)} 镜` },
        { label: "规划时长", value: `${String(data.durationSeconds || 0)} 秒` },
      ];
    case "assets":
      return [{ label: "已批准素材", value: `${String(data.approvedCount || 0)} 项` }];
    case "storyboard_table":
      return [
        { label: "分镜版本", value: data.storyboardRevision ? `R${String(data.storyboardRevision)}` : "尚未保存" },
        { label: "镜头数量", value: `${String(data.shotCount || 0)} 镜` },
        { label: "总时长", value: `${String(data.durationSeconds || 0)} 秒` },
      ];
    case "storyboard":
      return [{ label: "画面参考", value: `${String(data.assetCount || 0)} 项` }];
    case "workbench":
      return [
        { label: "当前采用版本", value: `${String(data.selectedVideoCount || 0)} 个` },
        { label: "进行中任务", value: `${String(data.activeTaskCount || 0)} 个` },
      ];
    default:
      return [];
  }
});
const shots = computed(() => {
  const table = flow.value?.nodes.find((node) => node.kind === "storyboard_table");
  return (table?.data.shots ?? []) as Array<Record<string, unknown>>;
});
const workbenchOpen = computed(() => route.query.workspace === "video");
const workbenchTab = computed<WorkbenchTab>(() => ["preview", "generate", "edit"].includes(String(route.query.tab)) ? String(route.query.tab) as WorkbenchTab : "preview");
const trackId = computed(() => typeof route.query.track === "string" ? route.query.track : "");
const shotId = computed(() => typeof route.query.shot === "string" ? route.query.shot : "");

function statusText(status: ProductionFlowNodeDto["status"]) {
  return ({
    blocked: "受阻",
    stale: "已失效",
    needs_review: "待审核",
    active: "进行中",
    complete: "已完成",
    ready: "可开始",
  } as Record<ProductionFlowNodeDto["status"], string>)[status] ?? status;
}

function rebuildGraph() {
  if (!flow.value) return;
  nodes.value = flow.value.nodes.map((artifact) => ({
    id: artifact.id,
    type: "artifact",
    position: artifact.position,
    data: { artifact, activate },
    selectable: true,
    draggable: true,
    zIndex: artifact.id === selectedId.value ? 10 : 1,
  }));
  edges.value = flow.value.edges.map((edge) => ({ ...edge, type: "smoothstep", animated: false, style: { stroke: "#46596c", strokeWidth: 1.5 } }));
}

async function load(background = false) {
  activeController?.abort("production flow superseded");
  const controller = new AbortController();
  activeController = controller;
  const sequence = ++requestSequence;
  state.value = background && flow.value ? "ready" : "loading";
  error.value = "";
  try {
    const result = await canvasApi.productionFlow(props.projectId, controller.signal);
    if (controller.signal.aborted || sequence !== requestSequence) return;
    flow.value = result;
    if (props.focusedItemId && result.nodes.some((node) => node.id === props.focusedItemId)) selectedId.value = props.focusedItemId;
    else if (!result.nodes.some((node) => node.id === selectedId.value)) selectedId.value = result.nodes[0]?.id ?? "";
    rebuildGraph();
    state.value = "ready";
    await nextTick();
    if (vueFlowReady) {
      if (props.focusedItemId) await focusNode(props.focusedItemId);
      else await fitProductionOverview();
    }
  } catch (reason) {
    if (controller.signal.aborted || sequence !== requestSequence) return;
    error.value = reason instanceof Error ? reason.message : String(reason);
    state.value = flow.value ? "stale" : "error";
  } finally {
    if (sequence === requestSequence) activeController = undefined;
  }
}

async function onFlowInit() {
  vueFlowReady = true;
  if (props.focusedItemId) await focusNode(props.focusedItemId);
  else await fitProductionOverview();
}

async function fitProductionOverview() {
  restoringViewport = true;
  try {
    await fitView({ padding: 0.18, minZoom: 0.62, maxZoom: 0.86, duration: reducedMotion() ? 0 : 240 });
  } finally {
    await nextTick();
    restoringViewport = false;
  }
}

function reducedMotion() {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

async function focusNode(nodeId: string) {
  if (!nodes.value.some((node) => node.id === nodeId)) return;
  await fitView({ nodes: [nodeId], padding: 0.7, minZoom: 0.72, maxZoom: 0.9, duration: reducedMotion() ? 0 : 220 });
}

function updateSelection(nodeId = "") {
  selectedId.value = nodeId;
  nodes.value = nodes.value.map((node) => ({ ...node, zIndex: node.id === nodeId ? 10 : 1 }));
  void router.replace({ name: "project-production", params: { projectId: props.projectId }, query: { ...(nodeId ? { item: nodeId } : {}), ...(workbenchOpen.value ? { workspace: "video", tab: workbenchTab.value, ...(trackId.value ? { track: trackId.value } : {}), ...(shotId.value ? { shot: shotId.value } : {}) } : {}) } });
}

function onNodeClick(event: NodeMouseEvent) {
  updateSelection(event.node.id);
}

function activate(artifact: ProductionFlowNodeDto) {
  updateSelection(artifact.id);
  if (artifact.kind === "script") {
    void router.push({ name: "project-script", params: { projectId: props.projectId }, query: artifact.data.storyId ? { item: String(artifact.data.storyId) } : {} });
    return;
  }
  if (artifact.kind === "assets") {
    void router.push({ name: "project-assets", params: { projectId: props.projectId } });
    return;
  }
  if (["director_plan", "storyboard_table", "storyboard"].includes(artifact.kind)) {
    storyboardOpen.value = true;
    return;
  }
  if (artifact.kind === "workbench") openWorkbench("preview");
}

async function persistLayout(operation: "move_node" | "viewport") {
  pendingLayoutOperations.add(operation);
  if (!flow.value || saveStatus.value === "saving") return;
  while (flow.value && pendingLayoutOperations.size) {
    const operations = [...pendingLayoutOperations];
    pendingLayoutOperations.clear();
    saveStatus.value = "saving";
    const nodeSnapshot = nodes.value.map((node) => ({ nodeId: node.id, x: node.position.x, y: node.position.y }));
    try {
      const result = await canvasApi.saveProductionFlowLayout(props.projectId, flow.value.revision, {
        nodes: nodeSnapshot,
        viewport: getViewport(),
        operations: operations.map((type) => ({ type })),
      });
      flow.value.revision = result.layoutVersion;
      flow.value.viewport = result.viewport;
      flow.value.nodes = flow.value.nodes.map((artifact) => {
        const node = nodeSnapshot.find((item) => item.nodeId === artifact.id);
        return node ? { ...artifact, position: { x: node.x, y: node.y } } : artifact;
      });
      saveStatus.value = "saved";
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      saveStatus.value = /409|版本|冲突/i.test(message) ? "conflict" : "error";
      ElMessage.error(`保存画布布局失败：${message}`);
      pendingLayoutOperations.clear();
      break;
    }
  }
}

function onViewportChangeEnd() {
  if (!restoringViewport) void persistLayout("viewport");
}

function openWorkbench(tab: WorkbenchTab) {
  void router.push({ name: "project-production", params: { projectId: props.projectId }, query: { ...(selectedId.value ? { item: selectedId.value } : {}), workspace: "video", tab } });
}

function closeWorkbench() {
  void router.push({ name: "project-production", params: { projectId: props.projectId }, query: selectedId.value ? { item: selectedId.value } : {} });
}

function updateWorkbenchTab(tab: WorkbenchTab) {
  void router.replace({ name: "project-production", params: { projectId: props.projectId }, query: { ...(selectedId.value ? { item: selectedId.value } : {}), workspace: "video", tab, ...(trackId.value ? { track: trackId.value } : {}), ...(shotId.value ? { shot: shotId.value } : {}) } });
}

function updateTrack(nextTrackId: string) {
  void router.replace({ name: "project-production", params: { projectId: props.projectId }, query: { ...(selectedId.value ? { item: selectedId.value } : {}), workspace: "video", tab: workbenchTab.value, track: nextTrackId, ...(shotId.value ? { shot: shotId.value } : {}) } });
}

watch(() => props.projectId, () => { flow.value = undefined; selectedId.value = ""; void load(false); }, { immediate: true });
watch(() => props.focusedItemId, (itemId) => { if (itemId && flow.value?.nodes.some((node) => node.id === itemId)) { updateSelection(itemId); void focusNode(itemId); } });
onBeforeUnmount(() => { requestSequence += 1; activeController?.abort("production flow unmounted"); emit("dirty-change", undefined); });
</script>

<template>
  <section class="production-workspace" :class="{ 'assistant-collapsed': !assistantOpen }" aria-label="一人一猫生产画布">
    <aside class="production-inspector">
      <header><div><span>PRODUCTION FLOW</span><h1>生产画布</h1></div><button type="button" aria-label="刷新生产画布" @click="load(true)"><Refresh /></button></header>
      <template v-if="selectedArtifact"><section><span>{{ selectedArtifact.kind.replace('_',' ') }}</span><h2>{{ selectedArtifact.title }}</h2><p>{{ selectedArtifact.subtitle }}</p><mark :data-status="selectedArtifact.status">{{ statusText(selectedArtifact.status) }}</mark></section><dl><div v-for="detail in selectedArtifactDetails" :key="detail.label"><dt>{{ detail.label }}</dt><dd>{{ detail.value }}</dd></div></dl><button class="primary" type="button" @click="activate(selectedArtifact)">{{ selectedArtifact.kind === 'workbench' ? '打开视频工作台' : ['director_plan','storyboard_table','storyboard'].includes(selectedArtifact.kind) ? '编辑导演分镜' : selectedArtifact.kind === 'script' ? '打开剧本' : '管理角色资产' }}</button></template>
      <p v-else>选择六个稳定产物中的一个，查看当前状态和下一操作。</p>
    </aside>
    <main class="flow-canvas">
      <div v-if="state === 'loading' && !flow" class="canvas-state" aria-busy="true">正在建立六个生产产物…</div>
      <div v-else-if="state === 'error' && !flow" class="canvas-state error" role="alert"><b>生产画布加载失败</b><p>{{ error }}</p><button type="button" @click="load(false)">重新加载</button></div>
      <VueFlow v-else id="toonflow-production" v-model:nodes="nodes" v-model:edges="edges" :min-zoom="0.4" :max-zoom="1.5" :default-viewport="flow?.viewport" :connect-on-click="false" :fit-view-on-init="false" @init="onFlowInit" @node-click="onNodeClick" @node-drag-stop="persistLayout('move_node')" @viewport-change-end="onViewportChangeEnd" @pane-click="updateSelection()">
        <Background pattern-color="#26313d" :gap="24" :size="1" />
        <template #node-artifact="{ data, selected }"><ProductionFlowNode :artifact="data.artifact" :selected="selected" :activate="data.activate" /></template>
      </VueFlow>
      <div v-if="state === 'stale'" class="canvas-warning">数据可能过期：{{ error }}</div>
      <div class="layout-status" :data-state="saveStatus">{{ { saved:'布局已保存',saving:'保存布局中',conflict:'布局版本冲突',error:'布局保存失败' }[saveStatus] }}</div>
    </main>
    <aside class="director-assistant" :class="{ collapsed: !assistantOpen }">
      <header><div><span>DIRECTOR ASSISTANT</span><b>制作检查</b></div><button type="button" :aria-label="assistantOpen ? '收起导演助手' : '展开导演助手'" @click="assistantOpen = !assistantOpen"><Close v-if="assistantOpen" /><ChatDotRound v-else /></button></header>
      <template v-if="assistantOpen"><section><b>当前产物</b><p>{{ selectedArtifact?.title ?? '选择一个生产产物' }}</p><small>{{ selectedArtifact?.subtitle ?? '画布只展示稳定业务产物，不显示 Gate、Review 或技术任务节点。' }}</small></section><section><b>下一步建议</b><p>{{ selectedArtifact?.status === 'blocked' ? '先处理当前执行阻塞，再提交任何 Provider。' : selectedArtifact?.kind === 'workbench' ? '打开视频工作台，同屏核对参考、Prompt、任务和版本。' : '使用当前产物的主操作继续。' }}</p></section><section><b>安全边界</b><p>助手只解释和导航，不保存正式版本、不批准审核，也不提交付费 Provider。</p></section><button class="workbench-button" type="button" @click="openWorkbench('preview')">打开视频工作台</button></template>
    </aside>
    <footer class="filmstrip"><div><span>SHOT STRIP</span><b>{{ shots.length }} 镜</b></div><button v-for="(shot,index) in shots" :key="String(shot.id)" type="button" @click="storyboardOpen = true"><span>{{ index + 1 }}</span><b>{{ String(shot.title || `镜头 ${index+1}`) }}</b><small>{{ String(shot.durationSeconds || 0) }}s</small></button><p v-if="!shots.length">等待导演分镜</p></footer>
    <StoryboardEditorOverlay v-if="storyboardOpen && flow" :project-id="projectId" :flow="flow" @close="storyboardOpen = false" @saved="storyboardOpen = false; load(false)" @dirty-change="$emit('dirty-change',$event)" />
    <VideoWorkbenchOverlay v-if="workbenchOpen" :project-id="projectId" :tab="workbenchTab" :track-id="trackId" :shot-id="shotId" @close="closeWorkbench" @tab-change="updateWorkbenchTab" @track-change="updateTrack" />
  </section>
</template>

<style scoped>
.production-workspace{--assistant:360px;height:100%;min-height:0;display:grid;grid-template-columns:280px minmax(0,1fr) var(--assistant);grid-template-rows:minmax(0,1fr) 126px;overflow:hidden;color:#e7eef7;background:#0c1218}.production-workspace.assistant-collapsed{--assistant:58px}.production-inspector,.director-assistant{min-height:0;overflow:auto;background:#151b22}.production-inspector{border-right:1px solid #2b3540}.director-assistant{border-left:1px solid #2b3540}.production-inspector>header,.director-assistant>header{min-height:66px;padding:10px 12px;display:flex;align-items:center;border-bottom:1px solid #2c3641}.production-inspector>header div,.director-assistant>header div{display:grid;gap:2px}.production-inspector span,.director-assistant span,.filmstrip>div span{color:#6887a6;font-size:9px;font-weight:800;letter-spacing:.13em}.production-inspector h1{margin:0;font-size:17px}.production-inspector>header button,.director-assistant>header button{width:44px;height:44px;margin-left:auto;padding:12px;color:#afbdcb;background:#202a34;border:1px solid #394756;border-radius:9px;cursor:pointer}.production-inspector>header svg,.director-assistant>header svg{width:17px}.production-inspector>section,.director-assistant section{margin:12px;padding:12px;background:#1a222b;border:1px solid #2f3a47;border-radius:10px}.production-inspector h2{margin:6px 0}.production-inspector p,.director-assistant p,.director-assistant small{color:#8493a5;line-height:1.6}.production-inspector mark{padding:4px 8px;color:#9dcbb0;background:#1c3928;border-radius:999px;font-size:9px}.production-inspector mark[data-status=blocked]{color:#e4a29c;background:#3a2023}.production-inspector dl{margin:12px;display:grid;gap:6px}.production-inspector dl div{display:grid;grid-template-columns:92px minmax(0,1fr);gap:9px;padding:8px 0;border-top:1px solid #2c3641}.production-inspector dt{color:#718195;font-size:9px}.production-inspector dd{margin:0;overflow-wrap:anywhere;color:#b9c6d4;font-size:10px;line-height:1.55}.production-inspector>.primary,.workbench-button{min-height:44px;margin:0 12px;padding:0 13px;color:#ecf7ff;background:#2a628f;border:1px solid #4180b1;border-radius:9px;cursor:pointer}.flow-canvas{position:relative;min-width:0;min-height:0;overflow:hidden;background:#0d131a}.canvas-state{height:100%;display:grid;place-items:center;align-content:center;gap:9px;color:#7e8d9e}.canvas-state.error{color:#dfaaa3}.canvas-state p{max-width:500px}.canvas-state button{min-height:44px;padding:0 13px;color:#fff;background:#70434a;border:1px solid #955962;border-radius:9px}.canvas-warning{position:absolute;z-index:20;top:12px;left:50%;max-width:70%;padding:9px 12px;transform:translateX(-50%);color:#dbb979;background:#30271c;border:1px solid #6e5836;border-radius:9px}.layout-status{position:absolute;z-index:10;right:12px;bottom:12px;padding:6px 9px;color:#71869b;background:rgb(18 25 32 / 88%);border:1px solid #2d3a47;border-radius:8px;font-size:9px}.layout-status[data-state=conflict],.layout-status[data-state=error]{color:#dca07d}.director-assistant.collapsed{overflow:hidden}.director-assistant.collapsed>header{writing-mode:vertical-rl;min-height:190px}.director-assistant.collapsed>header div{display:none}.director-assistant section{display:grid;gap:5px}.director-assistant section b{font-size:11px}.director-assistant section p{margin:0}.director-assistant .workbench-button{width:calc(100% - 24px);margin-top:4px}.filmstrip{grid-column:1/-1;padding:10px 12px;display:flex;align-items:stretch;gap:8px;overflow-x:auto;background:#121820;border-top:1px solid #303a46}.filmstrip>div{flex:0 0 110px;display:grid;align-content:center;gap:4px}.filmstrip>button{flex:0 0 190px;padding:9px;display:grid;grid-template-columns:28px minmax(0,1fr);grid-template-rows:1fr auto;gap:3px 8px;align-items:center;color:#cbd6e2;text-align:left;background:#1a222b;border:1px solid #303b47;border-radius:9px;cursor:pointer}.filmstrip>button>span{grid-row:1/3;width:28px;height:28px;display:grid;place-items:center;background:#2a3949;border-radius:7px}.filmstrip>button b,.filmstrip>button small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.filmstrip>button small{color:#78889a}.filmstrip>p{color:#768599}@media(max-width:1599px){.production-workspace{--assistant:58px}.director-assistant:not(.collapsed){position:absolute;z-index:70;top:0;right:0;bottom:126px;width:360px;box-shadow:-18px 0 44px rgb(0 0 0 / 42%)}}@media(max-width:920px){.production-workspace{grid-template-columns:220px minmax(0,1fr) 0}.director-assistant{display:none}}@media(max-width:680px){.production-workspace{grid-template-columns:1fr;grid-template-rows:180px minmax(0,1fr) 110px}.production-inspector{border-right:0;border-bottom:1px solid #2b3540}.production-inspector dl{display:none}.production-inspector>section{margin:8px}.production-inspector>section p{display:none}.production-inspector>.primary{position:absolute;top:120px;right:10px}.flow-canvas{grid-row:2}.filmstrip{grid-row:3}}
</style>
