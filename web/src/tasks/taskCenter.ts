import { computed, readonly, ref } from "vue";

import { api } from "../api/client";
import type {
  JobDto,
  PersistentTaskDto,
  PersistentTaskRecoveryDto,
  TaskCancellationPolicyDto,
} from "../api/types";

export type TaskCenterStatus =
  | "queued"
  | "pending"
  | "submitting"
  | "running"
  | "awaiting_review"
  | "succeeded"
  | "failed"
  | "submission_unknown"
  | "cancelling"
  | "cancellation_unknown"
  | "restart_pending"
  | "cancelled";

export interface TaskCenterItem {
  key: string;
  jobId?: string;
  stepId?: string;
  kind: string;
  label: string;
  status: TaskCenterStatus;
  projectId?: string;
  sceneId?: string;
  shotId?: string;
  operationKey?: string;
  canvasNodeId?: string;
  canvasGroupId?: string;
  recipeInstanceId?: string;
  businessObjectId?: string;
  parentStepId?: string;
  childStepIds?: string[];
  creationMode?: string;
  workflowStage?: string;
  phase?: string;
  attempt?: number;
  model?: string | null;
  providerTaskId?: string | null;
  recovery?: PersistentTaskRecoveryDto;
  cancellation?: TaskCancellationPolicyDto;
  result?: unknown;
  resultSummary?: Record<string, unknown> | null;
  progress?: {
    currentStep?: number;
    totalSteps?: number;
    percent?: number;
    message?: string;
    providerStatus?: string;
  };
  error?: Record<string, unknown> | null;
  createdAt?: string | null;
  completedAt?: string | null;
  updatedAt: string;
  source: "runtime" | "workflow";
  eventSequence?: number;
}

export interface RegisterTaskOptions {
  kind: string;
  label: string;
  projectId?: string;
  sceneId?: string;
  shotId?: string;
  operationKey?: string;
  canvasNodeId?: string;
  canvasGroupId?: string;
  recipeInstanceId?: string;
  businessObjectId?: string;
  parentStepId?: string;
  creationMode?: string;
  workflowStage?: string;
  phase?: string;
}

export interface TaskCenterEvent {
  item: TaskCenterItem;
  previousStatus: TaskCenterStatus;
}

export interface TaskCenterScopeSignal {
  revision: number;
  operationKey?: string;
}

export interface WorkspaceRefreshRequest {
  revision: number;
  projectId: string;
  shotId?: string;
}

const STORAGE_KEY = "cvg.v5.task-center";
const NOTIFICATIONS_KEY = "cvg.v5.task-notifications";
const EVENT_CURSOR_KEY = "cvg.v5.task-event-cursor";
const ACTIVE_INTERVAL_MS = 4_000;
const IDLE_INTERVAL_MS = 25_000;
const activeStatuses = new Set<TaskCenterStatus>([
  "queued", "pending", "submitting", "running", "cancelling", "restart_pending",
]);
const knownStatuses = new Set<TaskCenterStatus>([
  ...activeStatuses,
  "awaiting_review", "succeeded", "failed", "submission_unknown",
  "cancellation_unknown", "cancelled",
]);
const items = ref<TaskCenterItem[]>(loadStoredItems());
const notifiedTerminalEvents = new Set<string>(loadNotifiedTerminalEvents());
const lastNotification = ref<TaskCenterEvent | null>(null);
const projectSignals = ref<Record<string, TaskCenterScopeSignal>>({});
const sceneSignals = ref<Record<string, TaskCenterScopeSignal>>({});
const shotSignals = ref<Record<string, TaskCenterScopeSignal>>({});
const workspaceRefreshRequest = ref<WorkspaceRefreshRequest | null>(null);
const connectionError = ref("");
const recoveringStepIds = ref<string[]>([]);
const cancellingStepIds = ref<string[]>([]);
let timer: number | undefined;
let reconnectTimer: number | undefined;
let eventSource: EventSource | undefined;
let eventCursor = loadEventCursor();
let sseConnected = false;
let refreshing = false;
let hydrated = false;
let started = false;

