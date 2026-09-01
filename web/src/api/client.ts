import type {
  AssetDto,
  AcceptedVisualAssetPlan,
  ProductionFlowLayoutSaveResult,
  CanvasAssetHistoryDto,
  AssetGenerationLineageDto,
  CapabilityCompilationPlan,
  CreativeStepRecord,
  CreativeDocumentDto,
  CreativeWorkflowDto,
  CreateChildCatProjectInput,
  CreateChildCatProjectResult,
  HealthDto,
  JobDto,
  GenerationInputPreviewDto,
  CharacterDesignInputPreviewDto,
  CharacterDesignValidationPreviewDto,
  PersistentTaskDto,
  TaskCenterDto,
  PreviousTailStatus,
  ProductionBoardDto,
  ProductionFlowDto,
  ProductionRecipeDefinitionDto,
  ProductionRecipeInstanceDto,
  EpisodeRulesDto,
  EpisodeVisualProfileDto,
  HumanReviewDecision,
  PromptRunDto,
  ProviderCapabilityDto,
  VideoFilmstripDto,
  VideoWorkbenchDto,
  ProjectGraph,
  ProjectSummary,
  ProjectWorkspaceShellDto,
  ReferenceBinding,
  ReferenceImageDraft,
  ReferenceRole,
  ReferenceUsage,
  RuntimeProductionConfig,
  RuntimeSettingsDto,
  ScriptWorkspaceDto,
  SceneLookDraftDto,
  SceneLookDraftEnvelope,
  SceneDto,
  SceneLookPlan,
  SceneLookPromptPreview,
  SceneLookVersion,
  SceneVisualAssetsDto,
  SequenceDto,
  SequenceTransitionDto,
  ShotAssistContext,
  ShotAssistPatch,
  ShotAssistRecord,
  ShotDto,
  ShotGenerationWorkspaceDto,
  ShotPromptPreview,
  ShotSuggestion,
  ShotSuggestionOutput,
  StoryDiagnosisOutput,
  StoryDocumentEditRequest,
  StoryboardPromptCompilationDto,
  StoryBriefInput,
  StoryExpansionOutput,
  StoryRewriteOutput,
  StoryRewriteStrategy,
  SubjectInput,
  SubjectDto,
  SubjectCompletionRunDto,
  VideoEditAnnotationInput,
  VideoEditRecipeDto,
  VisualAssetPurpose,
  VisualProfileDraft,
  VisualProfileRevisionDto,
  VisualPresetProfileDto,
} from "./types";

const BASE = "/api/v1";
const CANVAS_BASE = "/api/v2";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

export class ApiTimeoutError extends Error {
  readonly timeoutMs: number;

  constructor(timeoutMs: number) {
    super(`请求在 ${Math.round(timeoutMs / 1000)} 秒内没有响应，请检查服务状态后重试`);
    this.name = "ApiTimeoutError";
    this.timeoutMs = timeoutMs;
  }
}

const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;

export async function request<T>(
  path: string,
  init?: RequestInit,
  base = BASE,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const externalSignal = init?.signal;
  let timedOut = false;
  const forwardExternalAbort = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) forwardExternalAbort();
  else externalSignal?.addEventListener("abort", forwardExternalAbort, { once: true });
  const timeoutId = window.setTimeout(() => {
    timedOut = true;
    controller.abort(new ApiTimeoutError(timeoutMs));
  }, timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, { ...init, signal: controller.signal });
  } catch (error) {
    if (timedOut) throw new ApiTimeoutError(timeoutMs);
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", forwardExternalAbort);
  }
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = body.detail ?? detail;
    } catch {
      // Keep the transport status text for non-JSON errors.
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json<T>(path: string, method: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function canvasJson<T>(path: string, method: string, body?: unknown, headers?: HeadersInit) {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  }, CANVAS_BASE);
}

function paidJson<T>(path: string, body: unknown, confirmedRevision?: number): Promise<T> {
  if (confirmedRevision === undefined) {
    return Promise.reject(new ApiError(409, "运行配置尚未加载，请刷新页面后重新确认"));
  }
  return request<T>(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CVG-Runtime-Config-Revision": String(confirmedRevision),
    },
    body: JSON.stringify(body),
  });
}

