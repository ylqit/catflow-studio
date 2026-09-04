import type {
  AssetDto,
  AssetGenerationKind,
  AssetGenerationPreviewDto,
  AssetSlot,
  CanonProfileDto,
  EditDecisionListDto,
  EditVersionDto,
  GenerationPreviewDto,
  JobDto,
  JobUsageDto,
  PlannerSnapshotDto,
  ObjectPublisherRuntimeDto,
  ProjectCreate,
  ProjectCollectionDto,
  ProjectDto,
  ProjectLibraryBatchAction,
  ProjectLibraryItemDto,
  ProjectLibraryPageDto,
  ProjectLibraryQuery,
  ProjectSeriesContextDto,
  ProjectTagSuggestionDto,
  ProjectUsageSummaryDto,
  RateCardRevisionDto,
  RuntimeBootstrapDto,
  SeriesCreateCommand,
  SeriesEpisodeDto,
  SeriesEpisodeStoryPreviewDto,
  SeriesAssetBindingDto,
  SeriesPlanDraft,
  SeriesPlanPreviewDto,
  SeriesPlanVersionDto,
  StoryImportCreateResultDto,
  StoryImportPreviewDto,
  StoryImportProjectDto,
  StorySeriesDto,
  StorySourceDocumentDto,
  EpisodeContinuityDto,
  EpisodeContinuityFramesDto,
  EpisodeContinuitySnapshotDto,
  SegmentRepairApproveCommand,
  SegmentRepairCreateCommand,
  SegmentRepairPreviewCommand,
  SegmentRepairPreviewDto,
  ShotPlanGenerationAttemptDto,
  ShotPlanVersionDto,
  StoryVersionDto,
  WorkspaceDto,
  VideoRepairDto,
} from "./types";
import { buildLibraryQuery } from "../projectLibrary";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

export class CatFlowClient {
  private csrfToken: string | null = null;

  async bootstrap(): Promise<RuntimeBootstrapDto> {
    const bootstrap = await this.request<RuntimeBootstrapDto>("/api/v1/runtime/bootstrap");
    this.csrfToken = bootstrap.csrfToken;
    return bootstrap;
  }

  runtime(): Promise<RuntimeBootstrapDto> {
    return this.bootstrap();
  }

  rateCards(): Promise<RateCardRevisionDto[]> {
    return this.request("/api/v1/runtime/rate-cards");
  }

  publishRateCard(command: Omit<RateCardRevisionDto, "active" | "createdAt">): Promise<RateCardRevisionDto> {
    return this.json("/api/v1/runtime/rate-cards", "POST", command);
  }

  checkObjectPublisher(): Promise<ObjectPublisherRuntimeDto> {
    return this.json("/api/v1/runtime/object-publisher/check", "POST", {});
  }

  currentCanon(): Promise<CanonProfileDto> {
    return this.request("/api/v1/canon/current");
  }

  uploadCanonAsset(role: "episode_child" | "episode_cat" | "pair_scale" | "style_board", file: File): Promise<AssetDto> {
    const body = new FormData();
    body.append("file", file);
    return this.write(`/api/v1/canon/assets/upload?role=${role}`, "POST", body);
  }

  publishCanon(fixedAssets: Record<string, string>): Promise<CanonProfileDto> {
    return this.json("/api/v1/canon/revisions", "POST", { fixedAssets });
  }

  projects(): Promise<ProjectDto[]> {
    return this.request("/api/v1/projects");
  }

  projectLibrary(query: ProjectLibraryQuery = {}): Promise<ProjectLibraryPageDto> {
    const params = buildLibraryQuery(query);
    return this.request(`/api/v1/project-library?${params.toString()}`);
  }

  projectCollections(): Promise<ProjectCollectionDto[]> {
    return this.request("/api/v1/project-collections");
  }

  projectTags(query = ""): Promise<ProjectTagSuggestionDto[]> {
    const params = new URLSearchParams();
    if (query) params.set("query", query);
    const suffix = params.size ? `?${params.toString()}` : "";
    return this.request(`/api/v1/project-tags${suffix}`);
  }

  createProjectCollection(command: { name: string; colorKey: ProjectCollectionDto["colorKey"] }): Promise<ProjectCollectionDto> {
    return this.json("/api/v1/project-collections", "POST", command);
  }

