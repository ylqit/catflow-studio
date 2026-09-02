import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { JobDto, SegmentRepairPreviewDto, WorkspaceDto } from "../../api/types";
import VideoRepairWorkspace from "./VideoRepairWorkspace.vue";

const client = vi.hoisted(() => ({
  videoRepairs: vi.fn(),
  assets: vi.fn(),
  edits: vi.fn(),
  currentValidationRun: vi.fn(),
  runtime: vi.fn(),
  previewVideoRepair: vi.fn(),
  createVideoRepair: vi.fn(),
  approveVideoRepair: vi.fn(),
  rejectVideoRepair: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: client }));

const workspace: WorkspaceDto = {
  eventCursor: 0,
  project: {
    id: "project-1",
    title: "雨天擦爪",
    theme: "雨天擦爪",
    targetDurationSeconds: 12,
    aspectRatio: "9:16",
    canonProfileId: "canon-1",
    createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-01T00:00:00Z",
  },
  steps: [],
  activeStory: null,
  activeShotPlan: null,
  selectionHash: "1".repeat(64),
  selections: {
    video: {
      id: "video-1",
      projectId: "project-1",
      role: "video",
      mediaType: "video",
      sha256: "a".repeat(64),
      byteSize: 1,
      metadata: { durationFrames: 288 },
      createdAt: "2026-09-01T00:00:00Z",
    },
  },
};

const preview: SegmentRepairPreviewDto = {
  repairId: "repair-1",
  projectId: "project-1",
  baseVideoAssetId: "video-1",
  baseTimelineHash: "b".repeat(64),
  frameRate: { numerator: 24, denominator: 1 },
  issueRange: { startFrame: 100, endFrame: 192 },
  generationRange: { startFrame: 76, endFrame: 216 },
  candidateCoreRange: { startFrame: 24, endFrame: 116 },
  providerDurationSeconds: 6,
  provider: "ark",
  model: "doubao-seedance-2-0-260128",
  capabilityRevision: "ark-seedance-2.0-v1",
  prompt: "重拍擦爪动作",
  negativePrompt: "禁止身份漂移",
  imageReferences: [
    { role: "anchor_in", sha256: "1".repeat(64), frameNumber: 100, derived: true },
    { role: "anchor_out", sha256: "2".repeat(64), frameNumber: 191, derived: true },
  ],
  videoReference: {
    role: "reference_video",
    assetId: "video-1",
    sha256: "a".repeat(64),
    range: { startFrame: 76, endFrame: 216 },
  },
  expectedCostMicros: 0,
  costEstimateStatus: "priced",
  inputHash: "c".repeat(64),
};

const recoveredRepairJob: JobDto = {
  id: "repair-job-1",
  projectId: "project-1",
  kind: "regenerate_video_segment",
  status: "polling",
  inputHash: "d".repeat(64),
  provider: "ark",
  model: "doubao-seedance-2-0-260128",
  providerTaskId: "cgt-repair-task-1",
  videoRepairId: "repair-1",
  providerResult: { publicationId: "publication-1", requestId: "req-repair-1" },
  publication: {
    id: "publication-1",
    state: "ready",
    publicHost: "test-vedio-ylq.tos-s3-cn-beijing.volces.com",
    signedUrlExpiresAt: "2026-09-02T14:20:00Z",
    deleteAfter: "2026-09-09T12:20:00Z",
  },
  frozenInput: {},
  resultAssetIds: [],
};

describe("VideoRepairWorkspace", () => {
  beforeEach(() => {
    client.videoRepairs.mockResolvedValue([]);
    client.assets.mockResolvedValue([]);
    client.edits.mockResolvedValue([]);
    client.currentValidationRun.mockResolvedValue(null);
    client.runtime.mockResolvedValue({
      provider: { name: "ark" },
      objectPublisher: {
        configured: true,
        ready: true,
        backend: "s3",
        endpointHost: "tos-s3-cn-beijing.volces.com",
        publicHost: "test-vedio-ylq.tos-s3-cn-beijing.volces.com",
        bucket: "test-vedio-ylq",
        region: "cn-beijing",
        addressingStyle: "virtual",
        presignTtlSeconds: 7200,
        retentionDays: 7,
      },
    });
    client.previewVideoRepair.mockResolvedValue(preview);
  });

  it("supports frame clicks, I/O shortcuts, and an explicit non-paying preview", async () => {
    const wrapper = mount(VideoRepairWorkspace, {
      props: { projectId: "project-1", workspace },
    });
    await flushPromises();

    await wrapper.get('[title="100 · 00:00:04:04"]').trigger("click");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "i", altKey: true }));
    await wrapper.findAll("button").find((item) => item.text().includes("查看正式预览"))!.trigger("click");
    await flushPromises();

    expect(client.previewVideoRepair).toHaveBeenCalledWith("project-1", expect.objectContaining({
      issueRange: { startFrame: 100, endFrame: 192 },
    }));
    expect(wrapper.get('[data-testid="repair-paid-preview"]').text()).toContain("[76, 216)");
    expect(wrapper.text()).toContain("实际 6 秒");
    expect(wrapper.text()).toContain("HTTPS 已验证");
    expect(wrapper.text()).toContain("test-vedio-ylq.tos-s3-cn-beijing.volces.com");
    expect(wrapper.text()).not.toContain("AccessKeyId");
    wrapper.unmount();
  });

  it("restores the durable repair job and provider task id after reopening the page", async () => {
    client.videoRepairs.mockResolvedValue([{ ...preview, id: "repair-1", status: "generating", preview }]);
    const wrapper = mount(VideoRepairWorkspace, {
      props: {
        projectId: "project-1",
        workspace: { ...workspace, latestRepairJob: recoveredRepairJob },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Repair Job repair-job-1 · polling");
    expect(wrapper.text()).toContain("Task cgt-repair-task-1");
    expect(wrapper.text()).toContain("Publication publication-1");
    expect(wrapper.text()).toContain("Request req-repair-1");
    expect(wrapper.text()).toContain("2026-09-09T12:20:00Z");
    wrapper.unmount();
  });

});
