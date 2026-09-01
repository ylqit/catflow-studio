export type AnchorMode = "text_only" | "existing" | "generate";
export type SceneLookUsage = "off" | "appearance_only" | "full_reference" | "derive_anchor";
export type ReferenceUsage = "approved_anchor" | "generation_reference";
export type ReferenceRole = "identity" | "style" | "scene" | "prop" | "composition";
export type ReferenceTarget = "anchor" | "video" | "both";
export type StoryMode = "single" | "multi";
export type StoryRewriteStrategy = "conservative" | "balanced" | "creative";
export type EnvironmentStyle = "outdoor" | "indoor";

export type WorkspaceModuleId = "script" | "assets" | "production";
export type WorkspaceStatus = "blocked" | "stale" | "needs_review" | "active" | "complete" | "ready";

export interface WorkspaceModuleDto {
  id: WorkspaceModuleId;
  title: string;
  order: number;
  status: WorkspaceStatus;
  progress?: number | null;
  attentionCount: number;
  primaryArtifactId?: string | null;
  blocker?: string | null;
  nextAction?: { label: string; moduleId: WorkspaceModuleId } | null;
}

export interface ProjectWorkspaceShellDto {
  project: { id: string; title: string; status: string; updatedAt: string };
  modules: WorkspaceModuleDto[];
  recommendedModuleId: WorkspaceModuleId;
  activeTaskSummary: {
    activeCount: number;
    attentionCount: number;
    latestTaskId?: string | null;
    latestStatus?: string | null;
  };
}

export interface StoryWorkspaceDocumentDto {
  id: string;
  title: string;
  body: string;
  summary?: string | null;
  revision: number;
  status: string;
  source: "ai" | "manual" | "unknown";
  warnings: Array<{ code: string; severity: "warning" | "blocker"; message: string; targetId?: string | null }>;
}

export interface ScriptWorkspaceDto {
  brief?: Record<string, unknown> | null;
  documents: StoryWorkspaceDocumentDto[];
  currentStoryId?: string | null;
  recipeInstanceId?: string | null;
}

export type ProductionFlowNodeKind =
  | "script"
  | "director_plan"
  | "assets"
  | "storyboard_table"
  | "storyboard"
  | "workbench";

export interface ProductionFlowNodeDto {
  id: string;
  kind: ProductionFlowNodeKind;
  title: string;
  subtitle: string;
  status: WorkspaceStatus;
  position: { x: number; y: number };
  data: Record<string, unknown>;
}

export interface ProductionFlowDto {
  revision: number;
  nodes: ProductionFlowNodeDto[];
  edges: Array<{ id: string; source: string; target: string }>;
  viewport: { x: number; y: number; zoom: number };
  activeStoryboardRevisionId?: string | null;
  activeTrackId?: string | null;
  shotOrder: string[];
}

export interface VideoWorkbenchReferenceDto {
  assetId: string;
  title: string;
  semanticRole: string;
  ordinal: number;
  providerEligible: boolean;
  contentUrl?: string | null;
  sourceRevision?: number | null;
}

export interface VideoWorkbenchVersionDto {
  assetId: string;
  status: string;
  contentUrl?: string | null;
  createdAt: string;
  selected: boolean;
}

export interface VideoWorkbenchTrackDto {
  id: string;
  shotIds: string[];
  title: string;
  durationSeconds: number;
  orderedReferences: VideoWorkbenchReferenceDto[];
  prompt: string;
  providerConfig: Record<string, unknown>;
  task?: Record<string, unknown> | null;
  versions: VideoWorkbenchVersionDto[];
  selectedVersionId?: string | null;
}

export interface VideoWorkbenchDto {
  activeTrackId?: string | null;
  tracks: VideoWorkbenchTrackDto[];
  approvedReferences: VideoWorkbenchReferenceDto[];
  timeline?: Record<string, unknown> | null;
  exportSummary?: Record<string, unknown> | null;
}

export interface CreateChildCatProjectInput {
  title: string;
  contentDate?: string | null;
  brief: {
    body: string;
    durationSeconds: number;
    aspectRatio: "9:16" | "16:9" | "1:1";
    qualityTier: "quick" | "balanced" | "premium";
  };
  childCanonProfileId: string;
  catCanonProfileId: string;
  styleBoardAssetId: string;
}

export interface CreateChildCatProjectResult {
  projectId: string;
  briefId: string;
  recipeInstanceId: string;
  subjectIds: Record<string, string>;
  providerCallCount: number;
}

export interface CreativeDocumentDto {
  id: string;
  title: string;
  body: string;
  summary: string | null;
  revision: number;
  status: string;
  source: "ai" | "manual" | "unknown";
  contractKind: "creative_text" | "legacy_structured";
  warnings: Array<{
    code: string;
    severity: "warning" | "blocker";
    message: string;
    targetId?: string | null;
  }>;
  legacyDetails: {
    scenes: Array<Record<string, unknown>>;
    scorecard: Record<string, unknown> | null;
  } | null;
}

export interface StoryDocumentEditRequest {
  title: string;
  body: string;
  summary: string | null;
  expectedRevision: number;
  idempotencyKey: string;
}

export type StoryboardPromptReferenceRole =
  | "identity"
  | "style"
  | "appearance"
  | "environment"
  | "prop"
  | "composition";

export interface StoryboardPromptReferenceBindingDto {
  assetId: string;
  role: StoryboardPromptReferenceRole;
  purpose: string;
  source: "canon" | "character_design" | "scene" | "shot";
  semanticKey?: string | null;
  title?: string | null;
  sha256: string;
}

export interface CanvasDiagnosticDto {
  code: string;
  severity: "warning" | "blocker";
  message: string;
  targetId?: string | null;
}

export interface StoryboardPromptCompilationShotDto {
  beatId?: string | null;
  order: number;
  finalPrompt: string;
  promptId?: string | null;
  referenceBindings: StoryboardPromptReferenceBindingDto[];
  warnings: string[];
  diagnostics: CanvasDiagnosticDto[];
  blockers: string[];
  estimatedCost: { currency: string; amountMicros: number };
  inputHash: string;
}

export interface StoryboardPromptCompilationDto {
  projectId: string;
  storyRevisionId: string;
  visualProfileRevisionId: string;
  status: "compiled" | "blocked";
  shots: StoryboardPromptCompilationShotDto[];
}

export type RecipePhaseKey =
  | "creative"
  | "story"
  | "character_design"
  | "storyboard"
  | "render"
  | "export"
  | "complete";

export type StoryboardCreationMode = "from_story" | "from_characters" | "manual";