  updateProjectCollection(collectionId: string, command: { name?: string; colorKey?: ProjectCollectionDto["colorKey"]; sortOrder?: number }): Promise<ProjectCollectionDto> {
    return this.json(`/api/v1/project-collections/${collectionId}`, "PATCH", command);
  }

  archiveProjectCollection(collectionId: string): Promise<ProjectCollectionDto> {
    return this.json(`/api/v1/project-collections/${collectionId}/archive`, "POST", {});
  }

  restoreProjectCollection(collectionId: string): Promise<ProjectCollectionDto> {
    return this.json(`/api/v1/project-collections/${collectionId}/restore`, "POST", {});
  }

  organizeProject(projectId: string, command: { collectionId?: string | null; tags?: string[]; pinned?: boolean; archived?: boolean }): Promise<ProjectLibraryItemDto> {
    return this.json(`/api/v1/projects/${projectId}/organization`, "PATCH", command);
  }

  projectLibraryAction(command: ProjectLibraryBatchAction): Promise<{ updatedCount: number }> {
    return this.json("/api/v1/project-library/actions", "POST", command);
  }

  createProject(draft: ProjectCreate): Promise<ProjectDto> {
    return this.json("/api/v1/projects", "POST", draft);
  }

  storySeries(): Promise<StorySeriesDto[]> {
    return this.request("/api/v1/story-series");
  }

  createStorySeries(command: SeriesCreateCommand): Promise<StorySeriesDto> {
    return this.json("/api/v1/story-series", "POST", command);
  }

  storySeriesDetail(seriesId: string): Promise<StorySeriesDto> {
    return this.request(`/api/v1/story-series/${seriesId}`);
  }

  previewSeriesPlan(seriesId: string): Promise<SeriesPlanPreviewDto> {
    return this.json(`/api/v1/story-series/${seriesId}/plans/preview`, "POST", {});
  }

  generateSeriesPlan(
    seriesId: string,
    command: { expectedInputHash: string; idempotencyKey: string },
  ): Promise<JobDto> {
    return this.json(`/api/v1/story-series/${seriesId}/plans/generations`, "POST", command);
  }

  seriesPlans(seriesId: string): Promise<SeriesPlanVersionDto[]> {
    return this.request(`/api/v1/story-series/${seriesId}/plans`);
  }

  materializeSeriesPlan(
    seriesId: string,
    planVersionId: string,
    command: { basePlanVersionId: string; plan: SeriesPlanDraft; idempotencyKey: string },
  ): Promise<SeriesPlanVersionDto> {
    return this.json(
      `/api/v1/story-series/${seriesId}/plans/${planVersionId}/materialize`,
      "POST",
      command,
    );
  }

  activateSeriesPlan(
    seriesId: string,
    planVersionId: string,
    command: { expectedActivePlanVersionId?: string | null; idempotencyKey: string },
  ): Promise<SeriesPlanVersionDto> {
    return this.json(
      `/api/v1/story-series/${seriesId}/plans/${planVersionId}/activate`,
      "POST",
      command,
    );
  }

  rejectSeriesPlan(seriesId: string, planVersionId: string): Promise<SeriesPlanVersionDto> {
    return this.json(`/api/v1/story-series/${seriesId}/plans/${planVersionId}/reject`, "POST", {});
  }

  seriesEpisodes(seriesId: string): Promise<SeriesEpisodeDto[]> {
    return this.request(`/api/v1/story-series/${seriesId}/episodes`);
  }

  seriesJobs(seriesId: string): Promise<JobDto[]> {
    return this.request(`/api/v1/story-series/${seriesId}/jobs`);
  }

  materializeSeriesEpisode(seriesId: string, episodeId: string, idempotencyKey: string): Promise<ProjectDto> {
    return this.json(
      `/api/v1/story-series/${seriesId}/episodes/${episodeId}/materialize`,
      "POST",
      { idempotencyKey },
    );
  }

  previewSeriesEpisodeStory(
    seriesId: string,
    episodeId: string,
    additionalNotes?: string | null,
  ): Promise<SeriesEpisodeStoryPreviewDto> {
    return this.json(
      `/api/v1/story-series/${seriesId}/episodes/${episodeId}/story-generations/preview`,
      "POST",
      { additionalNotes: additionalNotes ?? null },
    );
  }

