<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '../../api/client';
import type { AssetDto, WorkspaceDto, VideoEditDraftDto } from '../../api/types';
import type { ProductionPlan, ProductionPlanDraft, ProductionDocument, UnitSelection } from '../../api/productionTypes';
import type { PaidModelRuntime } from '../../presentation';
import { pendingIdempotencyKey, settleIdempotencyKey } from '../../idempotency';
import ProductionAssetsEditor from './ProductionAssetsEditor.vue';
import ProductionUnitCard from './ProductionUnitCard.vue';
const props = defineProps<{ projectId: string; workspace: WorkspaceDto; runtime?: PaidModelRuntime | null }>();
const emit = defineEmits<{ changed: [] }>();
const router = useRouter();
const plans = ref<ProductionPlan[]>([]), assets = ref<AssetDto[]>([]), error = ref(''), busy = ref(false);
const draft = ref<ProductionPlanDraft>({ shotPlanVersionId: '', scenes: [], props: [], units: [], idempotencyKey: 'pending-plan' });
const preview = ref<{ document: ProductionDocument; inputHash: string; unitCount: number }>();
const active = computed(() => plans.value.find(p => p.active));
const base = computed(() => `/api/v1/projects/${props.projectId}`);
const localKey = computed(() => `catflow:production-draft:${props.projectId}`);
const selections = ref<Record<string, UnitSelection>>({});
const unitKey = ref(0);
watch(draft, value => { sessionStorage.setItem(localKey.value, JSON.stringify(value)); preview.value = undefined; }, { deep: true });
async function load() {
  try {
    [plans.value, assets.value] = await Promise.all([api.request<ProductionPlan[]>(`${base.value}/production-plans`), api.assets(props.projectId)]);
    if (active.value) {
      const items = await Promise.all(active.value.document.units.map(u => api.request<UnitSelection[]>(`${base.value}/production-units/${u.id}/selections`)));
      selections.value = Object.fromEntries(items.flat().filter(s => s.active).map(s => [s.unitId, s]));
    }
  } catch (reason) { error.value = String(reason); }
}
onMounted(async () => {
  await load();
  const saved = sessionStorage.getItem(localKey.value);
  if (saved) { try { draft.value = JSON.parse(saved); } catch { error.value = '本地草稿无法读取，请重新编排。'; } }
  else if (active.value) draft.value = { scenes: JSON.parse(JSON.stringify(active.value.document.scenes)), props: JSON.parse(JSON.stringify(active.value.document.props)), units: JSON.parse(JSON.stringify(active.value.document.units)), shotPlanVersionId: props.workspace.activeShotPlan?.id ?? '', expectedActivePlanId: active.value.id, idempotencyKey: 'pending-plan' } as ProductionPlanDraft;
});
async function checkPlan() {
  busy.value = true; error.value = '';
  try {
    draft.value.shotPlanVersionId = props.workspace.activeShotPlan?.id ?? '';
    draft.value.expectedActivePlanId = active.value?.id ?? null;
    preview.value = await api.json(`${base.value}/production-plans/preview`, 'POST', draft.value);
  } catch (reason) { error.value = String(reason); } finally { busy.value = false; }
}
function useGrouping() {
  if (!preview.value) return;
  draft.value.scenes = JSON.parse(JSON.stringify(preview.value.document.scenes));
  draft.value.units = JSON.parse(JSON.stringify(preview.value.document.units));
}
function split(index: number, shotIndex: number) {
  const units = draft.value.units ?? [], unit = units[index];
  if (!unit || shotIndex <= 0) return;
  const ids = unit.shotIds.splice(shotIndex);
  units.splice(index+1, 0, { id: `unit-${crypto.randomUUID()}`, shotIds: ids, continuity: 'inherit', reason: '承接上一单元实际状态', generationMode: 'references' });
}
function merge(index: number) {
  const units = draft.value.units ?? [];
  if (!units[index+1]) return;
  units[index].shotIds.push(...units[index+1].shotIds); units.splice(index+1, 1);
}
async function save() {
  if (!preview.value) return;
  busy.value = true; error.value = '';
  const hash = preview.value.inputHash, scope = `production-plan:${props.projectId}`;
  try {
    const created = await api.json<ProductionPlan>(`${base.value}/production-plans`, 'POST', { ...draft.value, idempotencyKey: pendingIdempotencyKey(scope, hash) });
    await api.json(`${base.value}/production-plans/${created.id}/activate`, 'POST', { expectedActivePlanId: draft.value.expectedActivePlanId ?? null });
    settleIdempotencyKey(scope, hash); sessionStorage.removeItem(localKey.value);
    await load(); unitKey.value++; emit('changed');
  } catch (reason) { error.value = String(reason); } finally { busy.value = false; }
}
async function assemble() {
  if (!active.value) return;
  busy.value = true; error.value = '';
  const command = { expectedSelectionHashes: Object.fromEntries(Object.entries(selections.value).map(([id,s]) => [id,s.inputHash])) };
  const hash = JSON.stringify(command), scope = `production-assemble:${active.value.id}`;
  try {
    const result = await api.json<VideoEditDraftDto>(`${base.value}/production-plans/${active.value.id}/assemble`, 'POST', { ...command, idempotencyKey: pendingIdempotencyKey(scope,hash) });
    settleIdempotencyKey(scope,hash);
    await router.push({ path: `/projects/${props.projectId}/delivery`, query: { draftId: result.id } });
  } catch (reason) { error.value = String(reason); } finally { busy.value = false; }
}
async function selected() { await load(); unitKey.value++; }
</script>
<template>
  <section class="card production-workspace">
    <h2>生产计划与选片</h2><p>当前故事 {{ workspace.activeStory?.revision ?? '未采用' }} · 分镜 {{ workspace.activeShotPlan?.revision ?? '未采用' }} · 生产计划 {{ active?.revision ?? '未建立' }}。编排和选片不调用付费模型。</p>
    <p v-if="error" class="notice error">{{ error }}</p>
    <details :open="!active"><summary>编排新版本</summary>
      <ProductionAssetsEditor :project-id="projectId" v-model:scenes="draft.scenes!" v-model:props="draft.props!" :assets="assets.filter(a => a.mediaType === 'image')" @refresh="load" />
      <div v-for="(unit,index) in draft.units" :key="unit.id" class="unit-config">
        <b>单元 {{ index+1 }}</b><p>{{ unit.shotIds.join(' → ') }}</p>
        <label>承接方式<select v-model="unit.continuity"><option value="inherit" :disabled="index === 0">承接实际状态</option><option value="reset">独立建立起点</option></select></label><label>理由<input v-model="unit.reason" /></label>
        <label>生成依据<select v-model="unit.generationMode"><option value="references">普通参考图</option><option value="from_frame">已确认严格首帧（单镜）</option></select></label>
        <button v-for="(shotId,si) in unit.shotIds.slice(1)" :key="shotId" @click="split(index,si+1)">在 {{ shotId }} 前拆分</button><button v-if="index < (draft.units?.length ?? 0)-1" @click="merge(index)">与下一单元合并</button>
      </div>
      <button @click="draft.units = []">重新自动分组</button><button :disabled="busy || !workspace.activeShotPlan" @click="checkPlan">检查计划（免费）</button>
      <div v-if="preview"><p>作品 {{ preview.document.targetDurationFrames / 24 }} 秒，共 {{ preview.unitCount }} 个视频任务；图片与规划任务另计，费用待核价。</p><p v-for="u in preview.document.units" :key="u.id">{{ u.id }}：{{ u.shotIds.join('、') }}</p><button @click="useGrouping">编辑这份分组</button><button :disabled="busy" @click="save">保存并使用计划（不生成）</button></div>
    </details>
    <template v-if="active">
      <ProductionUnitCard v-for="unit in active.document.units" :key="`${active.id}:${unit.id}:${unitKey}`" :project-id="projectId" :plan="active" :unit="unit" :runtime="runtime" @selected="selected" />
      <button :disabled="busy || active.document.units.some(u => !selections[u.id])" @click="assemble">将当前选片组装为剪辑草稿（免费）</button>
    </template>
    <details><summary>历史计划</summary><p v-for="plan in plans" :key="plan.id">版本 {{ plan.revision }} · {{ plan.active ? '使用中' : '历史保留' }} · {{ plan.createdAt }} · {{ plan.document.units.length }} 单元</p></details>
  </section>
</template>
<style scoped>.production-workspace{padding:1.2rem}summary{cursor:pointer;margin:1rem 0}button{margin:.4rem}.unit-config{border:1px solid #ddd;padding:1rem;border-radius:10px}label{display:block;margin:.5rem 0}</style>
