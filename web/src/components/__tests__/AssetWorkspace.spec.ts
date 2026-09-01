import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AssetWorkspace from "../director/AssetWorkspace.vue";

const calls = vi.hoisted(() => ({
  assets: vi.fn(), profile: vi.fn(), presets: vi.fn(), update: vi.fn(), review: vi.fn(), reviewRecipe: vi.fn(),
  previewCharacterDesign: vi.fn(), runCharacterDesign: vi.fn(),
  canvas: vi.fn(), replace: vi.fn(), confirm: vi.fn(), success: vi.fn(), warning: vi.fn(), error: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: { reviewAsset: calls.review },
  canvasApi: {
    canvas: calls.canvas,
    assets: calls.assets,
    episodeVisualProfile: calls.profile,
    visualPresets: calls.presets,
    updateEpisodeVisualProfile: calls.update,
    previewRecipeCharacterDesign: calls.previewCharacterDesign,
    runRecipeCharacterDesign: calls.runCharacterDesign,
    reviewRecipeTarget: calls.reviewRecipe,
  },
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ replace: calls.replace }) }));
vi.mock("element-plus", async (importOriginal) => ({
  ...(await importOriginal<typeof import("element-plus")>()),
  ElMessageBox: { confirm: calls.confirm },
  ElMessage: { success: calls.success, warning: calls.warning, error: calls.error },
}));

enableAutoUnmount(afterEach);

const identity = (subject: "child" | "cat", source = "canon-v4") => ({
  role: "identity", providerEligible: true, priority: 100,
  lockedTraits: subject === "child" ? ["齐下颌短发"] : ["灰白毛色分区"],
  mutableTraits: ["表情", "姿态"], forbiddenTransfer: ["背景"], source,
});

function profile() {
  return {
    id: "profile-1", projectId: "project-1", recipeInstanceId: "recipe-1", revision: 4, sourceProfileId: "canon-v4",
    personIdentity: "固定儿童脸型与五官", personHair: "齐下颌短发", personBody: "儿童比例",
    catIdentity: "固定灰白虎斑与环纹尾巴", stylePositive: ["暖灰轮廓线"], styleNegative: ["摄影写实"],
    referenceBindings: [], lockedSemanticKeys: ["person:headshot", "cat:front", "style:board"], createdAt: "2026-08-31",
    references: [
      { assetId: "child", semanticKey: "person:headshot", title: "儿童面部", contentUrl: "/child.png", thumbnailUrl: "/child.png", approvalStatus: "approved", sha256: "a", required: true, authority: identity("child"), subjectId: "child-subject", subjectRevisionId: "child-v4", subjectRevision: 4, subjectKind: "person", subjectRole: "protagonist", authorityOrigin: "subject_revision", currentAuthority: true, visualProfileRevisionId: "profile-1" },
      { assetId: "cat", semanticKey: "cat:front", title: "猫咪正面", contentUrl: "/cat.png", thumbnailUrl: "/cat.png", approvalStatus: "approved", sha256: "b", required: true, authority: identity("cat"), subjectId: "cat-subject", subjectRevisionId: "cat-v4", subjectRevision: 4, subjectKind: "animal", subjectRole: "co_protagonist", authorityOrigin: "subject_revision", currentAuthority: true, visualProfileRevisionId: "profile-1" },
      { assetId: "source", semanticKey: "style_source:leaf_material_v1", title: "叶片材质来源", contentUrl: "/source.png", thumbnailUrl: "/source.png", approvalStatus: "approved", sha256: "c", required: false, authority: { role: "style_source", providerEligible: false, priority: 10, lockedTraits: [], mutableTraits: [], forbiddenTransfer: ["叶片", "绿色"] } },
      { assetId: "board", semanticKey: "style:healing_line_texture_v4", title: "Canon v4 画风板", contentUrl: "/board.png", thumbnailUrl: "/board.png", approvalStatus: "approved", sha256: "d", required: true, authority: { role: "style_board", providerEligible: true, priority: 80, lockedTraits: ["轮廓线"], mutableTraits: ["环境色"], forbiddenTransfer: [] } },
    ],
  };
}

