import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EditVersionDto, WorkspaceDto } from "../../api/types";
import DeliveryStep from "./DeliveryStep.vue";

const client = vi.hoisted(() => ({
  edits: vi.fn(),
  assets: vi.fn(),
  createEdit: vi.fn(),
  createExport: vi.fn(),
  approveFinal: vi.fn(),
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

const repairedEdit: EditVersionDto = {
  id: "edit-1",
  projectId: "project-1",
  revision: 1,
  sourceSelectionHash: "1".repeat(64),
  edl: {
    format: "catflow-edl-v2",
    frameRate: { numerator: 24, denominator: 1 },
    rootVideoAssetId: "video-1",
    rootVideoSha256: "a".repeat(64),
    videoSegments: [{
      id: "segment-1",
      assetId: "video-1",
      sha256: "a".repeat(64),
      sourceInFrame: 0,
      durationFrames: 288,
      origin: "base_video",
    }],
    transitions: [],
    audio: { policy: "preserve_original", assetId: "video-1", sha256: "a".repeat(64) },
    output: { aspectRatio: "9:16", width: 720, height: 1280, format: "mp4" },
  },
  status: "draft",
  formatVersion: 2,
  active: true,
  timelineHash: "b".repeat(64),
  createdAt: "2026-09-02T00:00:00Z",
};

describe("DeliveryStep", () => {
  beforeEach(() => {
    client.edits.mockReset().mockResolvedValueOnce([]).mockResolvedValueOnce([repairedEdit]);
    client.assets.mockReset().mockResolvedValue([]);
    client.createExport.mockReset();
    client.job.mockReset();
  });

  it("refreshes export state when a repair approval creates a new edit version", async () => {
    const wrapper = mount(DeliveryStep, {
      props: { projectId: "project-1", workspace },
      global: { stubs: { RouterLink: true, VideoRepairWorkspace: true } },
    });
    await flushPromises();

    wrapper.findComponent({ name: "VideoRepairWorkspace" }).vm.$emit("changed");
    await flushPromises();

    expect(client.edits).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("版本 1");
    expect(wrapper.get("button.primary").attributes("disabled")).toBeUndefined();
    expect(wrapper.emitted("changed")).toHaveLength(1);
  });

  it("refreshes a queued export and final assets when an SSE workspace event arrives", async () => {
    const queued = {
      id: "job-1",
      projectId: "project-1",
      kind: "render_export",
      status: "queued",
      inputHash: "c".repeat(64),
      frozenInput: {},
      resultAssetIds: [],
      createdAt: "2026-09-02T00:00:00Z",
      updatedAt: "2026-09-02T00:00:00Z",
    };
    const finalAsset = {
      ...workspace.selections.video!,
      id: "final-1",
      role: "final",
      metadata: {
        width: 720,
        height: 1280,
        durationMs: 12000,
        durationFrames: 288,
        codec: "h264",
        audioPolicy: "preserve_original",
        candidateAudioUsed: false,
      },
    };
    client.edits.mockReset().mockResolvedValue([repairedEdit]);
    client.assets.mockReset().mockResolvedValueOnce([]).mockResolvedValueOnce([finalAsset]);
    client.createExport.mockResolvedValue(queued);
    client.job.mockResolvedValue({ ...queued, status: "succeeded", resultAssetIds: ["final-1"] });

    const wrapper = mount(DeliveryStep, {
      props: { projectId: "project-1", workspace },
      global: { stubs: { RouterLink: true, VideoRepairWorkspace: true } },
    });
    await flushPromises();
    await wrapper.get("button.primary").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("导出进度：等待生成");

    await wrapper.setProps({ workspace: { ...workspace, eventCursor: 1 } });
    await flushPromises();

    expect(client.job).toHaveBeenCalledWith("job-1");
    expect(wrapper.text()).toContain("导出进度：已完成");
    expect(wrapper.text()).toContain("288 帧");
    expect(wrapper.text()).toContain("12.000 秒");
    expect(wrapper.text()).toContain("720 × 1280");
    expect(wrapper.text()).toContain("根视频原音轨");
  });
});
