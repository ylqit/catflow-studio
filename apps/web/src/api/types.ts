export interface RuntimeBootstrapDto {
  csrfToken: string;
  baseUrl: string;
  localOnly: true;
  databaseReady: boolean;
  workerReady: boolean;
  ffmpegReady: boolean;
  ffprobeReady: boolean;
  provider: {
    name: "fake" | "ark";
    planningModel: string;
    imageModel: string;
    videoModel: string;
    diagnosticModel: string;
    capabilityRevision: string;
    paidCallsEnabled: boolean;
    apiKeyConfigured: boolean;
  };
}

export type ValidationCallKind =
  | "plan_story"
  | "generate_image"
  | "diagnose_image"
  | "generate_video"
  | "diagnose_video";

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
  canon: ValidationCanonSnapshotDto;
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

export interface JobDto {
  id: string;
  projectId: string;
  kind: "plan_story" | "generate_image" | "diagnose_image" | "generate_video" | "diagnose_video" | "render_export";
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
  expectedCostMicros?: number | null;
  frozenInput: Record<string, unknown>;
  resultAssetIds: string[];
  error?: { code: string; message: string; retryable: boolean; requestId?: string; submissionUnknown?: boolean; timedOut?: boolean };
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
  edl: EditDecisionListDto;
  status: "draft" | "rendered" | "approved";
  renderedAssetId?: string;
  createdAt: string;
}

export interface WorkspaceDto {
    eventCursor: number;
    project: ProjectDto;
  steps: Array<{ id: string; ready: boolean }>;
  activeStory: StoryVersionDto | null;
  activeShotPlan: ShotPlanVersionDto | null;
  selections: Partial<Record<AssetSlot, AssetDto>>;
  selectionHash: string;
  latestVideoJob?: JobDto | null;
}
