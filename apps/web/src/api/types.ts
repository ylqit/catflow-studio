export interface RuntimeBootstrapDto {
  csrfToken: string;
  baseUrl: string;
  localOnly: true;
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
  kind: "plan_story" | "generate_image" | "diagnose_image" | "generate_video" | "render_export";
  status:
    | "queued"
    | "submitting"
    | "submitted"
    | "polling"
    | "storing"
    | "succeeded"
    | "failed"
    | "cancel_requested"
    | "cancelled";
  inputHash: string;
  providerTaskId?: string;
  expectedCostMicros?: number;
  frozenInput: Record<string, unknown>;
  resultAssetIds: string[];
  error?: { code: string; message: string; retryable: boolean };
}

export interface PlannerMessageDto {
  id: string;
  role: "user" | "assistant";
  content: string;
  ordinal: number;
  createdAt: string;
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
  projectId: string;
  role: string;
  mediaType: "image" | "video" | "audio";
  sha256: string;
  byteSize: number;
  metadata: Record<string, unknown>;
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
  expectedCostMicros: number;
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
  expectedCostMicros: number;
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
  project: ProjectDto;
  steps: Array<{ id: string; ready: boolean }>;
  activeStory: StoryVersionDto | null;
  activeShotPlan: ShotPlanVersionDto | null;
  selections: Partial<Record<AssetSlot, AssetDto>>;
  selectionHash: string;
}