const orderedItems = computed(() => [...items.value].sort((left, right) => {
  const leftActionable = activeStatuses.has(left.status) || left.status === "awaiting_review";
  const rightActionable = activeStatuses.has(right.status) || right.status === "awaiting_review";
  if (leftActionable !== rightActionable) return leftActionable ? -1 : 1;
  return String(right.createdAt ?? right.updatedAt).localeCompare(
    String(left.createdAt ?? left.updatedAt),
  );
}));
const activeCount = computed(() => items.value.filter(
  (item) => activeStatuses.has(item.status),
).length);
const attentionCount = computed(() => items.value.filter(
  (item) => item.status === "awaiting_review"
    || item.status === "failed"
    || item.status === "submission_unknown"
    || item.status === "cancellation_unknown",
).length);

function loadStoredItems(): TaskCenterItem[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const record = item as TaskCenterItem;
      return typeof record.key === "string" && typeof record.status === "string"
        ? [{ ...record, status: activeStatuses.has(record.status) ? "restart_pending" : record.status }]
        : [];
    }).slice(0, 100);
  } catch {
    return [];
  }
}

function loadNotifiedTerminalEvents(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(NOTIFICATIONS_KEY) ?? "[]") as unknown;
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

function loadEventCursor(): number {
  if (typeof window === "undefined") return 0;
  const value = Number(window.localStorage.getItem(EVENT_CURSOR_KEY) ?? "0");
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

function persist() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items.value.slice(0, 100)));
  window.sessionStorage.setItem(
    NOTIFICATIONS_KEY,
    JSON.stringify([...notifiedTerminalEvents].slice(-200)),
  );
  window.localStorage.setItem(EVENT_CURSOR_KEY, String(eventCursor));
}

function normalizedStatus(value: string): TaskCenterStatus {
  return knownStatuses.has(value as TaskCenterStatus)
    ? value as TaskCenterStatus
    : "running";
}

function materialSignature(item: TaskCenterItem): string {
  return JSON.stringify({
    jobId: item.jobId,
    stepId: item.stepId,
    status: item.status,
    operationKey: item.operationKey,
    attempt: item.attempt,
    model: item.model,
    providerTaskId: item.providerTaskId,
    result: item.result,
    resultSummary: item.resultSummary,
    progress: item.progress,
    error: item.error,
    recovery: item.recovery,
    cancellation: item.cancellation,
  });
}

function bumpSignal(
  target: typeof projectSignals,
  id: string | undefined,
  operationKey: string | undefined,
) {
  if (!id) return;
  const current = target.value[id];
  target.value = {
    ...target.value,
    [id]: {
      revision: (current?.revision ?? 0) + 1,
      operationKey,
    },
  };
}

function signalMaterialChange(item: TaskCenterItem) {
  bumpSignal(projectSignals, item.projectId, item.operationKey);
  bumpSignal(sceneSignals, item.sceneId, item.operationKey);
  bumpSignal(shotSignals, item.shotId, item.operationKey);
}

function runtimeResultStatus(job: JobDto): TaskCenterStatus {
  if (job.status !== "succeeded" || !job.result || typeof job.result !== "object") {
    return normalizedStatus(job.status);
  }
  const value = (job.result as Record<string, unknown>).status;
  return typeof value === "string" && knownStatuses.has(value as TaskCenterStatus)
    ? value as TaskCenterStatus
    : "succeeded";
}

