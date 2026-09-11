<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { api, ApiError } from '../../api/client';
import type { JobDto, SegmentRepairPreviewCommand, VideoEditPlanSuggestion } from '../../api/types';
import { pendingIdempotencyKey, settleIdempotencyKey } from '../../idempotency';
import { errorPresentation } from '../../presentation';
import JobStatusCard from '../JobStatusCard.vue';

const props = defineProps<{ projectId: string; command: SegmentRepairPreviewCommand | null; jobId?: string; blockedReason?: string; disabled?: boolean }>();
const emit = defineEmits<{ adopt: [VideoEditPlanSuggestion, string]; job: [string] }>();
const activeId = ref(props.jobId ?? '');
const job = ref<JobDto | null>(null);
const advice = ref<VideoEditPlanSuggestion | null>(null);
const busy = ref(false);
const error = ref('');
const pendingCommand = ref<(SegmentRepairPreviewCommand & { idempotencyKey: string }) | null>(null);
let resultId = '';
const terminal = computed(() => !activeId.value || !!job.value && job.value.id === activeId.value && ['succeeded', 'failed', 'cancelled'].includes(job.value.status));
const frozen = computed(() => (job.value?.frozenInput?.command ?? job.value?.frozenInput) as Record<string, unknown> | undefined);
// Only source facts make advice inapplicable. A later text edit remains the user's to adopt over.
const sourceFields = ['baseVideoAssetId', 'baseEditVersionId', 'editDraftId', 'sourceResultJobId', 'expectedSourceTimelineHash', 'issueRange'] as const;
const textFields = ['instruction', 'preserveContent', 'startState', 'actionProcess', 'desiredEndState', 'avoidProblems'] as const;
const sourceChanged = computed(() => !props.command || !frozen.value || sourceFields.some(key => JSON.stringify((props.command as Record<string, unknown>)[key] ?? null) !== JSON.stringify(frozen.value?.[key] ?? null)));
const textChanged = computed(() => frozen.value && textFields.some(key => ((props.command as Record<string, unknown> | null)?.[key] ?? '') !== (frozen.value?.[key] ?? '')));
const labels: Record<string, string> = { instruction: '修改目标', preserveContent: '保留内容', startState: '起始状态', actionProcess: '动作过程', desiredEndState: '期望结束状态', avoidProblems: '需要避免的问题' };
const modes: Record<string, string> = { edit_existing: '修改现有片段', from_frame: '从正确起点重新生成' };
const endings: Record<string, string> = { follow_instruction: '按修改描述结束', match_original: '保留原结束状态', replace: '使用新的结束状态' };
watch(() => props.jobId, id => {
  if ((id ?? '') === activeId.value) return;
  activeId.value = id ?? ''; job.value = null; advice.value = null; resultId = ''; error.value = '';
});
async function create() {
  if (busy.value || props.disabled || props.blockedReason || !props.command || !terminal.value) return;
  const projectId = props.projectId;
  const scope = `video-edit-plan:${projectId}:${props.command.editDraftId}`;
  const command = pendingCommand.value ?? { ...props.command, idempotencyKey: pendingIdempotencyKey(scope, JSON.stringify(props.command)) };
  const { idempotencyKey: _key, ...originalCommand } = command;
  const fingerprint = JSON.stringify(originalCommand);
  busy.value = true; error.value = '';
  try {
    const created = await api.planVideoEdit(projectId, command);
    settleIdempotencyKey(scope, fingerprint); pendingCommand.value = null;
    if (projectId !== props.projectId) return;
    activeId.value = created.id; job.value = created; advice.value = null; resultId = '';
    emit('job', created.id); await update(created);
  } catch (reason) {
    const definite = reason instanceof ApiError && [400, 401, 403, 404, 409, 422, 503].includes(reason.status);
    if (!definite) pendingCommand.value = command;
    error.value = errorPresentation(reason, '方案暂时未能整理').message + '。仍可直接填写和生成。';
  } finally { busy.value = false; }
}
async function update(received: JobDto) {
  if (received.id !== activeId.value) return;
  job.value = received;
  if (received.status !== 'succeeded' || resultId === received.id) return;
  const id = received.id;
  try {
    const result = await api.jobResult(id);
    if (id !== activeId.value) return;
    const suggestion = result.result?.editPlan ?? received.providerResult?.editPlan;
    if (suggestion && typeof suggestion === 'object' && 'instruction' in suggestion) {
      advice.value = suggestion as VideoEditPlanSuggestion; resultId = id;
    } else error.value = '任务尚未返回可编辑的修改方案；仍可直接填写。';
  } catch (reason) { if (id === activeId.value) error.value = errorPresentation(reason, '方案暂时无法读取；仍可直接填写').message; }
}
function replacement(received: JobDto) { activeId.value = received.id; advice.value = null; resultId = ''; emit('job', received.id); void update(received); }
</script>

