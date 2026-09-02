import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkspaceDto } from "../../api/types";
import GenerationStep from "./GenerationStep.vue";

const client = vi.hoisted(() => ({
  assets: vi.fn(), runtime: vi.fn(), previewVideo: vi.fn(), createVideoJob: vi.fn(),
  eventsUrl: vi.fn(() => "/api/v1/events"), job: vi.fn(), resumeJobStorage: vi.fn(),
  diagnoseVideo: vi.fn(), selectAsset: vi.fn(), projectUsageSummary: vi.fn(),
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

describe("GenerationStep", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", class { addEventListener() {} close() {} });
    client.assets.mockResolvedValue([]);
    client.runtime.mockResolvedValue({ provider: { name: "ark", videoModel: "seedance" } });
    client.projectUsageSummary.mockResolvedValue({ projectId: "project-1", jobs: [], totals: {}, calculatedCostMicros: 0, unpricedJobCount: 0, currency: "CNY" });
    client.previewVideo.mockResolvedValue({
      inputHash: "c".repeat(64), provider: "ark", model: "seedance", prompt: "专业镜头提示词",
      negativePrompt: "禁止身份漂移", expectedCostMicros: null, costEstimateStatus: "unmetered_paid",
      capabilityRevision: "v1", storyVersionId: "story-1", shotPlanVersionId: "shot-1",
      selectionHash: "a".repeat(64), durationSeconds: 12, references: [], warnings: [],
    });
    client.createVideoJob.mockResolvedValue({ id: "job-1", status: "queued" });
  });

  it("submits one paid video job directly and shows no custom quota", async () => {
    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    await wrapper.findAll("button").find((item) => item.text().includes("生成视频"))!.trigger("click");
    await flushPromises();

    expect(client.previewVideo).toHaveBeenCalledWith("project-1");
    expect(client.createVideoJob).toHaveBeenCalledWith("project-1", {
      expectedInputHash: "c".repeat(64),
      idempotencyKey: expect.any(String),
    });
    expect(wrapper.text()).toContain("本次生成会产生模型费用");
    expect(wrapper.find(".safety-card").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Validation Run");
    expect(wrapper.text()).not.toContain("额度");
    expect(wrapper.text()).not.toContain("确认并提交");
    wrapper.unmount();
  });

  it("shows the non-paying prompt preview as soon as the step opens", async () => {
    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    expect(client.previewVideo).toHaveBeenCalledWith("project-1");
    expect(wrapper.text()).toContain("本次画面内容");
    expect(wrapper.text()).toContain("画面描述");
    expect(wrapper.text()).toContain("查看完整生成指令");
    expect(wrapper.text()).toContain("专业镜头提示词");
    wrapper.unmount();
  });
});