function updateItem(next: TaskCenterItem) {
  const index = items.value.findIndex((item) => item.key === next.key || (
    next.stepId
    && item.stepId === next.stepId
    && (!item.operationKey || !next.operationKey || item.operationKey === next.operationKey)
  ));
  const previous = index < 0 ? null : items.value[index];
  const merged = previous ? { ...previous, ...next } : next;
  const materialChanged = !previous
    || materialSignature(previous) !== materialSignature(merged);
  if (index < 0) items.value.push(merged);
  else items.value.splice(index, 1, merged);
  if (
    materialChanged
    && previous
    && previous.status !== merged.status
    && ["awaiting_review", "succeeded"].includes(merged.status)
  ) signalMaterialChange(merged);
  if (previous && previous.status !== merged.status) {
    const event = { item: merged, previousStatus: previous.status };
    const terminal = [
      "awaiting_review",
      "succeeded",
      "failed",
      "submission_unknown",
      "cancellation_unknown",
    ].includes(
      merged.status,
    );
    const fingerprint = merged.eventSequence
      ? `event:${merged.eventSequence}`
      : `${merged.stepId ?? merged.jobId ?? merged.key}:${merged.status}`;
    if (
      hydrated
      && terminal
      && activeStatuses.has(previous.status)
      && !notifiedTerminalEvents.has(fingerprint)
    ) {
      notifiedTerminalEvents.add(fingerprint);
      lastNotification.value = event;
    }
  }
}

function mergeRuntimeJob(job: JobDto) {
  const context = job.context ?? {};
  const result = job.result && typeof job.result === "object"
    ? job.result as Record<string, unknown>
    : {};
  const stepId = typeof result.stepId === "string" ? result.stepId : context.stepId;
  const existing = items.value.find((item) => item.jobId === job.jobId)
    ?? items.value.find((item) => Boolean(stepId)
      && item.stepId === stepId
      && (!item.operationKey || item.operationKey === context.operationKey));
  updateItem({
    key: existing?.key ?? `job:${job.jobId}`,
    jobId: job.jobId,
    stepId: stepId ?? existing?.stepId,
    kind: existing?.kind ?? job.kind,
    label: existing?.label ?? taskKindLabel(job.kind),
    status: runtimeResultStatus(job),
    projectId: existing?.projectId ?? context.projectId,
    sceneId: existing?.sceneId ?? context.sceneId,
    shotId: existing?.shotId ?? context.shotId,
    operationKey: context.operationKey ?? existing?.operationKey,
    canvasNodeId: context.canvasNodeId ?? existing?.canvasNodeId,
    canvasGroupId: context.canvasGroupId ?? existing?.canvasGroupId,
    recipeInstanceId: context.recipeInstanceId ?? existing?.recipeInstanceId,
    businessObjectId: context.businessObjectId ?? existing?.businessObjectId,
    parentStepId: context.parentStepId ?? existing?.parentStepId,
    childStepIds: job.childStepIds ?? existing?.childStepIds,
    creationMode: context.creationMode ?? existing?.creationMode,
    workflowStage: context.workflowStage ?? existing?.workflowStage,
    phase: context.phase ?? existing?.phase,
    result: job.result,
    error: job.error,
    createdAt: job.createdAt ?? existing?.createdAt,
    completedAt: job.finishedAt ?? existing?.completedAt,
    updatedAt: new Date().toISOString(),
    source: "runtime",
  });
}

function mergeWorkflowTask(task: PersistentTaskDto) {
  const existing = items.value.find((item) => item.stepId === task.stepId
    && (!item.operationKey || item.operationKey === task.operationKey))
    ?? items.value.find((item) => !item.stepId
      && item.projectId === task.projectId
      && item.sceneId === (task.sceneId ?? undefined)
      && item.shotId === (task.shotId ?? undefined)
      && item.operationKey === task.operationKey
      && activeStatuses.has(item.status));
  const durableStatus = normalizedStatus(task.status);
  updateItem({
    key: existing?.key ?? `step:${task.stepId}`,
    jobId: existing?.jobId,
    stepId: task.stepId,
    kind: task.kind,
    label: operationLabel(task.operationKey),
    status: durableStatus,
    projectId: task.projectId,
    sceneId: task.sceneId ?? undefined,
    shotId: task.shotId ?? undefined,
    operationKey: task.operationKey,
    canvasNodeId: task.canvasNodeId ?? undefined,
    canvasGroupId: task.canvasGroupId ?? undefined,
    recipeInstanceId: task.recipeInstanceId ?? undefined,
    businessObjectId: task.businessObjectId ?? undefined,
    parentStepId: task.parentStepId ?? undefined,
    childStepIds: task.childStepIds ?? [],
    creationMode: task.creationMode ?? undefined,
    workflowStage: task.workflowStage ?? undefined,
    phase: task.phase ?? undefined,
    attempt: task.attempt,
    model: task.model,
    providerTaskId: task.providerTaskId,
    recovery: task.recovery ?? undefined,
    cancellation: task.cancellation ?? undefined,
    result: existing?.result,
    resultSummary: task.resultSummary,
    progress: task.progress,
    error: task.error,
    createdAt: task.createdAt,
    completedAt: task.completedAt,
    updatedAt: task.updatedAt ?? new Date().toISOString(),
    source: "workflow",
  });
}

