<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { api } from "../../api/client";
import type { JobDto, ShotPlanGenerationAttemptDto, ShotPlanVersionDto, ShotSpecDto, WorkspaceDto } from "../../api/types";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { billingPresentation, errorPresentation, jobPresentation, paidModelBlockedReason, type PaidModelRuntime } from "../../presentation";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto; runtime?: PaidModelRuntime | null }>();
const emit = defineEmits<{ changed: [] }>();
const saving = ref(false);
const generating = ref(false);
const recoveringJobId = ref<string | null>(null);
const materializingJobId = ref<string | null>(null);
const draftEditorJobId = ref<string | null>(null);
const draftEditorText = ref("");
const submittedDirectorJobId = ref<string | null>(null);
const error = ref("");
const errorDetail = ref("");
const versionsError = ref("");
const plans = ref<ShotPlanVersionDto[]>([]);
const attempts = ref<ShotPlanGenerationAttemptDto[]>([]);
const failedAttemptCount = computed(() => attempts.value.filter((attempt) => (
  attempt.status === "failed" && attempt.result?.disposition !== "candidate_ready"
)).length);
const selectedPlanId = ref<string | null>(null);
const originalShotsJson = ref("[]");
const compareOpen = ref(false);
const compareButton = ref<HTMLButtonElement | null>(null);
const storyboardRoot = ref<HTMLElement | null>(null);
const now = ref(Date.now());
const shots = reactive<ShotSpecDto[]>([]);
const totalDuration = computed(() => shots.reduce((sum, shot) => sum + shot.durationSeconds, 0));
const availablePlans = computed(() => {
  if (plans.value.length) return plans.value;
  return props.workspace.activeShotPlan ? [props.workspace.activeShotPlan] : [];
});
const activePlan = computed(() => props.workspace.activeShotPlan
  ?? availablePlans.value.find((plan) => plan.active)
  ?? null);
const selectedPlan = computed(() => availablePlans.value.find((plan) => plan.id === selectedPlanId.value)
  ?? activePlan.value
  ?? availablePlans.value[0]
  ?? null);
const displayedDirectorJob = computed(() => {
  const persisted = props.workspace.latestDirectorJob ?? null;
  if (submittedDirectorJobId.value && persisted?.id !== submittedDirectorJobId.value) return null;
  return persisted;
});
const displayedAttempt = computed(() => attempts.value.find(
  (attempt) => attempt.jobId === displayedDirectorJob.value?.id,
) ?? null);
const displayedGenerationResult = computed(() => displayedAttempt.value?.result ?? null);
const displayedResultIsCandidateReady = computed(() => (
  displayedGenerationResult.value?.disposition === "candidate_ready"
));
const recoverableDisplayedResult = computed(() => Boolean(
  displayedAttempt.value
    && displayedGenerationResult.value?.disposition === "candidate_ready"
    && displayedGenerationResult.value.recoverable
    && !displayedAttempt.value.resultShotPlanVersionId,
));
const directorJobPresentation = computed(() => displayedDirectorJob.value
  ? jobPresentation(displayedDirectorJob.value.status)
  : null);
const directorBillingPresentation = computed(() => displayedDirectorJob.value
  ? billingPresentation(displayedDirectorJob.value.billingStatus, displayedDirectorJob.value.actualCostMicros, displayedDirectorJob.value.provider)
  : null);
const paidBlockedReason = computed(() => paidModelBlockedReason(props.runtime));
const nonTerminalStatuses = new Set<JobDto["status"]>([
  "queued", "submitting", "submitted", "polling", "storing", "cancel_requested", "submission_unknown",
]);
const jobBusy = computed(() => Boolean(
  (submittedDirectorJobId.value && !displayedDirectorJob.value)
    || (displayedDirectorJob.value && nonTerminalStatuses.has(displayedDirectorJob.value.status)),
));
const workerReady = computed(() => props.runtime?.worker?.ready ?? true);
const queuedWhileWorkerUnavailable = computed(() => Boolean(
  displayedDirectorJob.value?.status === "queued" && !workerReady.value,
));
const shotsDirty = computed(() => JSON.stringify(shots) !== originalShotsJson.value);
const canEditSelected = computed(() => Boolean(
  selectedPlan.value && !selectedPlan.value.outdated
    && (selectedPlan.value.active || selectedPlan.value.reviewStatus === "candidate"),
));
const actionLabel = computed(() => {
  const plan = selectedPlan.value;
  if (!plan) return "保存新版本";
  if (plan.reviewStatus === "candidate") return shotsDirty.value ? "保存修改并采用" : "采用新版";
  if (!plan.active && plan.reviewStatus === "accepted") return "恢复使用此版本";
  return "保存新版本";
});
const failureMessage = computed(() => {
  const job = displayedDirectorJob.value;
  const current = activePlan.value;
  if (!job || job.status !== "failed") return "";
  if (displayedResultIsCandidateReady.value) return "";
  const suffix = current ? `当前仍保留版本 ${current.revision}。` : "当前还没有可用分镜。";
  if (job.error?.code === "response_not_completed" && job.error.incompleteReason === "max_output_tokens") {
    return `模型没有完整写完分镜，本次没有生成新版本。${suffix}`;
  }
  if (job.error?.code === "response_not_completed") {
    return `模型未返回完整分镜，本次结果没有保存。${suffix}`;
  }
  if (job.error?.code === "director_output_validation_failed") {
    return `模型返回的分镜结构未通过校验，本次没有生成新版本。${suffix}`;
  }
  if (
    job.error?.code === "result_storage_failed"
    && /DirectorPlanPayload|shots/i.test(job.error.message ?? "")
  ) {
    return `模型返回的分镜结构未通过校验，本次没有生成新版本。${suffix}`;
  }
  return `新版没有生成。${suffix}`;
});
const elapsedLabel = computed(() => {
  const job = displayedDirectorJob.value;
  if (!job?.createdAt) return "";
  const reference = job.status === "queued"
    ? job.createdAt
    : job.status === "storing"
      ? job.updatedAt || job.createdAt
      : job.providerSubmissionStartedAt || job.updatedAt || job.createdAt;
  const elapsed = Math.max(0, Math.floor((now.value - Date.parse(reference)) / 1000));
  if (elapsed < 60) return `${elapsed} 秒`;
  return `${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`;
});
const elapsedKind = computed(() => {
  const status = displayedDirectorJob.value?.status;
  if (status === "queued") return "排队时间";
  if (status === "storing") return "保存时间";
  return "模型处理时间";
});
const directorProgressHeadline = computed(() => {
  const job = displayedDirectorJob.value;
  const current = activePlan.value;
  if (!job) return "";
  if (
    displayedResultIsCandidateReady.value
    && displayedAttempt.value?.resultShotPlanVersionId
  ) {
    const issueCount = displayedGenerationResult.value?.issues.length ?? 0;
    return `新版分镜已经恢复，包含 ${issueCount} 项制作提示，等待确认。`;
  }
  if (recoverableDisplayedResult.value) {
    const issueCount = displayedGenerationResult.value?.issues.length ?? 0;
    return `新版分镜已经返回，包含 ${issueCount} 项制作提示。本次结果已保存，可以直接恢复。`;
  }
  if (displayedGenerationResult.value?.disposition === "needs_input") {
    const blockingCount = displayedGenerationResult.value.issues.filter(
      (issue) => issue.severity === "blocking",
    ).length;
    return `分镜已经返回，还需要补充 ${blockingCount} 项重要内容。本次结果已经保存，不需要重新调用模型。`;
  }
  if (failureMessage.value) return failureMessage.value;
  if (job.status === "queued" && !workerReady.value) {
    return "后台任务暂时离线，原任务已保存，尚未提交模型。";
  }
  if (job.status === "queued") return "等待后台任务领取。";
  if (["submitting", "submitted", "polling"].includes(job.status)) {
    return current
      ? `正在基于当前故事生成新分镜，版本 ${current.revision} 仍在使用。`
      : "正在生成分镜。";
  }
  if (job.status === "storing") return "模型结果已返回，正在校验并保存新版。";
  if (job.status === "submission_unknown") return "提交状态需要人工确认，请不要再次生成。";
  return directorJobPresentation.value?.label ?? "";
});
const comparisonSummary = computed(() => {
  const left = activePlan.value;
  const right = selectedPlan.value;
  if (!left || !right) return { added: 0, removed: 0, changed: 0, durationClosed: false };
  const coreFields: CoreComparisonField[] = [
    "durationSeconds", "framing", "cameraMovement", "childAction", "catAction", "environmentChange", "finalFrame", "transition",
  ];
  let changed = 0;
  for (let index = 0; index < Math.min(left.shots.length, right.shots.length); index += 1) {
    for (const field of coreFields) {
      if (coreFieldValue(left.shots[index], field) !== coreFieldValue(right.shots[index], field)) changed += 1;
    }
  }
  return {
    added: Math.max(0, right.shots.length - left.shots.length),
    removed: Math.max(0, left.shots.length - right.shots.length),
    changed,
    durationClosed: right.totalDurationSeconds === props.workspace.activeStory?.targetDurationSeconds,
  };
});

