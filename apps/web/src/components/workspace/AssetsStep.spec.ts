import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkspaceDto } from "../../api/types";
import AssetsStep from "./AssetsStep.vue";

const client = vi.hoisted(() => ({
  assets: vi.fn(),
  runtime: vi.fn(),
  previewAssetGeneration: vi.fn(),
  environmentDraft: vi.fn(),
  saveEnvironmentDraft: vi.fn(),
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
    client.job.mockReset();
    vi.stubGlobal("EventSource", class { addEventListener() {} close() {} });
    client.assets.mockResolvedValue([]);
    client.environmentDraft.mockResolvedValue({ revision: 0, mode: "description", sourceStoryVersionId: "story-1", description: "雨天玄关和吸水脚垫", prompt: null, negativePrompt: null, sourceAssetId: null });
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

  it("displays image preview advice separately and switches from a frozen final prompt to an honest historical record", async () => {
    const final = "已冻结环境\n避免项：额外主体\n参考图 1：仅画风\n";
    const previewFinal = "当前空场景\n参考图 1：画风\n";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const originalPreview = await client.previewAssetGeneration();
    client.previewAssetGeneration.mockResolvedValue({ ...originalPreview, compiledProviderPrompt: previewFinal, warnings: [{ code: "SPATIAL_REVIEW", message: "人工核对空间留白" }] });
    client.assets.mockResolvedValue([
      { id: "new-environment", role: "environment", mediaType: "image", metadata: {}, producingJobId: "new-image-job" },
      { id: "old-environment", role: "environment", mediaType: "image", metadata: {}, producingJobId: "old-image-job" },
    ]);
    client.job.mockImplementation(async id => ({ imageInputSnapshot: id === "new-image-job"
      ? { schemaVersion: 3, compiledProviderPrompt: final, prompt: "冻结正文", negativePrompt: "冻结避免项" }
      : { schemaVersion: 1, prompt: "历史正文", negativePrompt: "历史避免项" },
    }));
    const wrapper = mount(AssetsStep, { props: { projectId: "project-1", workspace, runtime }, global: { plugins: [createPinia()], stubs: { teleport: true } } });
    await flushPromises();
    expect(wrapper.get(".compiled-provider-prompt").element.textContent).toBe(previewFinal);
    expect(wrapper.get('[aria-label="制作审查建议"]').text()).toContain("人工核对空间留白");
    await wrapper.findAll("button").find(button => button.text() === "复制最终模型指令")!.trigger("click");
    expect(writeText).toHaveBeenLastCalledWith(previewFinal);
    await wrapper.findAll(".environment-candidates .image-open")[0].trigger("click");
    await flushPromises();
    const viewer = wrapper.get('[role="dialog"]');
    expect(viewer.get(".compiled-provider-prompt").element.textContent).toBe(final);
    await viewer.findAll("button").find(button => button.text() === "复制最终模型指令")!.trigger("click");
    expect(writeText).toHaveBeenLastCalledWith(final);
    await viewer.get('[aria-label="下一张候选"]').trigger("click");
    await flushPromises();
    expect(viewer.find(".compiled-provider-prompt").exists()).toBe(false);
    expect(viewer.text()).toContain("旧任务未记录最终模型指令");
    expect(viewer.text()).not.toContain("已冻结环境");
    expect(viewer.text()).not.toContain("当前空场景");
    await viewer.findAll("button").find(button => button.text() === "复制已记录正文（非完整指令）")!.trigger("click");
    expect(writeText).toHaveBeenLastCalledWith("历史正文");
    wrapper.unmount();
  });

  it("displays and copies a non-environment candidate's versioned frozen final prompt without an image snapshot", async () => {
    const final = "  已冻结角色原貌 ABC 123\n避免项：多余手指\n图1：提供角色身份\n";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const fixedWorkspace: WorkspaceDto = { ...workspace, selections: { ...workspace.selections,
      episode_child: { ...workspace.selections.episode_child!, producingJobId: "fixed-image-job" },
    } };
    client.job.mockResolvedValue({ frozenInput: { providerPromptVersion: 1, compiledProviderPrompt: final, prompt: "仅冻结正文" } });
    const wrapper = mount(AssetsStep, { props: { projectId: "project-1", workspace: fixedWorkspace, runtime }, global: { plugins: [createPinia()], stubs: { teleport: true } } });
    await flushPromises();
    await wrapper.findAll(".candidates.inherited .image-open")[0].trigger("click");
    await flushPromises();
    expect(client.job).toHaveBeenCalledWith("fixed-image-job");
    const viewer = wrapper.get('[role="dialog"]');
    expect(viewer.get(".compiled-provider-prompt").element.textContent).toBe(final);
    expect(viewer.text()).not.toContain("旧任务未记录");
    expect(viewer.text()).not.toContain("仅冻结正文");
    await viewer.findAll("button").find(button => button.text() === "复制最终模型指令")!.trigger("click");
    expect(writeText).toHaveBeenLastCalledWith(final);
    wrapper.unmount();
  });

  it("does not infer a final prompt from an unversioned historical frozen input", async () => {
    const fixedWorkspace: WorkspaceDto = { ...workspace, selections: { ...workspace.selections,
      episode_child: { ...workspace.selections.episode_child!, producingJobId: "historical-fixed-image-job" },
    } };
    client.job.mockResolvedValue({ frozenInput: { compiledProviderPrompt: "没有版本标记的文本", prompt: "历史拼装输入" } });
    const wrapper = mount(AssetsStep, { props: { projectId: "project-1", workspace: fixedWorkspace, runtime }, global: { plugins: [createPinia()], stubs: { teleport: true } } });
    await flushPromises();
    await wrapper.findAll(".candidates.inherited .image-open")[0].trigger("click");
    await flushPromises();
    const viewer = wrapper.get('[role="dialog"]');
    expect(viewer.find(".compiled-provider-prompt").exists()).toBe(false);
    expect(viewer.text()).toContain("旧任务未记录最终模型指令");
    expect(viewer.text()).not.toContain("没有版本标记的文本");
    expect(viewer.text()).not.toContain("历史拼装输入");
    expect(viewer.findAll("button").some(button => button.text() === "复制最终模型指令")).toBe(false);
    wrapper.unmount();
  });

  it("distinguishes an unreadable image job from a historical missing prompt", async () => {
    client.assets.mockResolvedValue([{ id: "unreadable", role: "environment", mediaType: "image", metadata: {}, producingJobId: "offline-job" }]);
    client.job.mockRejectedValue(new Error("temporary read failure"));
    const wrapper = mount(AssetsStep, { props: { projectId: "project-1", workspace, runtime }, global: { plugins: [createPinia()], stubs: { teleport: true } } });
    await flushPromises();
    await wrapper.get(".environment-candidates .image-open").trigger("click");
    await flushPromises();
    const viewer = wrapper.get('[role="dialog"]');
    expect(viewer.get('[role="alert"]').text()).toContain("生成指令记录暂时无法读取");
    expect(viewer.text()).not.toContain("旧任务未记录");
    expect(viewer.find(".compiled-provider-prompt").exists()).toBe(false);
    wrapper.unmount();
  });

  it("generates an environment directly without a validation-run confirmation", async () => {
    const wrapper = mount(AssetsStep, {
      props: { projectId: "project-1", workspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    expect(client.previewAssetGeneration).toHaveBeenCalledWith("project-1", "environment", { environmentDraftRevision: 0 });
    expect((wrapper.get('textarea[aria-label="场景描述"]').element as HTMLTextAreaElement).value).toBe("雨天玄关和吸水脚垫");
    expect(wrapper.text()).toContain("不包含人物与猫咪");
    expect(wrapper.text()).toContain("画风板");
    expect(wrapper.text()).toContain("本次会使用付费模型，完成后显示实际用量");

    await wrapper.get('.generate-candidate').trigger("click");
    await flushPromises();

    expect(client.previewAssetGeneration).toHaveBeenCalledWith("project-1", "environment", { environmentDraftRevision: 0 });
    expect(client.createAssetGeneration).toHaveBeenCalledWith("project-1", {
      kind: "environment",
      environmentDraftRevision: 0,
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
    client.job.mockResolvedValue(runningWorkspace.latestAssetJob);
    const wrapper = mount(AssetsStep, {
      props: { projectId: "project-1", workspace: runningWorkspace, runtime },
      global: { plugins: [createPinia()] },
    });
    await flushPromises();

    const generateButton = wrapper.findAll("button").find((item) => item.text().includes("环境候选"))!;
    expect(generateButton.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("环境生成");
    expect(wrapper.text()).toContain("环境生成任务正在处理");
    await generateButton.trigger("click");
    expect(client.createAssetGeneration).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("saves description edits without generating and preserves text on a revision conflict", async () => {
    const wrapper = mount(AssetsStep, { props: { projectId: "project-1", workspace, runtime }, global: { plugins: [createPinia()] } });
    await flushPromises();
    await wrapper.get('textarea[aria-label="场景描述"]').setValue("风车平躺地板，手柄贴地");
    expect(wrapper.get('.generate-candidate').attributes('disabled')).toBeDefined();
    client.saveEnvironmentDraft.mockRejectedValueOnce(new Error("环境草稿已在其他窗口更新"));
    await wrapper.findAll('button').find(b => b.text() === '保存修改')!.trigger('click');
    await flushPromises();
    expect((wrapper.get('textarea[aria-label="场景描述"]').element as HTMLTextAreaElement).value).toBe("风车平躺地板，手柄贴地");
    expect(wrapper.text()).toContain("其他窗口更新");
    client.saveEnvironmentDraft.mockImplementationOnce(async (_id, value) => ({ ...value, revision: 1 }));
    await wrapper.findAll('button').find(b => b.text() === '保存修改')!.trigger('click');
    await flushPromises();
    expect(client.saveEnvironmentDraft).toHaveBeenLastCalledWith('project-1', expect.objectContaining({ description: '风车平躺地板，手柄贴地', expectedRevision: 0, mode: 'description', prompt: null }));
    expect(client.previewAssetGeneration).toHaveBeenLastCalledWith('project-1', 'environment', { environmentDraftRevision: 1 });
    expect(wrapper.text()).toContain('版本 1');
    expect(client.createAssetGeneration).not.toHaveBeenCalled();
    expect(client.selectAsset).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("keeps custom prompt edits until an explicit return to description mode", async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const wrapper = mount(AssetsStep, { props: { projectId: 'project-1', workspace, runtime }, global: { plugins: [createPinia()] } });
    await flushPromises();
    await wrapper.get('textarea[aria-label="生成指令正文"]').setValue('只有平躺风车的空场景');
    await wrapper.get('textarea[aria-label="需要避免的问题"]').setValue('不要竖立支架');
    expect(wrapper.get('textarea[aria-label="场景描述"]').attributes('readonly')).toBeDefined();
    const button = wrapper.findAll('button').find(b => b.text() === '返回场景描述模式')!;
    await button.trigger('click');
    expect((wrapper.get('textarea[aria-label="生成指令正文"]').element as HTMLTextAreaElement).value).toBe('只有平躺风车的空场景');
    client.saveEnvironmentDraft.mockImplementationOnce(async (_id, value) => ({ ...value, revision: 1 }));
    await wrapper.findAll('button').find(b => b.text() === '保存修改')!.trigger('click');
    await flushPromises();
    expect(client.saveEnvironmentDraft).toHaveBeenCalledWith('project-1', expect.objectContaining({ mode: 'custom', prompt: '只有平躺风车的空场景', negativePrompt: '不要竖立支架' }));
    confirm.mockReturnValue(true);
    await button.trigger('click');
    expect(wrapper.get('textarea[aria-label="场景描述"]').attributes('readonly')).toBeUndefined();
    expect(wrapper.get('.generate-candidate').attributes('disabled')).toBeDefined();
    expect(client.createAssetGeneration).not.toHaveBeenCalled();
    confirm.mockRestore(); wrapper.unmount();
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
