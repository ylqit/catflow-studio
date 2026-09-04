import type { components } from "@catflow/contracts";

export interface ObjectPublisherRuntimeDto {
  configured: boolean;
  ready: boolean;
  backend: "s3";
  endpointHost: string;
  publicHost: string;
  bucket: string;
  region: string;
  addressingStyle: "virtual" | "path";
  presignTtlSeconds: number;
  retentionDays: number;
  error?: { code: string; message: string } | null;
}

export interface RuntimeBootstrapDto {
  csrfToken: string;
  baseUrl: string;
  localOnly: true;
  databaseReady: boolean;
  workerReady: boolean;
  worker: WorkerRuntimeDto;
  ffmpegReady: boolean;
  ffprobeReady: boolean;
  objectPublisher: ObjectPublisherRuntimeDto;
  provider: {
    name: "ark";
    planningModel: string;
    imageModel: string;
    videoModel: string;
    diagnosticModel: string;
    capabilityRevision: string;
    paidCallsEnabled: boolean;
    apiKeyConfigured: boolean;
    videoGeneration: {
      maximumImageReferences: number;
      maximumVideoReferences: number;
      previousEpisodeVideoSupported: boolean;
    };
    segmentRepair: {
      supported: boolean;
      blockedReason: string | null;
      maximumImageReferences: number;
      maximumVideoReferences: number;
    };
  };
}

export interface WorkerRuntimeDto {
  ready: boolean;
  state: "ready" | "offline" | "stale" | "restarting" | "degraded";
  lastHeartbeatAt?: string;
  lastExitAt?: string;
  restartCount: number;
  retryingAutomatically: boolean;
}

export type FixedCanonRole = "episode_child" | "episode_cat" | "pair_scale" | "style_board";

export interface ProjectCreate {
  title: string;
  theme: string;
  targetDurationSeconds: number;
}

export interface ProjectDto extends ProjectCreate {
  id: string;
  aspectRatio: "9:16";
  canonProfileId: string;
  createdAt: string;
  updatedAt: string;
}

export type SeriesNarrativeMode = "continuous" | "lightly_serialized" | "anthology";

export interface SeriesCreateCommand {
  title: string;
  premise: string;
  narrativeMode: SeriesNarrativeMode;
  plannedEpisodeCount: number;
  defaultEpisodeDurationSeconds: number;
  worldSetting: string;
  emotionalDirection: string;
  endingGoal?: string | null;
  recurringElements: string[];
  mustKeep: string[];
  mustAvoid: string[];
  additionalNotes?: string | null;
}