type CoreComparisonField = "durationSeconds" | "framing" | "cameraMovement" | "childAction" | "catAction" | "environmentChange" | "finalFrame" | "transition";

function trimSummaryBoundary(value?: string | null) {
  return (value ?? "").trim().replace(/[，,；;。.!！?？：:]+$/u, "");
}

function actionSummary(
  blocking: ShotSpecDto["childBlocking"] | ShotSpecDto["catBlocking"],
  fallback: string,
) {
  if (!blocking) return fallback;
  return [blocking.initialState, blocking.movementPath, blocking.endState]
    .map(trimSummaryBoundary)
    .filter(Boolean)
    .join(" → ");
}

function childSummary(shot: ShotSpecDto) {
  return actionSummary(shot.childBlocking, shot.childAction);
}

function catSummary(shot: ShotSpecDto) {
  return actionSummary(shot.catBlocking, shot.catAction);
}

function changeSummary(shot: ShotSpecDto) {
  if (!shot.physicalChange) return shot.environmentChange;
  const subject = trimSummaryBoundary(shot.physicalChange.subject);
  const before = trimSummaryBoundary(shot.physicalChange.before);
  const after = trimSummaryBoundary(shot.physicalChange.after);
  return `${subject} · ${before} → ${after}`;
}

function finalFrameSummary(shot: ShotSpecDto) {
  return shot.continuity?.finalFrame ?? "旧版分镜未记录最终帧";
}

function synchronizedShot(shot: ShotSpecDto): ShotSpecDto {
  const clone = JSON.parse(JSON.stringify(shot)) as ShotSpecDto;
  return {
    ...clone,
    durationFrames: shot.durationSeconds * 24,
    childAction: childSummary(shot),
    catAction: catSummary(shot),
    environmentChange: changeSummary(shot),
  };
}

function coreFieldValue(shot: ShotSpecDto, field: CoreComparisonField): string | number {
  if (field === "childAction") return childSummary(shot);
  if (field === "catAction") return catSummary(shot);
  if (field === "environmentChange") return changeSummary(shot);
  if (field === "finalFrame") return finalFrameSummary(shot);
  return shot[field];
}

const professionalFieldDefinitions = [
  ["动作与状态", "人物初始状态", (shot: ShotSpecDto) => shot.childBlocking?.initialState],
  ["动作与状态", "人物运动路径", (shot: ShotSpecDto) => shot.childBlocking?.movementPath],
  ["动作与状态", "人物结束状态", (shot: ShotSpecDto) => shot.childBlocking?.endState],
  ["动作与状态", "人物微动作", (shot: ShotSpecDto) => shot.childBlocking?.microMotions],
  ["动作与状态", "猫咪初始状态", (shot: ShotSpecDto) => shot.catBlocking?.initialState],
  ["动作与状态", "猫咪运动路径", (shot: ShotSpecDto) => shot.catBlocking?.movementPath],
  ["动作与状态", "猫咪结束状态", (shot: ShotSpecDto) => shot.catBlocking?.endState],
  ["动作与状态", "猫咪微动作", (shot: ShotSpecDto) => shot.catBlocking?.microMotions],
  ["动作与状态", "物理变化", (shot: ShotSpecDto) => shot.physicalChange],
  ["镜头画面", "焦距与机位", (shot: ShotSpecDto) => shot.lens],
  ["镜头画面", "构图与轴线", (shot: ShotSpecDto) => shot.composition],
  ["连续性与结尾", "镜头连续性", (shot: ShotSpecDto) => shot.continuity],
  ["光线与声音", "光线与色彩", (shot: ShotSpecDto) => shot.lighting],
  ["光线与声音", "声音设计", (shot: ShotSpecDto) => shot.sound],
  ["导演意图与风险", "导演意图", (shot: ShotSpecDto) => shot.directorIntent],
  ["导演意图与风险", "生成风险", (shot: ShotSpecDto) => shot.generationRisks],
] as const;

function formatComparisonValue(value: unknown) {
  if (value === undefined || value === null || value === "") return "未记录";
  if (Array.isArray(value)) return value.length ? value.join("、") : "无";
  if (typeof value === "object") {
    return Object.values(value as Record<string, unknown>)
      .flatMap((item) => Array.isArray(item) ? item : [item])
      .filter((item) => item !== undefined && item !== null && item !== "")
      .map(String)
      .join("；") || "未记录";
  }
  return String(value);
}

const professionalComparisonRows = computed(() => {
  const current = activePlan.value;
  const compared = selectedPlan.value;
  if (!current || !compared) return [];
  return professionalFieldDefinitions.flatMap(([group, label, read]) => (
    Array.from({ length: Math.max(current.shots.length, compared.shots.length) }, (_, index) => {
      const left = current.shots[index] ? read(current.shots[index]) : undefined;
      const right = compared.shots[index] ? read(compared.shots[index]) : undefined;
      if (JSON.stringify(left) === JSON.stringify(right)) return null;
      return {
        key: `${index}:${group}:${label}`,
        shotOrder: index + 1,
        group,
        label,
        current: formatComparisonValue(left),
        compared: formatComparisonValue(right),
      };
    }).filter((row): row is NonNullable<typeof row> => row !== null)
  ));
});

function compactStoryTitle(title: string) {
  return title.length > 20 ? `${title.slice(0, 18)}…` : title;
}

function versionStatus(plan: ShotPlanVersionDto) {
  if (plan.active && plan.outdated) return "当前使用 · 输入已变化";
  if (plan.active) return "当前使用";
  if (plan.outdated) return "输入已变化 · 仅供参考";
  if (plan.reviewStatus === "candidate") return "新生成 · 待确认";
  if (plan.reviewStatus === "rejected") return "未采用";
  if (plan.reviewStatus === "superseded") return "已被较新候选取代";
  return "历史已采用";
}

function versionSource(plan: ShotPlanVersionDto) {
  return plan.producingJobId ? "分镜生成" : "手工保存";
}

function versionIssueCount(plan: ShotPlanVersionDto) {
  return attempts.value.find((attempt) => (
    attempt.resultShotPlanVersionId === plan.id
      || attempt.result?.resultShotPlanVersionId === plan.id
  ))?.result?.issues.length ?? 0;
}