export interface ProductionFlowLayoutSaveResult {
  projectId: string;
  layoutVersion: number;
  syncStatus: "saved";
  viewport: { x: number; y: number; zoom: number };
  rebasedFromVersion: number | null;
}

export type ProductionRecipeKey = "healing_child_cat_v1";
export type QualityTier = "quick" | "balanced" | "premium";
export type CatBehaviorMode = "natural" | "light_anthropomorphic";
export type RecipeStage = "concept" | "storyboard" | "anchors" | "video" | "sequence" | "complete";
export type HumanReviewDecision = "approve" | "request_changes" | "override";

export interface EpisodeRulesDto {
  personWardrobe: string;
  timeWeather: string;
  mainScene: string;
  environment: "indoor" | "outdoor";
  coreProps: string[];
  catBehaviorMode: CatBehaviorMode;
  soundPlan: {
    ambient: string[];
    foley: string[];
    musicMood: string;
    dialoguePolicy: "none";
  };
  stylePositive: string[];
  styleExcluded: string[];
  canonProfileId: string;
}

export interface RecipeAssetCandidateDto {
  id: string;
  sha256: string | null;
  status: string;
  mediaType: "image" | "video";
  contentUrl: string;
  qc?: Record<string, unknown> | null;
  diagnosticStatus: "not_run" | "passed" | "failed";
  diagnostics: Array<Record<string, unknown>>;
}

export interface RecipeShotDto {
  beatId: string | null;
  shotId: string | null;
  generationPlanId?: string | null;
  editorialShotIds?: string[];
  title: string;
  durationSeconds: number;
  status: string;
  mode?: "single_shot" | "multi_shot";
  temporalBeats: Array<Record<string, unknown>>;
  promptId?: string | null;
  promptCompiled?: boolean;
  selectedAnchorAssetId: string | null;
  selectedVideoAssetId: string | null;
  anchorCandidates: RecipeAssetCandidateDto[];
  videoCandidates: RecipeAssetCandidateDto[];
}

export interface EditorialShotDto {
  id: string;
  sceneId: string;
  sceneTitle?: string | null;
  storyboardRevisionId: string | null;
  generationClipId: string | null;
  order: number;
  revision: number;
  referenceBindingRevision: number;
  referenceBindings: GenerationReferenceBindingDraftDto[];
  title: string;
  durationSeconds: number;
  direction: string;
  /** Legacy Canvas payload fallback; new clients persist and compile direction. */
  action?: string;
  visualDescription: string;
  childAction: string;
  catAction: string;
  spatialRelation: string;
  contactOcclusion: string;
  shotSize: string;
  camera: string;
  lighting: string;
  dialogue: string;
  soundEffect: string;
  musicIntent: string;
  wardrobeState: string;
  propState: string;
  continuityIn: string;
  continuityOut: string;
  cutIntent: "continuous" | "soft_cut" | "hard_cut";
  status: string;
  staleReason?: string | null;
  promptId?: string | null;
}

export interface StoryboardRevisionDto {
  id: string;
  storyRevisionId: string;
  revision: number;
  status: "draft" | "structure_approved" | "production_approved" | "changes_requested" | "superseded";
  structureHash: string;
  inputBindings?: Array<Record<string, unknown>>;
  approvedStructureAt?: string | null;
  productionPackageHash?: string | null;
  productionPackageReviewHash?: string | null;
  productionApprovedAt?: string | null;
  shotCount: number;
  totalDurationSeconds: number;
}

export interface VideoModelCapabilityDto {
  provider: string;
  model: string;
  capabilityRevision: string;
  minimumDurationSeconds: number;
  maximumDurationSeconds: number;
  supportedDurations: number[];
  supportsMultiShot: boolean;
  maximumEditorialShots: number;
  maximumImageReferences: number;
  maximumVideoReferences: number;
  maximumAudioReferences: number;
  supportsFirstFrame: boolean;
  firstFrameExcludesReferences: boolean;
  supportsNativeAudio: boolean;
  supportedResolutions: string[];
  supportedAspectRatios: string[];
  imageCallCostMicros?: number | null;
  videoCallCostMicros?: number | null;
}

export interface GenerationClipDto {
  id: string;
  title: string;
  durationSeconds: number;
  mode: "single_shot" | "multi_shot";
  editorialShotIds: string[];
}

export interface GenerationPlanDto {
  id: string;
  storyboardRevisionId: string;
  revision: number;
  status: "proposed" | "approved" | "stale";
  provider?: string;
  model?: string;
  capabilityRevision?: string;
  inputHash?: string;
  estimatedImageCallCount: number;
  estimatedVideoCallCount: number;
  estimatedCostMicros?: number | null;
  warnings: string[];
  blockers: string[];
  reason: string;
  capability: VideoModelCapabilityDto;
  clips: GenerationClipDto[];
  approvedAt?: string | null;
}

export interface RecipeSequenceCandidateDto {
  id: string;
  revision: number;
  status: "content_review" | "approved" | "rejected";
  durationMs: number;
  audioPolicy: "native_fades";
  renderedAssetId: string | null;
  contentUrl: string | null;
  sha256: string | null;
  qc?: Record<string, unknown> | null;
}

export interface RecipeStoryCandidateDto {
  id: string;
  revision: number;
  strategy: string;
  status: "candidate" | "approved";
  title: string;
  body: string;
  summary?: string | null;
  source: "ai" | "manual" | "unknown";
  contractKind: "creative_text" | "legacy_structured";
  warnings: Array<{
    code: string;
    severity: "warning" | "blocker";
    message: string;
    targetId?: string | null;
  }>;
  legacyDetails?: Record<string, unknown> | null;
  logline: string;
  synopsis: string;
  episodeRules: EpisodeRulesDto | null;
  sourceEventCandidateId?: string | null;
}

export interface RecipeStoryEventCandidateDto {
  id: string;
  revision: number;
  batchId: string;
  candidateIndex: number;
  strategy: string;
  status: "candidate" | "selected" | "superseded";
  title: string;
  premise: string;
  childAction: string;
  catParticipation: string;
  smallChange: string;
  warmEnding: string;
  suggestedScenes: Array<{
    sceneKey: string;
    title: string;
    purpose: string;
    environment: "indoor" | "outdoor";
    timeWeather: string;
    transitionReason?: string | null;
  }>;
  durationFitSummary: string;
  requiresSceneChange: boolean;
  sceneChangePurpose?: string | null;
  catBehaviorModeSuggestion: CatBehaviorMode;
  scoreAverage: number | null;
  scoreRationale: string | null;
  selectedAt?: string | null;
  createdAt?: string | null;
}

