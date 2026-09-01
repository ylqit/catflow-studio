import type { AssetDto, JobDto } from "./api/types";

export type AcceptanceVerdict = "pass" | "warning" | "fail" | "";

export interface AcceptanceEvidence {
  exportedAt: string;
  projectId: string;
  theme: string;
  videoAssetId: string;
  mediaSha256: string;
  technicalMetadata: Record<string, unknown>;
  providerJobId?: string;
  providerTaskId?: string;
  providerRequestId?: string;
  diagnosisJobId?: string;
  diagnosisProviderTaskId?: string;
  diagnosisProviderRequestId?: string;
  quality: Record<string, AcceptanceVerdict>;
  notes: string;
  passed: boolean;
  checkpointsSeconds: number[];
}

interface BuildAcceptanceEvidenceInput {
  exportedAt: string;
  projectId: string;
  theme: string;
  asset: AssetDto;
  videoJob?: JobDto | null;
  diagnosisJob?: JobDto | null;
  quality: Record<string, AcceptanceVerdict>;
  notes: string;
}

function metadataText(metadata: Record<string, unknown>, key: string): string | undefined {
  const value = metadata[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

export function buildAcceptanceEvidence(input: BuildAcceptanceEvidenceInput): AcceptanceEvidence {
  const { asset, diagnosisJob, videoJob } = input;
  const providerJobId = asset.producingJobId;
  const matchingVideoJob = videoJob?.kind === "generate_video"
    && (!providerJobId || videoJob.id === providerJobId)
    ? videoJob
    : undefined;
  const diagnosisJobId = metadataText(asset.metadata, "videoDiagnosisJobId");
  const matchingDiagnosisJob = diagnosisJob?.kind === "diagnose_video"
    && (!diagnosisJobId || diagnosisJob.id === diagnosisJobId)
    ? diagnosisJob
    : undefined;

  return {
    exportedAt: input.exportedAt,
    projectId: input.projectId,
    theme: input.theme,
    videoAssetId: asset.id,
    mediaSha256: asset.sha256,
    technicalMetadata: asset.metadata,
    providerJobId: providerJobId ?? matchingVideoJob?.id,
    providerTaskId: metadataText(asset.metadata, "providerTaskId") ?? matchingVideoJob?.providerTaskId,
    providerRequestId: metadataText(asset.metadata, "providerRequestId"),
    diagnosisJobId: diagnosisJobId ?? matchingDiagnosisJob?.id,
    diagnosisProviderTaskId: metadataText(asset.metadata, "videoDiagnosisProviderTaskId")
      ?? matchingDiagnosisJob?.providerTaskId,
    diagnosisProviderRequestId: metadataText(asset.metadata, "videoDiagnosisProviderRequestId"),
    quality: input.quality,
    notes: input.notes,
    passed: Object.values(input.quality).every((verdict) => verdict === "pass"),
    checkpointsSeconds: [0.5, 3, 6, 9, 11.5],
  };
}
