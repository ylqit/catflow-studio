<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, ApiError } from "../api/client";
import { pendingIdempotencyKey, readPendingIdempotency, settleIdempotencyKey, type PendingIdempotency } from '../idempotency';
import type { JobDto, JobResultDto, GenerationPreparationDto, ProviderTaskLookupDto } from "../api/types";
import { subscribeJobs } from "../jobUpdates";
import ProviderPrompt from "./ProviderPrompt.vue";
import { jobExecutionPresentation } from "../presentation";

const props = defineProps<{ jobId: string; title?: string; resultSummary?: string; preparationBlockedReason?: string }>();
const emit = defineEmits<{ changed: [job: JobDto]; replacement: [job: JobDto] }>();
const job = ref<JobDto | null>(null);
const result = ref<JobResultDto | null>(null);
let loadedResultKey = "";
let pendingResultKey = "";
let resultRequest = 0;
const expanded = ref(false);
const busy = ref(false);
const error = ref("");
const cloudTasks = ref<ProviderTaskLookupDto | null>(null);
const selectedCloudTask = ref('');
const associationAcknowledged = ref(false);
const associationRevision = ref<number | null>(null);
const associationKey = ref('');
const lookupBusy = ref(false);
let lookupRequest = 0;
const selectedCloudCandidate = computed(() => cloudTasks.value?.candidates?.find(item => item.providerTaskId === selectedCloudTask.value));
watch(selectedCloudTask, () => { associationAcknowledged.value = false; associationKey.value = crypto.randomUUID(); });
const preparation = ref<GenerationPreparationDto | null>(null);
const acknowledged = ref(false);
const replacementPhase = ref<'idle' | 'preparing' | 'submitting' | 'reconciling' | 'uncertain'>('idle');
const replacementError = ref('');
const pendingReplacement = ref<PendingIdempotency | null>(null);
const blockingJob = ref<{ jobId: string; draftId?: string; repairId?: string } | null>(null);
let contextRevision = 0;
let checkedReplacement = '';
let emittedReplacement = '';
const blockingUrl = computed(() => job.value?.projectId && blockingJob.value?.draftId
  ? `/projects/${job.value.projectId}/delivery?draftId=${encodeURIComponent(blockingJob.value.draftId)}${blockingJob.value.repairId ? `&candidateId=${encodeURIComponent(blockingJob.value.repairId)}` : ''}&view=result` : null);
const replacementDialog = ref<HTMLDialogElement | null>(null);
watch(preparation, value => {
  const dialog = replacementDialog.value;
  if (!dialog) return;
  if (value) {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  } else {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }
}, { flush: "post" });
let unsubscribe: (() => void) | undefined;
const execution = computed(() => job.value?.execution);
const diagnostics = computed(() => job.value?.error?.diagnostics ?? job.value?.providerResult?.submissionDiagnostics as Record<string, unknown> | undefined);
const timeoutNames: Record<string, string> = { connect: '连接超时', write: '发送请求超时', read: '读取响应超时', pool: '等待连接池超时', unknown: '超时类别未能确定' };
const actions = computed(() => execution.value?.availableActions ?? []);
const preparedInput = computed(() => preparation.value?.input ?? {});
const preparedReferences = computed(() => {
  const input = preparedInput.value;
  if (Array.isArray(input.imageReferences)) return input.imageReferences.map((reference: Record<string, unknown>, index: number) => ({
    id: typeof reference.assetId === 'string' ? reference.assetId : null,
    role: typeof reference.role === 'string' ? reference.role : `参考 ${index + 1}`,
    frame: typeof reference.frameNumber === 'number' ? reference.frameNumber : null,
  }));
  if (Array.isArray(input.referenceAssetIds)) return input.referenceAssetIds.map((id, index) => ({
    id: String(id), role: Array.isArray(input.referenceRoles) && input.referenceRoles.length === (input.referenceAssetIds as unknown[]).length ? String(input.referenceRoles[index]) : `参考 ${index + 1}（历史职责未能对应）`, frame: null,
  }));
  return [];
});
const preparedVideo = computed(() => preparedInput.value.videoReference as { assetId?: string } | undefined);
const preparedRanges = computed(() => ['issueRange', 'generationRange', 'candidateCoreRange'].flatMap((key, index) => {
  const value = preparedInput.value[key] as { startFrame?: number; endFrame?: number } | undefined;
  return typeof value?.startFrame === 'number' && typeof value.endFrame === 'number'
    ? [{ name: ['草稿替换区间', '来源参考区间', '候选取用区间'][index], ...value }] : [];
}));
const referenceNames: Record<string, string> = { anchor_in: '入点衔接参考', anchor_out: '出点衔接参考', first_frame: '严格首帧', last_frame: '严格尾帧', episode_child: "儿童", episode_cat: "猫咪", pair_scale: "人猫比例", environment: "当前环境", style_board: "画风" };
const stateText = computed(() => {
  const current = job.value, detail = execution.value;
  if (!current) return error.value ? "暂时无法读取本地任务记录，等待重新连接。" : "正在读取已保存的任务…";
  if (current.status === "submission_unknown" || detail?.queryError) return jobExecutionPresentation(current).description;
  if (detail?.historicalResult && current.status === "succeeded") return "历史结果已保留，当前制作内容未被替换。";
  if (current.status === "succeeded") {
    if (props.resultSummary) return props.resultSummary;
    const validation = current.providerResult?.validation as { disposition?: string } | undefined;
    if (validation?.disposition === "needs_input" || validation?.disposition === "invalid") return "结果已返回，请查看结构校验与待完善项。";
    if (validation?.disposition === "candidate_ready") return "分镜结构校验通过，结果已保存；采用状态请查看分镜版本。";
    return "结果已保存，内容质量仍需检查。";
  }
  return jobExecutionPresentation(current).description;
});
const time = (value?: string | null) => value ? new Date(value).toLocaleString() : "尚未取得";