function projectAssets() {
  return [
    { id: "child", projectId: "project-1", mediaType: "image", role: "person_identity", status: "approved", semanticKey: "person:headshot", sha256: "a", metadata: { title: "儿童面部", subjectRevisionId: "child-v4", visualProfileRevisionId: "profile-1", authority: identity("child") }, contentUrl: "/child.png" },
    { id: "cat", projectId: "project-1", mediaType: "image", role: "cat_identity", status: "approved", semanticKey: "cat:front", sha256: "b", metadata: { title: "猫咪正面", subjectRevisionId: "cat-v4", visualProfileRevisionId: "profile-1", authority: identity("cat") }, contentUrl: "/cat.png" },
    { id: "episode-child", projectId: "project-1", mediaType: "image", role: "character_design_child", status: "awaiting_review", semanticKey: "episode:child", sha256: "e", metadata: { title: "本集儿童设计", authority: { role: "episode_appearance", providerEligible: true, priority: 90, lockedTraits: ["本集服装"], mutableTraits: ["动作"], forbiddenTransfer: [] } }, contentUrl: "/episode-child.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r2", revision: 2, revisionStatus: "awaiting_review", isCurrentRevision: true, slot: "child", candidateIndex: 1, semanticRole: "appearance", selected: true }, reviewAction: { executable: true, route: "recipe_character_design", recipeInstanceId: "recipe-1", targetType: "character_design", targetId: "episode-child", targetHash: "e" } },
    { id: "environment", projectId: "project-1", mediaType: "image", role: "environment_reference", status: "approved", semanticKey: "scene:window", sha256: "f", metadata: { title: "窗边环境", authority: { role: "environment", providerEligible: true, priority: 40 } }, contentUrl: "/environment.png" },
    { id: "anchor", projectId: "project-1", mediaType: "image", role: "shot_anchor", status: "approved", semanticKey: "shot:anchor", sha256: "h", metadata: { title: "镜头锚点" }, contentUrl: "/anchor.png" },
    { id: "video", projectId: "project-1", mediaType: "video", role: "video_result", status: "approved", semanticKey: "video:result", sha256: "i", metadata: { title: "视频结果" }, contentUrl: "/video.mp4" },
    { id: "audio", projectId: "project-1", mediaType: "audio", role: "audio_result", status: "approved", semanticKey: "audio:result", sha256: "j", metadata: { title: "音频结果" }, contentUrl: "/audio.mp3" },
  ];
}

