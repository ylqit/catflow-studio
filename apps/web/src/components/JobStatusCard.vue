<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../api/client";
import type { JobDto, JobResultDto, JobEventsDto, GenerationPreparationDto } from "../api/types";
import { subscribeJobs } from "../jobUpdates";

const props = defineProps<{ jobId: string; title?: string; resultSummary?: string }>();
const emit = defineEmits<{ changed: [job: JobDto]; replacement: [job: JobDto] }>();
const job = ref<JobDto | null>(null);
const result = ref<JobResultDto | null>(null);
const history = ref<JobEventsDto | null>(null);
const expanded = ref(false);
const busy = ref(false);
const error = ref("");
const preparation = ref<GenerationPreparationDto | null>(null);
const acknowledged = ref(false);
const replacementKey = ref("");
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
const actions = computed(() => execution.value?.availableActions ?? []);
const preparedInput = computed(() => preparation.value?.input ?? {});
const preparedReferences = computed(() => {
  const input = preparedInput.value;
  if (Array.isArray(input.referenceAssetIds)) return input.referenceAssetIds.map((id, index) => ({
    id: String(id), role: String((input.referenceRoles as string[] | undefined)?.[index] ?? `参考 ${index + 1}`),
  }));
  return [];
});
const referenceNames: Record<string, string> = { episode_child: "儿童", episode_cat: "猫咪", pair_scale: "人猫比例", environment: "当前环境", style_board: "画风" };
const stateText = computed(() => {
  const current = job.value, detail = execution.value;
  if (!current) return error.value ? "暂时无法读取本地任务记录，等待重新连接。" : "正在读取已保存的任务…";
  if (detail?.queryError?.code === "local_adapter_error") return "本地查询组件异常，尚未取得火山最新状态。已暂停自动核实，修复后可重新核实原任务。";
  if (detail?.queryError) return `暂时无法查询最新进度；上次确认：${detail.providerStatus ?? '未取得外部状态'}。`;
  if (current.status === "submission_unknown") return "提交结果未知，暂未收到可查询编号。";
  if (detail?.historicalResult && current.status === "succeeded") return "历史结果已保留，当前制作内容未被替换。";
  if (current.status === "succeeded") {
    if (props.resultSummary) return props.resultSummary;
    const validation = current.providerResult?.validation as { disposition?: string } | undefined;
    if (validation?.disposition === "needs_input" || validation?.disposition === "invalid") return "结果已返回，请查看结构校验与待完善项。";
    if (validation?.disposition === "candidate_ready") return "分镜结构校验通过，结果已保存；采用状态请查看分镜版本。";
    return "结果已保存，内容质量仍需检查。";
  }
  if (["completed", "succeeded"].includes(detail?.providerStatus ?? "")) return current.status === "failed" ? "外部已生成，本地处理未完成。" : "火山已生成，正在保存到本地。";
  if (current.status === "failed") {
    if (detail?.providerStatus === "failed") return "火山任务明确失败，返回详情已保存。";
    if (detail?.stage === "submit" && current.error?.submissionUnknown === false && current.error.httpStatus) {
      return current.error.code === "AccountOverdueError"
        ? "火山因账户欠费拒绝本次提交；未创建生成任务，拒绝回执已保存。"
        : `火山拒绝本次提交（HTTP ${current.error.httpStatus}），拒绝回执已保存。`;
    }
    return "任务在本地处理或接收阶段中断，请查看详情。";
  }
  if (current.status === "cancelled") return "任务已取消。";
  return ({ queued: "等待处理", submitting: "正在提交或接收", submitted: "外部已受理", polling: "正在核实外部进度", storing: "正在处理本地结果", cancel_requested: "正在核实取消状态" } as Record<string, string>)[current.status] ?? current.status;
});
const time = (value?: string | null) => value ? new Date(value).toLocaleString() : "尚未取得";

async function loadDetails() {
  if (!expanded.value) return;
  const id = props.jobId;
  const [body, events] = await Promise.all([api.jobResult(id), api.jobEvents(id)]);
  if (id === props.jobId) { result.value = body; history.value = events; }
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
  if (changed) { emit("changed", current); await loadDetails(); }
}
watch(() => props.jobId, () => {
  unsubscribe?.(); job.value = null; result.value = null; history.value = null; preparation.value = null;
  unsubscribe = subscribeJobs(() => ({ jobId: props.jobId }), reload, () => execution.value?.waitingForProvider ?? true);
}, { immediate: true });
onBeforeUnmount(() => unsubscribe?.());