  generateSeriesEpisodeStory(
    seriesId: string,
    episodeId: string,
    command: { expectedInputHash: string; additionalNotes?: string | null; idempotencyKey: string },
  ): Promise<JobDto> {
    return this.json(
      `/api/v1/story-series/${seriesId}/episodes/${episodeId}/story-generations`,
      "POST",
      command,
    );
  }

  seriesEpisodeContinuity(seriesId: string, episodeId: string): Promise<EpisodeContinuityDto> {
    return this.request(`/api/v1/story-series/${seriesId}/episodes/${episodeId}/continuity`);
  }

  seriesEpisodeContinuityFrames(seriesId: string, episodeId: string): Promise<EpisodeContinuityFramesDto> {
    return this.request(`/api/v1/story-series/${seriesId}/episodes/${episodeId}/continuity/frames`);
  }

  selectSeriesEpisodeContinuityKeyframes(
    seriesId: string,
    episodeId: string,
    assetIds: string[],
  ): Promise<AssetDto[]> {
    return this.json(
      `/api/v1/story-series/${seriesId}/episodes/${episodeId}/continuity/keyframes`,
      "PUT",
      { assetIds },
    );
  }

  seriesAssets(seriesId: string): Promise<SeriesAssetBindingDto[]> {
    return this.request(`/api/v1/story-series/${seriesId}/assets`);
  }

  updateSeriesAssets(
    seriesId: string,
    bindings: Array<{ bindingKey: string; role: string; assetId: string }>,
  ): Promise<SeriesAssetBindingDto[]> {
    return this.json(`/api/v1/story-series/${seriesId}/assets`, "PATCH", { bindings });
  }

  confirmSeriesEpisodeContinuity(
    seriesId: string,
    episodeId: string,
    command: {
      direction: "incoming" | "outgoing";
      state: EpisodeContinuitySnapshotDto["state"];
      decisions: Record<string, "inherit" | "adjust" | "reset">;
      expectedSnapshotId?: string | null;
      idempotencyKey: string;
    },
  ): Promise<EpisodeContinuitySnapshotDto> {
    return this.json(
      `/api/v1/story-series/${seriesId}/episodes/${episodeId}/continuity/confirm`,
      "POST",
      command,
    );
  }

  previewStoryImport(command: {
    rawText: string;
    sourceFormat: "paste" | "txt" | "md";
    fileName?: string | null;
  }): Promise<StoryImportPreviewDto> {
    return this.json("/api/v1/story-imports/preview", "POST", command);
  }

  createStoryImport(command: {
    rawText: string;
    sourceFormat: "paste" | "txt" | "md";
    fileName?: string | null;
    expectedInputHash: string;
    idempotencyKey: string;
  }): Promise<StoryImportCreateResultDto> {
    return this.json("/api/v1/story-imports", "POST", command);
  }

  storyImports(): Promise<StorySourceDocumentDto[]> {
    return this.request("/api/v1/story-imports");
  }

  storyImport(documentId: string): Promise<StorySourceDocumentDto> {
    return this.request(`/api/v1/story-imports/${documentId}`);
  }

  reanalyzeStoryImport(
    documentId: string,
    command: { expectedInputHash: string; idempotencyKey: string },
  ): Promise<JobDto> {
    return this.json(`/api/v1/story-imports/${documentId}/reanalyze`, "POST", command);
  }

  confirmStoryImport(
    documentId: string,
    command: {
      suggestionId: string;
      target: "new_series" | "append_series" | "independent" | "revision" | "reference";
      targetSeriesId?: string | null;
      targetProjectId?: string | null;
      idempotencyKey: string;
    },
  ): Promise<{ series?: StorySeriesDto | null; projects: StoryImportProjectDto[] }> {
    return this.json(`/api/v1/story-imports/${documentId}/confirm`, "POST", command);
  }

  project(projectId: string): Promise<ProjectDto> {
    return this.request(`/api/v1/projects/${projectId}`);
  }

  projectSeriesContext(projectId: string): Promise<ProjectSeriesContextDto | null> {
    return this.request(`/api/v1/projects/${projectId}/series-context`);
  }

  workspace(projectId: string): Promise<WorkspaceDto> {
    return this.request(`/api/v1/projects/${projectId}/workspace`);
  }