async function loadDetails(force = false) {
  if (!expanded.value || !job.value) return;
  const id = props.jobId;
  const current = job.value;
  const key = JSON.stringify([id, current.execution?.resultState, current.execution?.providerStatus,
    ['succeeded', 'failed', 'cancelled'].includes(current.status) ? current.status : 'active',
    current.error?.code, current.execution?.queryError?.code]);
  if (!force && (loadedResultKey === key || pendingResultKey === key)) return;
  const request = ++resultRequest;
  pendingResultKey = key;
  try {
    const body = await api.jobResult(id);
    if (id === props.jobId && request === resultRequest) { result.value = body; loadedResultKey = key; }
  } finally { if (request === resultRequest) pendingResultKey = ""; }
}
async function reload() {
  const id = props.jobId;
  let current: JobDto;
  try { current = await api.job(id); }
  catch (reason) { if (id === props.jobId) error.value = String(reason); return; }
  if (id !== props.jobId) return;
  if (job.value && (current.revision ?? 0) < (job.value.revision ?? 0)) return;
  const changed = !job.value || current.revision !== job.value.revision || current.status !== job.value.status;
  job.value = current; error.value = "";
  if (changed) {
    emit("changed", current);
    try { await loadDetails(); } catch (reason) { if (id === props.jobId) error.value = String(reason); }
  }
  if (id !== props.jobId) return;
  const pending = readPendingIdempotency(`replacement:${id}`);
  pendingReplacement.value = pending;
  const signature = JSON.stringify([id, pending?.key, current.successorJobIds]);
  if (pending && !busy.value && checkedReplacement !== signature) {
    checkedReplacement = signature;
    await reconcileReplacement();
  }
}
watch(() => props.jobId, () => {
  contextRevision += 1; busy.value = false; replacementPhase.value = 'idle'; replacementError.value = ''; blockingJob.value = null;
  pendingReplacement.value = readPendingIdempotency(`replacement:${props.jobId}`); checkedReplacement = ''; emittedReplacement = '';
  unsubscribe?.(); job.value = null; result.value = null; preparation.value = null;
  lookupRequest += 1; cloudTasks.value = null; selectedCloudTask.value = ''; lookupBusy.value = false; associationRevision.value = null;
  resultRequest += 1; loadedResultKey = ""; pendingResultKey = "";
  unsubscribe = subscribeJobs(() => ({ jobId: props.jobId }), reload, () => execution.value?.waitingForProvider ?? true);
}, { immediate: true });
onBeforeUnmount(() => { contextRevision += 1; unsubscribe?.(); });