export interface RecipeStoryWorkflowDto {
  currentStep: 1 | 2 | 3 | 4;
  totalSteps: 2 | 4;
  status:
    | "generate_candidates"
    | "select_story"
    | "complete"
    | "generate_events"
    | "select_event"
    | "expand_script"
    | "approve_script";
  legacy?: boolean;
  selectedEventId?: string | null;
  scriptRevisionId?: string | null;
}

export interface ProductionRecipeDefinitionDto {
  key: ProductionRecipeKey;
  title: string;
  description: string;
  defaultDurationSeconds: number;
  minimumDurationSeconds: number;
  maximumDurationSeconds: number;
  aspectRatio: "9:16";
  resolution: "720p";
  storyCandidateCount: number;
}

export interface ProductionRecipeInstanceDto {
  id: string;
  projectId: string;
  recipeKey: ProductionRecipeKey;
  recipeVersion: number;
  revision: number;
  theme: string;
  inspirationKey?: string | null;
  targetDurationSeconds: number;
  qualityTier: QualityTier;
  canonProfileId: string;
  stage: RecipeStage;
  phase?: RecipePhaseKey;
  groupId?: string;
  lifecycleStatus?: "active" | "archived";
  shotDurations: number[];
  currentBlocker: string | null;
  primaryAction: string;
  estimatedCostMicros?: number | null;
  storyGenerationEstimatedCostMicros?: number | null;
  costEstimateStatus?: "metered" | "unmetered_paid";
  costEstimateLabel?: string;
  reviewStages: Array<{
    key: Exclude<RecipePhaseKey, "complete"> | "anchors" | "video" | "sequence";
    complete: boolean;
  }>;
  progress: {
    creativeCompleted?: boolean;
    creativeApproved?: boolean;
    storyApproved: boolean;
    characterDesignApproved?: boolean;
    storyboardApproved?: boolean;
    storyboardStructureApproved?: boolean;
    generationPlanApproved?: boolean;
    storyboardPackageApproved?: boolean;
    episodeRulesLocked: boolean;
    shotCount: number;
    storyboardShotCount?: number;
    generationClipCount?: number;
    approvedAnchorCount: number;
    approvedVideoCount: number;
    sequenceReady: boolean;
    finalApproved: boolean;
  };
  episodeRules?: EpisodeRulesDto | null;
  shots: RecipeShotDto[];
  editorialShots?: EditorialShotDto[];
  storyboard?: StoryboardRevisionDto | null;
  generationPlan?: GenerationPlanDto | null;
  sequenceCandidate?: RecipeSequenceCandidateDto | null;
  storyCandidates?: RecipeStoryCandidateDto[];
  storyEvents?: RecipeStoryEventCandidateDto[];
  selectedStoryEventId?: string | null;
  storyWorkflow?: RecipeStoryWorkflowDto;
  creativeBrief?: {
    id: string;
    revision: number;
    theme: string;
    audience: string;
    genre: string;
    tone: string;
    aspectRatio: string;
    targetDurationSeconds: number;
    constraints: string[];
  } | null;
  characterDesign?: {
    id: string;
    revision: number;
    status: "generating" | "awaiting_review" | "approved" | "stale";
    sourceStoryRevisionId: string;
    slots: Record<"child" | "cat" | "pair_scale", Array<{
      bindingId: string;
      assetId: string;
      candidateIndex: number;
      semanticRole: "appearance" | "pose" | "scale" | "composition";
      selected: boolean;
      status: string;
      sha256: string;
      contentUrl: string;
      validationOnly?: boolean;
      inputHash?: string | null;
      providerOrderEvidence?: "frozen" | "selected_only" | "unknown" | null;
    }>>;
  } | null;
  storyboardHash?: string | null;
}

export interface StoryBriefInput {
  theme: string;
  audience: string;
  genre: string;
  tone: string;
  aspectRatio: "9:16" | "16:9" | "1:1";
  targetDurationSeconds: number;
  constraints: string[];
}

export interface SubjectInput {
  name: string;
  kind: "person" | "animal" | "object" | "location" | "style" | "product";
  role: "protagonist" | "co_protagonist" | "support" | "prop" | "environment" | "hero_product";
  identityAnchors: string[];
  immutableTraits: string[];
  relationshipNotes?: string;
  dramaticFunction?: string;
  visualRisks?: string[];
  references?: Array<{
    assetId: string;
    semanticRole: "front" | "side" | "back" | "turnaround" | "expression" | "full_body" | "outfit" | "packshot_front" | "label_detail" | "material" | "size_scale" | "usage_scene" | "other";
    instruction: string;
  }>;
}

export interface SubjectDto extends SubjectInput {
  id: string;
  projectId: string;
  revisionId: string;
  revision: number;
  status: string;
}

export type SubjectCompletionField =
  | "identityAnchors"
  | "immutableTraits"
  | "relationshipNotes"
  | "dramaticFunction"
  | "visualRisks";

export interface SubjectCompletionProposalDto {
  identityAnchors: string[];
  immutableTraits: string[];
  relationshipNotes: string;
  dramaticFunction: string;
  visualRisks: string[];
  rationale: Record<string, string>;
  warnings: string[];
}

export interface SubjectCompletionRunDto {
  id: string;
  status: "pending" | "awaiting_review" | "applied" | "failed";
  subjectId?: string;
  sourceRevisionId?: string;
  missingFields: SubjectCompletionField[];
  proposal?: SubjectCompletionProposalDto | null;
  promptId?: string | null;
  error?: Record<string, unknown> | null;
}

export interface ReferenceAuthorityDto {
  role: "identity" | "episode_appearance" | "pair_scale" | "environment" | "style_source" | "style_board";
  providerEligible: boolean;
  priority: number;
  lockedTraits: string[];
  mutableTraits: string[];
  forbiddenTransfer: string[];
}

export interface ActualReferenceBindingDto {
  assetId: string;
  sourceNodeId?: string | null;
  sourceType?: string;
  subjectRevisionId?: string | null;
  semanticRole: string;
  purpose?: string;
  instruction?: string;
  ordinal?: number;
  locked?: boolean;
  sha256?: string | null;
  providerIncluded: boolean;
  omissionReason?: string | null;
  providerSlot?: string | null;
  origin?: string;
  title?: string | null;
  contentUrl?: string | null;
  evidenceLevel?: "frozen" | "selected_only" | "unknown";
  authority?: ReferenceAuthorityDto | null;
}

export interface GenerationReferenceBindingDraftDto {
  assetId: string;
  sourceNodeId?: string | null;
  sourceType?: string;
  subjectRevisionId?: string | null;
  semanticRole: string;
  purpose: string;
  instruction: string;
  ordinal: number;
  locked: boolean;
  sha256?: string | null;
  authority?: ReferenceAuthorityDto | null;
}

