import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkspaceDto } from "../../api/types";
import AssetsStep from "./AssetsStep.vue";

const client = vi.hoisted(() => ({
  assets: vi.fn(),
  runtime: vi.fn(),
  previewAssetGeneration: vi.fn(),
  createAssetGeneration: vi.fn(),
  diagnoseAsset: vi.fn(),
  uploadAsset: vi.fn(),
  selectAsset: vi.fn(),
  eventsUrl: vi.fn(() => "/api/v1/events"),
  job: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: client }));

const workspace: WorkspaceDto = {
  eventCursor: 0,
  project: {
    id: "project-1", title: "雨天擦爪", theme: "雨天擦爪",
    targetDurationSeconds: 12, aspectRatio: "9:16", canonProfileId: "canon-1",
    createdAt: "2026-09-01T00:00:00Z", updatedAt: "2026-09-01T00:00:00Z",
  },
  steps: [],
  activeStory: {
    id: "story-1", projectId: "project-1", revision: 1, title: "雨天擦爪",
    body: "猫咪回家，孩子替它擦爪。",
    microEvent: {
      trigger: "猫咪留下湿爪印", childAction: "孩子擦爪", catResponse: "猫咪抬爪",
      visibleChange: "水印减少", warmEnding: "猫咪走进室内",
    },
    targetDurationSeconds: 12, dialoguePolicy: "none", environmentIntent: "雨天玄关和吸水脚垫",
    active: true, createdAt: "2026-09-01T00:00:00Z",
  },
  activeShotPlan: null,
  selections: {
    episode_child: { id: "child-1", role: "episode_child", mediaType: "image", sha256: "1".repeat(64), byteSize: 1, metadata: {}, createdAt: "2026-09-01T00:00:00Z" },
    episode_cat: { id: "cat-1", role: "episode_cat", mediaType: "image", sha256: "2".repeat(64), byteSize: 1, metadata: {}, createdAt: "2026-09-01T00:00:00Z" },
    pair_scale: { id: "scale-1", role: "pair_scale", mediaType: "image", sha256: "3".repeat(64), byteSize: 1, metadata: {}, createdAt: "2026-09-01T00:00:00Z" },
    style_board: { id: "style-1", role: "style_board", mediaType: "image", sha256: "4".repeat(64), byteSize: 1, metadata: {}, createdAt: "2026-09-01T00:00:00Z" },
  },
  selectionHash: "a".repeat(64),
};

describe("AssetsStep", () => {
  const runtime = { provider: { apiKeyConfigured: true, paidCallsEnabled: true } };
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("EventSource", class { addEventListener() {} close() {} });
    client.assets.mockResolvedValue([]);
    client.runtime.mockResolvedValue({ provider: { name: "ark", imageModel: "seedream" } });
    client.previewAssetGeneration.mockResolvedValue({
      inputHash: "b".repeat(64), kind: "environment", provider: "ark", model: "seedream",
      capabilityRevision: "v1", prompt: "共享环境", negativePrompt: "不要人物",
      references: [
        { assetId: "style-1", role: "style_board", priority: 10, included: true, sha256: "4".repeat(64) },
        { assetId: "child-1", role: "episode_child", priority: 20, included: true, sha256: "1".repeat(64) },
        { assetId: "cat-1", role: "episode_cat", priority: 30, included: true, sha256: "2".repeat(64) },
      ],
      imageInputSnapshot: {
        schemaVersion: 1, state: "preview", kind: "environment", subjectPolicy: "empty_scene",
        sourceStoryVersionId: "story-1", environmentIntent: "雨天玄关和吸水脚垫",
        provider: "ark", model: "seedream", capabilityRevision: "v1", prompt: "共享环境",
        negativePrompt: "不要人物", inputHash: "b".repeat(64), promptCompilerRevision: "v2",
        createdAt: "2026-09-03T00:00:00Z", references: [],
      },
      expectedCostMicros: null, costEstimateStatus: "unmetered_paid", warnings: [],
    });
    client.createAssetGeneration.mockResolvedValue({ id: "job-1", status: "queued" });
  });

  it("generates an environment directly without a validation-run confirmation", async () => {
    const wrapper = mount(AssetsStep, {
      props: { projectId: "project-1", workspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    expect(client.previewAssetGeneration).toHaveBeenCalledWith("project-1", "environment");
    expect(wrapper.text()).toContain("雨天玄关和吸水脚垫");
    expect(wrapper.text()).toContain("空场景，不含人物与猫咪");
    expect(wrapper.text()).toContain("画风板");
    expect(wrapper.text()).toContain("本次会使用付费模型，完成后显示实际用量");

    await wrapper.findAll("button").find((item) => item.text().includes("生成环境候选"))!.trigger("click");
    await flushPromises();

    expect(client.previewAssetGeneration).toHaveBeenCalledWith("project-1", "environment");
    expect(client.createAssetGeneration).toHaveBeenCalledWith("project-1", {
      kind: "environment",
      expectedInputHash: "b".repeat(64),
      idempotencyKey: expect.any(String),
    });
    const generateButton = wrapper.findAll("button").find((item) => item.text().includes("环境候选"))!;
    expect(generateButton.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("环境生成任务正在处理");
    expect(wrapper.get(".asset-intro").text()).not.toContain("style_source");
    expect(wrapper.get(".asset-intro").text()).not.toContain("Provider");
    expect(wrapper.text()).not.toContain("图片付费确认");
    expect(wrapper.text()).not.toContain("额度");
    expect(wrapper.text()).not.toContain("确认并提交");
    wrapper.unmount();
  });

  it("restores an unfinished environment job and blocks another paid generation", async () => {
    const runningWorkspace: WorkspaceDto = {
      ...workspace,
      latestAssetJob: {
        id: "job-restored",
        projectId: "project-1",
        seriesId: null,
        kind: "generate_image",
        status: "submitting",
        inputHash: "b".repeat(64),
        frozenInput: {},
        resultAssetIds: [],
        billingStatus: "pending",
        createdAt: "2026-09-04T08:00:00Z",
        updatedAt: "2026-09-04T08:00:01Z",
      },
    };
    const wrapper = mount(AssetsStep, {
      props: { projectId: "project-1", workspace: runningWorkspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    const generateButton = wrapper.findAll("button").find((item) => item.text().includes("环境候选"))!;
    expect(generateButton.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("环境生成：正在准备");
    expect(wrapper.text()).toContain("环境生成任务正在处理");
    await generateButton.trigger("click");
    expect(client.createAssetGeneration).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("opens fixed assets and environment candidates in the image viewer", async () => {
    client.assets.mockResolvedValue([
      { id: "environment-1", projectId: "project-1", role: "environment", mediaType: "image", sha256: "5".repeat(64), byteSize: 1, metadata: {}, createdAt: "2026-09-03T00:00:00Z" },
    ]);
    const wrapper = mount(AssetsStep, {
      props: { projectId: "project-1", workspace, runtime },
      global: { plugins: [createPinia()], stubs: { teleport: true } },
    });
    await flushPromises();

    const openButtons = wrapper.findAll("button").filter((item) => item.text().includes("查看大图"));
    expect(openButtons.length).toBeGreaterThanOrEqual(5);
    await wrapper.get(".environment-candidates .image-open").trigger("click");
    expect(wrapper.get('[role="dialog"]').text()).toContain("对照固定参考");
    expect(wrapper.get(".viewer-main-image").attributes("src")).toContain("environment-1");
    wrapper.unmount();
  });
});
