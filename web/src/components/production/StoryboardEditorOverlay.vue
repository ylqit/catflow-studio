<script setup lang="ts">
import { ArrowDown, ArrowUp, Close, CopyDocument, Delete, Plus, Scissor } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { canvasApi } from "../../api/client";
import type { EditorialShotDto, ProductionFlowDto } from "../../api/types";
import type { DirectorDirtyRegistration } from "../director/directorDirtyState";

const props = defineProps<{ projectId: string; flow: ProductionFlowDto }>();
const emit = defineEmits<{ close: []; saved: []; dirtyChange: [registration?: DirectorDirtyRegistration] }>();
const draftShots = ref<EditorialShotDto[]>([]);
const selectedId = ref("");
const saving = ref(false);
const draggedId = ref("");
const sourceNode = computed(() => props.flow.nodes.find((node) => node.kind === "storyboard_table") ?? props.flow.nodes.find((node) => node.kind === "director_plan"));
const sourceShots = computed(() => (sourceNode.value?.data.shots ?? []) as unknown as EditorialShotDto[]);
const revision = computed(() => Number(sourceNode.value?.data.storyboardRevision ?? 0));
const targetDuration = computed(() => Number(sourceNode.value?.data.durationSeconds ?? sourceShots.value.reduce((sum, shot) => sum + shot.durationSeconds, 0)));
const totalDuration = computed(() => draftShots.value.reduce((sum, shot) => sum + Number(shot.durationSeconds || 0), 0));
const delta = computed(() => totalDuration.value - targetDuration.value);
const selected = computed(() => draftShots.value.find((shot) => shot.id === selectedId.value) ?? draftShots.value[0]);
const dirty = computed(() => JSON.stringify(draftShots.value) !== JSON.stringify(sourceShots.value));

function reset() {
  draftShots.value = sourceShots.value.map((shot) => ({ ...shot, referenceBindings: [...(shot.referenceBindings ?? [])] }));
  selectedId.value = draftShots.value.find((shot) => shot.id === selectedId.value)?.id ?? draftShots.value[0]?.id ?? "";
}

function serializableShot(shot: EditorialShotDto, order: number): Record<string, unknown> {
  const value: Record<string, unknown> = { ...shot, order, direction: shot.direction || shot.visualDescription, action: shot.direction || shot.visualDescription, referenceBindings: [...(shot.referenceBindings ?? [])] };
  if (shot.id.startsWith("draft:")) {
    delete value.id;
    delete value.revision;
    delete value.referenceBindingRevision;
  }
  delete value.status;
  delete value.staleReason;
  delete value.promptId;
  delete value.sceneTitle;
  return value;
}

function newDraft(source: EditorialShotDto, overrides: Partial<EditorialShotDto> = {}): EditorialShotDto {
  return { ...source, ...overrides, id: `draft:${crypto.randomUUID()}`, revision: 0, referenceBindingRevision: 0, referenceBindings: [...(overrides.referenceBindings ?? source.referenceBindings ?? [])], status: "draft", staleReason: null, promptId: null };
}

function splitShot(shotId: string) {
  const index = draftShots.value.findIndex((shot) => shot.id === shotId);
  const source = draftShots.value[index];
  if (!source || source.durationSeconds < 4) return ElMessage.warning("镜头至少 4 秒才能拆成两个有效镜头");
  const first = Math.ceil(source.durationSeconds / 2);
  const second = source.durationSeconds - first;
  source.durationSeconds = first;
  draftShots.value.splice(index + 1, 0, newDraft(source, { title: `${source.title}（续）`, durationSeconds: second, continuityIn: source.continuityOut || source.continuityIn }));
}

function addShot() {
  const source = [...draftShots.value].sort((left, right) => right.durationSeconds - left.durationSeconds)[0];
  if (!source || source.durationSeconds < 4) return ElMessage.warning("需要从一个至少 4 秒的镜头拆出时长，避免静默改变全片总时长");
  splitShot(source.id);
  const created = draftShots.value[draftShots.value.findIndex((shot) => shot.id === source.id) + 1];
  if (created) {
    created.title = `新镜头（承接${source.title}）`;
    created.direction = "请填写完整画面、动作和时间顺序。";
    selectedId.value = created.id;
  }
}

