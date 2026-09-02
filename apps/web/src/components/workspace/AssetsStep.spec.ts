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
  steps: [], activeStory: null, activeShotPlan: null, selections: {}, selectionHash: "a".repeat(64),
};

describe("AssetsStep", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", class { addEventListener() {} close() {} });
    client.assets.mockResolvedValue([]);
    client.runtime.mockResolvedValue({ provider: { name: "ark", imageModel: "seedream" } });
    client.previewAssetGeneration.mockResolvedValue({
      inputHash: "b".repeat(64), kind: "environment", provider: "ark", model: "seedream",
      capabilityRevision: "v1", prompt: "共享环境", negativePrompt: "不要人物",
      references: [], expectedCostMicros: null, costEstimateStatus: "unmetered_paid", warnings: [],
    });
    client.createAssetGeneration.mockResolvedValue({ id: "job-1", status: "queued" });
  });

  it("generates an environment directly without a validation-run confirmation", async () => {
    const wrapper = mount(AssetsStep, {
      props: { projectId: "project-1", workspace },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    await wrapper.findAll("button").find((item) => item.text().includes("生成环境候选"))!.trigger("click");
    await flushPromises();

    expect(client.previewAssetGeneration).toHaveBeenCalledWith("project-1", "environment");
    expect(client.createAssetGeneration).toHaveBeenCalledWith("project-1", {
      kind: "environment",
      expectedInputHash: "b".repeat(64),
      idempotencyKey: expect.any(String),
    });
    expect(wrapper.text()).toContain("本次会使用付费模型，完成后显示实际用量");
    expect(wrapper.get(".asset-intro").text()).not.toContain("style_source");
    expect(wrapper.get(".asset-intro").text()).not.toContain("Provider");
    expect(wrapper.text()).not.toContain("图片付费确认");
    expect(wrapper.text()).not.toContain("额度");
    expect(wrapper.text()).not.toContain("确认并提交");
    wrapper.unmount();
  });
});