<template>
  <section class="edit-planner" aria-label="可选 AI 修改方案">
    <div class="planning-action">
      <button type="button" class="secondary" data-testid="plan-edit" :disabled="busy || disabled || !!blockedReason || !command || !terminal" @click="create">{{ pendingCommand ? '重新确认方案任务' : 'AI 整理修改方案（可选，付费）' }}</button>
      <small>仅在点击时调用规划模型，费用按实际用量计算。建议会先展示，采用后仍可编辑。</small>
    </div>
    <p v-if="activeId && !job" role="status">正在核实已保存的方案任务。确认原任务结束前暂不能新建付费方案；仍可直接编辑，下方保留原任务的恢复入口。</p>
    <p v-if="blockedReason">{{ blockedReason }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <JobStatusCard v-if="activeId" :job-id="activeId" title="AI 修改方案" result-summary="修改建议已保存，等待人工采用。" @changed="update" @replacement="replacement" />
    <section v-if="advice" class="suggestion" aria-label="AI 修改建议">
      <h4>AI 修改建议</h4>
      <dl><template v-for="(label, key) in labels" :key="key"><dt>{{ label }}</dt><dd>{{ advice[key as keyof VideoEditPlanSuggestion] || '未补充' }}</dd></template></dl>
      <p v-if="advice.recommendedGenerationMode">生成方式建议：{{ modes[advice.recommendedGenerationMode] }}</p>
      <p v-if="advice.recommendedEndStatePolicy">结束策略建议：{{ endings[advice.recommendedEndStatePolicy] }}</p>
      <p v-if="advice.recommendedReferenceRoles?.length">参考选择建议：{{ advice.recommendedReferenceRoles.join('、') }}</p>
      <ul v-if="advice.notes?.length"><li v-for="note in advice.notes" :key="note">{{ note }}</li></ul>
      <p v-if="sourceChanged" role="status">来源或选区已经变化，这份建议属于之前的输入。可阅读参考，需重新整理当前选区的方案。</p>
      <p v-else-if="textChanged" role="status">整理后文字已经修改。点击填入会替换当前表单中的文字，请先比较。</p>
      <button type="button" class="secondary" data-testid="adopt-plan" :disabled="disabled || sourceChanged" @click="emit('adopt', advice, activeId)">将建议文字填入表单</button>
      <small>生成方式、选区和参考选择由你调整；填入后可继续修改文字。</small>
    </section>
  </section>
</template>

<style scoped>
.edit-planner { border:1px solid var(--line); border-radius:10px; padding:16px; }
.planning-action { display:flex; flex-wrap:wrap; gap:12px; align-items:center; }
small { color:var(--muted); line-height:1.6; }p,li { line-height:1.6; }
.suggestion { margin-top:16px; padding:16px; background:#f5f8f5; border-radius:8px; }
dl { display:grid; grid-template-columns:120px minmax(0,1fr); gap:10px; }dt { font-weight:600; }dd { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; }
.suggestion>small { display:block; margin-top:8px; }
@media(max-width:600px) { dl { grid-template-columns:1fr; } }
</style>