function duplicateShot(shotId: string) {
  const index = draftShots.value.findIndex((shot) => shot.id === shotId);
  const source = draftShots.value[index];
  if (source) draftShots.value.splice(index + 1, 0, newDraft(source, { title: `${source.title}（副本）` }));
}

function deleteShot(shotId: string) {
  if (draftShots.value.length <= 1) return ElMessage.warning("分镜至少保留一个镜头");
  const index = draftShots.value.findIndex((shot) => shot.id === shotId);
  if (index < 0) return;
  draftShots.value.splice(index, 1);
  selectedId.value = draftShots.value[Math.min(index, draftShots.value.length - 1)]?.id ?? "";
}

function moveShot(shotId: string, offset: -1 | 1) {
  const index = draftShots.value.findIndex((shot) => shot.id === shotId);
  const target = index + offset;
  if (index < 0 || target < 0 || target >= draftShots.value.length) return;
  const [shot] = draftShots.value.splice(index, 1);
  draftShots.value.splice(target, 0, shot);
}

function dropShot(targetId: string) {
  const sourceIndex = draftShots.value.findIndex((shot) => shot.id === draggedId.value);
  const targetIndex = draftShots.value.findIndex((shot) => shot.id === targetId);
  draggedId.value = "";
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return;
  const [shot] = draftShots.value.splice(sourceIndex, 1);
  draftShots.value.splice(targetIndex, 0, shot);
}

function rebalance() {
  const count = draftShots.value.length;
  if (!count || targetDuration.value < count * 2 || targetDuration.value > count * 15) return ElMessage.warning("目标时长无法在每镜 2–15 秒范围内均衡分配");
  const base = Math.floor(targetDuration.value / count);
  const remainder = targetDuration.value % count;
  draftShots.value.forEach((shot, index) => { shot.durationSeconds = base + (index < remainder ? 1 : 0); });
}

async function save(confirmImpact = true): Promise<boolean> {
  if (!dirty.value || saving.value) return !dirty.value;
  if (!draftShots.value.length || draftShots.value.some((shot) => !shot.title.trim() || !shot.direction.trim() || shot.durationSeconds < 2 || shot.durationSeconds > 15)) {
    ElMessage.error("每个镜头必须具有标题、完整镜头描述和 2–15 秒有效时长");
    return false;
  }
  if (confirmImpact) {
    try {
      await ElMessageBox.confirm("保存会创建新的 Storyboard Revision，并使依赖的 Prompt、视频和时间线引用失效；历史成片不会被覆盖。", "保存分镜新版本", { confirmButtonText: "保存新 Revision", cancelButtonText: "继续编辑", type: "warning" });
    } catch { return false; }
  }
  saving.value = true;
  try {
    await canvasApi.saveManualStoryboard(props.projectId, revision.value, draftShots.value.map((shot, index) => serializableShot(shot, index + 1)), true);
    ElMessage.success("分镜已保存为新 Revision；下游失效状态已由服务端传播");
    emit("saved");
    return true;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
    return false;
  } finally { saving.value = false; }
}

watch(() => props.flow.revision, reset, { immediate: true });
watch(dirty, (value) => emit("dirtyChange", value ? { scope: `storyboard:${props.projectId}`, label: "导演分镜", save: () => save(false), discard: reset } : undefined), { immediate: true });
onBeforeUnmount(() => emit("dirtyChange", undefined));
</script>