async function toggle(event: Event) {
  expanded.value = (event.target as HTMLDetailsElement).open;
  if (expanded.value) { try { await loadDetails(); } catch (reason) { error.value = String(reason); } }
}
async function recover(action: "query_provider" | "process_result") {
  if (!job.value || busy.value) return;
  busy.value = true; error.value = "";
  try { job.value = await api.recoverJob(job.value.id, action, job.value.revision ?? 0, crypto.randomUUID()); emit("changed", job.value); }
  catch (reason) { error.value = String(reason); await reload(); }
  finally { busy.value = false; }
}
async function prepareReplacement() {
  if (busy.value) return;
  busy.value = true; error.value = ""; acknowledged.value = false;
  try { preparation.value = await api.prepareJobReplacement(props.jobId); replacementKey.value = crypto.randomUUID(); }
  catch (reason) { error.value = String(reason); }
  finally { busy.value = false; }
}
async function submitReplacement() {
  if (!preparation.value || !acknowledged.value || busy.value) return;
  busy.value = true; error.value = "";
  try {
    const created = await api.replaceUnknownJob(props.jobId, preparation.value.executionInputHash, replacementKey.value);
    preparation.value = null; emit("replacement", created); await reload();
  } catch (reason) { error.value = String(reason); }
  finally { busy.value = false; }
}
async function moreHistory() {
  if (!history.value?.hasMore) return;
  const next = await api.jobEvents(props.jobId, history.value.nextCursor);
  history.value = { ...next, items: [...history.value.items, ...next.items] };
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
      <button v-if="actions.includes('query_provider')" class="secondary" :disabled="busy" @click="recover('query_provider')">重新核实外部进度</button>
      <button v-if="actions.includes('process_result')" class="secondary" :disabled="busy" @click="recover('process_result')">恢复本地结果处理</button>
      <button v-if="actions.includes('prepare_replacement')" class="secondary" :disabled="busy" @click="prepareReplacement">保留此记录，重新准备一次生成</button>
    </div>
    <p v-if="error" class="attention" role="alert">{{ error }}</p>
    <details v-for="successorId in job?.successorJobIds" :key="successorId">
      <summary>查看后续任务 {{ successorId }}</summary>
      <JobStatusCard :job-id="successorId" title="后续生成任务" />
    </details>
    <details @toggle="toggle"><summary>任务详情、返回内容与历史</summary>
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
      <p v-if="result?.message">{{ result.message }}</p>
      <details v-if="result?.result"><summary>已收到的实际内容</summary><pre>{{ JSON.stringify(result.result, null, 2) }}</pre></details>
      <details v-if="result?.error || execution?.queryError"><summary>错误正文</summary><pre>{{ JSON.stringify({ error: result?.error, queryError: execution?.queryError }, null, 2) }}</pre></details>
      <details v-if="job"><summary>冻结输入</summary><pre>{{ JSON.stringify(job.frozenInput, null, 2) }}</pre></details>
      <ol><li v-for="item in history?.items" :key="item.id"><time>{{ time(item.createdAt) }}</time> {{ item.eventType }}<details><summary>记录内容</summary><pre>{{ JSON.stringify(item.payload, null, 2) }}</pre></details></li></ol>
      <button v-if="history?.hasMore" class="secondary" @click="moreHistory">继续读取历史</button>
    </details>
    <dialog ref="replacementDialog" class="replacement-dialog" aria-label="重新生成准备" @cancel.prevent="!busy && (preparation = null)">
      <template v-if="preparation">
        <h3>重新准备一次生成</h3><p>旧任务继续保留为结果未知。本次使用当前输入，可能与旧请求重复执行和收费。</p>
        <p>{{ preparation.provider }} · {{ preparation.model }} · {{ preparation.expectedCostMicros == null ? '费用待核价' : `预估 ¥${preparation.expectedCostMicros / 1000000}` }}</p>
        <p v-if="preparedInput.targetDurationSeconds || preparedInput.durationSeconds">目标时长：{{ preparedInput.targetDurationSeconds ?? preparedInput.durationSeconds }} 秒 · {{ preparedInput.aspectRatio ?? '9:16' }}</p>
        <div v-if="preparedReferences.length" class="prepared-references">
          <figure v-for="reference in preparedReferences" :key="reference.id"><img :src="`/api/v1/assets/${reference.id}/content`" :alt="referenceNames[reference.role] ?? reference.role" /><figcaption>{{ referenceNames[reference.role] ?? reference.role }}</figcaption></figure>
        </div>
        <details open><summary>本次生成指令</summary><pre>{{ preparedInput.prompt ?? preparedInput.text ?? '请展开冻结输入查看本次完整内容。' }}</pre></details>
        <details><summary>完整输入、引用与执行配置</summary><pre>{{ JSON.stringify(preparation.input, null, 2) }}</pre></details>
        <label><input v-model="acknowledged" type="checkbox" />我确认可能重复执行和收费；仅授权本次输入和本次提交。</label>
        <div class="task-actions"><button class="secondary" :disabled="busy" @click="preparation = null">返回，不提交</button><button :disabled="!acknowledged || busy" @click="submitReplacement">确认并提交新的付费生成</button></div>
      </template>
    </dialog>
  </section>
</template>

<style scoped>
.task-card { margin: 12px 0; padding: 14px 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--paper, #fffdf9); font-size: 12px; overflow-wrap: anywhere; text-align: left; }
header { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; } small { color: var(--muted); } .attention { color: #a24b2c; }
.task-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }.task-actions button { padding: 8px 12px; }
details { margin-top: 10px; } summary { cursor: pointer; } pre { max-height: 360px; overflow: auto; white-space: pre-wrap; font-size: 11px; padding: 10px; background: #f4f0ea; }
dl { display: grid; grid-template-columns: 140px 1fr; gap: 7px; } dd { margin: 0; } li { margin: 8px 0; }
.replacement-dialog[open] { position: fixed; z-index: 1100; inset: 5vh 0 auto; width: min(800px, 90vw); max-height: 90vh; overflow: auto; border: 1px solid var(--line); border-radius: 16px; padding: 24px; box-shadow: 0 0 0 100vmax #0008; background: var(--paper, white); }
.prepared-references { display: flex; gap: 12px; flex-wrap: wrap; margin: 12px 0; }.prepared-references figure { margin: 0; }.prepared-references img { width: 80px; height: 80px; object-fit: contain; background: #f4f0ea; }.prepared-references figcaption { text-align: center; }
.replacement-dialog::backdrop { background: #0008; }
@media(max-width:600px) { dl { grid-template-columns: 1fr; } dd { margin-bottom: 10px; } }
</style>

<style>
body:has(.replacement-dialog[open]) { overflow: hidden; }
</style>
