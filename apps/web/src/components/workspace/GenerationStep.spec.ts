import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { JobDto, ProjectSeriesContextDto, WorkspaceDto } from "../../api/types";
import GenerationStep from "./GenerationStep.vue";

const client = vi.hoisted(() => ({
  assets: vi.fn(), runtime: vi.fn(), previewVideo: vi.fn(), createVideoJob: vi.fn(),
  eventsUrl: vi.fn(() => "/api/v1/events"), job: vi.fn(), resumeJobStorage: vi.fn(),
  diagnoseVideo: vi.fn(), selectAsset: vi.fn(), projectUsageSummary: vi.fn(),
  videoReviews: vi.fn(), createVideoReview: vi.fn(), createVideoEditDraft: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: client }));
const navigation = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("vue-router", () => ({ useRouter: () => navigation }));

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
  const runtime = { provider: { apiKeyConfigured: true, paidCallsEnabled: true } };
  beforeEach(() => {
    vi.clearAllMocks();
    client.job.mockReset();
    vi.stubGlobal("EventSource", class { addEventListener() {} close() {} });
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    client.assets.mockResolvedValue([]);
    client.videoReviews.mockResolvedValue([]);
    client.createVideoReview.mockResolvedValue({ id: "review-1" });
    client.runtime.mockResolvedValue({ provider: { name: "ark", videoModel: "seedance" } });
    client.projectUsageSummary.mockResolvedValue({ projectId: "project-1", jobs: [], totals: {}, calculatedCostMicros: 0, unpricedJobCount: 0, currency: "CNY" });
    client.previewVideo.mockResolvedValue({
      inputHash: "c".repeat(64), provider: "ark", model: "seedance", prompt: "专业镜头提示词",
      promptSummary: "孩子把食物放进野餐篮，猫咪在旁观察；篮子逐渐装满；最后一起准备出发。",
      promptSections: [
        { key: "identity_style", title: "角色与画风", content: "固定儿童、固定猫咪和统一画风。" },
        { key: "creative_treatment", title: "整体基调", content: "清晨、安静而期待。" },
        { key: "shot_execution", title: "逐镜执行", content: "镜头 1\n孩子依次整理食物，猫咪注视篮子。" },
        { key: "ending_constraints", title: "结尾与生成限制", content: "篮子装满，一人一猫准备出发。" },
      ],
      negativePrompt: "禁止身份漂移", expectedCostMicros: null, costEstimateStatus: "unmetered_paid",
      capabilityRevision: "v1", storyVersionId: "story-1", shotPlanVersionId: "shot-1",
      selectionHash: "a".repeat(64), durationSeconds: 12, references: [], videoReferences: [], warnings: [],
    });
    client.createVideoJob.mockResolvedValue({ id: "job-1", status: "queued" });
  });

  it("copies the final preview text and a selected candidate's frozen final text independently", async () => {
    const previewFinal = "当前预览\n避免项：额外角色\n参考图 1：画风\n";
    const candidateFinal = "历史候选\n避免项：多余道具\n参考图 1：身份\n";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const originalPreview = await client.previewVideo();
    client.previewVideo.mockResolvedValue({ ...originalPreview, compiledProviderPrompt: previewFinal });
    client.assets.mockResolvedValue([{ id: "candidate", role: "video", mediaType: "video", sha256: "b".repeat(64), metadata: {}, producingJobId: "frozen-job" }]);
    client.job.mockResolvedValue({
      id: "frozen-job", kind: "generate_video", status: "succeeded", inputHash: "old-hash", frozenInput: {}, resultAssetIds: ["candidate"],
      inputSnapshot: { schemaVersion: 3, prompt: "旧正文摘要", negativePrompt: "旧避免项摘要", compiledProviderPrompt: candidateFinal, source: {} },
    });
    const wrapper = mount(GenerationStep, { props: { projectId: "project-1", workspace, runtime }, global: { plugins: [createPinia()] } });
    await flushPromises();
    expect(wrapper.get(".compiled-provider-prompt").element.textContent).toBe(previewFinal);
    await wrapper.findAll("button").find(button => button.text() === "复制最终模型指令")!.trigger("click");
    expect(writeText).toHaveBeenLastCalledWith(previewFinal);
    await wrapper.findAll("button").find(button => button.text() === "检查视频")!.trigger("click");
    await flushPromises();
    const submitted = wrapper.get(".submitted-prompt");
    expect(submitted.get(".compiled-provider-prompt").element.textContent).toBe(candidateFinal);
    await submitted.findAll("button").find(button => button.text() === "复制最终模型指令")!.trigger("click");
    expect(writeText).toHaveBeenLastCalledWith(candidateFinal);
    expect(submitted.text()).not.toContain(previewFinal);
    wrapper.unmount();
  });

  it("submits one paid video job directly and shows no custom quota", async () => {
    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    await wrapper.findAll("button").find((item) => item.text().includes("生成视频"))!.trigger("click");
    await flushPromises();

    expect(client.previewVideo).toHaveBeenCalledWith("project-1", false);
    expect(client.createVideoJob).toHaveBeenCalledWith("project-1", {
      expectedInputHash: "c".repeat(64),
      idempotencyKey: expect.any(String),
      includePreviousEpisodeVideo: false,
    });
    expect(wrapper.text()).toContain("本次生成会产生模型费用");
    expect(wrapper.find(".safety-card").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Validation Run");
    expect(wrapper.text()).not.toContain("额度");
    expect(wrapper.text()).not.toContain("确认并提交");
    wrapper.unmount();
  });

  it("saves failed quality and enters an unselected video draft without acceptance", async () => {
    client.assets.mockResolvedValue([{ id: "problem-video", projectId: "project-1", role: "video", mediaType: "video", sha256: "e".repeat(64), byteSize: 100, metadata: { durationFrames: 289 }, createdAt: "2026-09-05T00:00:00Z" }]);
    client.createVideoEditDraft.mockResolvedValue({ id: "new-draft" });
    const wrapper = mount(GenerationStep, { props: { projectId: "project-1", workspace, runtime }, global: { plugins: [createPinia()] } });
    await flushPromises();
    await wrapper.findAll("button").find(button => button.text() === "检查视频")!.trigger("click");
    await flushPromises();
    await wrapper.findAll('input[value="fail"]')[6].setValue(true);
    await wrapper.get("textarea").setValue("7.5 秒饼干被拿到桌面");
    await wrapper.findAll("button").find(button => button.text() === "进入编辑草稿")!.trigger("click");
    await flushPromises();
    expect(client.createVideoReview).toHaveBeenCalledWith("project-1", expect.objectContaining({ checks: expect.objectContaining({ causalChainAndActiveEnding: "fail" }) }));
    expect(client.createVideoEditDraft).toHaveBeenCalledTimes(1);
    expect(client.selectAsset).not.toHaveBeenCalled();
    expect(navigation.push).toHaveBeenCalledWith({ path: "/projects/project-1/delivery", query: { draftId: "new-draft" } });
    wrapper.unmount();
  });

  it("waits for saved reviews before allowing edits and saves notes without quality approval", async () => {
    client.assets.mockResolvedValue([{ id: "video-1", projectId: "project-1", role: "video", mediaType: "video", sha256: "e".repeat(64), metadata: { durationFrames: 361 } }]);
    let resolveReviews!: (value: unknown[]) => void;
    client.videoReviews.mockReturnValueOnce(new Promise(resolve => { resolveReviews = resolve; }));
    const wrapper = mount(GenerationStep, { props: { projectId: "project-1", workspace, runtime }, global: { plugins: [createPinia()] } });
    await flushPromises();
    await wrapper.findAll("button").find(button => button.text() === "检查视频")!.trigger("click");
    await flushPromises();
    const save = wrapper.findAll("button").find(button => button.text() === "保存问题与验收记录")!;
    expect(wrapper.text()).toContain("正在读取已保存的验收记录");
    expect(wrapper.get("textarea").attributes("disabled")).toBeDefined();
    expect(save.attributes("disabled")).toBeDefined();
    expect(client.createVideoReview).not.toHaveBeenCalled();
    resolveReviews([{ notes: "瓶子留在袋内，声音待试听", checks: {}, audioChecks: {} }]);
    await flushPromises();
    expect((wrapper.get("textarea").element as HTMLTextAreaElement).value).toBe("瓶子留在袋内，声音待试听");
    expect(save.attributes("disabled")).toBeUndefined();
    await wrapper.get("textarea").setValue("补记：切镜提前");
    await save.trigger("click");
    await flushPromises();
    expect(client.createVideoReview).toHaveBeenCalledWith("project-1", expect.objectContaining({ notes: "补记：切镜提前", checks: {}, audioChecks: {} }));
    expect(wrapper.text()).toContain("问题与验收记录已保存");
    expect(client.selectAsset).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("routes a continuity precondition to the series without submitting a video", async () => {
    client.previewVideo.mockRejectedValue(new Error("confirm the episode's incoming continuity before video generation"));
    const seriesContext = { series: { id: "series-1" }, episode: { order: 2 } } as ProjectSeriesContextDto;
    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace, runtime, seriesContext },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("请先确认本集连续性");
    expect(wrapper.text()).not.toContain("请先完成故事、分镜和五张参考图");
    await wrapper.findAll("button").find(button => button.text() === "去系列确认连续性")!.trigger("click");
    expect(navigation.push).toHaveBeenCalledWith("/series/series-1#continuity");
    expect(client.createVideoJob).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("keeps an unread review blocked after a load error and allows a normal UI retry", async () => {
    client.assets.mockResolvedValue([{ id: "video-1", projectId: "project-1", role: "video", mediaType: "video", sha256: "e".repeat(64), metadata: {} }]);
    client.videoReviews.mockRejectedValueOnce(new Error("读取中断"));
    const wrapper = mount(GenerationStep, { props: { projectId: "project-1", workspace, runtime }, global: { plugins: [createPinia()] } });
    await flushPromises();
    const inspect = wrapper.findAll("button").find(button => button.text() === "检查视频")!;
    await inspect.trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBeTruthy();
    expect(wrapper.get("textarea").attributes("disabled")).toBeDefined();
    client.videoReviews.mockResolvedValueOnce([{ notes: "原记录仍在", checks: {} }]);
    await inspect.trigger("click");
    await flushPromises();
    expect((wrapper.get("textarea").element as HTMLTextAreaElement).value).toBe("原记录仍在");
    expect(wrapper.get("textarea").attributes("disabled")).toBeUndefined();
    expect(client.createVideoJob).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("keeps video generation locked after the API accepts a queued job", async () => {
    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    const generate = wrapper.findAll("button").find((item) => item.text().includes("生成视频"))!;
    await generate.trigger("click");
    await flushPromises();

    expect(generate.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("视频生成任务正在处理，请等待完成");

    await generate.trigger("click");
    await flushPromises();
    expect(client.createVideoJob).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("restores a running video job from the workspace and prevents a second submission", async () => {
    const runningJob: JobDto = {
      id: "job-running",
      projectId: "project-1",
      kind: "generate_video",
      status: "polling",
      provider: "ark",
      model: "seedance",
      inputHash: "d".repeat(64),
      billingStatus: "pending",
      frozenInput: {},
      resultAssetIds: [],
      createdAt: "2026-09-04T07:00:00Z",
      updatedAt: "2026-09-04T07:01:00Z",
    };
    const wrapper = mount(GenerationStep, {
      props: {
        projectId: "project-1",
        workspace: { ...workspace, latestVideoJob: runningJob },
        runtime,
      },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    const generate = wrapper.findAll("button").find((item) => item.text().includes("生成视频"))!;
    expect(generate.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("视频生成任务正在处理，请等待完成");
    expect(wrapper.text()).toContain("正在生成");

    await generate.trigger("click");
    await flushPromises();
    expect(client.createVideoJob).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("shows the non-paying prompt preview as soon as the step opens", async () => {
    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    expect(client.previewVideo).toHaveBeenCalledWith("project-1", false);
    expect(wrapper.text()).toContain("本次画面内容");
    expect(wrapper.text()).toContain("画面描述");
    expect(wrapper.text()).toContain("查看生成指令记录");
    expect(wrapper.get(".prompt-summary").text()).toContain("孩子把食物放进野餐篮");
    expect(wrapper.get(".prompt-summary").text()).not.toContain("专业镜头提示词");
    expect(wrapper.findAll(".prompt-section")).toHaveLength(4);
    expect(wrapper.text()).toContain("角色与画风");
    expect(wrapper.text()).toContain("整体基调");
    expect(wrapper.text()).toContain("逐镜执行");
    expect(wrapper.text()).toContain("结尾与生成限制");
    wrapper.unmount();
  });

  it("surfaces director generation risks before a paid video submission", async () => {
    client.previewVideo.mockResolvedValue({
      inputHash: "c".repeat(64), provider: "ark", model: "seedance", prompt: "专业镜头提示词",
      negativePrompt: "禁止身份漂移", expectedCostMicros: null, costEstimateStatus: "unmetered_paid",
      capabilityRevision: "v1", storyVersionId: "story-1", shotPlanVersionId: "shot-1",
      selectionHash: "a".repeat(64), durationSeconds: 12, references: [], videoReferences: [],
      warnings: [{
        code: "SINGLE_SHOT_TIMING",
        message: "12 秒内包含五次独立物件移动，建议先减少动作或拆分镜头。",
      }],
    });
    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    expect(wrapper.get("[data-testid='video-generation-warnings']").text()).toContain("制作前请留意");
    expect(wrapper.text()).toContain("12 秒内包含五次独立物件移动");
    expect(wrapper.findAll("button").find((item) => item.text().includes("生成视频"))!.attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("keeps the previous episode video off until the creator explicitly enables it", async () => {
    client.previewVideo.mockImplementation(async (_projectId: string, included: boolean) => ({
      inputHash: (included ? "d" : "c").repeat(64),
      provider: "ark", model: "seedance", prompt: "承接上一集的野餐故事",
      negativePrompt: "不要复制上一集镜头", expectedCostMicros: null,
      costEstimateStatus: "unmetered_paid", capabilityRevision: "v1",
      storyVersionId: "story-2", shotPlanVersionId: "shot-2",
      selectionHash: "a".repeat(64), durationSeconds: 12, references: [], warnings: [],
      videoReferences: [{
        assetId: "previous-video-1", role: "previous_episode_video",
        sha256: "e".repeat(64), durationSeconds: 12, included,
      }],
    }));
    const seriesRuntime = {
      ...runtime,
      objectPublisher: { ready: true },
      provider: {
        ...runtime.provider,
        videoGeneration: {
          maximumImageReferences: 9,
          maximumVideoReferences: 3,
          previousEpisodeVideoSupported: true,
        },
      },
    };
    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace, runtime: seriesRuntime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("高级连续性设置");
    expect(wrapper.text()).toContain("默认关闭");
    const checkbox = wrapper.get<HTMLInputElement>(".video-reference-option input");
    expect(checkbox.element.checked).toBe(false);
    await checkbox.setValue(true);
    await flushPromises();

    expect(client.previewVideo).toHaveBeenLastCalledWith("project-1", true);
    expect(wrapper.text()).toContain("已加入本次输入");

    await wrapper.findAll("button").find((item) => item.text().includes("生成视频"))!.trigger("click");
    await flushPromises();
    expect(client.createVideoJob).toHaveBeenCalledWith("project-1", {
      expectedInputHash: "d".repeat(64),
      idempotencyKey: expect.any(String),
      includePreviousEpisodeVideo: true,
    });
    wrapper.unmount();
  });

  it("marks a candidate as historical when its frozen input hash differs from the current preview", async () => {
    client.assets.mockResolvedValue([{
      id: "video-1",
      projectId: "project-1",
      producingJobId: "job-old-input",
      role: "video",
      mediaType: "video",
      sha256: "e".repeat(64),
      byteSize: 1024,
      metadata: { durationMs: 12_000 },
      createdAt: "2026-09-04T07:00:00Z",
    }]);
    client.job.mockResolvedValue({
      id: "job-old-input",
      projectId: "project-1",
      kind: "generate_video",
      status: "succeeded",
      provider: "ark",
      model: "seedance",
      inputHash: "d".repeat(64),
      billingStatus: "unpriced",
      inputSnapshot: {
        schemaVersion: 1,
        kind: "whole_video",
        state: "submitted",
        provider: "ark",
        model: "seedance",
        capabilityRevision: "v1",
        inputHash: "d".repeat(64),
        prompt: "旧版生成指令",
        negativePrompt: "旧版需要避免的问题",
        references: [],
        videoReferences: [],
        video: { generateAudio: false, durationSeconds: 12, resolution: "480p", aspectRatio: "9:16", frameRate: 24 },
        source: {
          storyVersionId: "story-1",
          shotPlanVersionId: "shot-1",
          selectionHash: "a".repeat(64),
        },
        promptCompilerRevision: "seedance-professional-v1",
        createdAt: "2026-09-04T07:00:00Z",
      },
      frozenInput: {},
      resultAssetIds: ["video-1"],
      createdAt: "2026-09-04T07:00:00Z",
      updatedAt: "2026-09-04T07:20:00Z",
    } satisfies JobDto);
    const currentWorkspace: WorkspaceDto = {
      ...workspace,
      activeStory: {
        id: "story-1",
        projectId: "project-1",
        revision: 1,
        title: "装进野餐篮",
        body: "孩子和猫咪准备野餐。",
        microEvent: {
          trigger: "准备野餐",
          childAction: "把食物装进篮子",
          catResponse: "在旁边观察",
          visibleChange: "篮子装满",
          warmEnding: "一起准备出发",
        },
        targetDurationSeconds: 12,
        dialoguePolicy: "none",
        environmentIntent: "清晨的家庭餐桌",
        active: true,
        createdAt: "2026-09-04T06:00:00Z",
      },
      activeShotPlan: {
        id: "shot-1",
        projectId: "project-1",
        revision: 1,
        sourceStoryVersionId: "story-1",
        sourceSelectionHash: "a".repeat(64),
        clip: {},
        shots: [],
        totalDurationSeconds: 12,
        reviewStatus: "accepted",
        active: true,
        outdated: false,
        createdAt: "2026-09-04T06:30:00Z",
      },
    };

    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace: currentWorkspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    expect(wrapper.get(".candidate-input-summary").text()).toContain("历史输入 / 已过期");
    expect(wrapper.get(".candidate-input-summary").text()).not.toContain("当前输入");
    await wrapper.findAll("button").find((button) => button.text() === "检查视频")!.trigger("click");
    await flushPromises();
    expect(wrapper.get(".submitted-prompt").text()).toContain("旧版生成指令");
    expect(wrapper.get(".submitted-prompt").text()).toContain("旧任务未记录最终模型指令");
    wrapper.unmount();
  });

  it("shows a candidate's frozen v2 summary and prompt sections", async () => {
    client.assets.mockResolvedValue([{
      id: "video-v2", projectId: "project-1", producingJobId: "job-v2", role: "video",
      mediaType: "video", sha256: "f".repeat(64), byteSize: 2048,
      metadata: { durationMs: 12_000 }, createdAt: "2026-09-04T08:00:00Z",
    }]);
    client.job.mockResolvedValue({
      id: "job-v2", projectId: "project-1", kind: "generate_video", status: "succeeded",
      provider: "ark", model: "seedance", inputHash: "c".repeat(64), billingStatus: "unpriced",
      inputSnapshot: {
        schemaVersion: 2, kind: "whole_video", state: "submitted", provider: "ark",
        model: "seedance", capabilityRevision: "v1", inputHash: "c".repeat(64),
        prompt: "冻结的完整 v4 指令", negativePrompt: "冻结的需要避免的问题",
        promptSummary: "冻结摘要：孩子装满野餐篮，猫咪准备同行。",
        promptSections: [
          { key: "identity_style", title: "角色与画风", content: "冻结的角色约束。" },
          { key: "creative_treatment", title: "整体基调", content: "冻结的整体基调。" },
          { key: "shot_execution", title: "逐镜执行", content: "冻结的逐镜动作。" },
          { key: "ending_constraints", title: "结尾与生成限制", content: "冻结的最终画面。" },
        ],
        references: [], videoReferences: [],
        video: { generateAudio: false, durationSeconds: 12, resolution: "480p", aspectRatio: "9:16", frameRate: 24 },
        source: {}, promptCompilerRevision: "seedance-professional-v4",
        createdAt: "2026-09-04T08:00:00Z",
      },
      frozenInput: {}, resultAssetIds: ["video-v2"],
      createdAt: "2026-09-04T08:00:00Z", updatedAt: "2026-09-04T08:20:00Z",
    } satisfies JobDto);
    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    expect(wrapper.get(".candidate-input-summary").text()).toContain("冻结摘要：孩子装满野餐篮");
    await wrapper.findAll("button").find((button) => button.text() === "检查视频")!.trigger("click");
    await flushPromises();
    expect(wrapper.get(".submitted-prompt").findAll(".prompt-section")).toHaveLength(4);
    expect(wrapper.get(".submitted-prompt").text()).toContain("冻结的逐镜动作");
    expect(wrapper.get(".submitted-prompt").text()).toContain("旧任务未记录最终模型指令");
    wrapper.unmount();
  });

  it("does not present unresolved Ark usage as a calculated zero cost", async () => {
    client.projectUsageSummary.mockResolvedValue({
      projectId: "project-1",
      jobs: [{
        jobId: "job-usage-1",
        provider: "ark",
        model: "seedance",
        totalTokens: 1168,
        providerUsage: { totalTokens: 1168 },
        billingStatus: "pending",
        currency: "CNY",
      }],
      totals: { totalTokens: 1168 },
      calculatedCostMicros: 0,
      unpricedJobCount: 0,
      currency: "CNY",
    });

    const wrapper = mount(GenerationStep, {
      props: { projectId: "project-1", workspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("费用计算中");
    expect(wrapper.text()).not.toContain("已计算费用¥0.0000");
    wrapper.unmount();
  });
});