<template>
  <section class="storyboard-overlay" aria-label="专业分镜编辑器">
    <header class="overlay-header"><div><span>STORYBOARD WORKSPACE</span><h2>导演分镜</h2><p>Revision {{ revision }} · {{ draftShots.length }} 镜 · {{ totalDuration }}s / {{ targetDuration }}s</p></div><button type="button" aria-label="关闭分镜编辑器" @click="$emit('close')"><Close /></button></header>
    <aside class="shot-list"><header><b>镜头列表</b><button type="button" @click="addShot"><Plus />添加</button></header><button v-for="(shot,index) in draftShots" :key="shot.id" type="button" draggable="true" :class="{ active: selected?.id === shot.id }" @dragstart="draggedId = shot.id" @dragend="draggedId = ''" @dragover.prevent @drop.prevent="dropShot(shot.id)" @click="selectedId = shot.id"><span>{{ index + 1 }}</span><div><b>{{ shot.title }}</b><small>{{ shot.direction }}</small></div><em>{{ shot.durationSeconds }}s</em></button></aside>
    <main v-if="selected" class="shot-editor">
      <div class="shot-meta"><span>SHOT {{ draftShots.indexOf(selected) + 1 }}</span><mark>{{ selected.status }}</mark></div>
      <label>镜头标题<input v-model="selected.title" aria-label="镜头标题" /></label>
      <label class="direction">完整镜头描述<textarea v-model="selected.direction" rows="9" aria-label="完整镜头描述" /></label>
      <div class="row"><label>时长（秒）<input v-model.number="selected.durationSeconds" type="number" min="2" max="15" /></label><label>场景<output>{{ selected.sceneTitle || "当前分镜场景" }}</output></label></div>
      <details><summary>更多导演参数</summary><div class="advanced"><label>儿童动作<textarea v-model="selected.childAction" rows="2" /></label><label>猫咪动作<textarea v-model="selected.catAction" rows="2" /></label><label>空间关系<textarea v-model="selected.spatialRelation" rows="2" /></label><label>景别<input v-model="selected.shotSize" /></label><label>运镜<input v-model="selected.camera" /></label><label>光线<input v-model="selected.lighting" /></label><label>声音<textarea v-model="selected.soundEffect" rows="2" /></label><label>连续性输入<textarea v-model="selected.continuityIn" rows="2" /></label></div><div class="audit-row"><span>场景审计 ID</span><code>{{ selected.sceneId }}</code></div></details>
      <div class="shot-actions"><button type="button" :disabled="draftShots.indexOf(selected) === 0" @click="moveShot(selected.id,-1)"><ArrowUp />上移</button><button type="button" :disabled="draftShots.indexOf(selected) === draftShots.length-1" @click="moveShot(selected.id,1)"><ArrowDown />下移</button><button type="button" @click="duplicateShot(selected.id)"><CopyDocument />复制</button><button type="button" @click="splitShot(selected.id)"><Scissor />拆分</button><button class="danger" type="button" @click="deleteShot(selected.id)"><Delete />删除</button></div>
    </main>
    <footer class="overlay-footer" :class="{ mismatch: delta !== 0 }"><span>总时长 {{ totalDuration }}s <template v-if="delta">· 与目标相差 {{ delta > 0 ? '+' : '' }}{{ delta }}s</template></span><button v-if="delta" type="button" @click="rebalance">重新平衡时长</button><button type="button" :disabled="!dirty" @click="reset">放弃修改</button><button class="primary" type="button" :disabled="!dirty || saving" @click="save(true)">{{ saving ? '保存中…' : '保存新 Revision' }}</button></footer>
  </section>
</template>

