import { describe, expect, it } from "vitest";

import { buildAcceptanceEvidence } from "./acceptanceEvidence";
import type { AssetDto, JobDto } from "./api/types";

function job(id: string, kind: JobDto["kind"], providerTaskId: string): JobDto {
  return {
    id,
    projectId: "project-1",
    kind,
    status: "succeeded",
    inputHash: "a".repeat(64),
    providerTaskId,
    frozenInput: {},
    resultAssetIds: [],
    createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-01T00:00:00Z",
  };
}

describe("buildAcceptanceEvidence", () => {
  it("keeps the video generation and later diagnosis provider identities separate", () => {
    const asset: AssetDto = {
      id: "video-asset-1",
      projectId: "project-1",
      producingJobId: "video-job-1",
      role: "video",
      mediaType: "video",
      sha256: "b".repeat(64),
      byteSize: 1024,
      metadata: {
        width: 480,
        height: 854,
        durationMs: 12000,
        providerTaskId: "video-task-1",
        providerRequestId: "video-request-1",
        videoDiagnosisJobId: "diagnosis-job-1",
        videoDiagnosisProviderTaskId: "diagnosis-task-1",
        videoDiagnosisProviderRequestId: "diagnosis-request-1",
      },
      createdAt: "2026-09-01T00:00:00Z",
    };

    const evidence = buildAcceptanceEvidence({
      exportedAt: "2026-09-01T00:00:00Z",
      projectId: "project-1",
      theme: "雨天擦爪",
      asset,
      videoJob: job("video-job-1", "generate_video", "video-task-from-job"),
      diagnosisJob: job("diagnosis-job-1", "diagnose_video", "diagnosis-task-from-job"),
      quality: { technical: "pass" },
      notes: "页面验收",
    });

    expect(evidence.providerJobId).toBe("video-job-1");
    expect(evidence.providerTaskId).toBe("video-task-1");
    expect(evidence.providerRequestId).toBe("video-request-1");
    expect(evidence.diagnosisJobId).toBe("diagnosis-job-1");
    expect(evidence.diagnosisProviderTaskId).toBe("diagnosis-task-1");
    expect(evidence.diagnosisProviderRequestId).toBe("diagnosis-request-1");
  });
});