export interface GenerationInputPreviewDto {
  provider: string;
  model: string;
  mode: string;
  capabilityRevision: string;
  prompt: string;
  references: ActualReferenceBindingDto[];
  blockers: string[];
  warnings: string[];
  estimatedCostMicros?: number | null;
  inputHash: string;
}

export interface CharacterDesignInputPreviewDto {
  recipeInstanceId: string;
  characterDesignRevisionId: string;
  candidateCountPerSlot: number;
  stage: "all" | "identity" | "pair_scale";
  slots: Array<GenerationInputPreviewDto & { slot: "child" | "cat" | "pair_scale" }>;
  estimatedCostMicros?: number | null;
  inputHash: string;
}

export interface CharacterDesignValidationPreviewDto extends CharacterDesignInputPreviewDto {
  mode: "validation_only";
  baseCharacterDesignRevisionId: string;
  preservesApprovedSelection: true;
  providerCallCount: 3;
}

export interface AssetGenerationLineageDto {
  assetId: string;
  assetSha256: string;
  contentUrl: string;
  batchId?: string | null;
  stepId?: string | null;
  promptId?: string | null;
  prompt?: string | null;
  provider?: string | null;
  model?: string | null;
  providerTaskId?: string | null;
  inputHash?: string | null;
  providerOrderEvidence: "frozen" | "selected_only" | "unknown";
  providerOrderNotice?: string | null;
  references: ActualReferenceBindingDto[];
}

export interface GenerationReferenceAnnotationDto {
  assetId: string;
  tool: "rectangle" | "brush" | "arrow" | "text" | "marker" | "eraser";
  points: Array<{ x: number; y: number }>;
  label: string;
}

export interface GenerationCapabilityDto {
  provider: string;
  model: string;
  modes: string[];
  aspectRatios: string[];
  resolutions: string[];
  durations: number[];
  candidateCounts: number[];
  audio: boolean;
  cameraMotions?: Array<{
    value: string;
    label: string;
    enabled?: boolean;
    disabledReason?: string;
  }>;
  estimatedCostMicros?: number | null;
}

export interface ProviderCapabilityDto {
  id?: string;
  provider: string;
  model: string;
  mediaKind: string;
  capabilities: GenerationCapabilityDto | Record<string, unknown>;
  active?: boolean;
}

export interface CanvasAssetHistoryDto {
  id: string;
  projectId: string;
  canvasNodeId?: string | null;
  mediaType: "image" | "video" | "audio";
  role: string;
  status: string;
  semanticKey?: string | null;
  sha256: string;
  metadata: Record<string, unknown>;
  contentUrl: string;
  createdAt?: string | null;
  characterDesign?: CharacterDesignAssetContextDto | null;
  reviewAction?: AssetReviewActionDto | null;
}

export interface CharacterDesignAssetContextDto {
  recipeInstanceId: string;
  revisionId: string;
  revision: number;
  revisionStatus: string;
  isCurrentRevision: boolean;
  slot: "child" | "cat" | "pair_scale";
  candidateIndex: number;
  semanticRole: string;
  selected: boolean;
}

export interface AssetReviewActionDto {
  executable: boolean;
  route: "recipe_character_design" | "legacy_asset" | "readonly";
  recipeInstanceId?: string | null;
  targetType?: "character_design" | null;
  targetId: string;
  targetHash?: string | null;
  disabledReason?: string | null;
}

export interface VideoFilmstripFrameDto {
  assetId: string;
  timestampMs: number;
  contentUrl: string;
  sha256?: string;
}

export interface VideoFilmstripDto {
  assetId: string;
  frameCount: number;
  status: "not_requested" | "pending" | "queued" | "running" | "ready" | "succeeded" | "failed";
  stepId?: string | null;
  error?: { code?: string; message?: string } | null;
  frames: VideoFilmstripFrameDto[];
}

export type VideoEditTool = "rectangle" | "brush" | "arrow" | "text" | "marker";

export interface VideoEditAnnotationInput {
  frameTimestampMs: number;
  coordinateSpace?: "source_normalized";
  tool: VideoEditTool;
  points: Array<{ x: number; y: number }>;
  label: string;
}

export interface VideoEditRecipeDto {
  id: string;
  canvasNodeId: string;
  projectId: string;
  sourceAssetId: string;
  parentRecipeId?: string | null;
  revision: number;
  startMs: number;
  endMs: number;
  instruction: string;
  referenceAssetIds: string[];
  annotations: VideoEditAnnotationInput[];
  status: string;
  compilation?: CapabilityCompilationPlan | null;
  estimatedCostMicros?: number | null;
  references?: Array<{
    assetId: string;
    semanticRole: string;
    providerIncluded: boolean;
    providerSlot?: string | null;
    omissionReason?: string | null;
  }>;
}

export interface CapabilityCompilationPlan {
  recipeId: string;
  mode: "direct" | "two_stage";
  stages: Array<{ kind: "control_anchor" | "video_edit"; boundary?: "start" | "end" | null }>;
  imageCallCount: number;
  videoCallCount: 1;
  estimatedCostMicros: number;
  warnings: string[];
  provider: string;
  model: string;
  inputHash?: string;
  actualReferences?: ActualReferenceBindingDto[];
}

export interface PromptRunDto {
  id: string;
  stepId: string;
  purpose: string;
  nodeId?: string | null;
  businessObjectType?: string | null;
  businessObjectId?: string | null;
  parentRunId?: string | null;
  templateName: string;
  templateVersion: string;
  systemPrompt?: string | null;
  userPrompt?: string | null;
  finalPrompt: string;
  providerInternalTransform: "not_observable";
  providerRequestSnapshot: Record<string, unknown>;
  inputSnapshot: Record<string, unknown>;
  provider?: string | null;
  model: string;
  parameters: Record<string, unknown>;
  rawResponse?: unknown;
  structuredResponse?: unknown;
  acceptedResponse?: unknown;
  responseDiff?: unknown;
  tokenUsage?: Record<string, unknown>;
  costMicros?: number | null;
  durationMs?: number | null;
  status: string;
  error?: Record<string, unknown> | null;
  inputHash?: string | null;
  outputHash?: string | null;
  retryChain: Array<Record<string, unknown>>;
  createdAt?: string;
  completedAt?: string | null;
}
export type LookReferencePurpose =
  | "person_identity"
  | "person_body"
  | "cat_identity"
  | "style"
  | "wardrobe"
  | "environment"
  | "prop"
  | "composition";

