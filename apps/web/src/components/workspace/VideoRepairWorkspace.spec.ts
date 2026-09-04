import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { JobDto, SegmentRepairPreviewDto, WorkspaceDto } from "../../api/types";
import VideoRepairWorkspace from "./VideoRepairWorkspace.vue";

const client = vi.hoisted(() => ({
  videoRepairs: vi.fn(),
  assets: vi.fn(),
  edits: vi.fn(),
  runtime: vi.fn(),
  previewVideoRepair: vi.fn(),
  createVideoRepair: vi.fn(),
  approveVideoRepair: vi.fn(),
  rejectVideoRepair: vi.fn(),
  job: vi.fn(),
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
  projectId: "project-1",
  baseVideoAssetId: "video-1",
  baseTimelineHash: "b".repeat(64),
  frameRate: { numerator: 24, denominator: 1 },
  issueRange: { startFrame: 0, endFrame: 96 },
  generationRange: { startFrame: 0, endFrame: 120 },
  candidateCoreRange: { startFrame: 0, endFrame: 96 },
  providerDurationSeconds: 5,
  instruction: "孩子蹲下，用软毛巾逐只擦干猫爪；猫咪自然抬爪配合，湿爪和地面水印明显减少。",
  provider: "ark",
  model: "doubao-seedance-2-0-260128",
  capabilityRevision: "ark-seedance-2.0-v1",
  prompt: "重拍擦爪动作",
  negativePrompt: "禁止身份漂移",
  imageReferences: [
    { role: "anchor_in", sha256: "1".repeat(64), frameNumber: 0, derived: true },
    { role: "anchor_out", sha256: "2".repeat(64), frameNumber: 95, derived: true },
  ],
  videoReference: {
    role: "reference_video",
    assetId: "video-1",
    sha256: "a".repeat(64),
    range: { startFrame: 0, endFrame: 120 },
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
  createdAt: "2026-09-02T12:20:00Z",
  updatedAt: "2026-09-02T12:21:00Z",
};

describe("VideoRepairWorkspace", () => {
  beforeEach(() => {
    sessionStorage.clear();
    client.videoRepairs.mockResolvedValue([]);
    client.assets.mockResolvedValue([]);
    client.edits.mockResolvedValue([]);
    client.runtime.mockResolvedValue({
      provider: { name: "ark", apiKeyConfigured: true, paidCallsEnabled: true },
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
    client.createVideoRepair.mockResolvedValue(recoveredRepairJob);
  });

  it("auto-previews one free-text instruction and exposes a four-second accessible range", async () => {
    const wrapper = mount(VideoRepairWorkspace, {
      props: { projectId: "project-1", workspace },
    });
    await flushPromises();

    expect(wrapper.find('[data-testid="edit-intent"]').exists()).toBe(false);
    expect(client.previewVideoRepair).toHaveBeenCalledWith("project-1", expect.objectContaining({
      issueRange: { startFrame: 0, endFrame: 96 },
      instruction: expect.any(String),
    }));
    expect(wrapper.text()).toContain("最短修改区间为 4.00 秒");
    expect(wrapper.text()).toContain("本次修改效果");
    expect(wrapper.text()).toContain("预览不产生费用");
    const start = wrapper.get('[aria-label="修改区间起点"]');
    const end = wrapper.get('[aria-label="修改区间终点"]');
    expect(start.attributes("max")).toBe("0");
    expect(end.attributes("min")).toBe("96");
    wrapper.unmount();
  });

  it("submits the current free-text preview in one click", async () => {
    const wrapper = mount(VideoRepairWorkspace, {
      props: { projectId: "project-1", workspace },
    });
    await flushPromises();

    await wrapper.findAll("button").find((item) => item.text().includes("生成修改结果"))!.trigger("click");
    await flushPromises();

    expect(client.previewVideoRepair).toHaveBeenCalledWith("project-1", expect.objectContaining({
      issueRange: { startFrame: 0, endFrame: 96 },
      instruction: "孩子蹲下，用软毛巾逐只擦干猫爪；猫咪自然抬爪配合，湿爪和地面水印明显减少。",
    }));
    expect(client.createVideoRepair).toHaveBeenCalledWith("project-1", {
      baseVideoAssetId: "video-1",
      baseEditVersionId: undefined,
      issueRange: { startFrame: 0, endFrame: 96 },
      instruction: "孩子蹲下，用软毛巾逐只擦干猫爪；猫咪自然抬爪配合，湿爪和地面水印明显减少。",
      expectedInputHash: "c".repeat(64),
      idempotencyKey: expect.any(String),
    });
    expect(wrapper.text()).toContain("本次操作会产生模型费用，完成后显示实际用量");
    expect(wrapper.text()).not.toContain("剩余额度");
    expect(wrapper.text()).not.toContain("确认并生成");
    expect(wrapper.get("details").attributes("open")).toBeUndefined();
    expect(wrapper.text()).not.toContain("AccessKeyId");
    wrapper.unmount();
  });

  it("clamps number input to the four-second boundary and restores the draft", async () => {
    const wrapper = mount(VideoRepairWorkspace, {
      props: { projectId: "project-1", workspace },
    });
    await flushPromises();

    const numberInputs = wrapper.findAll('input[type="number"]');
    await numberInputs[1].setValue("95");
    await numberInputs[1].trigger("change");
    await wrapper.get('[data-testid="edit-instruction"]').setValue("同时修正动作、毛巾和地面光线。");

    expect((numberInputs[1].element as HTMLInputElement).value).toBe("96");
    expect(wrapper.text()).toContain("96 帧 · 4.000 秒");
    wrapper.unmount();

    const reopened = mount(VideoRepairWorkspace, {
      props: { projectId: "project-1", workspace },
    });
    await flushPromises();
    expect((reopened.get('[data-testid="edit-instruction"]').element as HTMLTextAreaElement).value)
      .toBe("同时修正动作、毛巾和地面光线。");
    reopened.unmount();
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

    expect(wrapper.get('[data-testid="repair-job-summary"]').text()).toContain("正在生成");
    expect(wrapper.get('[data-testid="repair-job-summary"]').text()).not.toContain("repair-job-1");
    expect(wrapper.get('[data-testid="repair-job-details"]').text()).toContain("cgt-repair-task-1");
    expect(wrapper.get('[data-testid="repair-job-details"]').text()).toContain("publication-1");
    expect(wrapper.get('[data-testid="repair-job-details"]').text()).toContain("req-repair-1");
    expect(wrapper.get('[data-testid="repair-job-details"]').text()).toContain("2026-09-09T12:20:00Z");
    wrapper.unmount();
  });

  it("keeps approved Ark repairs in history instead of reopening candidate review", async () => {
    client.videoRepairs.mockResolvedValue([{
      ...preview,
      id: "repair-approved",
      selectionPolicyVersion: 2,
      legacyEditIntent: null,
      status: "approved",
      preview,
      candidateAssetId: "candidate-1",
      approvedEditVersionId: "edit-2",
    }]);
    client.assets.mockResolvedValue([{
      id: "candidate-1",
      projectId: "project-1",
      role: "repair_candidate",
      mediaType: "video",
      sha256: "f".repeat(64),
      byteSize: 1,
      metadata: { durationFrames: 144 },
      createdAt: "2026-09-01T00:00:00Z",
    }]);

    const wrapper = mount(VideoRepairWorkspace, {
      props: { projectId: "project-1", workspace },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("本次操作会产生模型费用");
    expect(wrapper.text()).toContain("生成修改结果");
    expect(wrapper.find('[data-testid="repair-candidate-review"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("repair-approved");
    expect(wrapper.text()).toContain("已创建新视频版本");
    wrapper.unmount();
  });

});