async function toggle(event: Event) {
  expanded.value = (event.target as HTMLDetailsElement).open;
  if (expanded.value) { try { await loadDetails(); } catch (reason) { error.value = String(reason); } }
}
async function recover(action: "query_provider" | "process_result") {
  if (!job.value || busy.value) return;
  const id = job.value.id, revision = job.value.revision ?? 0;
  busy.value = true; error.value = "";
  try {
    const received = await api.recoverJob(id, { action, expectedRevision: revision, idempotencyKey: crypto.randomUUID(), confirmAssociation: false, acknowledgeUnverifiedParameters: false });
    if (props.jobId !== id) return;
    job.value = received; emit("changed", received); await loadDetails(true);
  }
  catch (reason) { if (props.jobId === id) { await reload(); error.value = String(reason); } }
  finally { busy.value = false; }
}
async function lookupProviderTasks() {
  if (!job.value || lookupBusy.value || busy.value) return;
  const id = job.value.id, request = ++lookupRequest, revision = job.value.revision ?? 0;
  lookupBusy.value = true; error.value = ''; cloudTasks.value = null; selectedCloudTask.value = ''; associationAcknowledged.value = false;
  try {
    const found = await api.providerTasks(id);
    if (props.jobId === id && request === lookupRequest) { cloudTasks.value = found; associationRevision.value = revision; }
  } catch (reason) { if (props.jobId === id && request === lookupRequest) error.value = `云端核对失败：${String(reason)}。原任务仍保留为提交结果待核实。`; }
  finally { if (request === lookupRequest) lookupBusy.value = false; }
}
async function associateProviderTask() {
  if (!job.value || busy.value || !selectedCloudCandidate.value?.canAssociate || !associationAcknowledged.value || associationRevision.value !== (job.value.revision ?? 0)) return;
  const id = job.value.id;
  busy.value = true; error.value = '';
  try {
    const received = await api.recoverJob(id, { action: 'associate_provider_task', providerTaskId: selectedCloudTask.value, confirmAssociation: true, acknowledgeUnverifiedParameters: true, expectedRevision: associationRevision.value, idempotencyKey: associationKey.value });
    if (props.jobId !== id) return;
    job.value = received; cloudTasks.value = null; selectedCloudTask.value = ''; emit('changed', received); await loadDetails(true);
  } catch (reason) { if (props.jobId === id) { await reload(); error.value = String(reason); } }
  finally { busy.value = false; }
}
async function prepareReplacement() {
  if (busy.value || props.preparationBlockedReason) return;
  if (readPendingIdempotency(`replacement:${props.jobId}`)) { await reconcileReplacement(); return; }
  const id = props.jobId, context = contextRevision;
  busy.value = true; error.value = ""; replacementError.value = ''; blockingJob.value = null; acknowledged.value = false; replacementPhase.value = 'preparing';
  try {
    const received = await api.prepareJobReplacement(id);
    if (context === contextRevision && props.jobId === id) preparation.value = received;
  }
  catch (reason) { if (context === contextRevision && props.jobId === id) showReplacementError(reason); }
  finally { if (context === contextRevision) { busy.value = false; replacementPhase.value = 'idle'; } }
}
function showReplacementError(reason: unknown) {
  const detail = reason instanceof ApiError && reason.detail && typeof reason.detail === 'object' ? reason.detail as Record<string, unknown> : null;
  replacementError.value = typeof detail?.message === 'string' ? detail.message : String(reason);
  blockingJob.value = detail?.code === 'video_edit_in_progress' && typeof detail.blockingJobId === 'string'
    ? { jobId: detail.blockingJobId, draftId: typeof detail.editDraftId === 'string' ? detail.editDraftId : undefined, repairId: typeof detail.videoRepairId === 'string' ? detail.videoRepairId : undefined } : null;
}
function acceptReplacement(id: string, fingerprint: string, created: JobDto) {
  settleIdempotencyKey(`replacement:${id}`, fingerprint);
  pendingReplacement.value = null; preparation.value = null; replacementError.value = ''; replacementPhase.value = 'idle';
  if (emittedReplacement !== created.id) { emittedReplacement = created.id; emit('replacement', created); }
}
async function reconcileReplacement() {
  const id = props.jobId, context = contextRevision;
  const pending = readPendingIdempotency(`replacement:${id}`);
  if (!pending || replacementPhase.value === 'reconciling') return;
  pendingReplacement.value = pending; busy.value = true; replacementPhase.value = 'reconciling'; replacementError.value = '';
  try {
    const original = await api.job(id);
    const successors = await Promise.all((original.successorJobIds ?? []).map(successorId => api.job(successorId)));
    if (context !== contextRevision || props.jobId !== id) return;
    job.value = original;
    const created = successors.find(item => item.idempotencyKey === pending.key && item.frozenInput?.executionInputHash === pending.fingerprint);
    if (created) { acceptReplacement(id, pending.fingerprint, created); return; }
    replacementError.value = successors.length ? '已发现后续任务，但未确认与本次提交编号一致。请查看后续任务记录，不要重复提交。' : '暂未找到本次提交的本地任务记录。请求结果尚未确定，请稍后重新核对；系统不会自动再次提交。';
    replacementPhase.value = 'uncertain';
  } catch (reason) {
    if (context === contextRevision) { replacementError.value = `暂时无法核对提交记录：${String(reason)}。请重新核对，不要重复提交。`; replacementPhase.value = 'uncertain'; }
  } finally { if (context === contextRevision) busy.value = false; }
}
async function submitReplacement() {
  if (!preparation.value || !acknowledged.value || busy.value || pendingReplacement.value || props.preparationBlockedReason) return;
  const id = props.jobId, context = contextRevision, fingerprint = preparation.value.executionInputHash;
  const key = pendingIdempotencyKey(`replacement:${id}`, fingerprint);
  pendingReplacement.value = { fingerprint, key };
  busy.value = true; error.value = ""; replacementError.value = ''; blockingJob.value = null; replacementPhase.value = 'submitting';
  let created: JobDto;
  try {
    created = await api.replaceUnknownJob(id, fingerprint, key);
  } catch (reason) {
    const definite = reason instanceof ApiError && [400, 401, 403, 404, 409, 422, 503].includes(reason.status);
    if (definite) settleIdempotencyKey(`replacement:${id}`, fingerprint);
    if (context !== contextRevision || props.jobId !== id) return;
    if (definite) {
      pendingReplacement.value = null; replacementPhase.value = 'idle'; showReplacementError(reason);
    } else { replacementPhase.value = 'uncertain'; await reconcileReplacement(); }
    return;
  } finally { if (context === contextRevision) busy.value = false; }
  // A returned Job is an accepted submission even if the following read fails.
  if (context !== contextRevision || props.jobId !== id) return;
  acceptReplacement(id, fingerprint, created);
  if (context === contextRevision && props.jobId === id) await reload();
}
</script>