export type VisualAssetPurpose = "wardrobe" | "environment" | "prop" | "composition";
export type VisualAssetScope = "project" | "scene";
export type VisualAssetAction = "generate" | "upload" | "existing" | "skip";

export interface ReferenceBinding {
  assetId: string;
  usage: ReferenceUsage;
  role: ReferenceRole;
  applyTo: ReferenceTarget;
}

export interface ProjectSummary {
  id: string;
  title: string;
  contentDate: string;
  status: string;
}

export interface AssetDto {
  id: string;
  role: string;
  mediaType: "image" | "video";
  scope: string;
  status: string;
  projectId?: string | null;
  sceneId?: string | null;
  shotId?: string | null;
  producingStepId?: string | null;
  sha256: string;
  semanticKey?: string | null;
  metadata: Record<string, unknown>;
  contentReady: boolean;
  displayName: string;
  referencePurpose?: LookReferencePurpose | null;
  visualProfileRevisionId?: string | null;
  lookDraftRevision?: number | null;
  createdAt?: string | null;
}

export interface LookReferenceBinding {
  assetId: string;
  purpose: LookReferencePurpose;
  instruction: string;
}

export interface VisualProfileDraft {
  personIdentity: string;
  personHair: string;
  personBody: string;
  catIdentity: string;
  stylePositive: string[];
  styleNegative: string[];
  referenceBindings: LookReferenceBinding[];
}

export interface VisualProfileRevisionDto extends VisualProfileDraft {
  id: string;
  projectId: string;
  revision: number;
  profileHash: string;
  sourceProfileId: string;
  createdAt?: string | null;
  canonDefaults?: VisualProfileDraft;
  referenceSnapshot: Array<LookReferenceBinding & { semanticKey?: string | null; sha256: string }>;
}

export interface SceneLookPlan {
  personWardrobe: string;
  personAccessories: string;
  catAppearance: string;
  keyProps: string;
  environmentStyle: EnvironmentStyle;
  personPose: string;
  catPose: string;
  composition: string;
  additionalInstructions: string;
  imageRecommended: boolean;
  recommendationReason?: string | null;
}

export interface SceneLookDraftDto {
  visualProfileRevisionId: string;
  lookPlan: SceneLookPlan;
  referenceBindings: LookReferenceBinding[];
}

export interface SceneLookDraftEnvelope {
  sceneId: string;
  revision: number;
  draft: SceneLookDraftDto;
}

export interface SceneLookPromptPreview {
  provider: string;
  model: string;
  capabilityRevision: string;
  inputHash: string;
  prompt: string;
  charCount: number;
  utf8Bytes: number;
  referenceCount: number;
  references: Array<{
    index: number;
    assetId: string;
    sha256: string;
    semanticKey?: string | null;
    purpose: LookReferencePurpose;
    instruction: string;
    contentReady: boolean;
    semanticRole?: string;
    ordinal?: number;
    locked?: boolean;
    providerIncluded?: boolean;
    providerSlot?: string | null;
    omissionReason?: string | null;
    origin?: string;
    contentUrl?: string;
    evidenceLevel?: "frozen" | "selected_only" | "unknown";
  }>;
  warnings: string[];
  visualProfileRevisionId: string;
  visualProfileRevision: number;
  draftRevision: number;
}

export interface SceneLookVersion extends AssetDto {
  selected: boolean;
  attempt?: number | null;
  prompt?: PromptDto | null;
  inputSnapshot: Record<string, unknown>;
}

export interface ShotSuggestion {
  title: string;
  direction: string;
  suggestedDurationSeconds: number;
  anchorMode?: AnchorMode;
  sceneLookUsage?: SceneLookUsage;
}

export interface ShotSuggestionOutput {
  sceneTitle: string;
  lookPlan: SceneLookPlan;
  shots: ShotSuggestion[];
}

export interface StoryIssue {
  category:
    | "continuity"
    | "canon_conflict"
    | "physical_feasibility"
    | "action_density"
    | "causality"
    | "human_cat_interaction"
    | "generation_clarity"
    | "other";
  evidence: string;
  impact: string;
  suggestion: string;
}

export interface StoryDiagnosisOutput {
  overallAssessment: string;
  issues: StoryIssue[];
  rewriteOptions: Array<{
    strategy: StoryRewriteStrategy;
    title: string;
    summary: string;
    tradeoffs: string;
  }>;
}

export interface StoryRewriteOutput {
  rewrittenStory: string;
  changeSummary: string[];
  unresolvedQuestions: string[];
}

export interface StoryExpansionOutput {
  expandedStory: string;
  creativeSummary: string;
  unresolvedQuestions: string[];
}

export interface VisualAssetSuggestion {
  suggestionKey: string;
  displayName: string;
  purpose: VisualAssetPurpose;
  targetScope: VisualAssetScope;
  rationale: string;
  prompt: string;
  referenceAssetIds: string[];
}

export interface VisualAssetPlanOutput {
  overallAssessment: string;
  suggestions: VisualAssetSuggestion[];
  textOnlyItems: string[];
}

export interface VisualAssetPlanSelection {
  suggestionKey: string;
  action: VisualAssetAction;
  displayName: string;
  purpose: VisualAssetPurpose;
  targetScope: VisualAssetScope;
  prompt: string;
  referenceAssetIds: string[];
  existingAssetId?: string | null;
}

export interface AcceptedVisualAssetPlan {
  selections: VisualAssetPlanSelection[];
}

export interface ReferenceImageDraft {
  displayName: string;
  purpose: VisualAssetPurpose;
  prompt: string;
  referenceAssetIds: string[];
  sourceRevision: string;
}

export interface CreativeStepRecord {
  stepId: string;
  operationKey: string;
  status: string;
  attempt: number;
  model?: string | null;
  sourceHash?: string | null;
  shotSnapshotHash?: string | null;
  providerOutput?: Record<string, unknown> | null;
  acceptedOutput?: Record<string, unknown> | null;
  acceptedAt?: string | null;
  source?: string | null;
  manualRevisionOfStepId?: string | null;
  manualRevisionNote?: string | null;
  error?: Record<string, unknown> | null;
  createdAt?: string | null;
}

export interface CreativeWorkflowDto {
  sceneId: string;
  originalStory: string;
  currentStory: string;
  currentStoryHash: string;
  currentStorySource:
    | "scene_draft"
    | "preserved_original"
    | "accepted_expansion"
    | "accepted_rewrite";
  currentStorySourceStepId?: string | null;
  currentShotSnapshotHash: string;
  stages: {
    expansion: CreativeStepRecord[];
    diagnosis: CreativeStepRecord[];
    rewrite: CreativeStepRecord[];
    storyboard: CreativeStepRecord[];
    visualAssets?: CreativeStepRecord[];
  };
  reviews: CreativeStepRecord[];
}

