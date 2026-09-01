import type {
  AssetDto,
  AssetGenerationKind,
  AssetGenerationPreviewDto,
  AssetSlot,
  CanonProfileDto,
  EditDecisionListDto,
  EditVersionDto,
  EnvironmentPresetDto,
  GenerationPreviewDto,
  JobDto,
  PlannerSnapshotDto,
  ProjectCreate,
  ProjectDto,
  RuntimeBootstrapDto,
  ShotPlanVersionDto,
  StoryVersionDto,
  WorkspaceDto,
  ValidationRunDto,
  ValidationRunPreviewDto,
} from "./types";

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

  previewValidationRun(): Promise<ValidationRunPreviewDto> {
    return this.json("/api/v1/validation-runs/preview", "POST", {});
  }

  authorizeValidationRun(expectedManifestHash: string): Promise<ValidationRunDto> {
    return this.json("/api/v1/validation-runs", "POST", {
      expectedManifestHash,
      paidCallAcknowledged: true,
    });
  }

  currentValidationRun(): Promise<ValidationRunDto | null> {
    return this.request("/api/v1/validation-runs/current");
  }

  pauseValidationRun(runId: string): Promise<ValidationRunDto> {
    return this.json(`/api/v1/validation-runs/${runId}/pause`, "POST", {});
  }

  projects(): Promise<ProjectDto[]> {
    return this.request("/api/v1/projects");
  }

  createProject(draft: ProjectCreate): Promise<ProjectDto> {
    return this.json("/api/v1/projects", "POST", draft);
  }

  project(projectId: string): Promise<ProjectDto> {
    return this.request(`/api/v1/projects/${projectId}`);
  }

  workspace(projectId: string): Promise<WorkspaceDto> {
    return this.request(`/api/v1/projects/${projectId}/workspace`);
  }

  planner(projectId: string): Promise<PlannerSnapshotDto> {
    return this.request(`/api/v1/projects/${projectId}/planner`);
  }

  plannerMessage(
    projectId: string,
    command: { text: string; expectedContextRevision: number; idempotencyKey: string; validationRunId?: string; paidCallAcknowledged?: boolean },
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

  environmentPresets(): Promise<EnvironmentPresetDto[]> {
    return this.request("/api/v1/environment-presets");
  }

  shotPlans(projectId: string): Promise<ShotPlanVersionDto[]> {
    return this.request(`/api/v1/projects/${projectId}/shot-plans`);
  }

  createShotPlan(projectId: string, draft: Record<string, unknown>): Promise<ShotPlanVersionDto> {
    return this.json(`/api/v1/projects/${projectId}/shot-plans`, "POST", draft);
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
      expectedCostMicros: number | null;
      idempotencyKey: string;
      validationRunId?: string;
      paidCallAcknowledged?: boolean;
    },
  ): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/asset-generations`, "POST", command);
  }

  diagnoseAsset(projectId: string, assetId: string, validationRunId?: string): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/assets/${assetId}/diagnose`, "POST", {
      assetId,
      idempotencyKey: crypto.randomUUID(),
      expectedCostMicros: validationRunId ? null : 0,
      validationRunId,
      paidCallAcknowledged: Boolean(validationRunId),
    });
  }

  previewVideo(projectId: string): Promise<GenerationPreviewDto> {
    return this.json(`/api/v1/projects/${projectId}/video-generations/preview`, "POST", {});
  }

  createVideoJob(
    projectId: string,
    command: { expectedInputHash: string; expectedCostMicros: number | null; idempotencyKey: string; validationRunId?: string; paidCallAcknowledged?: boolean },
  ): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/video-generations`, "POST", command);
  }

  diagnoseVideo(projectId: string, assetId: string, validationRunId?: string): Promise<JobDto> {
    return this.json(`/api/v1/projects/${projectId}/video-diagnoses`, "POST", {
      assetId,
      idempotencyKey: crypto.randomUUID(),
      expectedCostMicros: validationRunId ? null : 0,
      validationRunId,
      paidCallAcknowledged: Boolean(validationRunId),
    });
  }

  job(jobId: string): Promise<JobDto> {
    return this.request(`/api/v1/jobs/${jobId}`);
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