  planner(projectId: string): Promise<PlannerSnapshotDto> {
    return this.request(`/api/v1/projects/${projectId}/planner`);
  }

  plannerMessage(
    projectId: string,
    command: { text: string; expectedContextRevision: number; idempotencyKey: string },
  ): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/planner/messages`, "POST", command);
  }

  adoptProposal(projectId: string, proposalId: string): Promise<StoryVersionDto> {
    return this.json(
      `/api/v1/projects/${projectId}/planner/proposals/${proposalId}/adopt`,
      "POST",
      {},
    );
  }

  assets(projectId: string): Promise<AssetDto[]> {
    return this.request(`/api/v1/projects/${projectId}/assets`);
  }

  shotPlans(projectId: string): Promise<ShotPlanVersionDto[]> {
    return this.request(`/api/v1/projects/${projectId}/shot-plans`);
  }

  createShotPlan(projectId: string, draft: Record<string, unknown>): Promise<ShotPlanVersionDto> {
    return this.json(`/api/v1/projects/${projectId}/shot-plans`, "POST", draft);
  }

  generateShotPlan(projectId: string, idempotencyKey: string): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/shot-plans/generations`, "POST", {
      idempotencyKey,
    });
  }

  shotPlanGenerationAttempts(
    projectId: string,
    limit = 20,
  ): Promise<ShotPlanGenerationAttemptDto[]> {
    return this.request(
      `/api/v1/projects/${projectId}/shot-plans/generations?limit=${limit}`,
    );
  }

  recoverShotPlanGeneration(
    projectId: string,
    jobId: string,
    idempotencyKey: string,
  ): Promise<ShotPlanVersionDto> {
    return this.json(
      `/api/v1/projects/${projectId}/shot-plans/generations/${jobId}/recover`,
      "POST",
      { idempotencyKey },
    );
  }

  materializeShotPlanGeneration(
    projectId: string,
    jobId: string,
    payload: Record<string, unknown>,
    idempotencyKey: string,
  ): Promise<ShotPlanVersionDto> {
    return this.json(
      `/api/v1/projects/${projectId}/shot-plans/generations/${jobId}/materialize`,
      "POST",
      { idempotencyKey, payload },
    );
  }

  activateShotPlan(
    projectId: string,
    shotPlanVersionId: string,
    expectedActiveShotPlanVersionId: string | null,
    idempotencyKey: string,
  ): Promise<ShotPlanVersionDto> {
    return this.json(
      `/api/v1/projects/${projectId}/shot-plans/${shotPlanVersionId}/activate`,
      "POST",
      { expectedActiveShotPlanVersionId, idempotencyKey },
    );
  }

  rejectShotPlan(projectId: string, shotPlanVersionId: string): Promise<ShotPlanVersionDto> {
    return this.json(
      `/api/v1/projects/${projectId}/shot-plans/${shotPlanVersionId}/reject`,
      "POST",
      {},
    );
  }

  uploadAsset(projectId: string, role: AssetSlot, file: File): Promise<AssetDto> {
    const body = new FormData();
    body.append("file", file);
    return this.write(`/api/v1/projects/${projectId}/assets/upload?role=${role}`, "POST", body);
  }

  selectAsset(projectId: string, slot: AssetSlot, assetId: string): Promise<unknown> {
    return this.json(`/api/v1/projects/${projectId}/selections`, "POST", { slot, assetId });
  }

  previewAssetGeneration(
    projectId: string,
    kind: AssetGenerationKind,
  ): Promise<AssetGenerationPreviewDto> {
    return this.json(`/api/v1/projects/${projectId}/asset-generations/preview`, "POST", {
      kind,
    });
  }

  createAssetGeneration(
    projectId: string,
    command: {
      kind: AssetGenerationKind;
      expectedInputHash: string;
      idempotencyKey: string;
    },
  ): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/asset-generations`, "POST", command);
  }

  diagnoseAsset(projectId: string, assetId: string, idempotencyKey: string): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/assets/${assetId}/diagnose`, "POST", {
      assetId,
      idempotencyKey,
    });
  }

  previewVideo(
    projectId: string,
    includePreviousEpisodeVideo = false,
  ): Promise<GenerationPreviewDto> {
    return this.json(`/api/v1/projects/${projectId}/video-generations/preview`, "POST", {
      includePreviousEpisodeVideo,
    });
  }

  createVideoJob(
    projectId: string,
    command: {
      expectedInputHash: string;
      idempotencyKey: string;
      includePreviousEpisodeVideo?: boolean;
    },
  ): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/video-generations`, "POST", command);
  }

  diagnoseVideo(projectId: string, assetId: string, idempotencyKey: string): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/video-diagnoses`, "POST", {
      assetId,
      idempotencyKey,
    });
  }

  job(jobId: string): Promise<JobDto> {
    return this.request(`/api/v1/jobs/${jobId}`);
  }

  jobUsage(jobId: string): Promise<JobUsageDto> {
    return this.request(`/api/v1/jobs/${jobId}/usage`);
  }

  projectUsageSummary(projectId: string): Promise<ProjectUsageSummaryDto> {
    return this.request(`/api/v1/projects/${projectId}/usage-summary`);
  }

  resumeJobStorage(jobId: string): Promise<JobDto> {
    return this.json(`/api/v1/jobs/${jobId}/resume-storage`, "POST", {});
  }

  edits(projectId: string): Promise<EditVersionDto[]> {
    return this.request(`/api/v1/projects/${projectId}/edits`);
  }

  createEdit(projectId: string, edl: EditDecisionListDto): Promise<EditVersionDto> {
    return this.json(`/api/v1/projects/${projectId}/edits`, "POST", { edl });
  }

  createExport(
    projectId: string,
    command: { editVersionId: string; idempotencyKey: string },
  ): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/exports`, "POST", command);
  }

  previewVideoRepair(
    projectId: string,
    command: SegmentRepairPreviewCommand,
  ): Promise<SegmentRepairPreviewDto> {
    return this.json(`/api/v1/projects/${projectId}/video-edits/preview`, "POST", command);
  }

  createVideoRepair(
    projectId: string,
    command: SegmentRepairCreateCommand,
  ): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/video-edits`, "POST", command);
  }

  videoRepairs(projectId: string): Promise<VideoRepairDto[]> {
    return this.request(`/api/v1/projects/${projectId}/video-edits`);
  }

  videoRepair(projectId: string, repairId: string): Promise<VideoRepairDto> {
    return this.request(`/api/v1/projects/${projectId}/video-edits/${repairId}`);
  }

  approveVideoRepair(
    projectId: string,
    repairId: string,
    command: SegmentRepairApproveCommand,
  ): Promise<EditVersionDto> {
    return this.json(
      `/api/v1/projects/${projectId}/video-edits/${repairId}/approve`,
      "POST",
      command,
    );
  }

  rejectVideoRepair(projectId: string, repairId: string): Promise<VideoRepairDto> {
    return this.json(
      `/api/v1/projects/${projectId}/video-edits/${repairId}/reject`,
      "POST",
      {},
    );
  }

  approveFinal(projectId: string, assetId: string): Promise<unknown> {
    return this.json(`/api/v1/projects/${projectId}/final-selection`, "POST", { assetId });
  }

  eventsUrl(afterEventId: number): string {
    return `/api/v1/events?afterEventId=${afterEventId}`;
  }

  private async json<T>(path: string, method: string, body: unknown): Promise<T> {
    return this.write(path, method, JSON.stringify(body), { "Content-Type": "application/json" });
  }

  private async write<T>(
    path: string,
    method: string,
    body: BodyInit,
    headers: Record<string, string> = {},
  ): Promise<T> {
    if (this.csrfToken === null) {
      await this.bootstrap();
    }
    return this.request<T>(path, {
      method,
      headers: {
        ...headers,
        "X-CatFlow-CSRF": this.csrfToken ?? "",
      },
      body,
    });
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    if (!path.startsWith("/api/v1/")) {
      throw new Error(`CatFlowClient only accepts /api/v1 paths: ${path}`);
    }
    const response = await window.fetch(path, init);
    const contentType = response.headers.get("content-type") ?? "";
    const payload = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = typeof payload === "object" && payload !== null && "detail" in payload
        ? (payload as { detail: unknown }).detail
        : payload;
      throw new ApiError(response.status, detail);
    }
    return payload as T;
  }
}

export const api = new CatFlowClient();