function generationAttemptLabel(attempt: ShotPlanGenerationAttemptDto) {
  if (attempt.result?.disposition === "candidate_ready") {
    return attempt.resultShotPlanVersionId ? "已有结果已恢复" : "已有结果，可恢复";
  }
  if (attempt.result?.disposition === "needs_input") return "已有结果，待补充";
  return jobPresentation(attempt.status).label;
}

function coreFieldChanged(index: number, field: CoreComparisonField) {
  const current = activePlan.value?.shots[index];
  const compared = selectedPlan.value?.shots[index];
  return !current || !compared || coreFieldValue(current, field) !== coreFieldValue(compared, field);
}

async function openShotDetails(shotId: string, target: "child" | "cat" | "change" | "ending") {
  const details = storyboardRoot.value?.querySelector<HTMLDetailsElement>(
    `[data-shot-details-id="${shotId}"]`,
  );
  if (!details) return;
  details.open = true;
  await nextTick();
  details.querySelector<HTMLElement>(`[data-detail-target="${target}"]`)?.focus();
}

function directorRequestFingerprint() {
  return `${props.workspace.activeStory?.id ?? "missing-story"}:${props.workspace.selectionHash}:${activePlan.value?.id ?? "no-plan"}`;
}

function hydratePlan(plan: ShotPlanVersionDto | null) {
  const cloned = plan ? JSON.parse(JSON.stringify(plan.shots)) as ShotSpecDto[] : [];
  shots.splice(0, shots.length, ...cloned);
  originalShotsJson.value = JSON.stringify(cloned);
}

async function loadVersionData(preferCandidate = false) {
  try {
    const [nextPlans, nextAttempts] = await Promise.all([
      api.shotPlans(props.projectId),
      api.shotPlanGenerationAttempts(props.projectId),
    ]);
    plans.value = nextPlans;
    attempts.value = nextAttempts;
    const candidate = nextPlans.find((plan) => plan.reviewStatus === "candidate" && !plan.outdated);
    const selectedStillExists = nextPlans.some((plan) => plan.id === selectedPlanId.value);
    if ((preferCandidate || !selectedStillExists) && candidate) selectedPlanId.value = candidate.id;
    else if (!selectedStillExists) selectedPlanId.value = nextPlans.find((plan) => plan.active)?.id ?? nextPlans[0]?.id ?? null;
    const latestJob = props.workspace.latestDirectorJob;
    if (!latestJob || !nonTerminalStatuses.has(latestJob.status)) {
      settleIdempotencyKey(`director:${props.projectId}`, directorRequestFingerprint());
    }
    versionsError.value = "";
  } catch (reason) {
    versionsError.value = reason instanceof Error ? reason.message : "分镜版本暂时无法读取";
    if (!selectedPlanId.value) selectedPlanId.value = props.workspace.activeShotPlan?.id ?? null;
  }
}

watch(
  () => [selectedPlan.value?.id, selectedPlan.value?.createdAt],
  () => hydratePlan(selectedPlan.value),
  { immediate: true },
);
watch(
  () => [displayedAttempt.value?.jobId, displayedGenerationResult.value?.disposition],
  () => {
    const attempt = displayedAttempt.value;
    const result = displayedGenerationResult.value;
    if (
      !attempt
      || result?.disposition !== "needs_input"
      || !result.draft
      || draftEditorJobId.value === attempt.jobId
    ) return;
    draftEditorJobId.value = attempt.jobId;
    draftEditorText.value = JSON.stringify({
      targetDurationSeconds: result.draft.targetDurationSeconds,
      directorTreatment: result.draft.directorTreatment,
      shots: result.draft.shots,
    }, null, 2);
  },
  { immediate: true },
);
watch(
  () => [
    props.projectId,
    props.workspace.activeShotPlan?.id,
    props.workspace.latestDirectorJob?.id,
    props.workspace.latestDirectorJob?.status,
    props.workspace.latestDirectorJob?.updatedAt,
  ],
  async ([, , latestJobId, latestStatus]) => {
    const terminal = latestStatus && !nonTerminalStatuses.has(latestStatus as JobDto["status"]);
    if (terminal && latestJobId === submittedDirectorJobId.value) {
      submittedDirectorJobId.value = null;
    }
    await loadVersionData(latestStatus === "succeeded");
  },
);

let clock: number | undefined;
onMounted(() => {
  void loadVersionData();
  clock = window.setInterval(() => { now.value = Date.now(); }, 1000);
});
onBeforeUnmount(() => { if (clock !== undefined) window.clearInterval(clock); });

async function generateDirectorPlan() {
  if (generating.value || jobBusy.value || paidBlockedReason.value) return;
  generating.value = true;
  error.value = "";
  errorDetail.value = "";
  const scope = `director:${props.projectId}`;
  const fingerprint = directorRequestFingerprint();
  try {
    const submitted = await api.generateShotPlan(
      props.projectId,
      pendingIdempotencyKey(scope, fingerprint),
    );
    settleIdempotencyKey(scope, fingerprint);
    submittedDirectorJobId.value = submitted.id;
    emit("changed");
  } catch (reason) {
    const apiFailure = typeof reason === "object" && reason !== null
      ? reason as { status?: unknown; detail?: unknown; message?: unknown }
      : null;
    const detail = apiFailure?.detail;
    const detailRecord = typeof detail === "object" && detail !== null
      ? detail as { code?: unknown; message?: unknown }
      : null;
    const detailCode = typeof detailRecord?.code === "string" ? detailRecord.code : "";
    const technicalMessage = typeof apiFailure?.message === "string"
      ? apiFailure.message
      : typeof detail === "string"
        ? detail
        : String(reason);
    const legacyInputConflict = apiFailure?.status === 409
      && technicalMessage.includes("idempotency key already belongs to different input");
    if (detailCode === "idempotency_input_conflict" || legacyInputConflict) {
      settleIdempotencyKey(scope, fingerprint);
      error.value = "生成输入已经更新，本次没有创建任务。请再次点击“重新生成分镜”。";
      errorDetail.value = technicalMessage;
    } else if (detailCode === "worker_unavailable") {
      settleIdempotencyKey(scope, fingerprint);
      error.value = "后台任务暂时不可用，本次没有创建任务。系统正在尝试恢复。";
      errorDetail.value = technicalMessage;
    } else if (
      apiFailure?.status === 409
      && technicalMessage.includes("a shot plan generation job is already running")
    ) {
      error.value = "已有一条分镜任务正在处理，请等待当前任务完成。";
      errorDetail.value = technicalMessage;
      emit("changed");
    } else if (typeof apiFailure?.status !== "number") {
      error.value = "暂时无法确认任务是否已经创建，请先刷新生成记录，不要重复点击。";
      errorDetail.value = technicalMessage;
    } else {
      const failure = errorPresentation(reason, "分镜没有成功开始生成");
      error.value = failure.message;
      errorDetail.value = failure.technicalMessage;
    }
  } finally {
    generating.value = false;
  }
}

async function recoverDirectorResult() {
  const attempt = displayedAttempt.value;
  if (!attempt || !recoverableDisplayedResult.value || recoveringJobId.value) return;
  recoveringJobId.value = attempt.jobId;
  error.value = "";
  errorDetail.value = "";
  try {
    const recovered = await api.recoverShotPlanGeneration(
      props.projectId,
      attempt.jobId,
      crypto.randomUUID(),
    );
    selectedPlanId.value = recovered.id;
    emit("changed");
    await loadVersionData(true);
  } catch (reason) {
    const failure = errorPresentation(reason, "已有分镜结果没有成功恢复");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    recoveringJobId.value = null;
  }
}