export type SuggestionApplyMode = "replace" | "update_existing";

export interface PromptDto {
  id: string;
  purpose: string;
  model: string;
  text: string;
  sha256: string;
}

export interface AttemptDto {
  id: string;
  kind: string;
  status: string;
  attempt: number;
  operationKey: string;
  provider?: string | null;
  providerTaskId?: string | null;
  model?: string | null;
  inputSnapshot: Record<string, unknown>;
  error?: Record<string, unknown> | null;
  createdAt?: string | null;
  prompt?: PromptDto | null;
  reviews: Array<{
    id: string;
    source: string;
    decision: string;
    reason?: string | null;
    warnings: Array<Record<string, unknown>>;
    evidence: Record<string, unknown>;
  }>;
}

export interface ShotDto {
  id: string;
  sceneId: string;
  order: number;
  title: string;
  direction: string;
  durationSeconds: number;
  draftRevision: number;
  anchorMode: AnchorMode;
  referenceBindings: ReferenceBinding[];
  inheritProjectReferences: boolean;
  sceneLookUsage: SceneLookUsage;
  /** V5 compatibility projection; sceneLookUsage is authoritative. */
  useSceneLook: boolean;
  status: string;
  selectedAnchorAssetId?: string | null;
  selectedVideoAssetId?: string | null;
  assets: AssetDto[];
  attempts: AttemptDto[];
}

export interface SceneDto {
  id: string;
  order: number;
  title: string;
  sourceText: string;
  chapterLabel?: string | null;
  contextNote?: string | null;
  storyMode: StoryMode;
  targetShotCount: number;
  lookPlan?: SceneLookPlan | null;
  selectedLookAssetId?: string | null;
  lookDraftRevision: number;
  status: string;
  attempts: AttemptDto[];
  shots: ShotDto[];
}

export interface SequenceDto {
  id: string;
  projectId: string;
  revision: number;
  parentSequenceId?: string | null;
  renderedAssetId?: string | null;
  status: string;
  plan: {
    duration_ms: number;
    clips: SequenceClipDto[];
  };
}

export type SequenceTransitionType = "cut" | "fade_black" | "cross_dissolve";

export interface SequenceTransitionDto {
  type: SequenceTransitionType;
  durationMs: number;
}

export interface RecipeSequenceDraftDto {
  transitions: Array<{ afterShotId: string; transition: SequenceTransitionDto }>;
  introTransition?: SequenceTransitionDto | null;
  outroTransition?: SequenceTransitionDto | null;
}

export interface SequenceClipDto {
  order: number;
  shot_card_id: string;
  source_asset_id: string;
  source_start_ms: number;
  source_end_ms: number;
  timeline_start_ms: number;
  timeline_end_ms: number;
  transitionFromPrevious?: SequenceTransitionDto | null;
}

export interface ProjectGraph {
  project: ProjectSummary & {
    selectedSequenceId?: string | null;
    contractVersion: number;
    defaultReferenceBindings: ReferenceBinding[];
    visualProfileRevisionId?: string | null;
  };
  assets: AssetDto[];
  scenes: SceneDto[];
  sequences: SequenceDto[];
}

export interface JobDto {
  jobId: string;
  kind: string;
  status: "pending" | "queued" | "running" | "awaiting_review" | "succeeded" | "failed";
  dedupKey?: string;
  context?: Record<string, string> & {
    projectId?: string;
    sceneId?: string;
    shotId?: string;
    stepId?: string;
    operationKey?: string;
    canvasNodeId?: string;
    canvasGroupId?: string;
    recipeInstanceId?: string;
    parentStepId?: string;
    businessObjectId?: string;
    creationMode?: StoryboardCreationMode;
    workflowStage?: string;
    phase?: RecipePhaseKey;
  };
  projectId?: string;
  sceneId?: string;
  shotId?: string;
  operationKey?: string;
  canvasNodeId?: string;
  canvasGroupId?: string;
  recipeInstanceId?: string;
  parentStepId?: string;
  businessObjectId?: string;
  childStepIds?: string[];
  creationMode?: StoryboardCreationMode;
  workflowStage?: string;
  phase?: RecipePhaseKey;
  progress?: {
    currentStep?: number;
    totalSteps?: number;
    percent?: number;
    message?: string;
    providerStatus?: string;
  };
  currentStep?: number | string | null;
  resultSummary?: Record<string, unknown> | null;
  completedAt?: string | null;
  result?: unknown;
  error?: Record<string, unknown> | null;
  createdAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
}

export interface PersistentTaskDto {
  stepId: string;
  projectId: string;
  sceneId?: string | null;
  shotId?: string | null;
  kind: string;
  status: string;
  attempt: number;
  operationKey: string;
  canvasNodeId?: string | null;
  canvasGroupId?: string | null;
  recipeInstanceId?: string | null;
  businessObjectId?: string | null;
  parentStepId?: string | null;
  childStepIds?: string[];
  creationMode?: StoryboardCreationMode | null;
  workflowStage?: string | null;
  phase?: RecipePhaseKey | null;
  provider?: string | null;
  providerTaskId?: string | null;
  model?: string | null;
  inputSnapshot: Record<string, unknown>;
  error?: Record<string, unknown> | null;
  progress?: {
    currentStep?: number;
    totalSteps?: number;
    percent?: number;
    message?: string;
    providerStatus?: string;
    resultSummary?: Record<string, unknown>;
  };
  resultSummary?: Record<string, unknown> | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  completedAt?: string | null;
  recovery?: PersistentTaskRecoveryDto | null;
  cancellation?: TaskCancellationPolicyDto | null;
}

export interface PersistentTaskRecoveryDto {
  allowed: boolean;
  mode?: "resume_pre_provider" | "resume_provider_tracking" | null;
  label?: string | null;
  disabledReason?: string | null;
}

export type ProviderCancellationStatus =
  | "not_submitted"
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "unknown";

export interface TaskCancellationPolicyDto {
  allowed: boolean;
  mode:
    | "local_before_provider"
    | "provider_queued"
    | "reconcile_required"
    | "unavailable";
  label: string;
  disabledReason?: string | null;
  providerStatus: ProviderCancellationStatus;
  costMayAlreadyApply: boolean;
}

export type VisualPresetKey =
  | "healing_child_cat_line_texture_v3"
  | "healing_child_cat_style_board_v4";