export async function recoverPersistentTask(
  task: Pick<TaskCenterItem, "stepId" | "recovery">,
): Promise<PersistentTaskDto> {
  if (!task.stepId) throw new Error("任务缺少持久步骤标识，无法安全恢复");
  if (!task.recovery?.allowed) {
    throw new Error(task.recovery?.disabledReason || "该任务当前不允许安全恢复");
  }
  if (recoveringStepIds.value.includes(task.stepId)) {
    throw new Error("恢复请求正在提交，请勿重复操作");
  }
  recoveringStepIds.value = [...recoveringStepIds.value, task.stepId];
  try {
    const recovered = await api.recoverPersistentTask(task.stepId);
    mergeWorkflowTask(recovered);
    persist();
    return recovered;
  } finally {
    recoveringStepIds.value = recoveringStepIds.value.filter((id) => id !== task.stepId);
  }
}

export async function cancelPersistentTask(
  task: {
    stepId?: string;
    status: string;
    providerTaskId?: string | null;
    cancellation?: TaskCancellationPolicyDto | null;
  },
  reason?: string,
): Promise<PersistentTaskDto> {
  if (!task.stepId) throw new Error("任务缺少持久步骤标识，无法取消");
  if (!task.cancellation?.allowed) {
    throw new Error(task.cancellation?.disabledReason || "该任务当前不允许取消");
  }
  if (cancellingStepIds.value.includes(task.stepId)) {
    throw new Error("取消请求正在提交，请勿重复操作");
  }
  cancellingStepIds.value = [...cancellingStepIds.value, task.stepId];
  try {
    const cancelled = await api.cancelPersistentTask(task.stepId, {
      expectedStatus: task.status,
      expectedProviderTaskId: task.providerTaskId ?? null,
      reason: reason?.trim() || null,
    });
    mergeWorkflowTask(cancelled);
    persist();
    return cancelled;
  } finally {
    cancellingStepIds.value = cancellingStepIds.value.filter((id) => id !== task.stepId);
  }
}

const pushedTaskEvents = [
  "task_queued",
  "task_running",
  "task_progress",
  "task_awaiting_review",
  "task_succeeded",
  "task_failed",
  "task_submission_unknown",
  "task_provider_cancelling",
  "task_cancelled_before_provider",
  "task_provider_cancelled",
  "task_cancellation_unknown",
] as const;
const projectionEvents = [
  "canvas_projection_changed",
  "workflow_changed",
  "template_instantiated",
  "canvas_node_created",
  "canvas_edge_created",
  "canvas_edge_deleted",
  "generation_batch_queued",
  "video_edit_recipe_created",
  "video_edit_recipe_revised",
  "video_edit_recipe_compiled",
  "video_edit_recipe_queued",
  "video_edit_candidate_ready",
  "subject_completion_ready",
  "subject_completion_applied",
  "node_generation_config_saved",
  "generation_candidate_ready",
  "video_filmstrip_queued",
  "video_filmstrip_ready",
] as const;