<template>
  <section class="task-card" data-testid="job-status-card" :data-job-id="jobId" :aria-busy="busy">
    <header><b>{{ title ?? '任务进度与恢复' }}</b><span>{{ stateText }}</span></header>
    <small>最后确认：{{ time(execution?.providerObservedAt) }} · 本地更新：{{ time(job?.updatedAt) }}</small>
    <p v-if="execution?.usageUnconfirmed" class="attention">用量未取得，费用待核实。</p>
    <p v-else-if="job?.actualUsage">实际用量：{{ JSON.stringify(job.actualUsage) }} · {{ job.actualCostMicros == null ? '待核价' : `¥${(job.actualCostMicros / 1000000).toFixed(4)}` }}</p>
    <p v-if="job?.error">{{ job.error.message }}</p>
    <div class="task-actions">
      <button v-if="actions.includes('lookup_provider_tasks')" class="secondary" :disabled="busy || lookupBusy" @click="lookupProviderTasks">{{ lookupBusy ? '正在只读核对云端任务' : '只读核对云端任务（不生成）' }}</button>
      <button v-if="actions.includes('query_provider')" class="secondary" :disabled="busy" @click="recover('query_provider')">重新核实外部进度</button>
      <button v-if="actions.includes('process_result')" class="secondary" :disabled="busy" @click="recover('process_result')">恢复本地结果处理</button>
      <button v-if="actions.includes('prepare_replacement')" class="secondary" :disabled="busy || Boolean(preparationBlockedReason)" @click="prepareReplacement">保留此记录，重新准备一次生成</button>
    </div>
    <section v-if="cloudTasks" class="cloud-task-lookup" aria-label="云端任务核对结果">
      <h4>{{ cloudTasks.status === 'query_failed' ? '云端查询失败' : cloudTasks.status === 'not_found' ? '查询窗口内未找到任务' : '找到可能相关任务，尚未关联' }}</h4>
      <p>{{ cloudTasks.message }}</p>
      <p>冻结模型：{{ cloudTasks.model }} · 提交时间窗口：{{ time(cloudTasks.windowStart) }} — {{ time(cloudTasks.windowEnd) }}</p>
      <p v-if="!cloudTasks.coverageComplete" class="attention">本次查询未覆盖完整任务列表，不能据此判断原请求未被受理。</p>
      <p>此查询不创建生成任务。模型和时间相近只能提供线索，不能证明任务归属。</p>
      <label v-for="item in cloudTasks.candidates" :key="item.providerTaskId" class="cloud-candidate">
        <input v-model="selectedCloudTask" type="radio" :value="item.providerTaskId" :disabled="!item.canAssociate || busy" />
        <span>{{ item.providerTaskId }} · {{ item.status }} · {{ time(item.createdAt) }}<br />{{ item.model }} · {{ JSON.stringify(item.parameters) }}<br /><span v-if="item.mismatches.length" class="attention">{{ item.mismatches.join('；') }}</span></span>
      </label>
      <template v-if="selectedCloudCandidate?.canAssociate">
        <p>云端接口不能证明 Prompt 和参考素材与本地请求完全相同。请结合云端记录核对任务编号及内容。</p>
        <label><input v-model="associationAcknowledged" type="checkbox" :disabled="busy" />我已人工核对该编号属于本次提交，确认关联；我理解 Prompt 与素材身份无法由接口自动验证。</label>
        <p v-if="associationRevision !== job?.revision" class="attention">本地任务已变化，请重新核对云端任务。</p>
        <button class="secondary" :disabled="busy || !associationAcknowledged || associationRevision !== (job?.revision ?? 0)" @click="associateProviderTask">确认关联此编号并恢复结果（不创建生成）</button>
      </template>
    </section>
    <p v-if="actions.includes('prepare_replacement') && preparationBlockedReason" class="attention">{{ preparationBlockedReason }}</p>
    <p v-if="error" class="attention" role="alert">{{ error }}</p>
    <section v-if="!preparation && (replacementError || pendingReplacement || replacementPhase === 'preparing')" class="replacement-feedback" aria-label="重建提交状态">
      <p v-if="replacementPhase === 'preparing' || replacementPhase === 'reconciling'" role="status">{{ replacementPhase === 'preparing' ? '正在准备本次提交预览…' : '正在核对本次提交的任务记录…' }}</p>
      <p v-if="replacementError" class="attention" role="alert">{{ replacementError }}</p>
      <a v-if="blockingUrl" :href="blockingUrl">查看阻塞任务 {{ blockingJob?.jobId }}</a>
      <button v-if="pendingReplacement" class="secondary" :disabled="busy" @click="reconcileReplacement">重新核对本次提交（不生成）</button>
    </section>
    <details v-for="successorId in job?.successorJobIds" :key="successorId">
      <summary>查看后续任务 {{ successorId }}</summary>
      <JobStatusCard :job-id="successorId" title="后续生成任务" />
    </details>
    <details @toggle="toggle"><summary>任务详情与返回内容</summary>
      <dl v-if="job">
        <dt>本地 Job</dt><dd><code>{{ job.id }}</code> · 修订 {{ job.revision ?? 0 }}</dd>
        <dt>火山 Task ID</dt><dd>{{ job.providerTaskId ?? '未收到／此接口不提供' }}</dd>
        <dt>Response ID</dt><dd>{{ execution?.providerResponseId ?? '未收到' }}</dd>
        <dt>客户端追踪编号</dt><dd>{{ execution?.providerClientRequestId ?? '未记录；历史错误中的 request_id 不作为任务编号' }}</dd>
        <dt>服务端请求编号</dt><dd>{{ (execution?.contractVersion ?? 1) >= 2 ? (job.providerRequestId ?? '未取得') : '历史字段未区分编号职责，请查看原始记录' }}</dd>
        <dt>外部状态</dt><dd>{{ execution?.providerStatus ?? '没有可信状态回执' }}</dd>
        <dt>本地阶段／恢复</dt><dd>{{ execution?.stage }} / {{ execution?.recoveryState }}</dd>
        <dt>结果完整性</dt><dd>{{ (result?.state ?? execution?.resultState) === 'complete' ? '完整回执' : (result?.state ?? execution?.resultState) === 'partial' ? '部分内容，尚未完成' : '没有收到正文' }}</dd>
        <dt>模型</dt><dd>{{ job.provider }} / {{ job.model }}</dd>
      </dl>
      <dl v-if="diagnostics" aria-label="传输诊断证据">
        <dt>超时类别</dt><dd>{{ timeoutNames[String(diagnostics.timeoutCategory)] ?? '未发生／未记录' }}</dd>
        <dt>请求体大小</dt><dd>{{ diagnostics.requestBytes ?? '未记录' }} 字节 · {{ diagnostics.requestBytesSource === 'estimated_json' ? '按 JSON 估算' : '实际 HTTP 请求' }}</dd>
        <dt>实际参考数量</dt><dd>{{ diagnostics.imageReferenceCount ?? '未记录' }} 张图片 · {{ diagnostics.videoReferenceCount ?? '未记录' }} 段视频</dd>
        <dt>阶段耗时</dt><dd>参考发布准备 {{ diagnostics.localPreparationMs ?? '未记录' }} ms · 提交等待 {{ diagnostics.submitElapsedMs ?? '未记录' }} ms</dd>
        <dt>请求超时设置</dt><dd>{{ diagnostics.timeoutSeconds ?? '未记录' }} 秒</dd>
      </dl>
      <p v-if="result?.message">{{ result.message }}</p>
      <details v-if="result?.result"><summary>已收到的实际内容</summary><pre>{{ JSON.stringify(result.result, null, 2) }}</pre></details>
      <details v-if="result?.error || execution?.queryError"><summary>错误正文</summary><pre>{{ JSON.stringify({ error: result?.error, queryError: execution?.queryError }, null, 2) }}</pre></details>
    </details>
    <dialog ref="replacementDialog" class="replacement-dialog" aria-label="重新生成准备" @cancel.prevent="!busy && (preparation = null)">
      <template v-if="preparation">
        <div class="replacement-body">
        <h3>重新准备一次生成</h3><p>旧任务继续保留为结果未知。本次使用当前输入，可能与旧请求重复执行和收费。</p>
        <p>{{ preparation.provider }} · {{ preparation.model }} · {{ preparation.expectedCostMicros == null ? '费用待核价' : `预估 ¥${preparation.expectedCostMicros / 1000000}` }}</p>
        <p v-if="preparedInput.targetDurationSeconds || preparedInput.durationSeconds">目标时长：{{ preparedInput.targetDurationSeconds ?? preparedInput.durationSeconds }} 秒 · {{ preparedInput.aspectRatio ?? '9:16' }}</p>
        <div v-if="preparedReferences.length" class="prepared-references">
            <figure v-for="(reference, index) in preparedReferences" :key="`${reference.id}:${index}`"><img v-if="reference.id" :src="`/api/v1/assets/${reference.id}/content`" :alt="referenceNames[reference.role] ?? reference.role" /><figcaption>图片 {{ index + 1 }} · {{ referenceNames[reference.role] ?? reference.role }}<template v-if="reference.frame != null"> · 草稿第 {{ reference.frame }} 帧</template><template v-if="!reference.id"> · 历史素材编号缺失</template></figcaption></figure>
        </div>
        <div v-if="preparedRanges.length" class="prepared-ranges"><p v-for="range in preparedRanges" :key="range.name">{{ range.name }}：[{{ range.startFrame }}, {{ range.endFrame }}) · {{ range.endFrame! - range.startFrame! }} 帧</p></div>
        <video v-if="preparedVideo?.assetId" class="prepared-video" :src="`/api/v1/assets/${preparedVideo.assetId}/content`" controls preload="metadata" aria-label="本次实际参考视频" />
        <p v-else-if="preparation.kind === 'regenerate_video_segment'">本次没有发送参考视频。</p>
        <ProviderPrompt v-if="['generate_image', 'generate_video', 'regenerate_video_segment'].includes(preparation.kind) || typeof preparedInput.compiledProviderPrompt === 'string'" :compiled-provider-prompt="typeof preparedInput.compiledProviderPrompt === 'string' ? preparedInput.compiledProviderPrompt : null" :prompt="typeof preparedInput.prompt === 'string' ? preparedInput.prompt : null" :negative-prompt="typeof preparedInput.negativePrompt === 'string' ? preparedInput.negativePrompt : null" />
        <details v-else open><summary>本次生成指令正文</summary><pre>{{ preparedInput.prompt ?? preparedInput.text ?? preparedInput.rawText ?? '本次任务未提供可展示的指令正文，请返回对应创作页面核对。' }}</pre></details>
        </div>
        <footer class="replacement-footer">
        <p v-if="replacementPhase === 'submitting' || replacementPhase === 'reconciling'" role="status" aria-live="polite">{{ replacementPhase === 'submitting' ? '正在创建修改任务…取得本地任务编号后将显示执行进度。' : '正在核对本次提交的任务记录，不会再次生成…' }}</p>
        <p v-if="replacementError" class="attention" role="alert">{{ replacementError }}</p>
        <a v-if="blockingUrl" :href="blockingUrl">查看阻塞任务 {{ blockingJob?.jobId }}</a>
        <label><input v-model="acknowledged" type="checkbox" :disabled="busy || Boolean(pendingReplacement)" />我确认可能重复执行和收费；仅授权本次输入和本次提交。</label>
        <p v-if="preparationBlockedReason" class="attention">{{ preparationBlockedReason }}</p>
        <div class="task-actions"><button class="secondary" :disabled="busy" @click="preparation = null">{{ pendingReplacement ? '返回，保留待核对记录' : '返回，不提交' }}</button><button v-if="pendingReplacement && !busy" class="secondary" @click="reconcileReplacement">重新核对本次提交（不生成）</button><button :disabled="!acknowledged || busy || Boolean(pendingReplacement) || Boolean(preparationBlockedReason)" @click="submitReplacement">{{ replacementPhase === 'submitting' ? '正在创建修改任务…' : '确认并提交新的付费生成' }}</button></div>
        </footer>
      </template>
    </dialog>
  </section>