async function materializeDirectorDraft() {
  const attempt = displayedAttempt.value;
  if (!attempt || displayedGenerationResult.value?.disposition !== "needs_input" || materializingJobId.value) return;
  error.value = "";
  errorDetail.value = "";
  let payload: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(draftEditorText.value);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("分镜草稿必须是一个完整对象");
    }
    payload = parsed as Record<string, unknown>;
  } catch (reason) {
    error.value = "分镜草稿还不能保存，请检查标出的字段和 JSON 格式。";
    errorDetail.value = reason instanceof Error ? reason.message : String(reason);
    return;
  }
  materializingJobId.value = attempt.jobId;
  try {
    const candidate = await api.materializeShotPlanGeneration(
      props.projectId,
      attempt.jobId,
      payload,
      crypto.randomUUID(),
    );
    selectedPlanId.value = candidate.id;
    emit("changed");
    await loadVersionData(true);
  } catch (reason) {
    const failure = errorPresentation(reason, "补充后的分镜还不能创建待确认版本");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    materializingJobId.value = null;
  }
}

async function save() {
  const story = props.workspace.activeStory;
  const plan = selectedPlan.value;
  if (!story || !plan) return;
  saving.value = true;
  error.value = "";
  try {
    if (plan.reviewStatus === "candidate" && !shotsDirty.value) {
      await api.activateShotPlan(
        props.projectId,
        plan.id,
        activePlan.value?.id ?? null,
        pendingIdempotencyKey(`shot-plan-activate:${props.projectId}`, `${plan.id}:${activePlan.value?.id ?? "none"}`),
      );
    } else if (!plan.active && plan.reviewStatus === "accepted" && !shotsDirty.value) {
      await api.activateShotPlan(
        props.projectId,
        plan.id,
        activePlan.value?.id ?? null,
        pendingIdempotencyKey(`shot-plan-restore:${props.projectId}`, `${plan.id}:${activePlan.value?.id ?? "none"}`),
      );
    } else {
      await api.createShotPlan(props.projectId, {
        sourceStoryVersionId: story.id,
        sourceSelectionHash: props.workspace.selectionHash,
        baseShotPlanVersionId: plan.id,
        expectedActiveShotPlanVersionId: activePlan.value?.id ?? null,
        clip: plan.clip,
        shots: shots.map(synchronizedShot),
        directorTreatment: plan.directorTreatment,
        directorPromptRevision: plan.directorPromptRevision,
        directorModel: plan.directorModel,
        directorInputHash: plan.directorInputHash,
      });
    }
    emit("changed");
    await loadVersionData();
  } catch (reason) {
    const failure = errorPresentation(reason, "分镜没有成功保存");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    saving.value = false;
  }
}

async function rejectSelected() {
  const plan = selectedPlan.value;
  if (!plan || plan.reviewStatus !== "candidate") return;
  saving.value = true;
  try {
    await api.rejectShotPlan(props.projectId, plan.id);
    selectedPlanId.value = activePlan.value?.id ?? null;
    emit("changed");
    await loadVersionData();
  } catch (reason) {
    const failure = errorPresentation(reason, "这版分镜没有成功标记为不采用");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    saving.value = false;
  }
}

function openComparison() {
  compareOpen.value = true;
}

async function closeComparison() {
  compareOpen.value = false;
  await nextTick();
  compareButton.value?.focus();
}
</script>