export interface SubjectReferenceDto {
  assetId: string | null;
  semanticRole?: string;
  semanticKey: string;
  title: string;
  contentUrl: string | null;
  thumbnailUrl: string | null;
  approvalStatus: string;
  sha256: string | null;
  required: boolean;
  instruction?: string;
  authority?: ReferenceAuthorityDto | null;
  visualProfileRevisionId?: string | null;
  authorityOrigin?: "subject_revision" | "visual_profile" | "preset" | null;
  currentAuthority?: boolean;
  subjectId?: string | null;
  subjectRevisionId?: string | null;
  subjectRevision?: number | null;
  subjectKind?: string | null;
  subjectRole?: string | null;
}

export interface VisualPresetSlotDto extends SubjectReferenceDto {
  role: "person" | "cat" | "style";
  purpose: "identity" | "style";
  instruction: string;
}

export interface VisualPresetProfileDto {
  key: VisualPresetKey;
  canonProfileId: string;
  title: string;
  description: string;
  version: number;
  ready: boolean;
  slots: VisualPresetSlotDto[];
}

export interface EpisodeVisualProfileDto extends VisualProfileDraft {
  id: string;
  projectId: string;
  recipeInstanceId?: string | null;
  revision: number;
  sourceProfileId: string;
  references: SubjectReferenceDto[];
  lockedSemanticKeys: string[];
  sourceExclusions?: string[];
  createdAt: string;
}

export interface TaskCenterDto {
  runtimeJobs: JobDto[];
  persistentTasks: PersistentTaskDto[];
}

export interface HealthDto {
  ready: boolean;
  databaseReady: boolean;
  applicationVersion?: string;
  serverStartedAt?: string;
  apiFeatures?: string[];
  contractVersion: number;
  alembicRevision: string;
  expectedAlembicRevision: string;
  arkImageModel?: string;
  arkVideoModel?: string;
  arkPlanningModel?: string;
  arkReviewModel?: string;
  arkReady?: boolean;
  ffmpegAvailable?: boolean;
  ffprobeAvailable?: boolean;
  videoGenerationReady?: boolean;
  localCompositionReady?: boolean;
  configurationWarnings?: string[];
  generationConfigurationValid?: boolean;
  provider?: string;
  providerMode?: "ark";
  realArkCalls?: number | null;
  runtimeConfigRevision?: number;
  runtimeConfigUpdatedAt?: string | null;
  runtimeConfigUsingOverride?: boolean;
}

export type RuntimeModelRole = "planning" | "image" | "video" | "review";

export interface RuntimeModelCatalogEntry {
  id: string;
  role: RuntimeModelRole;
  displayName: string;
  supportedResolutions: string[];
  supportedInputModes: string[];
}

export interface RuntimeProductionConfig {
  planningModel: string;
  imageModel: string;
  videoModel: string;
  reviewModel: string;
  videoResolution: "480p" | "720p";
  semanticReviewEnabled: boolean;
  revision: number;
  updatedAt: string | null;
  usingOverride: boolean;
}

export interface RuntimeSettingsDto {
  current: RuntimeProductionConfig;
  deploymentDefaults: RuntimeProductionConfig;
  modelCatalog: RuntimeModelCatalogEntry[];
  arkApiKeyConfigured: boolean;
  arkReady: boolean;
  ffmpegAvailable: boolean;
  ffprobeAvailable: boolean;
  databaseReady: boolean;
  videoGenerationReady: boolean;
  localCompositionReady: boolean;
  databaseManagedSeparately: boolean;
  diagnostics: {
    provider: string;
    arkBaseUrlProfile: string;
    directorRequestTimeoutSeconds: number;
    reviewRequestTimeoutSeconds: number;
    videoApiTimeoutSeconds: number;
    pollIntervalSeconds: number;
    taskTimeoutSeconds: number;
    imageRequestTimeoutSeconds: number;
    workRoot: string;
    assetRoot: string;
    configurationWarnings: string[];
    configurationIssues: string[];
  };
}

export interface ShotRuleFinding {
  code: string;
  severity: "info" | "warning";
  message: string;
}

export interface ShotLocalAnalysis {
  suggestedSubshotMin: number;
  suggestedSubshotMax: number;
  detectedSubshotCount: number;
  actionCount: number | null;
  cameraMoveCount: number | null;
  hasStableEnding: boolean | null;
  hasSound: boolean | null;
  qualitativePacing: string;
  findings: ShotRuleFinding[];
}

export interface PreviousTailStatus {
  available: boolean;
  reason?: string;
  previousShotId?: string;
  sourceVideoAssetId?: string | null;
  assetId?: string | null;
  boundAssetId?: string | null;
  stale: boolean;
}

export type ProviderInputMode = "text_only" | "reference_media" | "first_frame" | "first_last_frame";

export interface ShotPromptReference {
  index: number;
  assetId: string;
  displayName: string;
  promptAlias: string;
  subjectLabel: string;
  sourceLayer: "shot" | "scene_look" | "project" | "previous_tail" | "episode_design" | "candidate";
  responsibility: string;
  contentReady: boolean;
  sha256?: string;
  purpose?: string | null;
  providerIncluded?: boolean;
  providerSlot?: string | null;
  locked?: boolean;
}

export interface ShotPromptPreview {
  target: "anchor" | "video";
  providerInputMode: ProviderInputMode;
  ready: boolean;
  blockers: string[];
  inputHash: string;
  sourceRevisionHash: string;
  prompt: string;
  creativeBody: string;
  systemShell: string;
  charCount: number;
  utf8Bytes: number;
  inputPlan?: Record<string, unknown> | null;
  draftRevision: number;
  anchorMode: AnchorMode;
  sceneLookUsage: SceneLookUsage;
  localAnalysis: ShotLocalAnalysis;
  qualitativePacing: string;
  linkWarnings: string[];
  actualInputCount: number;
  references: ShotPromptReference[];
  actualInputs: ShotPromptReference[];
  upstreamLineage?: ActualReferenceBindingDto[];
  providerReferencePolicy?: "approved_anchor_only_baked_lineage" | "compiled_production_references" | "draft_reference_resolution";
  legacyPromptLabels?: boolean;
  previousTail: PreviousTailStatus;
}

export type ProductionNextAction =
  | "open_scene_look"
  | "write_anchor_brief"
  | "assistance_running"
  | "review_assistance"
  | "generate_anchor"
  | "anchor_generating"
  | "open_task"
  | "generate_video"
  | "video_generating"
  | "review_anchor"
  | "review_video"
  | "completed"
  | "review_media"
  | "open_versions"
  | "fix_inputs";