export interface StorySeriesDto extends SeriesCreateCommand {
  id: string;
  canonProfileId: string;
  activePlanVersionId?: string | null;
  plannedCount: number;
  materializedCount: number;
  completedCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface SeriesEpisodeOutlineDraft {
  order: number;
  title: string;
  targetDurationSeconds: number;
  premise: string;
  openingState: string;
  trigger: string;
  childIntent: string;
  childAction: string;
  catResponse: string;
  visibleChange: string;
  endingState: string;
  continuityCarryover: string[];
  recurringLocationKeys: string[];
  recurringPropKeys: string[];
  productionWarnings: string[];
}

export interface SeriesPlanDraft {
  seriesBible: {
    logline: string;
    centralTheme: string;
    narrativeMode: SeriesNarrativeMode | null;
    worldRules: string[];
    emotionalArc: { opening: string; development: string; climax: string; resolution: string };
    recurringLocations: Array<{ key: string; name: string; description: string }>;
    recurringProps: Array<{ key: string; name: string; continuityRule: string }>;
    wardrobeRules: string[];
    continuityRules: string[];
    visualMotifs: string[];
    soundMotifs: string[];
    forbiddenChanges: string[];
  };
  episodes: SeriesEpisodeOutlineDraft[];
}

export interface SeriesPlanVersionDto {
  id: string;
  seriesId: string;
  revision: number;
  status: "candidate" | "accepted" | "rejected" | "superseded";
  active: boolean;
  disposition: "candidate_ready" | "needs_input" | "invalid";
  plan: SeriesPlanDraft;
  inputHash: string;
  promptRevision: string;
  producingJobId?: string | null;
  basePlanVersionId?: string | null;
  issues: Array<{ code: string; severity: "fatal" | "blocking" | "warning"; path: string; message: string; suggestedAction?: string | null }>;
  decidedAt?: string | null;
  createdAt: string;
}

export interface SeriesEpisodeDto {
  id: string;
  seriesId: string;
  order: number;
  title: string;
  targetDurationSeconds: number;
  status: "outline" | "story_review" | "assets" | "storyboard" | "generating" | "selecting" | "editing" | "completed" | "needs_attention";
  projectId?: string | null;
  activeOutlineVersionId: string;
  outline: SeriesEpisodeOutlineDraft;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectSeriesContextDto {
  series: StorySeriesDto;
  episode: SeriesEpisodeDto;
  episodes: SeriesEpisodeDto[];
}

export interface SeriesPlanPreviewDto {
  seriesId: string;
  provider: string;
  model: string;
  capabilityRevision: string;
  inputHash: string;
  prompt: string;
  outputSchema: Record<string, unknown>;
  plannedEpisodeCount: number;
  defaultEpisodeDurationSeconds: number;
  promptRevision: string;
}

export interface SeriesEpisodeStoryPreviewDto {
  seriesId: string;
  seriesPlanVersionId: string;
  seriesEpisodeId: string;
  episodeOutlineVersionId: string;
  projectId: string;
  incomingContinuity?: string | null;
  provider: string;
  model: string;
  capabilityRevision: string;
  inputHash: string;
  prompt: string;
  outputSchema: Record<string, unknown>;
  promptRevision: string;
}

export interface EpisodeContinuityStateDto {
  wardrobe: string;
  location: string;
  weather: string;
  timeOfDay: string;
  lighting: string;
  childState: string;
  catState: string;
  spatialPositions: string;
  props: Array<{ key: string; name: string; state: string; location?: string | null; owner?: "child" | "cat" | "environment" | null }>;
  unfinishedActions: string[];
  endingImage: string;
}

export interface EpisodeContinuitySnapshotDto {
  id: string;
  episodeId: string;
  direction: "incoming" | "outgoing";
  source: "planned" | "confirmed" | "final_video";
  state: EpisodeContinuityStateDto;
  decisions: Record<string, "inherit" | "adjust" | "reset">;
  confirmed: boolean;
  active: boolean;
  createdAt: string;
}

export interface EpisodeContinuityDto {
  episodeId: string;
  previousEpisodeId?: string | null;
  incoming?: EpisodeContinuitySnapshotDto | null;
  outgoing?: EpisodeContinuitySnapshotDto | null;
}

export interface EpisodeContinuityFramesDto {
  episodeId: string;
  sourceVideoAssetId?: string | null;
  lastFrame?: AssetDto | null;
  candidates: AssetDto[];
  selectedKeyframes: AssetDto[];
}

export interface SeriesAssetBindingDto {
  id: string;
  seriesId: string;
  bindingKey: string;
  role: string;
  assetId: string;
  assetSha256: string;
  active: boolean;
  createdAt: string;
}

export interface StoryImportPreviewDto {
  contentHash: string;
  inputHash: string;
  characterCount: number;
  duplicateDocumentId?: string | null;
  prompt: string;
  outputSchema: Record<string, unknown>;
  promptRevision: string;
}

export interface StorySourceUnitDto {
  id: string;
  documentId: string;
  ordinal: number;
  title: string;
  theme?: string | null;
  rawText: string;
  analysis: Record<string, unknown>;
  createdAt: string;
}

export interface StorySourceRelationSuggestionDto {
  id: string;
  documentId: string;
  relationType: "independent" | "new_series" | "append_series" | "revision" | "reference";
  unitIds: string[];
  title: string;
  narrativeMode?: SeriesNarrativeMode | null;
  suggestedSeriesId?: string | null;
  confidence: number;
  rationale: string;
  status: "suggested" | "accepted" | "rejected";
  createdAt: string;
}

export interface StorySourceDocumentDto {
  id: string;
  contentHash: string;
  sourceFormat: "paste" | "txt" | "md";
  fileName?: string | null;
  rawText: string;
  status: "pending" | "analyzing" | "analyzed" | "confirmed" | "failed";
  analysisJobId?: string | null;
  units: StorySourceUnitDto[];
  relationSuggestions: StorySourceRelationSuggestionDto[];
  createdAt: string;
  updatedAt: string;
}

export interface StoryImportCreateResultDto {
  document: StorySourceDocumentDto;
  analysisJob?: JobDto | null;
  reused: boolean;
}

export interface StoryImportProjectDto {
  id: string;
  title: string;
  theme: string;
  targetDurationSeconds: number;
}

export type ProjectStage = "story" | "assets" | "storyboard" | "generation" | "editing" | "completed";
export type ProjectAttention = "normal" | "running" | "needs_attention";
export type ProjectSystemView = "all" | "recent" | "in_progress" | "needs_attention" | "completed" | "pinned" | "archived";
export type ProjectLibrarySort = "activity" | "created" | "title" | "stage";
export type ProjectLibraryGroupMode = "date" | "collection" | "none";
export type ProjectLibraryLayout = "grid" | "list";

export interface ProjectCollectionDto {
  id: string;
  name: string;
  colorKey: "clay" | "sage" | "sky" | "lavender" | "sand" | "rose";
  sortOrder: number;
  archived: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectTagDto { name: string; normalizedName: string }

export interface ProjectLibrarySeriesDto {
  seriesId: string;
  seriesTitle: string;
  episodeId: string;
  episodeOrder: number;
}

export interface ProjectLibraryItemDto {
  id: string;
  title: string;
  themeSummary: string;
  targetDurationSeconds: number;
  aspectRatio: "9:16";
  coverAssetId?: string | null;
  series?: ProjectLibrarySeriesDto | null;
  collection?: ProjectCollectionDto | null;
  tags: ProjectTagDto[];
  stage: ProjectStage;
  attention: ProjectAttention;
  attentionReasons: string[];
  pinned: boolean;
  archived: boolean;
  lastActivityAt: string;
  createdAt: string;
}

export interface ProjectLibraryFacetsDto {
  systemViews: Record<ProjectSystemView, number>;
  stages: Record<ProjectStage, number>;
  collections: Array<{ id: string; name: string; count: number }>;
  tags: Array<{ name: string; count: number }>;
}

export interface ProjectTagSuggestionDto {
  name: string;
  count: number;
}

export interface ProjectLibraryPageDto {
  items: ProjectLibraryItemDto[];
  nextCursor?: string | null;
  total: number;
  facets: ProjectLibraryFacetsDto;
}

export interface ProjectLibraryQuery {
  q?: string;
  systemView?: ProjectSystemView;
  collectionId?: string;
  unassigned?: boolean;
  tags?: string[];
  stage?: ProjectStage;
  dateFrom?: string;
  dateTo?: string;
  sort?: ProjectLibrarySort;
  cursor?: string;
  limit?: number;
}

export type ProjectLibraryBatchAction =
  | { action: "move_collection"; projectIds: string[]; collectionId: string | null }
  | { action: "add_tags"; projectIds: string[]; tags: string[] }
  | { action: "remove_tags"; projectIds: string[]; tags: string[] }
  | { action: "pin" | "unpin" | "archive" | "restore"; projectIds: string[] };

export type GenerationInputSnapshotDto = components["schemas"]["GenerationInputSnapshotDto"];
export type GenerationPromptSectionDto = components["schemas"]["GenerationPromptSectionDto"];
export type ImageGenerationInputSnapshotDto = components["schemas"]["ImageGenerationInputSnapshotDto"];

export interface JobDto {
  id: string;
  projectId?: string | null;
  seriesId?: string | null;
  storySourceDocumentId?: string | null;
  kind: "plan_story" | "plan_shots" | "plan_series" | "plan_series_episode" | "analyze_story_source" | "extract_continuity_frames" | "generate_image" | "diagnose_image" | "generate_video" | "diagnose_video" | "regenerate_video_segment" | "render_export";
  status:
    | "queued"
    | "submitting"
    | "submitted"
    | "polling"
    | "storing"
    | "succeeded"
    | "failed"
    | "cancel_requested"
    | "cancelled"
    | "submission_unknown";
  inputHash: string;
  provider?: string;
  model?: string;
  providerTaskId?: string;
  providerSubmissionStartedAt?: string | null;
  validationRunId?: string;
  videoRepairId?: string;
  expectedCostMicros?: number | null;
  providerResult?: Record<string, unknown> | null;
  publication?: {
    id: string;
    state: "uploading" | "ready" | "delete_pending" | "deleted" | "failed";
    publicHost: string;
    signedUrlExpiresAt?: string | null;
    deleteAfter: string;
  } | null;
  actualUsage?: Record<string, unknown> | null;
  actualCostMicros?: number | null;
  currency?: "CNY";
  billingStatus?: "pending" | "usage_reported" | "calculated" | "unpriced" | "provider_adjusted";
  rateCardRevision?: string | null;
  providerRequestId?: string | null;
  inputSnapshot?: GenerationInputSnapshotDto | null;
  imageInputSnapshot?: ImageGenerationInputSnapshotDto | null;
  frozenInput: Record<string, unknown>;
  resultAssetIds: string[];
  error?: { code: string; message: string; retryable: boolean; requestId?: string; submissionUnknown?: boolean; timedOut?: boolean; incompleteReason?: string; providerStatus?: string; maxOutputTokens?: number };
  createdAt: string;
  updatedAt: string;
}

export interface JobUsageDto {
  jobId: string;
  provider: string;
  model: string;
  inputTokens?: number | null;
  outputTokens?: number | null;
  completionTokens?: number | null;
  totalTokens?: number | null;
  generatedImages?: number | null;
  generatedVideoSeconds?: number | null;
  providerUsage: Record<string, number>;
  billingStatus: "pending" | "usage_reported" | "calculated" | "unpriced" | "provider_adjusted";
  calculatedCostMicros?: number | null;
  currency: "CNY";
  rateCardRevision?: string | null;
  priceSource?: string | null;
}

export interface ProjectUsageSummaryDto {
  projectId: string;
  jobs: JobUsageDto[];
  totals: Record<string, number>;
  calculatedCostMicros: number;
  unpricedJobCount: number;
  currency: "CNY";
}

export type RateCardMetric = "inputTokens" | "outputTokens" | "completionTokens" | "totalTokens" | "generatedImages" | "generatedVideoSeconds";
export type RateCardUnit = "million_tokens" | "image" | "video_second";
export interface RateCardItemDto { metric: RateCardMetric; unit: RateCardUnit; unitPriceMicros: number }
export interface RateCardRevisionDto {
  provider: string;
  model: string;
  revision: string;
  sourceUrl?: string | null;
  effectiveFrom: string;
  rates: RateCardItemDto[];
  active: boolean;
  createdAt: string;
}


export interface PlannerMessageDto {
  id: string;
  role: "user" | "assistant";
  content: string;
  ordinal: number;
  createdAt: string;
}

export interface PlannerJobDto {
  id: string;
  status: JobDto["status"];
  provider?: string;
  model?: string;
  providerTaskId?: string;
  actualUsage?: Record<string, unknown> | null;
  actualCostMicros?: number | null;
  currency?: "CNY";
  billingStatus?: "pending" | "usage_reported" | "calculated" | "unpriced" | "provider_adjusted";
  rateCardRevision?: string | null;
  error?: { code?: string; message?: string; retryable?: boolean; requestId?: string };
  createdAt: string;
  updatedAt: string;
}

export interface MicroEventDto {
  trigger: string;
  childAction: string;
  catResponse: string;
  visibleChange: string;
  warmEnding: string;
}

export interface LifeStoryProposalDto {
  id: string;
  projectId: string;
  status: "draft" | "adopted" | "outdated";
  title: string;
  summary: string;
  body: string;
  microEvent: MicroEventDto;
  targetDurationSeconds: number;
  dialoguePolicy: "none" | "minimal";
  environmentIntent: string;
  contextHash: string;
  warnings: Array<{ code: string; message: string }>;
}

export interface PlannerSnapshotDto {
  sessionId: string;
  projectId: string;
  contextRevision: number;
  messages: PlannerMessageDto[];
  proposals: LifeStoryProposalDto[];
  latestJob?: PlannerJobDto;
}

export interface StoryVersionDto {
  id: string;
  projectId: string;
  revision: number;
  title: string;
  body: string;
  microEvent: MicroEventDto;
  targetDurationSeconds: number;
  dialoguePolicy: "none" | "minimal";
  environmentIntent: string;
  active: boolean;
  createdAt: string;
}

export type AssetSlot =
  | "episode_child"
  | "episode_cat"
  | "pair_scale"
  | "environment"
  | "style_board"
  | "video"
  | "final";

export type AssetGenerationKind = Exclude<AssetSlot, "video" | "final">;

export interface AssetDto {
  id: string;
  projectId?: string;
  canonProfileId?: string;
  producingJobId?: string;
  role: string;
  mediaType: "image" | "video" | "audio";
  sha256: string;
  byteSize: number;
  metadata: Record<string, unknown>;
  createdAt: string;
}

export interface CanonProfileDto {
  id: string;
  version: number;
  specVersion: 4;
  active: boolean;
  profileHash: string;
  profile: Record<string, unknown>;
  fixedAssets: Partial<Record<FixedCanonRole, AssetDto>>;
  createdAt: string;
}

export interface ShotSpecDto {
  id: string;
  order: number;
  durationSeconds: number;
  framing: string;
  cameraMovement: string;
  childAction: string;
  catAction: string;
  environmentChange: string;
  transition: "continuous" | "soft_cut" | "hard_cut";
  durationFrames?: number | null;
  lens?: { focalLengthEquivalent: string; cameraHeight: string; cameraAngle: string; perspectiveIntent: string } | null;
  composition?: { subjectPlacement: string; foreground: string; middleGround: string; background: string; screenDirection: string; eyeLine: string } | null;
  childBlocking?: BlockingDesignDto | null;
  catBlocking?: BlockingDesignDto | null;
  physicalChange?: { subject: string; before: string; after: string } | null;
  continuity?: { incoming: string; outgoing: string; sharedVisualElement: string; finalFrame: string } | null;
  lighting?: { direction: string; softness: string; colorIntent: string } | null;
  sound?: { ambience: string[]; objectEffects: string[]; movementEffects: string[]; musicIntent: string; dialogue?: string | null } | null;
  directorIntent?: string | null;
  generationRisks?: Array<{ code: string; message: string }>;
}

export interface BlockingDesignDto {
  initialState: string;
  movementPath: string;
  endState: string;
  microMotions: string[];
}

export interface ShotPlanVersionDto {
  id: string;
  projectId: string;
  revision: number;
  sourceStoryVersionId: string;
  sourceSelectionHash: string;
  clip: Record<string, unknown>;
  shots: ShotSpecDto[];
  totalDurationSeconds: number;
  directorTreatment?: Record<string, unknown> | null;
  directorPromptRevision?: string | null;
  directorModel?: string | null;
  directorInputHash?: string | null;
  reviewStatus: "accepted" | "candidate" | "rejected" | "superseded";
  producingJobId?: string | null;
  baseShotPlanVersionId?: string | null;
  decidedAt?: string | null;
  active: boolean;
  outdated: boolean;
  createdAt: string;
}

export interface ShotPlanGenerationAttemptDto {
  jobId: string;
  status: JobDto["status"];
  storyVersionId: string;
  baseShotPlanVersionId?: string | null;
  resultShotPlanVersionId?: string | null;
  provider?: string | null;
  model?: string | null;
  createdAt: string;
  updatedAt: string;
  actualUsage?: Record<string, unknown> | null;
  actualCostMicros?: number | null;
  billingStatus: NonNullable<JobDto["billingStatus"]>;
  error?: {
    code: string;
    message: string;
    incompleteReason?: string | null;
    requestId?: string | null;
    retryable: boolean;
    submissionUnknown: boolean;
  } | null;
  result?: {
    disposition: "candidate_ready" | "needs_input" | "invalid";
    resultShotPlanVersionId?: string | null;
    recoverable: boolean;
    draft?: {
      targetDurationSeconds?: number | null;
      directorTreatment?: Record<string, unknown> | null;
      shots: Array<Record<string, unknown>>;
    } | null;
    issues: Array<{
      code: string;
      severity: "fatal" | "blocking" | "warning";
      path: string;
      message: string;
      suggestedAction?: string | null;
      providerValue?: unknown;
    }>;
  } | null;
}

export interface GenerationPreviewDto {
  inputHash: string;
  provider: string;
  model: string;
  prompt: string;
  promptSummary: string;
  promptSections: GenerationPromptSectionDto[];
  negativePrompt: string;
  expectedCostMicros: number | null;
  costEstimateStatus: "priced" | "unmetered_paid";
  capabilityRevision: string;
  storyVersionId: string;
  shotPlanVersionId: string;
  selectionHash: string;
  durationSeconds: number;
  seriesEpisodeId?: string | null;
  continuitySnapshotId?: string | null;
  inputSnapshot?: GenerationInputSnapshotDto | null;
  references: Array<{
    assetId: string;
    role: string;
    priority: number;
    included: boolean;
    omittedReason?: string;
    sha256: string;
  }>;
  videoReferences: Array<{
    assetId: string;
    role: "previous_episode_video";
    sha256: string;
    durationSeconds?: number | null;
    included: boolean;
  }>;
  warnings: Array<{ code: string; message: string }>;
}

export interface AssetGenerationPreviewDto {
  inputHash: string;
  kind: AssetGenerationKind;
  provider: string;
  model: string;
  capabilityRevision: string;
  prompt: string;
  negativePrompt: string;
  references: GenerationPreviewDto["references"];
  expectedCostMicros: number | null;
  costEstimateStatus: "priced" | "unmetered_paid";
  imageInputSnapshot?: ImageGenerationInputSnapshotDto | null;
  warnings: Array<{ code: string; message: string }>;
}

export interface EditDecisionListDto {
  sourceVideoSelections: Array<{
    assetId: string;
    sha256: string;
    startMs: number;
    endMs: number;
  }>;
  transitions: Array<{
    afterClipIndex: number;
    type: "none" | "fade" | "crossfade";
    durationMs: number;
  }>;
  audioPolicy: "native" | "mute" | "native_fades";
  output: { aspectRatio: "9:16"; width: 720; height: 1280; format: "mp4" };
}

export interface EditVersionDto {
  id: string;
  projectId: string;
  revision: number;
  sourceSelectionHash: string;
  edl: EditDecisionListDto | EditDecisionListV2Dto;
  status: "draft" | "rendered" | "approved";
  renderedAssetId?: string;
  parentEditVersionId?: string;
  formatVersion: 1 | 2;
  active: boolean;
  timelineHash?: string;
  createdAt: string;
}

export type FrameRangeDto = components["schemas"]["FrameRange"];
export type EditDecisionListV2Dto = components["schemas"]["EditDecisionListV2"];
export type SegmentRepairPreviewCommand = components["schemas"]["SegmentRepairPreviewCommand"];
export type SegmentRepairPreviewDto = components["schemas"]["SegmentRepairPreviewDto"];
export type SegmentRepairCreateCommand = components["schemas"]["SegmentRepairCreateCommand"];
export type SegmentRepairApproveCommand = components["schemas"]["SegmentRepairApproveCommand"];
export type VideoRepairDto = components["schemas"]["VideoRepairDto"];

export interface WorkspaceDto {
    eventCursor: number;
    project: ProjectDto;
  steps: Array<{ id: string; ready: boolean }>;
  activeStory: StoryVersionDto | null;
  activeShotPlan: ShotPlanVersionDto | null;
  selections: Partial<Record<AssetSlot, AssetDto>>;
  selectionHash: string;
  latestVideoJob?: JobDto | null;
  latestDirectorJob?: JobDto | null;
  latestRepairJob?: JobDto | null;
  latestAssetJob?: JobDto | null;
}
