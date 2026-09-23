<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue';
import { api } from '../../api/client';
import type { AssetDto, JobDto } from '../../api/types';
import type { ProductionPlan, ProductionUnit, UnitPreview, UnitSelection } from '../../api/productionTypes';
import { pendingIdempotencyKey, settleIdempotencyKey } from '../../idempotency';
import { paidModelBlockedReason, type PaidModelRuntime } from '../../presentation';
import { subscribeJobs } from '../../jobUpdates';
import JobStatusCard from '../JobStatusCard.vue';
import UnitSelectionEditor from './UnitSelectionEditor.vue';
const props = defineProps<{ projectId: string; plan: ProductionPlan; unit: ProductionUnit; runtime?: PaidModelRuntime | null }>();
const emit = defineEmits<{ selected: [] }>();
const base = computed(() => `/api/v1/projects/${props.projectId}/production-units/${props.unit.id}`);
const preview = ref<UnitPreview>(), assets = ref<AssetDto[]>([]), jobs = ref<JobDto[]>([]), selections = ref<UnitSelection[]>([]);
const busy = ref(false), error = ref(''), showSelection = ref(false);
const block = computed(() => paidModelBlockedReason(props.runtime));
const running = computed(() => jobs.value.some(j => !['succeeded','failed','cancelled'].includes(j.status)));
const current = computed(() => selections.value.find(s => s.active));
const candidates = computed(() => assets.value.filter(a => a.mediaType === 'video' && a.metadata.productionUnitId === props.unit.id));
const shots = computed(() => props.plan.document.shots.filter(s => props.unit.shotIds.includes(s.id)));
let stop: (() => void) | undefined, disposed = false;
async function load() {
  try {
    const [a,j,s] = await Promise.all([api.assets(props.projectId), api.request<JobDto[]>(`${base.value}/jobs`), api.request<UnitSelection[]>(`${base.value}/selections`)]);
    if (disposed) return; assets.value=a; jobs.value=j; selections.value=s;
  } catch (reason) { error.value=String(reason); }
}
onMounted(() => { void load(); stop=subscribeJobs(() => ({projectId:props.projectId}),load,()=>running.value); });
onBeforeUnmount(() => { disposed=true; stop?.(); });
async function prepare() {
  busy.value=true; error.value=''; preview.value=undefined;
  try { preview.value=await api.json(`${base.value}/preview`,'POST',{planId:props.plan.id}); }
  catch (reason) { error.value=String(reason); } finally { busy.value=false; }
}
async function generate() {
  if (!preview.value || running.value || block.value) return;
  busy.value=true; error.value=''; const hash=preview.value.inputHash, scope=`production-unit:${props.projectId}:${props.unit.id}`;
  try {
    await api.json<JobDto>(`${base.value}/generations`,'POST',{planId:props.plan.id, expectedInputHash:hash, idempotencyKey:pendingIdempotencyKey(scope,hash)});
    settleIdempotencyKey(scope,hash); await load();
  } catch (reason) { error.value=String(reason)+'；请先读取任务记录确认提交状态。'; await load(); }
  finally { busy.value=false; }
}
</script>
<template>
  <article class="unit-card">
    <h3>{{ unit.id }} · {{ shots.reduce((sum,s) => sum+(s.durationFrames ?? s.durationSeconds*24),0)/24 }} 秒取用</h3>
    <p>{{ unit.continuity === 'inherit' ? '等待上游实际状态确认后生成' : '独立建立起点' }} · {{ unit.reason }}</p>
    <p>{{ shots.map(s => `${s.order}. ${s.information?.newInformation ?? s.environmentChange}`).join(' → ') }}</p>
    <p v-if="current">当前选片版本 {{ current.revision }} · {{ current.document.disposition === 'accepted' ? '内容通过' : current.document.disposition === 'accepted_with_issues' ? '带问题采用' : '拒绝' }}。是否仍适用以最新预览校验为准。</p>
    <p v-if="error" class="notice error">{{ error }}</p>
    <button :disabled="busy" @click="prepare">查看真实生成输入（免费）</button><button @click="load">刷新任务和候选</button>
    <div v-if="preview">
      <p>单次生成 {{ preview.durationSeconds }} 秒；采用 {{ preview.targetDurationFrames }} 帧。费用待核价。</p>
      <div class="references"><figure v-for="r in preview.references" :key="r.assetId"><img :src="`/api/v1/assets/${r.assetId}/content`" :alt="r.role" /><figcaption>直接提交 · {{ r.duties.join('、') }}</figcaption></figure></div>
      <details v-if="unit.generationMode === 'from_frame'"><summary>用于首帧的上游依据</summary><p v-for="r in preview.upstreamReferences" :key="r.assetId">{{ r.role }} · {{ r.assetId }} · {{ r.sha256 }}</p></details>
      <details><summary>提示词与时间安排</summary><pre>{{ preview.compiledProviderPrompt }}</pre><p v-for="event in preview.keyEvents" :key="event.shotId+event.id">{{ (event.unitStartFrame/24).toFixed(2) }}–{{ (event.unitEndFrame/24).toFixed(2) }} 秒：{{ event.description }}</p></details>
      <p v-for="warning in preview.warnings" :key="warning.code">{{ warning.message }}</p><p v-if="block">{{ block }}</p>
      <button :disabled="busy || running || Boolean(block)" @click="generate">提交一个付费候选</button>
    </div>
    <JobStatusCard v-for="job in jobs" :key="job.id" :job-id="job.id" title="单元任务" />
    <button v-if="candidates.length" @click="showSelection=!showSelection">{{ showSelection ? '收起选片' : `检查与采用候选（${candidates.length}）` }}</button>
    <UnitSelectionEditor v-if="showSelection" :project-id="projectId" :plan="plan" :unit="unit" :preview="preview" :candidates="candidates" :assets="assets" :selection="current" @refresh="load" @saved="emit('selected')" />
  </article>
</template>
<style scoped>.unit-card{border-top:1px solid #ddd;margin:1rem 0;padding:1rem 0}.references{display:flex;gap:1rem;flex-wrap:wrap}figure{margin:0;width:120px}img{width:100%;height:110px;object-fit:contain}pre{white-space:pre-wrap;max-height:28rem;overflow:auto}button{margin:.4rem}</style>
