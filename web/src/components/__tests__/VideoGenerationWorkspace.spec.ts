import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, ref } from "vue";

import type { ShotGenerationWorkspaceDto, ShotPromptReference } from "../../api/types";
import VideoGenerationWorkspace from "../director/VideoGenerationWorkspace.vue";

const calls = vi.hoisted(() => ({
  board: vi.fn(), workspace: vi.fn(), tasks: vi.fn(), preview: vi.fn(), generate: vi.fn(),
  review: vi.fn(), select: vi.fn(), register: vi.fn(), replace: vi.fn(), push: vi.fn(),
  updateShot: vi.fn(), updateReferences: vi.fn(), prompt: vi.fn(), confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  assetContentUrl: (id: string) => `/api/v1/assets/${id}/content`,
  api: {
    productionBoard: calls.board,
    projectTasks: calls.tasks,
    shotGenerationWorkspace: calls.workspace,
    promptPreview: calls.preview,
    generateVideo: calls.generate,
    reviewAsset: calls.review,
    selectVersion: calls.select,
    updateShot: calls.updateShot,
    updateReferences: calls.updateReferences,
  },
}));
const settings = ref({
  current: { videoModel: "doubao-seedance-2", videoResolution: "720p", revision: 7 },
  arkReady: true, videoGenerationReady: true,
});
const health = ref({ provider: "ark" });
vi.mock("../../runtimeStatus", () => ({
  useRuntimeStatus: () => ({ settings, health, unreachable: ref(false) }),
  refreshRuntimeStatus: vi.fn(),
}));
vi.mock("../../tasks/taskCenter", () => ({
  registerTask: calls.register,
  cancelPersistentTask: vi.fn(),
  useTaskCenter: () => ({
    shotSignals: ref<Record<string, { revision: number }>>({}),
    workspaceRefreshRequest: ref(),
    cancellingStepIds: ref<string[]>([]),
  }),
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ replace: calls.replace, push: calls.push }) }));
vi.mock("element-plus", async (importOriginal) => ({
  ...(await importOriginal<typeof import("element-plus")>()),
  ElMessageBox: { prompt: calls.prompt, confirm: calls.confirm },
  ElMessage: { success: calls.success, error: calls.error, warning: calls.warning },
}));
vi.mock("../canvas/CanvasReviewDialog.vue", () => ({
  default: defineComponent({
    emits: ["close"],
    template: `<section class="review-stub"><slot /><footer><slot name="actions" /></footer></section>`,
  }),
}));

enableAutoUnmount(afterEach);

function asset(id: string, role: string, mediaType: "image" | "video" = "image", status = "approved") {
  return {
    id, role, mediaType, scope: "shot", status, projectId: "project-1", sceneId: "scene-1", shotId: "shot-1",
    producingStepId: null, sha256: `sha-${id}`, semanticKey: `${role}:${id}`, metadata: {}, contentReady: true,
    displayName: id, referencePurpose: null, visualProfileRevisionId: null, lookDraftRevision: null, createdAt: "2026-08-31T10:00:00Z",
  };
}

function shot() {
  return {
    id: "shot-1", sceneId: "scene-1", order: 1, title: "纸星星回到窗边", direction: "孩子发现纸星星，猫咪轻轻推回。",
    durationSeconds: 8, draftRevision: 3, anchorMode: "text_only", referenceBindings: [], inheritProjectReferences: true,
    sceneLookUsage: "off", useSceneLook: false, status: "ready", selectedAnchorAssetId: null, selectedVideoAssetId: null,
    assets: [asset("video-1", "shot_video", "video", "candidate")], attempts: [],
  };
}

function promptReference(index: number, id: string, name: string, responsibility: string): ShotPromptReference {
  return {
    index, assetId: id, displayName: name, promptAlias: `@图片${index}`, subjectLabel: name, sourceLayer: "project",
    responsibility, contentReady: true, sha256: `sha-${id}`, purpose: "episode_appearance", providerIncluded: true, providerSlot: `reference_${index}`, locked: true,
  };
}

function videoPreview() {
  const actualInputs = [
    promptReference(1, "child-1", "本集儿童", "唯一儿童身份与本集服装"),
    promptReference(2, "cat-1", "本集猫咪", "唯一猫咪身份与四足结构"),
    promptReference(3, "pair-1", "人猫同框", "相对比例与接触尺度"),
    promptReference(4, "environment-1", "窗边环境", "空间陈设和晨光"),
    promptReference(5, "style-1", "Canon v4 画风板", "线条、材质和色阶"),
  ];
  return {
    target: "video", providerInputMode: "reference_media", ready: true, blockers: [], inputHash: "input-hash-1",
    sourceRevisionHash: "source-hash-1", prompt: "生成一个9:16、8秒的原创二维治愈生活短片。儿童和猫咪身份始终一致。",
    creativeBody: "镜头正文", systemShell: "技术参数外壳", charCount: 50, utf8Bytes: 100, inputPlan: {}, draftRevision: 3,
    anchorMode: "text_only", sceneLookUsage: "off", localAnalysis: { suggestedSubshotMin: 1, suggestedSubshotMax: 1, detectedSubshotCount: 1, actionCount: 3, cameraMoveCount: 1, hasStableEnding: true, hasSound: false, qualitativePacing: "紧凑", findings: [] },
    qualitativePacing: "紧凑", linkWarnings: [], actualInputCount: 5, references: actualInputs, actualInputs,
    upstreamLineage: [], providerReferencePolicy: "compiled_production_references", previousTail: { available: false, stale: false },
  };
}

