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
  ffmpegReady: boolean;
  ffprobeReady: boolean;
  objectPublisher: ObjectPublisherRuntimeDto;
  provider: {
    name: "fake" | "ark";
    planningModel: string;
    imageModel: string;
    videoModel: string;
    diagnosticModel: string;
    capabilityRevision: string;
    paidCallsEnabled: boolean;
    apiKeyConfigured: boolean;
    segmentRepair: {
      supported: boolean;
      blockedReason: string | null;
      maximumImageReferences: number;
      maximumVideoReferences: number;
    };
  };
}

export type ValidationCallKind =
  | "plan_story"
  | "generate_image"
  | "diagnose_image"
  | "generate_video"
  | "diagnose_video"
  | "regenerate_video_segment";

export type FixedCanonRole = "episode_child" | "episode_cat" | "pair_scale" | "style_board";

export interface ValidationCanonSnapshotDto {
  profileId: string;
  version: number;
  profileHash: string;
  childAge: "6-7";
  childHeightCm: 120;
  references: Array<{
    role: FixedCanonRole;
    assetId: string;
    sha256: string;
  }>;
}

export interface ValidationRunPreviewDto {
  manifestHash: string;
  topics: string[];
  durationSeconds: 12;
  resolution: "480p";
  aspectRatio: "9:16";
  targetBudgetCny: number;
  callLimits: Record<ValidationCallKind, number>;
  totalCallLimit: number;
  maximumVideoCalls: number;
  provider: string;
  models: Record<string, string>;
  capabilityRevision: string;
  costEstimateStatus: "priced" | "unmetered_paid";
  authorizationReady: boolean;
  blockingReasons: string[];
  canon: ValidationCanonSnapshotDto;
  repair: {
    topic: "雨天擦爪";
    issueRange: FrameRangeDto;
    prompt: string;
  };
}

export interface ValidationRunDto extends Omit<ValidationRunPreviewDto, "canon"> {
  canon: ValidationCanonSnapshotDto | null;
  id: string;
  status: "draft" | "authorized" | "paused" | "completed" | "cancelled";
  usage: Record<ValidationCallKind, number>;
  createdAt: string;
  authorizedAt?: string;
}

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

export type GenerationInputSnapshotDto = components["schemas"]["GenerationInputSnapshotDto"];

export interface JobDto {
  id: string;
  projectId: string;
  kind: "plan_story" | "plan_shots" | "generate_image" | "diagnose_image" | "generate_video" | "diagnose_video" | "probe_segment_video_data_url" | "regenerate_video_segment" | "render_export";
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
  frozenInput: Record<string, unknown>;
  resultAssetIds: string[];
  error?: { code: string; message: string; retryable: boolean; requestId?: string; submissionUnknown?: boolean; timedOut?: boolean };
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

export interface EnvironmentPresetDto {
  id: string;
  sourceProjectId: string;
  asset: AssetDto;
  active: boolean;
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
  active: boolean;
  outdated: boolean;
  createdAt: string;
}

export interface GenerationPreviewDto {
  inputHash: string;
  provider: string;
  model: string;
  prompt: string;
  negativePrompt: string;
  expectedCostMicros: number | null;
  costEstimateStatus: "priced" | "unmetered_paid";
  capabilityRevision: string;
  storyVersionId: string;
  shotPlanVersionId: string;
  selectionHash: string;
  durationSeconds: number;
  inputSnapshot?: GenerationInputSnapshotDto | null;
  references: Array<{
    assetId: string;
    role: string;
    priority: number;
    included: boolean;
    omittedReason?: string;
    sha256: string;
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
}