<style scoped>
.storyboard-overlay{position:absolute;z-index:120;inset:14px;display:grid;grid-template-columns:290px minmax(0,1fr);grid-template-rows:72px minmax(0,1fr) 68px;overflow:hidden;color:#e8eef6;background:#11171e;border:1px solid #35414f;border-radius:15px;box-shadow:0 26px 70px rgb(0 0 0 / 48%)}.overlay-header{grid-column:1/-1;padding:10px 16px;display:flex;align-items:center;border-bottom:1px solid #2d3742}.overlay-header>div{display:grid;gap:2px}.overlay-header span{color:#6e90b1;font-size:9px;font-weight:800;letter-spacing:.14em}.overlay-header h2{margin:0;font-size:18px}.overlay-header p{margin:0;color:#7f8c9d;font-size:10px}.overlay-header button{width:44px;height:44px;margin-left:auto;padding:12px;color:#afbbc9;background:#1c242e;border:1px solid #354250;border-radius:9px;cursor:pointer}.shot-list{padding:12px;display:grid;align-content:start;gap:6px;overflow:auto;background:#151b22;border-right:1px solid #2c3641}.shot-list header{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}.shot-list header button,.shot-actions button,.overlay-footer button{min-height:44px;padding:0 11px;display:inline-flex;align-items:center;gap:6px;color:#cbd6e2;background:#242e39;border:1px solid #3c4a59;border-radius:8px;cursor:pointer}.shot-list header svg,.shot-actions svg{width:15px}.shot-list>button{min-height:64px;padding:8px;display:grid;grid-template-columns:28px minmax(0,1fr) auto;gap:8px;align-items:center;color:#cbd6e2;text-align:left;background:#1a222b;border:1px solid #2f3a47;border-radius:9px;cursor:pointer}.shot-list>button.active{background:#203247;border-color:#4f7dab}.shot-list>button>span{width:28px;height:28px;display:grid;place-items:center;background:#293848;border-radius:7px}.shot-list>button div{min-width:0;display:grid;gap:3px}.shot-list small{overflow:hidden;color:#748397;text-overflow:ellipsis;white-space:nowrap}.shot-list em{color:#8fa7bd;font-style:normal}.shot-editor{padding:18px 22px;display:grid;align-content:start;gap:12px;overflow:auto}.shot-meta{display:flex;align-items:center;justify-content:space-between;color:#6e9ac5;font-size:10px;font-weight:800}.shot-meta mark{padding:4px 8px;color:#a9b8c8;background:#25313c;border-radius:999px}.shot-editor label{display:grid;gap:5px;color:#8999ab;font-size:10px}.shot-editor input,.shot-editor textarea,.shot-editor output{box-sizing:border-box;width:100%;min-height:44px;padding:10px 11px;color:#eef4fb;background:#0f151b;border:1px solid #34414e;border-radius:8px;font:inherit;line-height:1.55;resize:vertical}.shot-editor output{display:flex;align-items:center;color:#c8d3df;background:#151d25}.direction textarea{min-height:180px}.row{display:grid;grid-template-columns:130px minmax(0,1fr);gap:9px}.shot-editor summary{min-height:44px;display:flex;align-items:center;color:#aab7c6;cursor:pointer}.advanced{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.audit-row{margin-top:10px;padding:10px 12px;display:grid;gap:5px;color:#708093;background:#0f151b;border:1px solid #293540;border-radius:8px}.audit-row span{font-size:10px}.audit-row code{overflow:hidden;color:#8998a8;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.shot-actions{display:flex;flex-wrap:wrap;gap:7px}.shot-actions .danger{color:#e5a49e}.shot-actions button:disabled,.overlay-footer button:disabled{opacity:.38;cursor:not-allowed}.overlay-footer{grid-column:1/-1;padding:11px 16px;display:flex;align-items:center;justify-content:flex-end;gap:8px;color:#91cba6;background:#17251d;border-top:1px solid #304d39}.overlay-footer.mismatch{color:#dbb673;background:#2c251b;border-color:#5d4b31}.overlay-footer span{margin-right:auto}.overlay-footer .primary{color:#eef7ff;background:#2d6f9f;border-color:#4a8dc1;font-weight:700}@media(max-width:920px){.storyboard-overlay{inset:8px;grid-template-columns:1fr;grid-template-rows:72px 210px minmax(0,1fr) auto}.shot-list{border-right:0;border-bottom:1px solid #2c3641}.advanced{grid-template-columns:1fr}.overlay-footer{flex-wrap:wrap}.overlay-footer span{width:100%}}
</style>