describe("AssetWorkspace", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    calls.assets.mockImplementation(async () => projectAssets());
    calls.profile.mockResolvedValue(profile());
    calls.presets.mockResolvedValue([]);
    calls.update.mockImplementation(async (_project, _revision, draft) => ({ ...profile(), ...draft, revision: 5 }));
    calls.review.mockResolvedValue({ status: "approved" });
    calls.reviewRecipe.mockResolvedValue({ decision: "approve" });
    calls.previewCharacterDesign.mockResolvedValue({
      recipeInstanceId: "recipe-1",
      characterDesignRevisionId: "design-r3",
      candidateCountPerSlot: 1,
      stage: "all",
      slots: ["child", "cat", "pair_scale"].map((slot) => ({
        slot,
        provider: "ark",
        model: "seedream",
        mode: "reference_media",
        capabilityRevision: "seedream-v1",
        prompt: `${slot} prompt`,
        references: [{ assetId: `${slot}-reference` }],
        blockers: [],
        warnings: [],
        estimatedCostMicros: 10_000,
        inputHash: `${slot}-hash`,
      })),
      estimatedCostMicros: 30_000,
      inputHash: "aggregate-hash",
    });
    calls.runCharacterDesign.mockResolvedValue({ id: "job-design-1" });
    calls.confirm.mockResolvedValue("confirm");
  });

  it("loads real media, profile and preset reads without Canvas v2 and exposes exact authority semantics", async () => {
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();

    expect(calls.canvas).not.toHaveBeenCalled();
    expect(calls.assets).toHaveBeenCalledWith("project-1", "image", expect.any(AbortSignal));
    expect(wrapper.text()).toContain("儿童 Canon");
    expect(wrapper.text()).toContain("画风来源");
    expect(wrapper.text()).toContain("只用于画风提炼，不会提交 Provider");
    expect(wrapper.text()).toContain("可提交人物、猫咪、环境和视频 Provider");
    expect(wrapper.text()).not.toContain("视频结果");
    expect(wrapper.text()).not.toContain("音频结果");
    expect(wrapper.text()).toContain("镜头锚点");
    expect(wrapper.text()).toContain("未声明 Reference Authority，不可提交 Provider");
    expect(wrapper.findAll(".media-card").length).toBeGreaterThanOrEqual(7);
  });

  it("previews exact character references and cost before creating the paid task", async () => {
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get(".generate-designs").trigger("click");
    await flushPromises();

    const idempotencyKey = calls.previewCharacterDesign.mock.calls[0][1];
    expect(calls.previewCharacterDesign).toHaveBeenCalledWith("recipe-1", idempotencyKey, 0, "all");
    expect(calls.confirm).toHaveBeenCalledWith(
      expect.stringContaining("共 3 组图片调用"),
      "生成本集角色设计",
      expect.any(Object),
    );
    expect(calls.runCharacterDesign).toHaveBeenCalledWith(
      "recipe-1",
      30_000,
      idempotencyKey,
      "aggregate-hash",
      "all",
    );
  });

  it("does not create a character task when cost confirmation is cancelled", async () => {
    calls.confirm.mockRejectedValueOnce("cancel");
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get(".generate-designs").trigger("click");
    await flushPromises();
    expect(calls.previewCharacterDesign).toHaveBeenCalledTimes(1);
    expect(calls.runCharacterDesign).not.toHaveBeenCalled();
  });

  it("keeps ordinary selection read-only and requires an explicit confirmed review action", async () => {
    const initial = [
      ...projectAssets().map((asset: any) => asset.id === "episode-child"
        ? { ...asset, characterDesign: { ...asset.characterDesign, selected: false } }
        : asset),
      { id: "episode-child-current", projectId: "project-1", mediaType: "image", role: "character_design_child", status: "approved", semanticKey: "episode:child:current", sha256: "current", metadata: { title: "当前本集儿童", authority: { role: "episode_appearance", providerEligible: true, priority: 90, lockedTraits: [], mutableTraits: [], forbiddenTransfer: [] } }, contentUrl: "/current.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r2", revision: 2, revisionStatus: "awaiting_review", isCurrentRevision: true, slot: "child", candidateIndex: 2, semanticRole: "appearance", selected: true }, reviewAction: { executable: true, route: "recipe_character_design", recipeInstanceId: "recipe-1", targetType: "character_design", targetId: "episode-child-current", targetHash: "current" } },
    ];
    const reconciled = initial.map((asset: any) => asset.id === "episode-child"
      ? { ...asset, status: "approved", characterDesign: { ...asset.characterDesign, selected: true } }
      : asset.id === "episode-child-current"
      ? { ...asset, status: "rejected", characterDesign: { ...asset.characterDesign, selected: false } }
      : asset);
    calls.assets.mockResolvedValueOnce(initial).mockResolvedValueOnce(reconciled);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1", focusedItemId: "episode-child" } });
    await flushPromises();
    await wrapper.get(".media-card.selected>button").trigger("click");
    expect(calls.review).not.toHaveBeenCalled();

    await wrapper.get(".asset-inspector footer button:first-child").trigger("click");
    await flushPromises();
    expect(calls.confirm).toHaveBeenCalledTimes(1);
    expect(calls.reviewRecipe).toHaveBeenCalledWith({
      recipeInstanceId: "recipe-1",
      targetType: "character_design",
      targetId: "episode-child",
      targetHash: "e",
      decision: "approve",
      reason: "导演资产工作区审核通过",
    });
    expect(calls.review).not.toHaveBeenCalled();
    expect(calls.assets).toHaveBeenCalledTimes(2);
    expect(wrapper.get(".asset-inspector dl").text()).toContain("approved");
    const competitor = wrapper.findAll(".media-card").find((card) => card.text().includes("当前本集儿童"))!;
    expect(competitor.text()).toContain("rejected");
    expect(competitor.get("em").attributes("data-provider-eligible")).toBe("false");
  });

  it("cancels review quietly and reports review API failures without changing the asset status", async () => {
    calls.confirm.mockRejectedValueOnce("cancel");
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1", focusedItemId: "episode-child" } });
    await flushPromises();
    await wrapper.get(".asset-inspector footer button:first-child").trigger("click");
    await flushPromises();
    expect(calls.reviewRecipe).not.toHaveBeenCalled();
    expect(calls.error).not.toHaveBeenCalled();

    calls.confirm.mockResolvedValue("confirm");
    calls.reviewRecipe.mockRejectedValueOnce(new Error("review unavailable"));
    await wrapper.get(".asset-inspector footer button:first-child").trigger("click");
    await flushPromises();
    expect(calls.error).toHaveBeenCalledWith(expect.stringContaining("review unavailable"));
    expect(wrapper.get(".asset-inspector dl").text()).toContain("awaiting_review");
  });

  it("keeps batch review explicit and reports zero Provider calls", async () => {
    calls.assets
      .mockResolvedValueOnce(projectAssets())
      .mockResolvedValueOnce(projectAssets().map((asset: any) => asset.id === "episode-child"
        ? { ...asset, status: "approved", characterDesign: { ...asset.characterDesign, selected: true } }
        : asset));
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get("[aria-label='选择 本集儿童设计 用于批量操作']").setValue(true);
    const batchButton = wrapper.get(".batch-review");
    expect(batchButton.text()).toContain("0 次 Provider");
    await batchButton.trigger("click");
    await flushPromises();
    expect(calls.reviewRecipe).toHaveBeenCalledWith(expect.objectContaining({ targetId: "episode-child", targetType: "character_design" }));
    expect(calls.confirm).toHaveBeenCalledWith(expect.stringContaining("Provider 调用 0 次"), expect.any(String), expect.any(Object));
    expect(calls.assets).toHaveBeenCalledTimes(2);
    expect(wrapper.findAll(".media-card").find((card) => card.text().includes("本集儿童设计"))!.text()).toContain("approved");
    expect(wrapper.get(".batch-toggle").element.tagName).toBe("LABEL");
  });

  it("reports partial batch review truthfully and retains failed items selected", async () => {
    const currentAssets = await calls.assets.getMockImplementation()!();
    const initial = [
      ...currentAssets,
      { id: "episode-cat", projectId: "project-1", mediaType: "image", role: "character_design_cat", status: "awaiting_review", semanticKey: "episode:cat", sha256: "k", metadata: { title: "本集猫咪设计", authority: { role: "episode_appearance", providerEligible: true, priority: 90, lockedTraits: ["毛色"], mutableTraits: ["动作"], forbiddenTransfer: [] } }, contentUrl: "/episode-cat.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r2", revision: 2, revisionStatus: "awaiting_review", isCurrentRevision: true, slot: "cat", candidateIndex: 1, semanticRole: "pose", selected: false }, reviewAction: { executable: true, route: "recipe_character_design", recipeInstanceId: "recipe-1", targetType: "character_design", targetId: "episode-cat", targetHash: "k" } },
    ];
    calls.assets
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(initial.map((asset: any) => asset.id === "episode-child"
        ? { ...asset, status: "approved", characterDesign: { ...asset.characterDesign, selected: true } }
        : asset));
    calls.reviewRecipe.mockResolvedValueOnce({ status: "approved" }).mockRejectedValueOnce(new Error("second review failed"));
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get("[aria-label='选择 本集儿童设计 用于批量操作']").setValue(true);
    await wrapper.get("[aria-label='选择 本集猫咪设计 用于批量操作']").setValue(true);
    await wrapper.get(".batch-review").trigger("click");
    await flushPromises();
    expect(calls.error).toHaveBeenCalledWith(expect.stringContaining("成功 1 项，失败 1 项"));
    expect((wrapper.get("[aria-label='选择 本集儿童设计 用于批量操作']").element as HTMLInputElement).checked).toBe(false);
    expect((wrapper.get("[aria-label='选择 本集猫咪设计 用于批量操作']").element as HTMLInputElement).checked).toBe(true);
    const successfulCard = wrapper.findAll(".media-card").find((card) => card.text().includes("本集儿童设计"))!;
    expect(successfulCard.text()).toContain("approved");
    expect(calls.assets).toHaveBeenCalledTimes(2);
  });

  it("routes each batch item through its executable domain review path", async () => {
    const currentAssets = await calls.assets.getMockImplementation()!();
    calls.assets.mockResolvedValue([
      ...currentAssets,
      { id: "legacy-produced", projectId: "project-1", mediaType: "image", role: "environment_reference", status: "ready", semanticKey: "scene:legacy", sha256: "z", metadata: { title: "旧版生成环境", authority: { role: "environment", providerEligible: true, priority: 40 } }, contentUrl: "/legacy.png", reviewAction: { executable: true, route: "legacy_asset", targetId: "legacy-produced" } },
    ]);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get("[aria-label='选择 本集儿童设计 用于批量操作']").setValue(true);
    await wrapper.get("[aria-label='选择 旧版生成环境 用于批量操作']").setValue(true);
    await wrapper.get(".batch-review").trigger("click");
    await flushPromises();
    expect(calls.reviewRecipe).toHaveBeenCalledWith(expect.objectContaining({ targetId: "episode-child" }));
    expect(calls.review).toHaveBeenCalledWith("legacy-produced", "approved", expect.any(String));
  });

  it("does not expose review controls for imported immutable Canon and preset references", async () => {
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1", focusedItemId: "child" } });
    await flushPromises();
    expect(wrapper.find(".asset-inspector footer").exists()).toBe(false);
    expect(wrapper.text()).toContain("当前绑定为只读");
  });

  it("uses CharacterDesign selection truth for episode Provider eligibility", async () => {
    const currentAssets = await calls.assets.getMockImplementation()!();
    calls.assets.mockResolvedValue([
      ...currentAssets.map((asset: any) => asset.id === "episode-child" ? { ...asset, status: "approved" } : asset),
      { id: "episode-child-old", projectId: "project-1", mediaType: "image", role: "character_design_child", status: "approved", semanticKey: "episode:child:old", sha256: "old", metadata: { title: "历史本集儿童", authority: { role: "episode_appearance", providerEligible: true, priority: 90, lockedTraits: [], mutableTraits: [], forbiddenTransfer: [] } }, contentUrl: "/old.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r1", revision: 1, revisionStatus: "approved", isCurrentRevision: false, slot: "child", candidateIndex: 1, semanticRole: "appearance", selected: true }, reviewAction: { executable: false, route: "readonly", targetId: "episode-child-old", disabledReason: "仅当前角色设计 Revision 可执行审核" } },
      { id: "episode-child-alt", projectId: "project-1", mediaType: "image", role: "character_design_child", status: "approved", semanticKey: "episode:child:alt", sha256: "alt", metadata: { title: "未选本集儿童", authority: { role: "episode_appearance", providerEligible: true, priority: 90, lockedTraits: [], mutableTraits: [], forbiddenTransfer: [] } }, contentUrl: "/alt.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r2", revision: 2, revisionStatus: "approved", isCurrentRevision: true, slot: "child", candidateIndex: 2, semanticRole: "appearance", selected: false }, reviewAction: { executable: true, route: "recipe_character_design", recipeInstanceId: "recipe-1", targetType: "character_design", targetId: "episode-child-alt", targetHash: "alt" } },
    ]);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    const card = (title: string) => wrapper.findAll(".media-card").find((item) => item.text().includes(title))!;
    expect(card("本集儿童设计").get("em").attributes("data-provider-eligible")).toBe("true");
    expect(card("历史本集儿童").get("em").attributes("data-provider-eligible")).toBe("false");
    expect(card("历史本集儿童").text()).toContain("不是当前角色设计 Revision");
    expect(card("未选本集儿童").get("em").attributes("data-provider-eligible")).toBe("false");
    expect(card("未选本集儿童").text()).toContain("未被当前槽位选中");
  });

  it("uses the persisted compiler roles appearance, pose and scale for CharacterDesign readiness", async () => {
    calls.assets.mockResolvedValue([
      ...projectAssets().map((asset: any) => asset.id === "episode-child"
        ? { ...asset, status: "approved", characterDesign: { ...asset.characterDesign, revisionStatus: "approved", selected: true } }
        : asset),
      { id: "episode-cat", projectId: "project-1", mediaType: "image", role: "character_design_cat", status: "approved", semanticKey: "episode:cat", sha256: "cat-design", metadata: { title: "本集猫咪设计", authority: { role: "episode_appearance", providerEligible: true, priority: 90 } }, contentUrl: "/episode-cat.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r2", revision: 2, revisionStatus: "approved", isCurrentRevision: true, slot: "cat", candidateIndex: 1, semanticRole: "pose", selected: true }, reviewAction: { executable: true, route: "recipe_character_design", recipeInstanceId: "recipe-1", targetType: "character_design", targetId: "episode-cat", targetHash: "cat-design" } },
      { id: "episode-pair", projectId: "project-1", mediaType: "image", role: "character_design_pair_scale", status: "approved", semanticKey: "episode:pair", sha256: "pair-design", metadata: { title: "本集同框比例", authority: { role: "pair_scale", providerEligible: true, priority: 80 } }, contentUrl: "/episode-pair.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r2", revision: 2, revisionStatus: "approved", isCurrentRevision: true, slot: "pair_scale", candidateIndex: 1, semanticRole: "scale", selected: true }, reviewAction: { executable: true, route: "recipe_character_design", recipeInstanceId: "recipe-1", targetType: "character_design", targetId: "episode-pair", targetHash: "pair-design" } },
      { id: "episode-cat-wrong-role", projectId: "project-1", mediaType: "image", role: "character_design_cat", status: "approved", semanticKey: "episode:cat:wrong", sha256: "wrong", metadata: { title: "错误职责猫咪", authority: { role: "episode_appearance", providerEligible: true, priority: 90 } }, contentUrl: "/wrong.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r2", revision: 2, revisionStatus: "approved", isCurrentRevision: true, slot: "cat", candidateIndex: 2, semanticRole: "appearance", selected: true }, reviewAction: { executable: true, route: "recipe_character_design", recipeInstanceId: "recipe-1", targetType: "character_design", targetId: "episode-cat-wrong-role", targetHash: "wrong" } },
    ]);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    const card = (title: string) => wrapper.findAll(".media-card").find((item) => item.text().includes(title))!;
    expect(card("本集儿童设计").get("em").attributes("data-provider-eligible")).toBe("true");
    expect(card("本集猫咪设计").get("em").attributes("data-provider-eligible")).toBe("true");
    expect(card("本集同框比例").get("em").attributes("data-provider-eligible")).toBe("true");
    expect(card("错误职责猫咪").get("em").attributes("data-provider-eligible")).toBe("false");
    expect(card("错误职责猫咪").text()).toContain("槽位或语义职责不匹配");
  });

  it("keeps validation-only CharacterDesign candidates read-only", async () => {
    calls.assets.mockResolvedValue([
      ...projectAssets(),
      { id: "episode-validation", projectId: "project-1", mediaType: "image", role: "character_design_child", status: "ready", semanticKey: "episode:validation", sha256: "validation", metadata: { title: "引用顺序验证候选", characterDesign: { validationOnly: true }, authority: { role: "episode_appearance", providerEligible: true, priority: 90 } }, contentUrl: "/validation.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r2", revision: 2, revisionStatus: "awaiting_review", isCurrentRevision: true, slot: "child", candidateIndex: 3, semanticRole: "appearance", selected: false }, reviewAction: { executable: false, route: "readonly", recipeInstanceId: "recipe-1", targetType: "character_design", targetId: "episode-validation", targetHash: "validation", disabledReason: "引用顺序验证候选只用于审计，不能审核或替换生产版本" } },
    ]);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1", focusedItemId: "episode-validation" } });
    await flushPromises();
    expect(wrapper.find(".asset-inspector footer").exists()).toBe(false);
    expect(wrapper.get(".readonly-binding").text()).toContain("引用顺序验证候选只用于审计");
    expect((wrapper.get("[aria-label='选择 引用顺序验证候选 用于批量操作']").element as HTMLInputElement).disabled).toBe(true);
    expect(calls.reviewRecipe).not.toHaveBeenCalled();
  });

  it("saves formal Canon edits explicitly and exposes a dirty guard", async () => {
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1", panel: "references" } });
    await flushPromises();
    const identityField = wrapper.findAll(".canon-fields textarea")[0];
    await identityField.setValue("固定儿童脸型、五官与年龄感");

    expect(wrapper.emitted("dirty-change")?.at(-1)?.[0]).toMatchObject({ label: "人物、猫咪与画风 Canon" });
    await wrapper.get(".canon-editor button.save").trigger("click");
    await flushPromises();
    expect(calls.update).toHaveBeenCalledWith("project-1", 4, expect.objectContaining({ personIdentity: "固定儿童脸型、五官与年龄感" }));
    expect(wrapper.text()).toContain("Revision 5");
  });

  it("hard-blocks Provider readiness when one subject has multiple authority revisions", async () => {
    const duplicated = profile();
    duplicated.references.push({
      assetId: "child-other", semanticKey: "person:headshot:other", title: "另一儿童权威", contentUrl: "/other.png", thumbnailUrl: "/other.png", approvalStatus: "approved", sha256: "g", required: true,
      authority: identity("child", "canon-v5"), subjectId: "child-subject-other", subjectRevisionId: "child-v5", subjectRevision: 5, subjectKind: "person", subjectRole: "protagonist", authorityOrigin: "subject_revision", currentAuthority: true, visualProfileRevisionId: "profile-1",
    });
    calls.profile.mockResolvedValue(duplicated);
    calls.assets.mockResolvedValue([
      ...await calls.assets.getMockImplementation()!(),
      { id: "child-other", projectId: "project-1", mediaType: "image", role: "person_identity", status: "approved", semanticKey: "person:headshot:other", sha256: "g", metadata: { subjectRevisionId: "child-v5", visualProfileRevisionId: "profile-1", authority: identity("child", "canon-v5") }, contentUrl: "/other.png" },
    ]);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    expect(wrapper.get(".authority-blocker").text()).toContain("儿童存在多个身份权威来源");
    expect(wrapper.text()).toContain("Provider 提交已阻断");
  });

  it("does not treat historical, rejected, preset or revision-less identity media as an active authority conflict", async () => {
    const currentAssets = await calls.assets.getMockImplementation()!();
    const visualProfile = profile();
    visualProfile.references.push({
      assetId: "child-rejected", semanticKey: "person:rejected", title: "已拒绝儿童参考", contentUrl: "/rejected.png", thumbnailUrl: "/rejected.png", approvalStatus: "rejected", sha256: "l", required: false,
      authority: identity("child", "child-v5"),
      subjectId: "rejected-subject", subjectRevisionId: "child-v5", subjectRevision: 5, subjectKind: "person", subjectRole: "protagonist", authorityOrigin: "subject_revision", currentAuthority: false, visualProfileRevisionId: "profile-1",
    });
    calls.profile.mockResolvedValue(visualProfile);
    calls.assets.mockResolvedValue([
      ...currentAssets,
      { id: "child-history", projectId: "project-1", mediaType: "image", role: "person_identity", status: "approved", semanticKey: "person:history", sha256: "m", metadata: { title: "历史身份", subjectRevisionId: "child-v2", authority: identity("child", "child-v2") }, contentUrl: "/history.png" },
      { id: "child-rejected", projectId: "project-1", mediaType: "image", role: "person_identity", status: "rejected", semanticKey: "person:rejected", sha256: "l", metadata: { title: "已拒绝儿童参考", subjectRevisionId: "child-v5", authority: identity("child", "child-v5") }, contentUrl: "/rejected.png" },
      { id: "child-missing-revision", projectId: "project-1", mediaType: "image", role: "person_identity", status: "approved", semanticKey: "person:missing", sha256: "n", metadata: { title: "缺少 Revision 的历史身份", authority: identity("child") }, contentUrl: "/missing.png" },
    ]);
    calls.presets.mockResolvedValue([{ key: "healing_child_cat_style_board_v4", canonProfileId: "old", title: "历史预设", description: "", version: 3, ready: true, slots: [{ assetId: "preset-child", semanticKey: "person:preset", title: "预设儿童", contentUrl: "/preset.png", thumbnailUrl: "/preset.png", approvalStatus: "approved", sha256: "o", required: true, role: "person", purpose: "identity", instruction: "", authority: identity("child", "preset-v3") }] }]);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    expect(wrapper.find(".authority-blocker").exists()).toBe(false);
    expect(wrapper.text()).toContain("身份权威唯一");
  });

  it("rejects an old SubjectRevision identity even when it remains bound in the current profile", async () => {
    const visualProfile = profile();
    visualProfile.references.push({
      assetId: "child-old-bound", semanticKey: "person:old-bound", title: "旧儿童身份", contentUrl: "/old-child.png", thumbnailUrl: "/old-child.png", approvalStatus: "approved", sha256: "old-child", required: false,
      authority: identity("child", "child-v2"), subjectId: "child-subject", subjectRevisionId: "child-v2", subjectRevision: 2, subjectKind: "person", subjectRole: "protagonist", authorityOrigin: "subject_revision", currentAuthority: false, visualProfileRevisionId: "profile-1",
    });
    calls.profile.mockResolvedValue(visualProfile);
    calls.assets.mockResolvedValue([
      ...projectAssets(),
      { id: "child-old-bound", projectId: "project-1", mediaType: "image", role: "person_identity", status: "approved", semanticKey: "person:old-bound", sha256: "old-child", metadata: { title: "旧儿童身份", subjectRevisionId: "child-v2", visualProfileRevisionId: "profile-1", authority: identity("child", "child-v2") }, contentUrl: "/old-child.png" },
    ]);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    const oldIdentity = wrapper.findAll(".media-card").find((item) => item.text().includes("旧儿童身份"))!;
    expect(oldIdentity.get("em").attributes("data-provider-eligible")).toBe("false");
    expect(oldIdentity.text()).toContain("不是当前已批准 SubjectRevision 身份权威");
  });

  it("does not let free-form asset metadata relabel a bound identity and bypass current-authority checks", async () => {
    const visualProfile = profile();
    const childReference = visualProfile.references.find((item) => item.assetId === "child")!;
    childReference.currentAuthority = false;
    calls.profile.mockResolvedValue(visualProfile);
    calls.assets.mockResolvedValue(projectAssets().map((asset) => asset.id === "child"
      ? {
          ...asset,
          metadata: {
            ...asset.metadata,
            title: "被错误 metadata 标记的儿童身份",
            authority: {
              role: "style_board",
              providerEligible: true,
              priority: 999,
              lockedTraits: [],
              mutableTraits: [],
              forbiddenTransfer: [],
            },
          },
        }
      : asset));

    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();

    const childIdentity = wrapper.findAll(".media-card").find((item) => item.text().includes("被错误 metadata 标记的儿童身份"))!;
    expect(childIdentity.get("em").attributes("data-provider-eligible")).toBe("false");
    expect(childIdentity.text()).toContain("不是当前已批准 SubjectRevision 身份权威");
  });

  it("computes truthful Provider eligibility from authority, approval and current-profile binding", async () => {
    const currentProfile = profile();
    currentProfile.references.push({ assetId: "pending-board", semanticKey: "style_board:pending", title: "待审核画风板", contentUrl: "/pending.png", thumbnailUrl: "/pending.png", approvalStatus: "awaiting_review", sha256: "q", required: false, authority: { role: "style_board", providerEligible: true, priority: 80, lockedTraits: [], mutableTraits: [], forbiddenTransfer: [] } });
    currentProfile.references.push({ assetId: "rejected-board", semanticKey: "style_board:rejected", title: "已拒绝画风板", contentUrl: "/rejected-board.png", thumbnailUrl: "/rejected-board.png", approvalStatus: "rejected", sha256: "r", required: false, authority: { role: "style_board", providerEligible: true, priority: 80, lockedTraits: [], mutableTraits: [], forbiddenTransfer: [] } });
    calls.profile.mockResolvedValue(currentProfile);
    calls.presets.mockResolvedValue([{ key: "healing_child_cat_style_board_v4", canonProfileId: "old", title: "未应用画风板", description: "", version: 3, ready: true, slots: [{ assetId: "preset-board", semanticKey: "style_board:old", title: "未应用画风板", contentUrl: "/old-board.png", thumbnailUrl: "/old-board.png", approvalStatus: "approved", sha256: "p", required: true, role: "style", purpose: "style", instruction: "", authority: { role: "style_board", providerEligible: true, priority: 80, lockedTraits: [], mutableTraits: [], forbiddenTransfer: [] } }] }]);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    const card = (title: string) => wrapper.findAll(".media-card").find((item) => item.text().includes(title))!;
    expect(card("叶片材质来源").get("em").attributes("data-provider-eligible")).toBe("false");
    expect(card("Canon v4 画风板").get("em").attributes("data-provider-eligible")).toBe("true");
    expect(card("未应用画风板").get("em").attributes("data-provider-eligible")).toBe("false");
    expect(card("未应用画风板").text()).toContain("未绑定到当前视觉档案 Revision");
    expect(card("待审核画风板").get("em").attributes("data-provider-eligible")).toBe("false");
    expect(card("待审核画风板").text()).toContain("仅已批准资产可提交");
    expect(card("已拒绝画风板").get("em").attributes("data-provider-eligible")).toBe("false");
    expect(card("窗边环境").get("em").attributes("data-provider-eligible")).toBe("true");
    expect(card("镜头锚点").get("em").attributes("data-provider-eligible")).toBe("false");
  });

  it("keeps a partial read failure visible as stale-success even when successful sources contain zero media", async () => {
    calls.assets.mockResolvedValue([]);
    calls.profile.mockRejectedValue(new Error("profile offline"));
    calls.presets.mockResolvedValue([]);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    expect(wrapper.get(".asset-warning").text()).toContain("profile offline");
    expect(wrapper.text()).toContain("当前分类没有真实媒体");
    expect(wrapper.find(".asset-state.error").exists()).toBe(false);
  });

  it("stages retry data instead of overwriting a dirty Canon draft", async () => {
    calls.assets.mockRejectedValueOnce(new Error("asset history unavailable"));
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1", panel: "references" } });
    await flushPromises();
    const identityField = wrapper.findAll(".canon-fields textarea")[0];
    await identityField.setValue("必须保留的本地儿童身份");
    calls.assets.mockResolvedValue([]);
    calls.profile.mockResolvedValue({ ...profile(), personIdentity: "服务器新身份", revision: 5 });
    await wrapper.get(".asset-warning button").trigger("click");
    await flushPromises();
    expect(identityField.element).toHaveProperty("value", "必须保留的本地儿童身份");
    expect(wrapper.get(".asset-warning").text()).toContain("当前未保存 Canon 已保留");

    await wrapper.get(".canon-editor button:not(.save)").trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".canon-fields textarea")[0].element).toHaveProperty("value", "服务器新身份");
  });

  it("reconciles recipe review truth without overwriting a dirty Canon draft", async () => {
    const initial = [
      ...projectAssets().map((asset: any) => asset.id === "episode-child"
        ? { ...asset, characterDesign: { ...asset.characterDesign, selected: false } }
        : asset),
      { id: "episode-child-competitor", projectId: "project-1", mediaType: "image", role: "character_design_child", status: "approved", semanticKey: "episode:child:competitor", sha256: "competitor", metadata: { title: "原选中儿童", authority: { role: "episode_appearance", providerEligible: true, priority: 90 } }, contentUrl: "/competitor.png", characterDesign: { recipeInstanceId: "recipe-1", revisionId: "design-r2", revision: 2, revisionStatus: "awaiting_review", isCurrentRevision: true, slot: "child", candidateIndex: 2, semanticRole: "appearance", selected: true }, reviewAction: { executable: true, route: "recipe_character_design", recipeInstanceId: "recipe-1", targetType: "character_design", targetId: "episode-child-competitor", targetHash: "competitor" } },
    ];
    const reconciled = initial.map((asset: any) => asset.id === "episode-child"
      ? { ...asset, status: "approved", characterDesign: { ...asset.characterDesign, selected: true } }
      : asset.id === "episode-child-competitor"
      ? { ...asset, status: "rejected", characterDesign: { ...asset.characterDesign, selected: false } }
      : asset);
    calls.assets.mockResolvedValueOnce(initial).mockResolvedValueOnce(reconciled);
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1", focusedItemId: "episode-child", panel: "references" } });
    await flushPromises();
    const identityField = wrapper.findAll(".canon-fields textarea")[0];
    await identityField.setValue("审核期间不能丢失的 Canon 草稿");
    await wrapper.get(".asset-inspector footer button:first-child").trigger("click");
    await flushPromises();
    expect(identityField.element).toHaveProperty("value", "审核期间不能丢失的 Canon 草稿");
    expect(calls.profile).toHaveBeenCalledTimes(1);
    expect(calls.assets).toHaveBeenCalledTimes(2);
    expect(wrapper.get(".asset-inspector dl").text()).toContain("approved");
    expect(wrapper.findAll(".media-card").find((item) => item.text().includes("原选中儿童"))!.text()).toContain("rejected");
  });

  it("describes missing authority declarations truthfully", async () => {
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1", focusedItemId: "anchor" } });
    await flushPromises();
    expect(wrapper.text()).toContain("当前资产未声明允许变化特征");
    expect(wrapper.text()).not.toContain("遵循项目 Canon 默认可变范围");
  });

  it("provides an accessible narrow-screen panel switch and focuses the selected panel", async () => {
    const wrapper = mount(AssetWorkspace, { attachTo: document.body, props: { projectId: "project-1" } });
    await flushPromises();
    const tabs = wrapper.findAll("[aria-label='角色资产工作区面板'] [role='tab']");
    expect(tabs.map((tab) => tab.text())).toEqual(["分类与 Canon", "媒体资产", "职责检查"]);
    expect(tabs[1].attributes("aria-selected")).toBe("true");

    await tabs[0].trigger("click");
    await flushPromises();
    expect(tabs[0].attributes("aria-selected")).toBe("true");
    expect(document.activeElement?.id).toBe("asset-panel-categories");

    await tabs[2].trigger("click");
    await flushPromises();
    expect(tabs[2].attributes("aria-selected")).toBe("true");
    expect(document.activeElement?.id).toBe("asset-panel-inspector");
  });
});