</template>

<style scoped>
.task-card { margin: 12px 0; padding: 14px 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--paper, #fffdf9); font-size: 12px; overflow-wrap: anywhere; text-align: left; }
header { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; } small { color: var(--muted); } .attention { color: #a24b2c; }
.task-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }.task-actions button { padding: 8px 12px; }
.cloud-task-lookup { margin-top:14px; padding:14px; border:1px solid var(--line); border-radius:8px; }.cloud-task-lookup input { width:auto; }.cloud-candidate { display:flex; gap:10px; align-items:flex-start; padding:10px 0; }.cloud-task-lookup button { margin-top:12px; }
details { margin-top: 10px; } summary { cursor: pointer; } pre { max-height: 360px; overflow: auto; white-space: pre-wrap; font-size: 11px; padding: 10px; background: #f4f0ea; }
dl { display: grid; grid-template-columns: 140px 1fr; gap: 7px; } dd { margin: 0; } li { margin: 8px 0; }
.replacement-dialog[open] { position: fixed; display:flex; flex-direction:column; z-index: 1100; inset: 5vh 0 auto; width: min(900px, 90vw); max-height: 90vh; overflow: hidden; border: 1px solid var(--line); border-radius: 16px; padding: 0; box-shadow: 0 0 0 100vmax #0008; background: var(--paper, white); }
.replacement-body { min-height:0; overflow:auto; padding:20px 24px; }.replacement-footer { flex:none; padding:12px 24px; border-top:1px solid var(--line); background:var(--paper,white); }.replacement-footer p { margin:8px 0; }.replacement-footer label { display:flex; align-items:flex-start; gap:6px; }.replacement-footer input { width:auto; flex:none; }.prepared-video { display:block; max-height:220px; max-width:100%; margin:12px 0; }.prepared-ranges p { margin:5px 0; }.replacement-feedback button { margin-top:8px; }
.prepared-references { display: flex; gap: 12px; flex-wrap: wrap; margin: 12px 0; }.prepared-references figure { margin: 0; max-width:130px; }.prepared-references img { width: 80px; height: 80px; object-fit: contain; background: #f4f0ea; }.prepared-references figcaption { text-align: center; }
.replacement-dialog::backdrop { background: #0008; }
@media(max-width:600px) { dl { grid-template-columns: 1fr; } dd { margin-bottom: 10px; } }
</style>

<style>
body:has(.replacement-dialog[open]) { overflow: hidden; }
</style>