<template>
  <section v-if="!workspace.activeStory" class="card empty missing-story">
    <div>✦</div><h2>先采用一个生活故事</h2><p>分镜会根据当前故事安排镜头和动作。</p><RouterLink class="primary" :to="`/projects/${projectId}/planner`">回到故事灵感</RouterLink>
  </section>

  <section v-else ref="storyboardRoot" class="storyboard-layout">
    <aside class="story-source card">
      <p class="eyebrow">当前故事 · 版本 {{ workspace.activeStory.revision }}</p>
      <h2 :title="workspace.activeStory.title">{{ compactStoryTitle(workspace.activeStory.title) }}</h2>
      <details class="story-body"><summary>查看故事全文</summary><p>{{ workspace.activeStory.body }}</p></details>
      <div class="story-rule"><b>{{ workspace.activeStory.targetDurationSeconds }} 秒</b><span>9:16</span><span>24 fps</span><span>{{ workspace.activeStory.dialoguePolicy === "none" ? "无对白" : "极少对白" }}</span></div>
      <ol>
        <li><b>触发</b>{{ workspace.activeStory.microEvent.trigger }}</li>
        <li><b>孩子动作</b>{{ workspace.activeStory.microEvent.childAction }}</li>
        <li><b>猫咪回应</b>{{ workspace.activeStory.microEvent.catResponse }}</li>
        <li><b>可见变化</b>{{ workspace.activeStory.microEvent.visibleChange }}</li>
        <li><b>温暖结尾</b>{{ workspace.activeStory.microEvent.warmEnding }}</li>
      </ol>
      <details v-if="selectedPlan?.directorTreatment" class="treatment"><summary>故事导演解析</summary><pre>{{ JSON.stringify(selectedPlan.directorTreatment, null, 2) }}</pre></details>
      <p v-if="selectedPlan?.outdated" class="notice error">故事、角色或环境已经更新，这版分镜仅作历史参考。</p>
    </aside>

    <div v-if="!selectedPlan" class="director-empty card">
      <p class="eyebrow">分镜建议</p><h2>把故事拆成可拍的镜头</h2>
      <p>根据当前故事生成 1–4 个镜头，安排机位、构图、孩子与猫咪的动作、画面变化和前后衔接。生成后仍可逐项修改。</p>
      <div class="paid-note"><b>{{ paidBlockedReason || "本次会使用付费模型，完成后显示实际用量。" }}</b><span>离开页面后仍会继续，完成时会自动保存。</span></div>
      <button data-testid="generate-director-plan" class="primary" :disabled="generating || jobBusy || Boolean(paidBlockedReason)" @click="generateDirectorPlan"><span v-if="generating" class="spinner" />生成分镜</button>
      <section v-if="displayedDirectorJob && directorJobPresentation" class="notice director-job" :class="{ error: ['warn', 'danger'].includes(directorJobPresentation.tone) }" aria-live="polite"><b>{{ directorProgressHeadline }}</b><span v-if="queuedWhileWorkerUnavailable">系统恢复后会继续同一任务。</span><span v-else-if="jobBusy">任务已经保存，可以离开此页面。</span><details><summary>查看生成记录</summary><p>任务编号：<code>{{ displayedDirectorJob.id }}</code></p><p>原始状态：{{ displayedDirectorJob.status }}</p><p v-if="displayedDirectorJob.error?.incompleteReason">未完成原因：{{ displayedDirectorJob.error.incompleteReason }}</p><p v-else-if="displayedDirectorJob.error?.code === 'response_not_completed'">旧任务未记录具体 incomplete 原因</p><p v-if="displayedDirectorJob.error">原始错误：<code>{{ displayedDirectorJob.error.message }}</code></p><p v-if="displayedDirectorJob.actualUsage">实际用量：{{ JSON.stringify(displayedDirectorJob.actualUsage) }}</p><p v-if="directorBillingPresentation">费用：{{ directorBillingPresentation.label }} · {{ directorBillingPresentation.detail }}</p></details></section>
      <div v-if="error" class="notice error creator-error"><p>{{ error }}</p><details v-if="errorDetail && errorDetail !== error"><summary>技术详情</summary><code>{{ errorDetail }}</code></details></div>
    </div>

    <div v-else class="shot-editor card">
      <header class="editor-head">
        <div><h2>分镜设计</h2><p class="viewing-version">正在查看版本 {{ selectedPlan.revision }} · {{ versionStatus(selectedPlan) }}</p></div>
        <div class="head-actions">
          <button ref="compareButton" data-testid="compare-shot-plan" class="secondary" :disabled="!activePlan || selectedPlan.id === activePlan.id" @click="openComparison">对比当前版本</button>
          <button data-testid="regenerate-director-plan" class="secondary" :disabled="generating || jobBusy || Boolean(paidBlockedReason)" @click="generateDirectorPlan">重新生成分镜</button>
          <button v-if="selectedPlan.reviewStatus === 'candidate'" class="quiet danger-text" :disabled="saving" @click="rejectSelected">不采用</button>
          <button class="primary" :disabled="saving || selectedPlan.outdated || (!shotsDirty && selectedPlan.active)" @click="save"><span v-if="saving" class="spinner" />{{ actionLabel }}</button>
        </div>
      </header>
      <nav class="version-bar" aria-label="分镜版本">
        <button v-for="plan in availablePlans" :key="plan.id" :data-testid="`shot-plan-version-${plan.id}`" :class="{ selected: selectedPlan.id === plan.id, candidate: plan.reviewStatus === 'candidate' && !plan.outdated }" @click="selectedPlanId = plan.id">
          <b>版本 {{ plan.revision }} · {{ versionStatus(plan) }}</b>
          <span>{{ versionSource(plan) }} · {{ new Date(plan.createdAt).toLocaleString() }}</span>
          <small>故事版本 {{ workspace.activeStory?.id === plan.sourceStoryVersionId ? workspace.activeStory.revision : "历史" }} · {{ plan.sourceSelectionHash === workspace.selectionHash ? "角色与环境一致" : "角色或环境已变化" }}</small>
          <small v-if="versionIssueCount(plan)">{{ versionIssueCount(plan) }} 项制作提示</small>
        </button>
      </nav>
      <p v-if="versionsError" class="notice warn editor-error">{{ versionsError }}</p>
      <div v-if="error" class="notice error editor-error creator-error"><p>{{ error }}</p><details v-if="errorDetail && errorDetail !== error"><summary>技术详情</summary><code>{{ errorDetail }}</code></details></div>
      <section v-if="displayedDirectorJob && directorJobPresentation" class="generation-progress editor-error" :class="{ failed: displayedDirectorJob.status === 'failed' && !displayedResultIsCandidateReady, advisory: displayedResultIsCandidateReady }" aria-live="polite">
        <div class="progress-copy">
          <b>{{ directorProgressHeadline }}</b>
          <span v-if="queuedWhileWorkerUnavailable">{{ elapsedKind }} {{ elapsedLabel || "片刻" }} · 系统恢复后会继续同一任务。</span>
          <span v-else-if="jobBusy">{{ elapsedKind }} {{ elapsedLabel || "片刻" }} · 任务已经保存，可以离开此页面。</span>
          <span v-if="failureMessage && activePlan?.outdated">版本 {{ activePlan.revision }} 已因故事、角色或环境变化而过期，暂不能用于生成视频。</span>
          <ul v-if="displayedGenerationResult?.issues.length" class="validation-issues">
            <li v-for="issue in displayedGenerationResult.issues" :key="`${issue.path}:${issue.code}`" :class="issue.severity">
              <b>{{ issue.severity === "warning" ? "制作提示" : issue.severity === "blocking" ? "需要补充" : "无法读取" }}</b>
              <span>{{ issue.message }}</span>
              <small v-if="issue.suggestedAction">{{ issue.suggestedAction }}</small>
            </li>
          </ul>
          <button v-if="recoverableDisplayedResult" data-testid="recover-director-result" class="secondary recover-result" :disabled="Boolean(recoveringJobId)" @click="recoverDirectorResult">
            <span v-if="recoveringJobId" class="spinner" />从已有结果恢复（不调用模型、不产生费用）
          </button>
          <details v-if="displayedGenerationResult?.disposition === 'needs_input'" class="draft-editor">
            <summary>补充已有分镜内容</summary>
            <p>请按上方字段路径补全重要内容。这里只处理已经返回的结果，不会再次调用模型。</p>
            <textarea v-model="draftEditorText" aria-label="待补充的分镜草稿" spellcheck="false" />
            <button data-testid="materialize-director-result" class="secondary" :disabled="Boolean(materializingJobId)" @click="materializeDirectorDraft">
              <span v-if="materializingJobId" class="spinner" />检查并创建待确认版本
            </button>
          </details>
        </div>
        <ol v-if="jobBusy" class="progress-steps">
          <li :class="{ current: displayedDirectorJob.status === 'queued', done: displayedDirectorJob.status !== 'queued' }">已加入队列</li>
          <li :class="{ current: ['submitting', 'submitted', 'polling'].includes(displayedDirectorJob.status), done: ['storing'].includes(displayedDirectorJob.status) }">正在生成分镜</li>
          <li :class="{ current: displayedDirectorJob.status === 'storing' }">正在校验内容</li>
          <li>正在保存新版</li>
          <li>等待确认</li>
        </ol>
        <details><summary>查看生成记录{{ failedAttemptCount ? `（${failedAttemptCount} 次失败）` : attempts.length ? `（${attempts.length} 次）` : "" }}</summary><ul v-if="attempts.length" class="attempt-list"><li v-for="attempt in attempts" :key="attempt.jobId"><b>{{ new Date(attempt.createdAt).toLocaleString() }} · {{ generationAttemptLabel(attempt) }}</b><span v-if="attempt.resultShotPlanVersionId">生成版本 {{ availablePlans.find((plan) => plan.id === attempt.resultShotPlanVersionId)?.revision ?? "历史" }}</span><span v-if="attempt.result?.issues.length">校验结果：{{ attempt.result.issues.filter((issue) => issue.severity === "warning").length }} 项提示，{{ attempt.result.issues.filter((issue) => issue.severity === "blocking").length }} 项需要补充</span><details v-if="attempt.result?.issues.length" class="attempt-validation"><summary>查看校验与额外说明</summary><ul><li v-for="issue in attempt.result.issues" :key="`${attempt.jobId}:${issue.path}:${issue.code}`"><b>{{ issue.path || "结果" }}</b><span>{{ issue.message }}</span><code v-if="issue.providerValue !== undefined && issue.providerValue !== null">{{ JSON.stringify(issue.providerValue) }}</code></li></ul></details><span v-if="attempt.error?.incompleteReason">未完成原因：{{ attempt.error.incompleteReason }}</span><span v-else-if="attempt.error?.code === 'response_not_completed'">旧任务未记录具体 incomplete 原因</span><code v-if="attempt.error && !attempt.result">{{ attempt.error.message }}</code><small>{{ attempt.jobId }}</small></li></ul><p v-else>任务编号：<code>{{ displayedDirectorJob.id }}</code> · 原始状态：{{ displayedDirectorJob.status }}</p><p v-if="displayedDirectorJob.actualUsage">实际用量：{{ JSON.stringify(displayedDirectorJob.actualUsage) }}</p><p v-if="directorBillingPresentation">费用：{{ directorBillingPresentation.label }} · {{ directorBillingPresentation.detail }}</p></details>
      </section>
      <div class="timeline-ruler"><span v-for="tick in 6" :key="tick">{{ Math.round(((tick - 1) / 5) * workspace.activeStory.targetDurationSeconds) }}s</span></div>

      <div class="shot-list">
        <article v-for="shot in shots" :key="shot.id" class="shot-card">
          <div class="shot-summary">
            <div class="shot-number">{{ String(shot.order).padStart(2, "0") }}<label><input v-model.number="shot.durationSeconds" type="number" min="2" max="15" :disabled="!canEditSelected" /> 秒</label></div>
            <div class="shot-fields">
              <div class="field compact"><label>景别</label><input v-model="shot.framing" :disabled="!canEditSelected" /></div><div class="field compact"><label>运镜</label><input v-model="shot.cameraMovement" :disabled="!canEditSelected" /></div>
              <div data-testid="shot-child-summary" class="field wide derived-summary"><div class="summary-label"><label>人物动作</label><button v-if="shot.childBlocking" type="button" class="quiet" :disabled="!canEditSelected" @click="openShotDetails(shot.id, 'child')">编辑动作</button></div><p>人物：{{ childSummary(shot) }}</p><small v-if="shot.childBlocking?.microMotions.length">{{ shot.childBlocking.microMotions.length }} 项微动作</small></div>
              <div data-testid="shot-cat-summary" class="field wide derived-summary"><div class="summary-label"><label>猫咪动作</label><button v-if="shot.catBlocking" type="button" class="quiet" :disabled="!canEditSelected" @click="openShotDetails(shot.id, 'cat')">编辑动作</button></div><p>猫咪：{{ catSummary(shot) }}</p><small v-if="shot.catBlocking?.microMotions.length">{{ shot.catBlocking.microMotions.length }} 项微动作</small></div>
              <div data-testid="shot-change-summary" class="field wide derived-summary"><div class="summary-label"><label>画面变化</label><button v-if="shot.physicalChange" type="button" class="quiet" :disabled="!canEditSelected" @click="openShotDetails(shot.id, 'change')">编辑变化</button></div><p>变化：{{ changeSummary(shot) }}</p></div>
              <div data-testid="shot-ending-summary" class="field wide derived-summary"><div class="summary-label"><label>最终状态</label><button v-if="shot.continuity" type="button" class="quiet" :disabled="!canEditSelected" @click="openShotDetails(shot.id, 'ending')">编辑结尾</button></div><p>结尾：{{ finalFrameSummary(shot) }}</p></div>
              <div class="field compact"><label>转场</label><select v-model="shot.transition" :disabled="!canEditSelected"><option value="continuous">连续</option><option value="soft_cut">柔切</option><option value="hard_cut">硬切</option></select></div>
              <div class="field compact production-hints"><label>制作提示</label><span>{{ shot.generationRisks?.length ?? 0 }} 项</span></div>
            </div>
          </div>

          <details :data-shot-details-id="shot.id" data-testid="professional-shot-details" class="professional-details">
            <summary>查看镜头细节</summary>
            <fieldset v-if="shot.lens && shot.composition && shot.childBlocking && shot.catBlocking && shot.physicalChange && shot.continuity && shot.lighting && shot.sound" class="professional-editor" :disabled="!canEditSelected">
            <div class="professional-grid">
              <section class="detail-group span-two"><h3>动作与状态</h3><div class="detail-subgrid detail-three">
                <div class="detail-subgroup"><h4>人物走位</h4><label>初始状态<textarea v-model="shot.childBlocking.initialState" data-detail-target="child" /></label><label>运动路径<textarea v-model="shot.childBlocking.movementPath" :data-testid="`${shot.id}-child-movement`" /></label><label>结束状态<textarea v-model="shot.childBlocking.endState" /></label><div class="micro-motion-editor"><b>微动作</b><div v-for="(_, index) in shot.childBlocking.microMotions" :key="`child-motion-${index}`"><input v-model="shot.childBlocking.microMotions[index]" :aria-label="`人物微动作 ${index + 1}`"><button type="button" class="quiet" :aria-label="`移除人物微动作 ${index + 1}`" @click="shot.childBlocking.microMotions.splice(index, 1)">移除</button></div><span v-if="!shot.childBlocking.microMotions.length">暂无微动作</span></div></div>
                <div class="detail-subgroup"><h4>猫咪走位</h4><label>初始状态<textarea v-model="shot.catBlocking.initialState" data-detail-target="cat" /></label><label>运动路径<textarea v-model="shot.catBlocking.movementPath" /></label><label>结束状态<textarea v-model="shot.catBlocking.endState" /></label><div class="micro-motion-editor"><b>微动作</b><div v-for="(_, index) in shot.catBlocking.microMotions" :key="`cat-motion-${index}`"><input v-model="shot.catBlocking.microMotions[index]" :aria-label="`猫咪微动作 ${index + 1}`"><button type="button" class="quiet" :aria-label="`移除猫咪微动作 ${index + 1}`" @click="shot.catBlocking.microMotions.splice(index, 1)">移除</button></div><span v-if="!shot.catBlocking.microMotions.length">暂无微动作</span></div></div>
                <div class="detail-subgroup"><h4>物理变化</h4><label>对象<input v-model="shot.physicalChange.subject" data-detail-target="change" /></label><label>变化前<textarea v-model="shot.physicalChange.before" /></label><label>变化后<textarea v-model="shot.physicalChange.after" /></label></div>
              </div></section>
              <section class="detail-group span-two"><h3>镜头画面</h3><div class="detail-subgrid">
                <div class="detail-subgroup"><h4>焦距与机位</h4><label>等效焦距<input v-model="shot.lens.focalLengthEquivalent" /></label><label>机位高度<input v-model="shot.lens.cameraHeight" /></label><label>机位角度<input v-model="shot.lens.cameraAngle" /></label><label>透视意图<textarea v-model="shot.lens.perspectiveIntent" /></label></div>
                <div class="detail-subgroup"><h4>构图与轴线</h4><label>主体位置<input v-model="shot.composition.subjectPlacement" /></label><label>前景<input v-model="shot.composition.foreground" /></label><label>中景<input v-model="shot.composition.middleGround" /></label><label>背景<input v-model="shot.composition.background" /></label><label>运动方向<input v-model="shot.composition.screenDirection" /></label><label>视线<input v-model="shot.composition.eyeLine" /></label></div>
              </div></section>
              <section class="detail-group span-two"><h3>连续性与结尾</h3><div class="detail-subgrid"><div class="detail-subgroup"><label>承接状态<textarea v-model="shot.continuity.incoming" /></label><label>离开状态<textarea v-model="shot.continuity.outgoing" /></label></div><div class="detail-subgroup"><label>共享视觉元素<input v-model="shot.continuity.sharedVisualElement" /></label><label>最终帧<textarea v-model="shot.continuity.finalFrame" data-detail-target="ending" /></label></div></div></section>
              <section class="detail-group span-two"><h3>光线与声音</h3><div class="detail-subgrid"><div class="detail-subgroup"><h4>光线与色彩</h4><label>光线方向<input v-model="shot.lighting.direction" /></label><label>柔和度<input v-model="shot.lighting.softness" /></label><label>色彩意图<textarea v-model="shot.lighting.colorIntent" /></label></div><div class="detail-subgroup"><h4>声音设计</h4><p><b>环境声</b>{{ shot.sound.ambience.join("、") }}</p><p><b>物件声</b>{{ shot.sound.objectEffects.join("、") }}</p><p><b>动作声</b>{{ shot.sound.movementEffects.join("、") }}</p><label>音乐意图<textarea v-model="shot.sound.musicIntent" /></label><label>对白<input v-model="shot.sound.dialogue" /></label></div></div></section>
              <section class="detail-group span-two"><h3>导演意图与风险</h3><label>导演意图<textarea v-model="shot.directorIntent" /></label><div v-for="(risk, index) in shot.generationRisks" :key="`${risk.code}-${index}`" class="risk"><code>{{ risk.code }}</code><span>{{ risk.message }}</span><button type="button" class="quiet" :aria-label="`移除生成风险 ${risk.code}`" @click="shot.generationRisks?.splice(index, 1)">移除</button></div><p v-if="!shot.generationRisks?.length" class="empty-detail">暂无制作风险</p></section>
            </div>
            </fieldset>
            <p v-else class="notice warn">这是旧版简化分镜，只保留当时的历史摘要，缺少可编辑的镜头细节。建议重新生成分镜。</p>
          </details>
        </article>
      </div>
      <footer class="duration-check"><span>镜头总时长</span><strong>{{ totalDuration }} / {{ workspace.activeStory.targetDurationSeconds }} 秒</strong><span class="pill" :class="{ good: totalDuration === workspace.activeStory.targetDurationSeconds }">{{ totalDuration === workspace.activeStory.targetDurationSeconds ? "帧数闭合" : "需要调整" }}</span></footer>
    </div>

    <div v-if="compareOpen && activePlan && selectedPlan" class="compare-backdrop" @click.self="closeComparison">
      <aside data-testid="shot-plan-compare-drawer" class="compare-drawer" role="dialog" aria-modal="true" aria-labelledby="compare-title">
        <header><div><small>版本对比</small><h2 id="compare-title">当前版本与版本 {{ selectedPlan.revision }}</h2></div><button aria-label="关闭版本对比" class="quiet" @click="closeComparison">×</button></header>
        <div class="compare-summary"><span>新增镜头 {{ comparisonSummary.added }}</span><span>删除镜头 {{ comparisonSummary.removed }}</span><span>修改字段 {{ comparisonSummary.changed }}</span><span>{{ comparisonSummary.durationClosed ? "总时长闭合" : "总时长需要调整" }}</span></div>
        <div class="compare-columns">
          <section><h3>版本 {{ activePlan.revision }} · 当前使用</h3><article v-for="(shot, index) in activePlan.shots" :key="shot.id"><b :class="{ changed: coreFieldChanged(index, 'durationSeconds') }">镜头 {{ shot.order }} · {{ shot.durationSeconds }} 秒</b><p :class="{ changed: coreFieldChanged(index, 'framing') }">景别：{{ shot.framing }}</p><p :class="{ changed: coreFieldChanged(index, 'cameraMovement') }">运镜：{{ shot.cameraMovement }}</p><p :class="{ changed: coreFieldChanged(index, 'childAction') }">人物：{{ childSummary(shot) }}</p><p :class="{ changed: coreFieldChanged(index, 'catAction') }">猫咪：{{ catSummary(shot) }}</p><p :class="{ changed: coreFieldChanged(index, 'environmentChange') }">变化：{{ changeSummary(shot) }}</p><p :class="{ changed: coreFieldChanged(index, 'finalFrame') }">结尾：{{ finalFrameSummary(shot) }}</p><p :class="{ changed: coreFieldChanged(index, 'transition') }">转场：{{ shot.transition }}</p></article></section>
          <section><h3>版本 {{ selectedPlan.revision }} · {{ versionStatus(selectedPlan) }}</h3><article v-for="(shot, index) in selectedPlan.shots" :key="shot.id"><b :class="{ changed: coreFieldChanged(index, 'durationSeconds') }">镜头 {{ shot.order }} · {{ shot.durationSeconds }} 秒</b><p :class="{ changed: coreFieldChanged(index, 'framing') }">景别：{{ shot.framing }}</p><p :class="{ changed: coreFieldChanged(index, 'cameraMovement') }">运镜：{{ shot.cameraMovement }}</p><p :class="{ changed: coreFieldChanged(index, 'childAction') }">人物：{{ childSummary(shot) }}</p><p :class="{ changed: coreFieldChanged(index, 'catAction') }">猫咪：{{ catSummary(shot) }}</p><p :class="{ changed: coreFieldChanged(index, 'environmentChange') }">变化：{{ changeSummary(shot) }}</p><p :class="{ changed: coreFieldChanged(index, 'finalFrame') }">结尾：{{ finalFrameSummary(shot) }}</p><p :class="{ changed: coreFieldChanged(index, 'transition') }">转场：{{ shot.transition }}</p></article></section>
        </div>
        <details class="professional-compare"><summary>查看专业差异</summary><div v-if="professionalComparisonRows.length" class="professional-diff-list"><article v-for="row in professionalComparisonRows" :key="row.key"><header><b>镜头 {{ row.shotOrder }} · {{ row.group }}</b><span>{{ row.label }}</span></header><div><p><small>当前版本</small>{{ row.current }}</p><p><small>对比版本</small>{{ row.compared }}</p></div></article></div><p v-else class="no-differences">除上方基本信息外，没有其他镜头细节差异。</p></details>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.missing-story, .director-empty { padding: 70px; }.missing-story > div { font-size: 34px; color: var(--accent); }.missing-story p, .director-empty p { color: var(--muted); line-height: 1.7; }.missing-story .primary { display: inline-flex; align-items: center; }