export interface ShotProductionSummaryDto {
  shotId: string;
  sceneId: string;
  state:
    | "needs_opening"
    | "generating_anchor"
    | "ready_video"
    | "generating_video"
    | "awaiting_review"
    | "approved"
    | "stale"
    | "blocked";
  stateLabel: string;
  nextAction: ProductionNextAction;
  primaryActionLabel: string;
  providerInputMode: ProviderInputMode;
  actualInputCount: number;
  actualInputs: ShotPromptReference[];
  upstreamLineage: string[];
  ready: boolean;
  blockers: string[];
  referenceCounts: {
    custom: number;
    scene: number;
    project: number;
    opening: number;
    person: number;
    cat: number;
    style: number;
    prop: number;
    total: number;
  };
  anchorVersionCount: number;
  videoVersionCount: number;
  activeTaskCount: number;
  latestActionableTask?: AttemptDto | null;
  previewAssetId?: string | null;
  previewMediaType?: "image" | "video" | null;
  usesSceneLook: boolean;
  inputHash: string;
  currentInputHash: string;
}

export interface SceneProductionSummaryDto {
  sceneId: string;
  selectedLookAssetId?: string | null;
  lookVersionCount: number;
  lookStatus: string;
  lookRecommended: boolean;
  lookRecommendationReason?: string | null;
  shots: ShotProductionSummaryDto[];
}

export interface ProductionBoardDto {
  projectId: string;
  projectGraph: ProjectGraph;
  scenes: SceneProductionSummaryDto[];
}

export interface ReferenceSlotDto {
  key: "person" | "cat" | "style" | "scene" | "prop" | "opening" | "custom";
  label: string;
  target: "anchor" | "video";
  items: Array<ShotPromptPreview["references"][number] & { asset: AssetDto }>;
}

export interface AnchorBriefVersionDto {
  stepId: string;
  version: number;
  source: "manual" | "llm";
  brief: string;
  sourceDraftRevision: number;
  acceptedDraftRevision: number;
  acceptedAt?: string | null;
  createdAt?: string | null;
  stale: boolean;
  current: boolean;
}

export interface ShotGenerationWorkspaceDto {
  projectId: string;
  shot: ShotDto;
  scene: {
    id: string;
    title: string;
    selectedLookAssetId?: string | null;
  };
  assets: AssetDto[];
  generationSpec: {
    providerInputMode: ProviderInputMode;
    actualInputCount: number;
    actualInputs: ShotPromptReference[];
    ready: boolean;
    blockers: string[];
    warnings: string[];
    inputHash: string;
    sourceRevisionHash: string;
  };
  anchorPreview: ShotPromptPreview;
  videoPreview: ShotPromptPreview;
  actualInputs: ShotPromptReference[];
  upstreamLineage: AssetDto[];
  referenceSlots: {
    anchor: ReferenceSlotDto[];
    video: ReferenceSlotDto[];
  };
  previousTail: PreviousTailStatus;
  activeTasks: AttemptDto[];
  anchorVersions: VisualAssetVersion[];
  videoVersions: VisualAssetVersion[];
  anchorBrief?: AnchorBriefVersionDto | null;
  anchorBriefVersions: AnchorBriefVersionDto[];
  nextAction: ProductionNextAction;
  nextActionLabel: string;
  blockers: string[];
}

export interface ShotAssistPatch {
  title?: string;
  direction?: string;
  durationSeconds?: number;
  sceneLookUsage?: SceneLookUsage;
  anchorMode?: AnchorMode;
  referenceBindings?: ReferenceBinding[];
}

export interface ShotAssistAnalysis {
  actionDensityAssessment: string;
  assetCompatibilityAssessment?: string;
  pacingPlan: {
    recommendedDurationSeconds: number;
    rationale: string;
    beats: Array<{ ordinal: number; description: string; rhythm: "brief" | "standard" | "expanded" }>;
  };
  recommendedSceneLookUsage: SceneLookUsage;
  recommendedAnchorMode: AnchorMode;
  referenceDecisions: Array<{
    assetId: string;
    decision: "keep" | "remove" | "change_role";
    recommendedRole?: ReferenceRole | null;
    reason: string;
  }>;
  continuity: { previousIssues: string[]; nextIssues: string[]; recommendation: string };
  promptRisks: string[];
  creativeBody?: string | null;
  creativeAlternatives?: Array<{
    label: "conservative" | "stable";
    body: string;
    rationale: string;
  }>;
  anchorBrief?: string | null;
  patch?: ShotAssistPatch | null;
}

export interface ShotAssistRecord {
  stepId: string;
  status: string;
  sourceDraftRevision: number;
  stale: boolean;
  analysis?: ShotAssistAnalysis | null;
  acceptedOutput?: ShotAssistPatch | null;
  acceptedAnchorBrief?: string | null;
  acceptedPatchAt?: string | null;
  acceptedAnchorBriefAt?: string | null;
  acceptedAt?: string | null;
  error?: Record<string, unknown> | null;
  createdAt?: string | null;
}

export interface VisualAssetVersion extends AssetDto {
  attempt?: number | null;
  prompt?: PromptDto | null;
  inputSnapshot: Record<string, unknown>;
}

export interface SceneAssetSlotReadinessDto {
  key: string;
  displayName: string;
  purpose: VisualAssetPurpose;
  required: boolean;
  assetIds: string[];
  status: "ready" | "missing" | "stale";
}

export interface SceneAssetReadinessDto {
  requiredSlots: SceneAssetSlotReadinessDto[];
  boundAssetIds: string[];
  missingAssetKeys: string[];
  staleAssetKeys: string[];
  sceneLookStatus: "approved" | "missing" | "stale" | "off";
  visualAssetPlanCurrent: boolean;
  canGenerateSceneLook: boolean;
  canCompileShotPrompt: boolean;
  blockers: string[];
}

export interface SceneVisualAssetsDto {
  sceneId: string;
  lookDraftRevision: number;
  selectedReferenceAssetIds: string[];
  canon: AssetDto[];
  project: VisualAssetVersion[];
  scene: VisualAssetVersion[];
  plans: CreativeStepRecord[];
  readiness: SceneAssetReadinessDto;
}

export interface ShotAssistContext {
  shotId: string;
  sourceDraftRevision: number;
  model?: string | null;
  localAnalysis: ShotLocalAnalysis;
  previousShot?: { id: string; title: string } | null;
  nextShot?: { id: string; title: string } | null;
  previousTail: PreviousTailStatus;
  candidates: Array<{
    assetId: string;
    displayName: string;
    sha256: string;
    sourceLayer: string;
    responsibility: string;
    contentReady: boolean;
    available: boolean;
    duplicate: boolean;
  }>;
  defaultCandidateAssetIds: string[];
  warnings: string[];
}