function handlePushedEvent(eventType: string, event: MessageEvent<string>) {
  const sequence = Number(event.lastEventId);
  if (!Number.isSafeInteger(sequence) || sequence <= eventCursor) return;
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(event.data) as Record<string, unknown>;
  } catch {
    return;
  }
  eventCursor = sequence;
  if ((projectionEvents as readonly string[]).includes(eventType)) {
    bumpSignal(
      projectSignals,
      typeof data.projectId === "string" ? data.projectId : undefined,
      typeof data.operationKey === "string" ? data.operationKey : undefined,
    );
    persist();
    return;
  }
  const stepId = typeof data.stepId === "string" ? data.stepId : undefined;
  if (!stepId) return;
  const existing = items.value.find((item) => item.stepId === stepId);
  const operationKey = typeof data.operationKey === "string"
    ? data.operationKey
    : existing?.operationKey;
  const statusByEvent: Record<string, TaskCenterStatus> = {
    task_queued: "queued",
    task_running: "running",
    task_awaiting_review: "awaiting_review",
    task_succeeded: "succeeded",
    task_failed: "failed",
    task_submission_unknown: "submission_unknown",
    task_provider_cancelling: "cancelling",
    task_cancelled_before_provider: "cancelled",
    task_provider_cancelled: "cancelled",
    task_cancellation_unknown: "cancellation_unknown",
  };
  const rawStatus = typeof data.status === "string" ? data.status : statusByEvent[eventType];
  const progress = data.progress && typeof data.progress === "object"
    ? data.progress as TaskCenterItem["progress"]
    : existing?.progress;
  const resultSummary = data.resultSummary && typeof data.resultSummary === "object"
    ? data.resultSummary as Record<string, unknown>
    : existing?.resultSummary;
  const error = data.error && typeof data.error === "object"
    ? data.error as Record<string, unknown>
    : null;
  updateItem({
    key: existing?.key ?? `step:${stepId}`,
    jobId: existing?.jobId,
    stepId,
    kind: typeof data.kind === "string" ? data.kind : existing?.kind ?? "workflow",
    label: operationKey ? operationLabel(operationKey) : existing?.label ?? "后台任务",
    status: normalizedStatus(rawStatus ?? "running"),
    projectId: typeof data.projectId === "string" ? data.projectId : existing?.projectId,
    sceneId: typeof data.sceneId === "string" ? data.sceneId : existing?.sceneId,
    shotId: typeof data.shotId === "string" ? data.shotId : existing?.shotId,
    operationKey,
    canvasNodeId: typeof data.canvasNodeId === "string"
      ? data.canvasNodeId
      : existing?.canvasNodeId,
    canvasGroupId: typeof data.canvasGroupId === "string"
      ? data.canvasGroupId
      : existing?.canvasGroupId,
    recipeInstanceId: typeof data.recipeInstanceId === "string"
      ? data.recipeInstanceId
      : existing?.recipeInstanceId,
    businessObjectId: typeof data.businessObjectId === "string"
      ? data.businessObjectId
      : existing?.businessObjectId,
    parentStepId: typeof data.parentStepId === "string"
      ? data.parentStepId
      : existing?.parentStepId,
    childStepIds: Array.isArray(data.childStepIds)
      ? data.childStepIds.filter((item): item is string => typeof item === "string")
      : existing?.childStepIds,
    creationMode: typeof data.creationMode === "string"
      ? data.creationMode
      : existing?.creationMode,
    workflowStage: typeof data.phase === "string" ? data.phase : existing?.workflowStage,
    phase: typeof data.phase === "string" ? data.phase : existing?.phase,
    attempt: existing?.attempt,
    model: existing?.model,
    providerTaskId: typeof data.providerTaskId === "string"
      ? data.providerTaskId
      : existing?.providerTaskId,
    result: existing?.result,
    resultSummary,
    progress,
    error,
    createdAt: existing?.createdAt ?? new Date().toISOString(),
    completedAt: typeof data.completedAt === "string"
      ? data.completedAt
      : existing?.completedAt,
    updatedAt: new Date().toISOString(),
    source: "workflow",
    eventSequence: sequence,
  });
  persist();
}