export const api = {
  health: () => request<HealthDto>("/health"),
  runtimeSettings: () => request<RuntimeSettingsDto>("/runtime-settings"),
  updateRuntimeSettings: (
    expectedRevision: number,
    config: Omit<RuntimeProductionConfig, "revision" | "updatedAt" | "usingOverride">,
  ) => json<RuntimeSettingsDto>("/runtime-settings", "PUT", {
    expectedRevision,
    ...config,
  }),
  restoreRuntimeSettings: (expectedRevision: number) => request<RuntimeSettingsDto>(
    "/runtime-settings/override",
    {
      method: "DELETE",
      headers: { "X-CVG-Runtime-Config-Revision": String(expectedRevision) },
    },
  ),
  projects: () => request<ProjectSummary[]>("/projects"),
  project: (id: string) => request<ProjectGraph>(`/projects/${id}`),
  productionBoard: (id: string) =>
    request<ProductionBoardDto>(`/projects/${id}/production-board`),
  createProject: (body: {
    project: { title: string; firstSceneTitle: string; firstSceneText: string };
    contentDate?: string;
  }) => json<{ projectId: string }>("/projects", "POST", body),
  updateProject: (projectId: string, body: { title: string; contentDate: string }) =>
    json<ProjectSummary>(`/projects/${projectId}`, "PATCH", body),
  updateProjectDefaultReferences: (projectId: string, references: ReferenceBinding[]) =>
    json<{ projectId: string; defaultReferenceBindings: ReferenceBinding[] }>(
      `/projects/${projectId}/default-references`,
      "PUT",
      { references },
    ),
  visualProfile: (projectId: string) =>
    request<VisualProfileRevisionDto>(`/projects/${projectId}/visual-profile`),
  updateVisualProfile: (projectId: string, draft: VisualProfileDraft) =>
    json<VisualProfileRevisionDto>(`/projects/${projectId}/visual-profile`, "PUT", draft),
  restoreProjectCanonReferences: (projectId: string) =>
    json<{
      projectId: string;
      visualProfileRevisionId: string;
      visualProfileRevision: number;
      referenceCount: number;
      cleanedShotCount: number;
    }>(`/projects/${projectId}/restore-canon-references`, "POST"),
  addScene: (projectId: string, body: Record<string, unknown>) =>
    json<SceneDto>(`/projects/${projectId}/scenes`, "POST", body),
  updateScene: (sceneId: string, body: Record<string, unknown>) =>
    json<SceneDto>(`/scenes/${sceneId}`, "PATCH", body),
  deleteScene: (sceneId: string) => request<void>(`/scenes/${sceneId}`, { method: "DELETE" }),
  reorderScenes: (projectId: string, ids: string[]) =>
    json<{ saved: boolean }>(`/projects/${projectId}/scene-order`, "PUT", { ids }),
  creativeWorkflow: (sceneId: string) =>
    request<CreativeWorkflowDto>(`/scenes/${sceneId}/creative-workflow`),
  expandStory: (sceneId: string, runtimeRevision?: number) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/story-expansions`, {
      allowPaidGeneration: true,
    }, runtimeRevision),
  acceptStoryExpansion: (stepId: string, expansion: StoryExpansionOutput) =>
    json<SceneDto>(`/steps/${stepId}/accept-story-expansion`, "POST", { expansion }),
  diagnoseStory: (sceneId: string, runtimeRevision?: number) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/story-diagnoses`, {
      allowPaidGeneration: true,
    }, runtimeRevision),
  acceptStoryDiagnosis: (
    stepId: string,
    diagnosis: StoryDiagnosisOutput,
    selectedStrategy: StoryRewriteStrategy | null,
    additionalInstructions: string,
    preserveOriginal: boolean,
  ) => json<CreativeStepRecord>(`/steps/${stepId}/accept-story-diagnosis`, "POST", {
    diagnosis,
    selectedStrategy,
    additionalInstructions,
    preserveOriginal,
  }),
  rewriteStory: (sceneId: string, diagnosisStepId: string, runtimeRevision?: number) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/story-rewrites`, {
      diagnosisStepId,
      allowPaidGeneration: true,
    }, runtimeRevision),
  acceptStoryRewrite: (stepId: string, rewrite: StoryRewriteOutput) =>
    json<SceneDto>(`/steps/${stepId}/accept-story-rewrite`, "POST", { rewrite }),
  suggestShots: (sceneId: string, runtimeRevision?: number) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/shot-suggestions`, {
      allowPaidGeneration: true,
    }, runtimeRevision),
  acceptSuggestions: (
    stepId: string,
    lookPlan: SceneLookPlan | null,
    shots: ShotSuggestion[],
    applyMode: "replace" | "update_existing",
    sourceShotRevisions: Record<string, number>,
  ) => json<ShotDto[]>(`/steps/${stepId}/accept-suggestions`, "POST", {
    lookPlan,
    shots,
    applyMode,
    sourceShotRevisions,
  }),
  planVisualAssets: (
    sceneId: string,
    runtimeRevision: number | undefined,
    lineage: {
      storyboardRevisionId: string;
      structureHash: string;
      generationPlanId: string;
      generationPlanHash: string;
    },
  ) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/visual-asset-plans`, {
      allowPaidGeneration: true,
      ...lineage,
    }, runtimeRevision),
  acceptVisualAssetPlan: (stepId: string, plan: AcceptedVisualAssetPlan) =>
    json<CreativeStepRecord>(`/steps/${stepId}/accept-visual-asset-plan`, "POST", {
      plan,
    }),
  reviseVisualAssetPlan: (
    stepId: string,
    revision: number,
    plan: AcceptedVisualAssetPlan,
    note = "",
  ) => request<CreativeStepRecord>(`/steps/${stepId}/visual-asset-plan-revisions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "If-Match": String(revision),
    },
    body: JSON.stringify({ selections: plan.selections, note }),
  }),
  sceneVisualAssets: (sceneId: string) =>
    request<SceneVisualAssetsDto>(`/scenes/${sceneId}/visual-assets`),
  addShot: (sceneId: string, body: Record<string, unknown>) =>
    json<ShotDto>(`/scenes/${sceneId}/shots`, "POST", body),
  updateShot: (shotId: string, body: Record<string, unknown>) =>
    json<ShotDto>(`/shots/${shotId}`, "PATCH", body),
  deleteShot: (shotId: string) => request<void>(`/shots/${shotId}`, { method: "DELETE" }),
  reorderShots: (sceneId: string, ids: string[]) =>
    json<{ saved: boolean }>(`/scenes/${sceneId}/shot-order`, "PUT", { ids }),
  shot: (shotId: string) => request<ShotDto>(`/shots/${shotId}`),
  shotGenerationWorkspace: (shotId: string) =>
    request<ShotGenerationWorkspaceDto>(`/shots/${shotId}/generation-workspace`),
  saveAnchorBrief: (shotId: string, sourceDraftRevision: number, brief: string) =>
    json<ShotGenerationWorkspaceDto>(`/shots/${shotId}/anchor-briefs`, "POST", {
      sourceDraftRevision,
      brief,
    }),
  promptPreview: (
    shotId: string,
    target: "anchor" | "video" = "video",
    regenerationInstruction?: string,
  ) => {
    const query = new URLSearchParams({ target });
    if (regenerationInstruction) {
      query.set("regeneration_instruction", regenerationInstruction);
    }
    return request<ShotPromptPreview>(`/shots/${shotId}/prompt-preview?${query.toString()}`);
  },
  shotAssistContext: (shotId: string) =>
    request<ShotAssistContext>(`/shots/${shotId}/assist-context`),
  assistShot: (
    shotId: string,
    sourceDraftRevision: number,
    candidateAssetIds: string[],
    runtimeRevision?: number,
  ) => paidJson<{ jobId: string }>(`/shots/${shotId}/assist`, {
    sourceDraftRevision,
    candidateAssetIds,
    allowPaidGeneration: true,
  }, runtimeRevision),
  shotAssistAnalyses: (shotId: string) =>
    request<ShotAssistRecord[]>(`/shots/${shotId}/assist-analyses`),
  acceptShotAssistance: (
    stepId: string,
    sourceDraftRevision: number,
    patch: ShotAssistPatch | null,
    acceptedAnchorBrief?: string | null,
  ) => json<ShotDto>(`/steps/${stepId}/accept-shot-assistance`, "POST", {
    sourceDraftRevision,
    patch,
    acceptedAnchorBrief,
  }),
  previousTail: (shotId: string) =>
    request<PreviousTailStatus>(`/shots/${shotId}/previous-tail`),
  adoptPreviousTailAnchor: (shotId: string) =>
    json<ShotDto & { previousTail: PreviousTailStatus }>(
      `/shots/${shotId}/adopt-previous-tail-anchor`,
      "POST",
    ),
  updateReferences: (shotId: string, references: ReferenceBinding[]) =>
    json<ShotDto>(`/shots/${shotId}/references`, "PUT", { references }),
  selectSceneLook: (sceneId: string, assetId: string | null) =>
    json<SceneDto>(`/scenes/${sceneId}/look-asset`, "PUT", { assetId }),
  sceneLookDraft: (sceneId: string) =>
    request<SceneLookDraftEnvelope>(`/scenes/${sceneId}/look-draft`),
  saveSceneLookDraft: (
    sceneId: string,
    expectedRevision: number,
    draft: SceneLookDraftDto,
  ) => json<SceneLookDraftEnvelope>(`/scenes/${sceneId}/look-draft`, "PUT", {
    expectedRevision,
    draft,
  }),
  previewSceneLookPrompt: (sceneId: string) =>
    json<SceneLookPromptPreview>(`/scenes/${sceneId}/look-prompt-preview`, "POST"),
  sceneLookVersions: (sceneId: string) =>
    request<SceneLookVersion[]>(`/scenes/${sceneId}/look-versions`),
  uploadReference: async (
    projectId: string,
    usage: ReferenceUsage,
    role: ReferenceRole,
    displayName: string,
    file: File,
  ) => {
    const form = new FormData();
    form.append("usage", usage);
    form.append("role", role);
    form.append("displayName", displayName.trim() || file.name.replace(/\.[^.]+$/, ""));
    form.append("file", file);
    return request<AssetDto>(`/projects/${projectId}/references`, { method: "POST", body: form });
  },
  uploadVisualReference: async (
    target: { projectId: string; sceneId?: string | null },
    purpose: VisualAssetPurpose,
    displayName: string,
    file: File,
  ) => {
    const form = new FormData();
    form.append("purpose", purpose);
    form.append("displayName", displayName.trim() || file.name.replace(/\.[^.]+$/, ""));
    form.append("file", file);
    const path = target.sceneId
      ? `/scenes/${target.sceneId}/visual-references`
      : `/projects/${target.projectId}/visual-references`;
    return request<AssetDto>(path, { method: "POST", body: form });
  },
  generateAnchor: (
    shotId: string,
    regenerate = false,
    reason?: string,
    expectedInputHash?: string,
    runtimeRevision?: number,
  ) =>
    paidJson<{ jobId: string }>(`/shots/${shotId}/anchors`, {
      allowPaidGeneration: true,
      regenerate,
      reason,
      expectedInputHash,
    }, runtimeRevision),
  generateSceneLook: (
    sceneId: string,
    draftRevision: number,
    regenerate = false,
    reason?: string,
    expectedInputHash?: string,
    runtimeRevision?: number,
  ) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/look-images`, {
      allowPaidGeneration: true,
      draftRevision,
      regenerate,
      reason,
      expectedInputHash,
    }, runtimeRevision),
  generateReferenceImage: (
    target: { projectId: string; sceneId?: string | null },
    draft: ReferenceImageDraft,
    regenerate = false,
    reason?: string,
    expectedInputHash?: string,
    runtimeRevision?: number,
  ) => paidJson<{ jobId: string; operationKey: string }>(
    target.sceneId
      ? `/scenes/${target.sceneId}/reference-images`
      : `/projects/${target.projectId}/reference-images`,
    {
      allowPaidGeneration: true,
      regenerate,
      reason,
      draft,
      expectedInputHash,
    },
    runtimeRevision,
  ),
  previewReferenceImage: (
    target: { projectId: string; sceneId?: string | null },
    draft: ReferenceImageDraft,
    regenerate = false,
    reason?: string,
  ) => json<GenerationInputPreviewDto & { operationKey: string }>(
    target.sceneId
      ? `/scenes/${target.sceneId}/reference-images/preview`
      : `/projects/${target.projectId}/reference-images/preview`,
    "POST",
    { draft, regenerate, reason },
  ),
  generateVideo: (
    shotId: string,
    regenerate = false,
    reason?: string,
    expectedInputHash?: string,
    runtimeRevision?: number,
  ) =>
    paidJson<{ jobId: string }>(`/shots/${shotId}/videos`, {
      allowPaidGeneration: true,
      regenerate,
      reason,
      expectedInputHash,
    }, runtimeRevision),
  reviewAsset: (assetId: string, decision: "approved" | "rejected", reason: string) =>
    json(`/assets/${assetId}/review`, "POST", { decision, reason, select: true }),
  selectVersion: (shotId: string, assetId: string) =>
    json<ShotDto>(`/shots/${shotId}/versions/${assetId}/select`, "POST"),
  rangeEdit: (
    shotId: string,
    body: {
      sourceAssetId: string;
      startMs: number;
      endMs: number;
      instruction: string;
      allowPaidGeneration: true;
    },
    runtimeRevision?: number,
  ) => paidJson<{ jobId: string }>(`/shots/${shotId}/range-edits`, body, runtimeRevision),
  buildSequence: (
    projectId: string,
    draft: {
      transitions: Array<{ afterShotId: string; transition: SequenceTransitionDto }>;
      introTransition?: SequenceTransitionDto | null;
      outroTransition?: SequenceTransitionDto | null;
    },
  ) => json<{ jobId: string }>(`/projects/${projectId}/sequences`, "POST", draft),
  sequences: (projectId: string) => request<SequenceDto[]>(`/projects/${projectId}/sequences`),
  selectSequence: (projectId: string, sequenceId: string, approve: boolean) =>
    json<SequenceDto>(`/projects/${projectId}/sequences/${sequenceId}/select`, "POST", {
      approve,
    }),
  resumeStep: (stepId: string) => json<{ jobId: string }>(`/steps/${stepId}/resume`, "POST"),
  reconciliationCandidates: (stepId: string) =>
    request<Array<Record<string, unknown>>>(`/steps/${stepId}/reconciliation-candidates`),
  reconcileStep: (stepId: string, providerTaskId: string) =>
    json(`/steps/${stepId}/reconcile`, "POST", { providerTaskId }),
  jobs: () => request<JobDto[]>("/jobs"),
  job: (jobId: string) => request<JobDto>(`/jobs/${jobId}`),
  taskCenter: () => request<TaskCenterDto>("/task-center"),
  recoverPersistentTask: (stepId: string) =>
    json<PersistentTaskDto>(`/task-center/tasks/${stepId}/recover`, "POST"),
  cancelPersistentTask: (
    stepId: string,
    payload: {
      expectedStatus: string;
      expectedProviderTaskId?: string | null;
      reason?: string | null;
    },
  ) => json<PersistentTaskDto>(`/steps/${stepId}/cancellation`, "POST", payload),
  taskCenterEventsUrl: (afterEventId = 0) =>
    `${BASE}/task-center/events?afterEventId=${Math.max(0, afterEventId)}`,
  projectTasks: (projectId: string) =>
    request<PersistentTaskDto[]>(`/projects/${projectId}/tasks`),
  canon: () => request<AssetDto[]>("/canon"),
};

export const canvasApi = {
  productionRecipes: () => request<ProductionRecipeDefinitionDto[]>(
    "/production-recipes", undefined, CANVAS_BASE,
  ),
  createRecipeInstance: (
    projectId: string,
    payload: {
      recipeKey: "healing_child_cat_v1";
      theme: string;
      inspirationKey?: string;
      targetDurationSeconds: number;
      qualityTier: "quick" | "balanced" | "premium";
    },
  ) => canvasJson<ProductionRecipeInstanceDto>(
    `/projects/${projectId}/recipe-instances`, "POST", payload,
  ),
  recipeInstance: (instanceId: string) => request<ProductionRecipeInstanceDto>(
    `/recipe-instances/${instanceId}`, undefined, CANVAS_BASE,
  ),
  updateRecipeInstance: (
    instanceId: string,
    revision: number,
    payload: Record<string, unknown>,
  ) => canvasJson<ProductionRecipeInstanceDto>(
    `/recipe-instances/${instanceId}`,
    "PATCH",
    payload,
    { "If-Match": String(revision) },
  ),
  reviseGenerationPlan: (
    recipeInstanceId: string,
    planId: string,
    revision: number,
    payload: {
      provider: string;
      model: string;
      capabilityRevision: string;
      clips: Array<{ shotBeatIds: string[] }>;
      reason?: string;
    },
  ) => canvasJson<ProductionRecipeInstanceDto>(
    `/recipe-instances/${recipeInstanceId}/generation-plans/${planId}`,
    "PUT",
    payload,
    { "If-Match": String(revision) },
  ),
  runRecipeStory: (
    instanceId: string,
    acceptEstimatedCostMicros: number,
    idempotencyKey: string,
  ) =>
    canvasJson<JobDto>(
      `/recipe-instances/${instanceId}/story-runs`,
      "POST",
      { idempotencyKey, acceptEstimatedCostMicros },
    ),
  runRecipeStoryEvents: (instanceId: string, acceptEstimatedCostMicros = 0) =>
    canvasJson<JobDto>(
      `/recipe-instances/${instanceId}/story-event-runs`,
      "POST",
      { idempotencyKey: crypto.randomUUID(), acceptEstimatedCostMicros },
    ),
  runRecipeStoryScript: (instanceId: string, acceptEstimatedCostMicros = 0) =>
    canvasJson<JobDto>(
      `/recipe-instances/${instanceId}/story-script-runs`,
      "POST",
      { idempotencyKey: crypto.randomUUID(), acceptEstimatedCostMicros },
    ),
  runRecipeCreativeBrief: (instanceId: string) =>
    canvasJson<JobDto>(
      `/recipe-instances/${instanceId}/creative-brief-runs`,
      "POST",
      { idempotencyKey: crypto.randomUUID(), acceptEstimatedCostMicros: 0 },
    ),
  previewRecipeCharacterDesign: (
    instanceId: string,
    idempotencyKey: string,
    acceptEstimatedCostMicros = 0,
    characterDesignStage: "all" | "identity" | "pair_scale" = "all",
  ) => canvasJson<CharacterDesignInputPreviewDto>(
    `/recipe-instances/${instanceId}/character-design-input-preview`,
    "POST",
    { idempotencyKey, acceptEstimatedCostMicros, characterDesignStage },
  ),
  runRecipeCharacterDesign: (
    instanceId: string,
    acceptEstimatedCostMicros = 0,
    idempotencyKey: string = crypto.randomUUID(),
    expectedInputHash?: string,
    characterDesignStage: "all" | "identity" | "pair_scale" = "all",
  ) =>
    canvasJson<JobDto>(
      `/recipe-instances/${instanceId}/character-design-runs`,
      "POST",
      { idempotencyKey, acceptEstimatedCostMicros, expectedInputHash, characterDesignStage },
    ),
  previewRecipeCharacterDesignValidation: (
    instanceId: string,
    idempotencyKey: string,
  ) => canvasJson<CharacterDesignValidationPreviewDto>(
    `/recipe-instances/${instanceId}/character-design-validation-input-preview`,
    "POST",
    { idempotencyKey, acceptEstimatedCostMicros: 0 },
  ),
  runRecipeCharacterDesignValidation: (
    instanceId: string,
    acceptEstimatedCostMicros: number,
    idempotencyKey: string,
    expectedInputHash: string,
  ) => canvasJson<JobDto>(
    `/recipe-instances/${instanceId}/character-design-validation-runs`,
    "POST",
    { idempotencyKey, acceptEstimatedCostMicros, expectedInputHash },
  ),
  runRecipeStoryboard: (
    instanceId: string,
    acceptEstimatedCostMicros = 0,
    options: {
      creationMode?: "from_story" | "from_characters";
      sourceStoryRevisionId: string;
      referenceAssetIds?: string[];
      instruction?: string;
    },
  ) =>
    canvasJson<JobDto>(
      `/recipe-instances/${instanceId}/storyboard-runs`,
      "POST",
      { idempotencyKey: crypto.randomUUID(), acceptEstimatedCostMicros, ...options },
    ),
  runRecipeAnchor: (
    instanceId: string,
    shotId: string,
    acceptEstimatedCostMicros = 0,
    reason?: string,
    expectedInputHash?: string,
  ) =>
    canvasJson<JobDto>(
      `/recipe-instances/${instanceId}/shots/${shotId}/anchor-runs`,
      "POST",
      { idempotencyKey: crypto.randomUUID(), acceptEstimatedCostMicros, reason, expectedInputHash },
    ),
  runRecipeVideo: (
    instanceId: string,
    shotId: string,
    acceptEstimatedCostMicros = 0,
    reason?: string,
    expectedInputHash?: string,
  ) =>
    canvasJson<JobDto>(
      `/recipe-instances/${instanceId}/shots/${shotId}/video-runs`,
      "POST",
      { idempotencyKey: crypto.randomUUID(), acceptEstimatedCostMicros, reason, expectedInputHash },
    ),
  runRecipeSequence: (
    instanceId: string,
    acceptEstimatedCostMicros = 0,
    sequence: {
      transitions: Array<{ afterShotId: string; transition: SequenceTransitionDto }>;
      introTransition?: SequenceTransitionDto | null;
      outroTransition?: SequenceTransitionDto | null;
    } = { transitions: [] },
  ) =>
    canvasJson<JobDto>(
      `/recipe-instances/${instanceId}/sequence-runs`,
      "POST",
      { idempotencyKey: crypto.randomUUID(), acceptEstimatedCostMicros, ...sequence },
    ),
  reviewRecipeTarget: (payload: {
    recipeInstanceId: string;
    targetType: "creative_brief" | "story_event" | "story_revision" | "episode_rules" | "character_design" | "storyboard_structure" | "generation_plan" | "storyboard_package" | "storyboard_revision" | "shot_beat" | "anchor_asset" | "video_asset" | "final_sequence";
    targetId: string;
    targetRevision?: number;
    targetHash?: string;
    decision: HumanReviewDecision;
    blockingDiagnosticPresent?: boolean;
    issues?: string[];
    reason?: string;
    episodeRules?: EpisodeRulesDto;
  }) => canvasJson<Record<string, unknown>>("/review-decisions", "POST", payload),
  confirmStoryboardProductionPlan: (
    instanceId: string,
    payload: {
      idempotencyKey: string;
      storyboardRevisionId: string;
      storyboardRevision: number;
      structureHash: string;
      generationPlanId: string;
      generationPlanRevision: number;
      generationPlanHash: string;
      reason?: string;
    },
  ) => canvasJson<Record<string, unknown>>(
    `/recipe-instances/${instanceId}/storyboard-production-confirmations`,
    "POST",
    payload,
  ),
  workspaceShell: (projectId: string, signal?: AbortSignal) => request<ProjectWorkspaceShellDto>(
    `/projects/${projectId}/workspace-shell`,
    signal ? { signal } : undefined,
    CANVAS_BASE,
  ),
  scriptWorkspace: (projectId: string, signal?: AbortSignal) => request<ScriptWorkspaceDto>(
    `/projects/${projectId}/script-workspace`,
    signal ? { signal } : undefined,
    CANVAS_BASE,
  ),
  productionFlow: (projectId: string, signal?: AbortSignal) => request<ProductionFlowDto>(
    `/projects/${projectId}/production-flow`,
    signal ? { signal } : undefined,
    CANVAS_BASE,
  ),
  saveProductionFlowLayout: (
    projectId: string,
    revision: number,
    payload: {
      nodes: Array<{ nodeId: string; x: number; y: number }>;
      viewport: { x: number; y: number; zoom: number };
      operations?: Array<Record<string, unknown>>;
    },
  ) => canvasJson<ProductionFlowLayoutSaveResult>(
    `/projects/${projectId}/production-flow/layout`,
    "PATCH",
    payload,
    { "If-Match": String(revision) },
  ),
  videoWorkbench: (projectId: string, signal?: AbortSignal) => request<VideoWorkbenchDto>(
    `/projects/${projectId}/video-workbench`,
    signal ? { signal } : undefined,
    CANVAS_BASE,
  ),
  createChildCatProject: (payload: CreateChildCatProjectInput) =>
    canvasJson<CreateChildCatProjectResult>("/projects", "POST", payload),
  saveBrief: (projectId: string, brief: StoryBriefInput) =>
    canvasJson<Record<string, unknown>>(`/projects/${projectId}/brief`, "PUT", brief),
  createSubject: (projectId: string, subject: SubjectInput) =>
    canvasJson<Record<string, unknown>>(`/projects/${projectId}/subjects`, "POST", subject),
  subjects: (projectId: string) =>
    request<SubjectDto[]>(`/projects/${projectId}/subjects`, undefined, CANVAS_BASE),
  createSubjectCompletionRun: (
    projectId: string,
    subjectId: string,
    instruction: string,
  ) => canvasJson<SubjectCompletionRunDto>(
    `/projects/${projectId}/subject-assistant-runs`,
    "POST",
    { subjectId, instruction, idempotencyKey: crypto.randomUUID() },
  ),
  subjectCompletionRun: (runId: string) =>
    request<SubjectCompletionRunDto>(
      `/subject-assistant-runs/${runId}`,
      undefined,
      CANVAS_BASE,
    ),
  applySubjectCompletion: (
    runId: string,
    acceptedFields: string[],
    finalDraft: SubjectInput,
  ) => canvasJson<Record<string, unknown>>(
    `/subject-assistant-runs/${runId}/apply`,
    "POST",
    { acceptedFields, finalDraft },
  ),
  assets: (projectId: string, kind?: "image" | "video" | "audio", signal?: AbortSignal) =>
    request<CanvasAssetHistoryDto[]>(
      `/projects/${projectId}/assets${kind ? `?kind=${kind}` : ""}`,
      signal ? { signal } : undefined,
      CANVAS_BASE,
    ),
  visualPresets: (signal?: AbortSignal) => request<VisualPresetProfileDto[]>(
    "/visual-presets",
    signal ? { signal } : undefined,
    CANVAS_BASE,
  ),
  applyVisualPreset: (projectId: string, presetKey: string) =>
    canvasJson<{
      preset: VisualPresetProfileDto;
      visualProfile: EpisodeVisualProfileDto;
      canvasNodeId: string;
      canvasNodeIds: string[];
      reusedAssetIds: string[];
    }>(`/projects/${projectId}/visual-presets/${presetKey}/apply`, "POST", {}),
  episodeVisualProfile: (projectId: string, signal?: AbortSignal) => request<EpisodeVisualProfileDto>(
    `/projects/${projectId}/visual-profile`,
    signal ? { signal } : undefined,
    CANVAS_BASE,
  ),
  updateEpisodeVisualProfile: (
    projectId: string,
    revision: number,
    draft: VisualProfileDraft,
  ) => canvasJson<EpisodeVisualProfileDto>(
    `/projects/${projectId}/visual-profile`,
    "PATCH",
    draft,
    { "If-Match": String(revision) },
  ),
  createVideoFilmstrip: (assetId: string, frameCount = 12) =>
    canvasJson<VideoFilmstripDto>(
      `/assets/${assetId}/filmstrip-runs?frameCount=${frameCount}`,
      "POST",
    ),
  videoFilmstrip: (assetId: string, frameCount = 12) =>
    request<VideoFilmstripDto>(
      `/assets/${assetId}/filmstrip?frameCount=${frameCount}`,
      undefined,
      CANVAS_BASE,
    ),
  providerCapabilities: (mediaKind?: "image" | "video" | "audio" | "video_edit") =>
    request<ProviderCapabilityDto[]>(
      `/provider-capabilities${mediaKind ? `?mediaKind=${mediaKind}` : ""}`,
      undefined,
      CANVAS_BASE,
    ),
  runStoryStrategies: (projectId: string, rewriteInstruction?: string) =>
    canvasJson<JobDto>(
      `/projects/${projectId}/story-strategy-runs`,
      "POST",
      {
        idempotencyKey: crypto.randomUUID(),
        rewriteInstruction: rewriteInstruction || undefined,
      },
    ),
  approveStory: (revisionId: string) =>
    canvasJson<Record<string, unknown>>(`/story-revisions/${revisionId}/approve`, "POST", {}),
  editStoryRevision: (revisionId: string, payload: StoryDocumentEditRequest) =>
    canvasJson<CreativeDocumentDto>(`/story-revisions/${revisionId}/edits`, "POST", payload),
  createStoryboard: (
    projectId: string,
    options: {
      creationMode?: "from_story" | "from_characters";
      sourceStoryRevisionId: string;
      referenceAssetIds?: string[];
      instruction?: string;
    },
  ) => canvasJson<JobDto>(
    `/projects/${projectId}/storyboard-runs`,
    "POST",
    { idempotencyKey: crypto.randomUUID(), ...options },
  ),
  updateBeat: (beatId: string, revision: number, patch: Record<string, unknown>) =>
    canvasJson<Record<string, unknown>>(
      `/shot-beats/${beatId}`,
      "PATCH",
      patch,
      { "If-Match": String(revision) },
    ),
  replaceBeatReferences: (
    beatId: string,
    referenceRevision: number,
    bindings: Array<{
      assetId: string;
      semanticRole: "composition" | "pose" | "wardrobe" | "prop" | "environment_detail";
      instruction: string;
      ordinal: number;
    }>,
  ) => canvasJson<Record<string, unknown>>(
    `/shot-beats/${beatId}/reference-bindings`,
    "PUT",
    { bindings },
    { "If-Match": String(referenceRevision) },
  ),
  saveManualStoryboard: (
    projectId: string,
    revision: number,
    shots: Array<Record<string, unknown>>,
    healingRecipe: boolean,
  ) => canvasJson<Record<string, unknown>>(
    `/projects/${projectId}/storyboard-drafts`,
    "PUT",
    { shots, healingRecipe },
    { "If-Match": String(revision) },
  ),
  compileStoryboardPrompts: (
    projectId: string,
    payload: {
      storyRevisionId: string;
      storyboardRevisionId?: string;
      structureHash?: string;
      generationPlanId?: string;
      generationPlanHash?: string;
      visualProfileRevisionId: string;
      healingRecipe: boolean;
      shots: Array<Record<string, unknown>>;
    },
  ) => canvasJson<StoryboardPromptCompilationDto>(
    `/projects/${projectId}/storyboard-prompt-compilations`,
    "POST",
    payload,
  ),
  createGenerationBatch: (payload: {
    projectId: string;
    canvasNodeId: string;
    mediaKind: "image" | "video";
    candidateCount: number;
    provider?: string;
    model?: string;
    idempotencyKey: string;
    expectedInputHash: string;
    input: Record<string, unknown>;
  }) => canvasJson<Record<string, unknown>>("/generation-batches", "POST", payload),
  assetGenerationLineage: (assetId: string) =>
    request<AssetGenerationLineageDto>(
      `/assets/${assetId}/generation-lineage`,
      undefined,
      CANVAS_BASE,
    ),
  createVideoEditRecipe: (payload: {
    projectId: string;
    sourceAssetId: string;
    startMs: number;
    endMs: number;
    instruction: string;
    referenceAssetIds: string[];
    annotations: VideoEditAnnotationInput[];
  }) => canvasJson<VideoEditRecipeDto>("/video-edit-recipes", "POST", payload),
  updateVideoEditRecipe: (
    recipeId: string,
    revision: number,
    payload: Partial<Pick<VideoEditRecipeDto,
      "startMs" | "endMs" | "instruction" | "referenceAssetIds">>,
  ) => canvasJson<VideoEditRecipeDto>(
    `/video-edit-recipes/${recipeId}`,
    "PATCH",
    payload,
    { "If-Match": String(revision) },
  ),
  replaceVideoEditAnnotations: (
    recipeId: string,
    revision: number,
    annotations: VideoEditAnnotationInput[],
  ) => canvasJson<VideoEditRecipeDto>(
    `/video-edit-recipes/${recipeId}/annotations`,
    "PUT",
    { annotations },
    { "If-Match": String(revision) },
  ),
  compileVideoEditRecipe: (recipeId: string) =>
    canvasJson<CapabilityCompilationPlan>(
      `/video-edit-recipes/${recipeId}/compile`, "POST", {},
    ),
  submitVideoEditRecipe: (
    recipeId: string,
    idempotencyKey: string,
    acceptEstimatedCostMicros: number,
  ) => canvasJson<Record<string, unknown>>(
    `/video-edit-recipes/${recipeId}/submit`,
    "POST",
    { idempotencyKey, acceptEstimatedCostMicros },
  ),
  promptRun: (promptId: string) =>
    request<PromptRunDto>(`/prompt-runs/${promptId}`, undefined, CANVAS_BASE),
};

export type SuggestionJobResult = { stepId: string; output: ShotSuggestionOutput };

export function assetContentUrl(assetId: string): string {
  return `${BASE}/assets/${assetId}/content`;
}