function workspace(): ShotGenerationWorkspaceDto {
  const images = [
    asset("child-1", "episode_child"), asset("cat-1", "episode_cat"), asset("pair-1", "pair_scale"),
    asset("environment-1", "environment"), asset("style-1", "style_board"),
  ];
  return {
    projectId: "project-1", shot: shot(), scene: { id: "scene-1", title: "清晨窗边", selectedLookAssetId: null }, assets: images,
    generationSpec: { providerInputMode: "reference_media", actualInputCount: 5, actualInputs: videoPreview().actualInputs, ready: true, blockers: [], warnings: [], inputHash: "input-hash-1", sourceRevisionHash: "source-hash-1" },
    anchorPreview: { ...videoPreview(), target: "anchor", actualInputCount: 0, references: [], actualInputs: [] }, videoPreview: videoPreview(),
    actualInputs: videoPreview().actualInputs, upstreamLineage: [], referenceSlots: { anchor: [], video: [] }, previousTail: { available: false, stale: false },
    activeTasks: [], anchorVersions: [], videoVersions: [{ ...asset("video-1", "shot_video", "video", "candidate"), attempt: 1, prompt: null, inputSnapshot: { inputHash: "input-hash-1" } }],
    anchorBrief: null, anchorBriefVersions: [], nextAction: "review_video", nextActionLabel: "审核视频", blockers: [],
  } as ShotGenerationWorkspaceDto;
}

function board() {
  return {
    projectId: "project-1",
    projectGraph: {
      project: { id: "project-1", title: "一人一猫", contentDate: "2026-08-31", status: "active", selectedSequenceId: null, contractVersion: 5, defaultReferenceBindings: [], visualProfileRevisionId: "profile-4" },
      assets: [], scenes: [{ id: "scene-1", order: 1, title: "清晨窗边", sourceText: "", storyMode: "single_scene", targetShotCount: 1, selectedLookAssetId: null, lookDraftRevision: 1, status: "ready", attempts: [], shots: [shot()] }], sequences: [],
    },
    scenes: [],
  };
}