function connectTaskEvents() {
  if (!started || typeof window === "undefined" || typeof EventSource === "undefined") return;
  eventSource?.close();
  const source = new EventSource(api.taskCenterEventsUrl(eventCursor));
  eventSource = source;
  source.onopen = () => {
    sseConnected = true;
    connectionError.value = "";
    scheduleNextRefresh();
  };
  for (const eventType of [...pushedTaskEvents, ...projectionEvents]) {
    source.addEventListener(eventType, (event) => {
      handlePushedEvent(eventType, event as MessageEvent<string>);
    });
  }
  source.onerror = () => {
    source.close();
    if (eventSource === source) eventSource = undefined;
    sseConnected = false;
    connectionError.value = "实时推送暂不可用，已降级为任务轮询";
    void refreshTaskCenter();
    if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(connectTaskEvents, 3_000);
  };
}

function scheduleNextRefresh() {
  if (!started || typeof window === "undefined") return;
  if (timer !== undefined) window.clearTimeout(timer);
  if (document.hidden) {
    timer = undefined;
    return;
  }
  timer = window.setTimeout(
    () => void refreshTaskCenter(),
    sseConnected ? IDLE_INTERVAL_MS
      : activeCount.value > 0 ? ACTIVE_INTERVAL_MS : IDLE_INTERVAL_MS,
  );
}

function handleVisibilityChange() {
  if (!started || typeof window === "undefined") return;
  if (document.hidden) {
    if (timer !== undefined) window.clearTimeout(timer);
    timer = undefined;
  } else {
    void refreshTaskCenter();
    if (!eventSource) connectTaskEvents();
  }
}

export function registerTask(jobId: string, options: RegisterTaskOptions) {
  updateItem({
    key: `job:${jobId}`,
    jobId,
    kind: options.kind,
    label: options.label,
    status: "queued",
    projectId: options.projectId,
    sceneId: options.sceneId,
    shotId: options.shotId,
    operationKey: options.operationKey,
    canvasNodeId: options.canvasNodeId,
    canvasGroupId: options.canvasGroupId,
    recipeInstanceId: options.recipeInstanceId,
    businessObjectId: options.businessObjectId,
    parentStepId: options.parentStepId,
    creationMode: options.creationMode,
    workflowStage: options.workflowStage,
    phase: options.phase,
    updatedAt: new Date().toISOString(),
    createdAt: new Date().toISOString(),
    source: "runtime",
  });
  persist();
  void refreshTaskCenter();
}

export function requestWorkspaceRefresh(projectId: string, shotId?: string) {
  workspaceRefreshRequest.value = {
    revision: (workspaceRefreshRequest.value?.revision ?? 0) + 1,
    projectId,
    shotId,
  };
}

export async function refreshTaskCenter() {
  if (refreshing) return;
  refreshing = true;
  try {
    const payload = await api.taskCenter();
    const runtimeIds = new Set(payload.runtimeJobs.map((job) => job.jobId));
    payload.runtimeJobs.forEach(mergeRuntimeJob);
    payload.persistentTasks.forEach(mergeWorkflowTask);

    const serverStepIds = new Set(payload.persistentTasks.map((task) => task.stepId));
    items.value = items.value.filter((item) => {
      if (item.source === "workflow") return Boolean(item.stepId && serverStepIds.has(item.stepId));
      if (!item.jobId || runtimeIds.has(item.jobId)) return true;
      return Boolean(item.stepId && serverStepIds.has(item.stepId));
    });

    connectionError.value = "";
    persist();
    hydrated = true;
  } catch (error) {
    connectionError.value = error instanceof Error ? error.message : String(error);
  } finally {
    refreshing = false;
    scheduleNextRefresh();
  }
}