.storyboard-layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 20px; align-items: start; }.story-source { position: sticky; top: 96px; padding: 24px; }.story-body { color: var(--muted); line-height: 1.7; font-size: 12px; }.story-body summary { cursor: pointer; font-weight: 700; }.story-body p { margin-bottom: 0; }.story-rule { display: flex; flex-wrap: wrap; gap: 7px; margin: 16px 0; }.story-rule > * { padding: 6px 9px; border-radius: 8px; background: #f2ece4; color: #776e66; font-size: 11px; }.story-source ol { margin: 20px 0; padding: 0; list-style: none; display: grid; gap: 10px; }.story-source li { display: grid; gap: 3px; color: #766e67; font-size: 12px; line-height: 1.5; }.story-source li:not(:last-child) { display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }.story-source li b { display: block; color: #b25e49; font-size: 10px; }.treatment pre { max-height: 260px; overflow: auto; white-space: pre-wrap; font-size: 9px; }
.director-empty { text-align: center; }.director-empty .paid-note { display: grid; gap: 5px; width: min(560px, 100%); margin: 22px auto; padding: 14px; border-radius: 12px; background: #fff4ea; color: var(--muted); font-size: 11px; }.director-empty .paid-note b { color: var(--ink); }.director-empty button { min-width: 260px; }
.director-job { display: grid; gap: 6px; }.director-job > span { color: var(--muted); }.director-job details summary, .plan-technical summary { cursor: pointer; font-weight: 700; }.director-job details p { margin: 6px 0 0; overflow-wrap: anywhere; }
.shot-editor { overflow: hidden; }.editor-head { padding: 22px 24px; display: flex; justify-content: space-between; align-items: center; gap: 20px; border-bottom: 1px solid var(--line); }.editor-head h2 { margin: 0; font-size: 20px; }.viewing-version { margin: 5px 0 0; color: var(--muted); font-size: 11px; }.head-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }.danger-text { color: #a64e42; }.editor-error { margin: 16px 24px 0; }.timeline-ruler { display: flex; justify-content: space-between; margin: 20px 28px 0 100px; color: #aaa198; font-size: 9px; border-bottom: 1px solid #e4dbd2; padding-bottom: 5px; }
.version-bar { display: flex; gap: 9px; overflow-x: auto; padding: 14px 24px; border-bottom: 1px solid var(--line); background: #fbf7f2; }.version-bar button { min-width: 190px; display: grid; gap: 4px; padding: 10px 12px; text-align: left; border: 1px solid var(--line); border-radius: 11px; background: white; color: var(--ink); }.version-bar button.selected { border-color: var(--accent); box-shadow: 0 0 0 2px rgb(216 113 82 / 12%); }.version-bar button.candidate { background: #fff4ea; }.version-bar b { font-size: 11px; }.version-bar span, .version-bar small { color: var(--muted); font-size: 9px; }
.generation-progress { display: grid; gap: 12px; margin: 16px 24px 0; padding: 15px 17px; border-radius: 13px; background: #f2f6ef; color: #526257; }.generation-progress.failed { background: #fff0ec; color: #914c40; }.progress-copy { display: grid; gap: 4px; }.progress-copy span { font-size: 11px; }.progress-steps { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; margin: 0; padding: 0; list-style: none; }.progress-steps li { padding: 7px 8px; border-radius: 8px; background: rgb(255 255 255 / 62%); color: #958d84; font-size: 9px; }.progress-steps li.done { color: #56705c; }.progress-steps li.current { background: white; color: var(--ink); font-weight: 800; }.generation-progress details summary { cursor: pointer; font-size: 10px; font-weight: 800; }.generation-progress details p { margin: 6px 0 0; font-size: 10px; overflow-wrap: anywhere; }
.generation-progress.advisory { background: #fff7e8; color: #765c35; }.validation-issues { display: grid; gap: 7px; margin: 8px 0 0; padding: 0; list-style: none; }.validation-issues li { display: grid; gap: 2px; padding: 8px 10px; border-radius: 8px; background: rgb(255 255 255 / 68%); font-size: 10px; }.validation-issues li.blocking, .validation-issues li.fatal { color: #974c40; }.validation-issues small { color: var(--muted); }.recover-result { justify-self: start; margin-top: 7px; }
.draft-editor { margin-top: 7px; padding: 10px; border-radius: 9px; background: rgb(255 255 255 / 68%); }.draft-editor summary { cursor: pointer; font-weight: 800; }.draft-editor p { color: var(--muted); font-size: 10px; }.draft-editor textarea { width: 100%; min-height: 260px; resize: vertical; padding: 10px; border: 1px solid var(--line); border-radius: 8px; font: 10px/1.5 Consolas, monospace; }.draft-editor button { margin-top: 8px; }
.shot-list { display: grid; gap: 14px; padding: 18px 24px; }.shot-card { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; background: #fff; }.shot-summary { display: grid; grid-template-columns: 65px 1fr; gap: 14px; align-items: center; padding: 16px; }.shot-number { font: 500 22px Georgia, serif; color: #c56d55; }.shot-number label { display: flex; align-items: center; gap: 3px; margin-top: 6px; color: #9c9289; font: 10px Inter, sans-serif; }.shot-number input { width: 35px; padding: 4px; }.shot-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px 12px; }.shot-fields .wide { grid-column: span 2; }.field { display: grid; gap: 4px; }.field.compact { grid-template-columns: 50px 1fr; align-items: center; }.field label { font-size: 10px; }.field input, .field select { padding: 7px 9px; font-size: 11px; }
.derived-summary { padding: 9px 11px; border: 1px solid #eadfd5; border-radius: 9px; background: #fcfaf7; }.summary-label { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.summary-label button { padding: 3px 6px; color: #9d5845; font-size: 9px; }.derived-summary p { margin: 0; color: #615a54; font-size: 11px; line-height: 1.55; }.derived-summary small { color: var(--muted); font-size: 9px; }.production-hints span { color: var(--muted); font-size: 10px; }
.professional-details { border-top: 1px solid var(--line); background: #f8f3ed; }.professional-details > summary { padding: 12px 16px; cursor: pointer; color: #9d5845; font-size: 11px; font-weight: 800; }.professional-editor { margin: 0; padding: 0; border: 0; }.professional-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 0 16px 16px; }.professional-grid fieldset { display: grid; gap: 8px; align-content: start; margin: 0; padding: 13px; border: 1px solid var(--line); border-radius: 11px; background: white; }.professional-grid legend { color: #a45e4c; font-size: 11px; font-weight: 800; }.professional-grid label { display: grid; gap: 4px; color: var(--muted); font-size: 9px; }.professional-grid input, .professional-grid textarea { padding: 7px 8px; border: 1px solid var(--line); border-radius: 7px; font-size: 10px; }.professional-grid p { display: grid; gap: 3px; margin: 0; color: var(--muted); font-size: 10px; }.professional-grid p b { color: var(--ink); }.professional-grid .span-two { grid-column: 1 / -1; }.micro-motion-editor { display: grid; gap: 5px; }.micro-motion-editor > b, .micro-motion-editor > span { color: var(--muted); font-size: 9px; }.micro-motion-editor > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px; }.micro-motion-editor button { padding: 5px 7px; font-size: 9px; }.risk { display: grid; grid-template-columns: 120px minmax(0, 1fr) auto; gap: 8px; align-items: center; padding: 7px; border-radius: 7px; background: #fff2e8; font-size: 10px; }.risk button { padding: 5px 7px; font-size: 9px; }
.detail-group { display: grid; gap: 10px; padding: 13px; border: 1px solid var(--line); border-radius: 11px; background: white; }.detail-group > h3 { margin: 0; color: #a45e4c; font-size: 12px; }.detail-subgrid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.detail-subgrid.detail-three { grid-template-columns: repeat(3, minmax(0, 1fr)); }.detail-subgroup { display: grid; gap: 8px; align-content: start; padding: 10px; border-radius: 9px; background: #faf7f2; }.detail-subgroup h4 { margin: 0; color: var(--ink); font-size: 10px; }.empty-detail { color: var(--muted); }
.duration-check { padding: 16px 24px; display: flex; gap: 12px; align-items: center; justify-content: flex-end; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }.duration-check strong { color: var(--ink); }
.compare-backdrop { position: fixed; inset: 0; z-index: 80; background: rgb(37 31 27 / 32%); }.compare-drawer { position: absolute; inset: 0 0 0 auto; width: min(960px, 92vw); overflow: auto; padding: 24px; background: #fbf7f2; box-shadow: -18px 0 50px rgb(41 32 27 / 18%); }.compare-drawer > header { display: flex; justify-content: space-between; align-items: start; }.compare-drawer h2 { margin: 4px 0 18px; }.compare-summary { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 16px; }.compare-summary span { padding: 7px 9px; border-radius: 8px; background: #eee5dc; color: #675f58; font-size: 10px; }.compare-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }.compare-columns > section { padding: 15px; border: 1px solid var(--line); border-radius: 13px; background: white; }.compare-columns h3 { margin: 0 0 12px; font-size: 13px; }.compare-columns article { padding: 11px 0; border-top: 1px solid var(--line); }.compare-columns p { margin: 5px 0; color: var(--muted); font-size: 10px; line-height: 1.45; }.professional-compare { margin-top: 14px; }.professional-compare summary { cursor: pointer; font-weight: 800; }
.compare-columns .changed { padding: 3px 5px; border-radius: 5px; background: #fff0df; color: #9a4f3e; }.professional-diff-list { display: grid; gap: 9px; margin-top: 10px; }.professional-diff-list article { padding: 11px; border: 1px solid var(--line); border-radius: 10px; background: white; }.professional-diff-list header { display: flex; justify-content: space-between; gap: 8px; color: #9a4f3e; font-size: 10px; }.professional-diff-list article > div { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 8px; }.professional-diff-list p { display: grid; gap: 4px; margin: 0; padding: 8px; border-radius: 7px; background: #faf7f2; color: #615a54; font-size: 10px; line-height: 1.5; }.professional-diff-list small { color: var(--muted); font-size: 8px; }.no-differences { color: var(--muted); font-size: 10px; }
.attempt-list { display: grid; gap: 8px; margin: 9px 0 0; padding: 0; list-style: none; }.attempt-list li { display: grid; gap: 3px; padding: 9px 10px; border: 1px solid rgb(145 76 64 / 16%); border-radius: 9px; background: rgb(255 255 255 / 60%); font-size: 10px; }.attempt-list span, .attempt-list small { color: var(--muted); }.attempt-list code { overflow-wrap: anywhere; white-space: normal; }.attempt-list small { font-size: 8px; }
.attempt-validation { margin-top: 4px; }.attempt-validation > summary { cursor: pointer; }.attempt-validation ul { display: grid; gap: 5px; margin: 6px 0 0; padding: 0; list-style: none; }.attempt-validation li { padding: 6px 8px; background: #fffaf4; }.attempt-validation code { color: #6b5043; }
@media (max-width: 1050px) { .storyboard-layout { grid-template-columns: 1fr; }.story-source { position: static; }.professional-grid, .compare-columns, .detail-subgrid, .detail-subgrid.detail-three { grid-template-columns: 1fr; }.professional-grid .span-two { grid-column: auto; }.editor-head { align-items: flex-start; }.progress-steps { grid-template-columns: 1fr; } }
</style>