describe("VideoGenerationWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.board.mockResolvedValue(board());
    calls.tasks.mockResolvedValue([]);
    calls.workspace.mockResolvedValue(workspace());
    calls.preview.mockResolvedValue(videoPreview());
    calls.generate.mockResolvedValue({ jobId: "job-video-1" });
    calls.review.mockResolvedValue({ status: "approved" });
    calls.select.mockResolvedValue(shot());
    calls.updateShot.mockResolvedValue(shot());
    calls.updateReferences.mockResolvedValue(shot());
    calls.prompt.mockResolvedValue({ value: "保持身份，只修正尾巴环纹" });
    calls.confirm.mockResolvedValue("confirm");
    settings.value.arkReady = true;
    settings.value.videoGenerationReady = true;
  });

  it("loads the production board and one real shot workspace, then renders five frozen inputs, Prompt, result and history together", async () => {
    const wrapper = mount(VideoGenerationWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    expect(calls.board).toHaveBeenCalledWith("project-1");
    expect(calls.workspace).toHaveBeenCalledWith("shot-1");
    expect(wrapper.findAll(".reference-scroll article")).toHaveLength(5);
    expect(wrapper.get(".video-prompt-panel").text()).toContain("原创二维治愈生活短片");
    expect(wrapper.get(".video-result-stage").text()).toContain("纸星星回到窗边");
    expect(wrapper.findAll(".version-list article")).toHaveLength(1);
    expect(wrapper.text()).not.toContain("style_source");
  });

  it("cancels the fee review without creating a task or calling the Provider", async () => {
    const empty = workspace();
    empty.videoVersions = [];
    empty.shot.assets = [];
    calls.workspace.mockResolvedValue(empty);
    const wrapper = mount(VideoGenerationWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get(".video-controls>button").trigger("click");
    expect(wrapper.find(".review-stub").exists()).toBe(true);
    const cancel = wrapper.findAll(".review-stub footer button").find((button) => button.text().includes("取消"));
    await cancel!.trigger("click");
    expect(calls.generate).not.toHaveBeenCalled();
    expect(calls.register).not.toHaveBeenCalled();
  });

  it("submits exactly one frozen video task after explicit fee confirmation", async () => {
    const empty = workspace();
    empty.videoVersions = [];
    empty.shot.assets = [];
    calls.workspace.mockResolvedValue(empty);
    const wrapper = mount(VideoGenerationWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get(".video-controls>button").trigger("click");
    const confirm = wrapper.findAll(".review-stub footer button").find((button) => button.text().includes("确认并提交"));
    await confirm!.trigger("click");
    await flushPromises();
    expect(calls.generate).toHaveBeenCalledTimes(1);
    expect(calls.generate).toHaveBeenCalledWith("shot-1", false, "生成当前导演镜头视频", "input-hash-1", 7);
    expect(calls.register).toHaveBeenCalledWith("job-video-1", expect.objectContaining({ operationKey: "video:shot", shotId: "shot-1" }));
  });

  it("blocks duplicate submission while Provider is running and exposes the backend cancellation reason", async () => {
    calls.tasks.mockResolvedValue([{ stepId: "task-1", projectId: "project-1", sceneId: "scene-1", shotId: "shot-1", kind: "generate_video", status: "running", attempt: 1, operationKey: "video:shot", provider: "ark", providerTaskId: "provider-1", model: "seedance", inputSnapshot: {}, cancellation: { allowed: false, mode: "unavailable", label: "Provider 已运行，无法取消", disabledReason: "Provider 已开始生成，当前接口不支持取消；任务会继续跟踪", providerStatus: "running", costMayAlreadyApply: true } }]);
    const wrapper = mount(VideoGenerationWorkspace, { props: { projectId: "project-1", panel: "tasks" } });
    await flushPromises();
    expect(wrapper.get(".video-controls>button").attributes("disabled")).toBeDefined();
    expect(wrapper.get(".video-task-status").text()).toContain("Provider 已运行，无法取消");
    expect(wrapper.get(".video-task-status article>button").attributes("disabled")).toBeDefined();
    expect(calls.generate).not.toHaveBeenCalled();
  });

  it("saves a video input-mode change without a Provider call and refuses first-frame mode without an approved anchor", async () => {
    const wrapper = mount(VideoGenerationWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    const mode = wrapper.get("select[aria-label='视频控制模式']");
    await mode.setValue("text_only");
    await flushPromises();
    expect(calls.updateShot).toHaveBeenCalledWith("shot-1", expect.objectContaining({ anchorMode: "text_only", inheritProjectReferences: false }));
    expect(calls.generate).not.toHaveBeenCalled();

    await mode.setValue("first_frame");
    expect(calls.warning).toHaveBeenCalledWith(expect.stringContaining("已批准"));
    expect(calls.updateShot).toHaveBeenCalledTimes(1);
  });

  it("replaces only a shot-owned custom reference and keeps identity authorities outside the picker", async () => {
    const custom = workspace();
    const oldAsset = asset("prop-old", "prop");
    const nextAsset = asset("prop-next", "prop");
    oldAsset.metadata = { referenceRole: "prop" };
    nextAsset.metadata = { referenceRole: "prop" };
    custom.assets.push(oldAsset, nextAsset);
    custom.shot.referenceBindings = [{ assetId: "prop-old", usage: "generation_reference", role: "prop", applyTo: "video" }];
    custom.videoPreview.actualInputs.push({ ...promptReference(6, "prop-old", "纸星星道具", "当前镜头道具"), sourceLayer: "shot", purpose: "prop" });
    custom.videoPreview.references = custom.videoPreview.actualInputs;
    custom.videoPreview.actualInputCount = 6;
    calls.workspace.mockResolvedValue(custom);
    const wrapper = mount(VideoGenerationWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    const customCard = wrapper.findAll(".reference-scroll article").find((card) => card.text().includes("纸星星道具"));
    const manage = customCard!.findAll("button").find((button) => button.text() === "更换");
    await manage!.trigger("click");
    expect(wrapper.find(".video-reference-picker").exists()).toBe(true);
    expect(wrapper.get(".video-reference-picker").text()).not.toContain("本集儿童");
    const replacement = wrapper.findAll(".video-reference-picker>div>button").find((button) => button.text().includes("prop-next"));
    await replacement!.trigger("click");
    await flushPromises();
    expect(calls.updateReferences).toHaveBeenCalledWith("shot-1", [expect.objectContaining({ assetId: "prop-next", role: "prop", applyTo: "video" })]);
    expect(calls.generate).not.toHaveBeenCalled();
  });

  it("blocks submission when an actual Provider manifest contains a style_source asset", async () => {
    const polluted = workspace();
    polluted.videoPreview.actualInputs.push({
      ...promptReference(6, "leaf-source", "绿色叶片画风来源", "只用于 style_source 提炼"),
      purpose: "style_source",
    });
    polluted.videoPreview.references = polluted.videoPreview.actualInputs;
    polluted.videoPreview.actualInputCount = 6;
    calls.workspace.mockResolvedValue(polluted);
    const wrapper = mount(VideoGenerationWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    expect(wrapper.get(".prompt-blockers").text()).toContain("不能提交视频 Provider");
    expect(wrapper.get(".video-controls>button").attributes("disabled")).toBeDefined();
    expect(calls.generate).not.toHaveBeenCalled();
  });
});