export function startTaskCenter() {
  if (started || typeof window === "undefined") return;
  started = true;
  document.addEventListener("visibilitychange", handleVisibilityChange);
  void refreshTaskCenter();
  connectTaskEvents();
}

export function stopTaskCenter() {
  if (!started || typeof window === "undefined") return;
  started = false;
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  if (timer !== undefined) window.clearTimeout(timer);
  if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
  eventSource?.close();
  eventSource = undefined;
  sseConnected = false;
  timer = undefined;
  reconnectTimer = undefined;
}

export function clearCompletedTasks() {
  items.value = items.value.filter((item) => activeStatuses.has(item.status)
    || item.status === "awaiting_review"
    || item.status === "submission_unknown"
    || item.status === "cancellation_unknown");
  persist();
}

export function taskKindLabel(kind: string): string {
  return ({
    story_expansion: "剧情扩写",
    story_diagnosis: "剧情诊断",
    story_rewrite: "剧情重写",
    shot_suggestions: "分镜导演",
    visual_asset_plan: "视觉资产规划",
    shot_assistance: "片段视觉与 Prompt 审稿",
    generate_scene_look: "场景视觉基准",
    generate_reference_image: "视觉参考图",
    generate_anchor: "片段开场图",
    generate_video: "视频片段",
    range_edit: "区间重拍",
    recipe_story: "治愈短片故事候选",
    recipe_story_events: "完整故事候选（兼容入口）",
    recipe_story_script: "治愈短片剧情脚本扩写",
    recipe_creative_brief: "治愈短片创意补全",
    recipe_character_design: "治愈短片角色设计",
    recipe_storyboard: "治愈短片分镜脚本",
    recipe_anchor: "治愈短片视觉锚点",
    recipe_video: "治愈短片逐镜视频",
    recipe_sequence: "治愈短片最终音画",
    story_strategy: "故事候选批次生成",
    storyboard: "分镜脚本生成",
    build_sequence: "本地成片合成",
    resume_step: "Provider 任务恢复",
  } as Record<string, string>)[kind] ?? kind;
}

function operationLabel(operationKey: string): string {
  return ({
    "director:story-expansion": "剧情扩写",
    "director:story-diagnosis": "剧情诊断",
    "director:story-rewrite": "剧情重写",
    "director:shot-suggestions": "分镜导演",
    "director:visual-asset-plan": "视觉资产规划",
    "director:shot-assistance": "片段视觉与 Prompt 审稿",
    "image:scene-look": "场景视觉基准",
    "image:anchor": "片段开场图",
    "video:shot": "视频片段",
    "video:range-edit": "区间重拍",
    "recipe:story": "治愈短片故事候选",
    "recipe:story_events": "生成完整故事候选（兼容入口）",
    "recipe:story_script": "扩写完整剧情脚本",
    "recipe:creative": "治愈短片创意补全",
    "recipe:character_design": "治愈短片角色设计",
    "recipe:character_design_validation": "三槽位引用顺序验证",
    "recipe:storyboard": "治愈短片分镜脚本",
    "recipe:anchor": "治愈短片视觉锚点",
    "recipe:video": "治愈短片逐镜视频",
    "recipe:sequence": "治愈短片最终音画",
    "canvas:story_strategy": "故事候选批次生成",
    "canvas:storyboard": "分镜脚本生成",
    "canvas-group:run": "一人一猫整组执行",
  } as Record<string, string>)[operationKey]
    ?? (operationKey.startsWith("image:reference:") ? "视觉参考图" : operationKey);
}

export function useTaskCenter() {
  return {
    items: orderedItems,
    activeCount,
    attentionCount,
    lastNotification: readonly(lastNotification),
    projectSignals: readonly(projectSignals),
    sceneSignals: readonly(sceneSignals),
    shotSignals: readonly(shotSignals),
    workspaceRefreshRequest: readonly(workspaceRefreshRequest),
    connectionError: readonly(connectionError),
    recoveringStepIds: readonly(recoveringStepIds),
    cancellingStepIds: readonly(cancellingStepIds),
  };
}
