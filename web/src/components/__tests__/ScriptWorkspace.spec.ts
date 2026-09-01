import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ScriptWorkspace from "../director/ScriptWorkspace.vue";

const calls = vi.hoisted(() => ({
  approveStory: vi.fn(),
  editStoryRevision: vi.fn(),
  recipeInstance: vi.fn(),
  runRecipeStory: vi.fn(),
  replace: vi.fn(),
  scriptWorkspace: vi.fn(),
  confirm: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  canvasApi: {
    approveStory: calls.approveStory,
    editStoryRevision: calls.editStoryRevision,
    recipeInstance: calls.recipeInstance,
    runRecipeStory: calls.runRecipeStory,
    scriptWorkspace: calls.scriptWorkspace,
  },
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ replace: calls.replace }) }));
vi.mock("element-plus", async (importOriginal) => ({
  ...(await importOriginal<typeof import("element-plus")>()),
  ElMessageBox: { confirm: calls.confirm },
  ElMessage: { success: calls.success, warning: calls.warning, error: calls.error },
}));

enableAutoUnmount(afterEach);

function document(index: number, status = "candidate") {
  return {
    id: `story-${index}`,
    title: `故事 ${index}`,
    body: `完整正文 ${index}\n\n第二段`,
    summary: `摘要 ${index}`,
    revision: index,
    status,
    source: "ai",
    warnings: index === 1 ? [{ code: "pacing", severity: "warning", message: "节奏可继续打磨" }] : [],
  };
}

function workspace(count = 3) {
  return {
    brief: { body: "无对白、单场景的一人一猫短片" },
    documents: Array.from({ length: count }, (_, index) => document(index + 1, index === 0 ? "approved" : "candidate")),
    currentStoryId: count ? "story-1" : null,
    recipeInstanceId: "recipe-1",
  };
}

describe("ScriptWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    calls.scriptWorkspace.mockResolvedValue(workspace());
    calls.editStoryRevision.mockImplementation(async (_id, payload) => ({
      id: "story-saved",
      title: payload.title,
      body: payload.body,
      summary: payload.summary,
      revision: payload.expectedRevision + 1,
      status: "candidate",
      source: "manual",
      warnings: [],
    }));
    calls.approveStory.mockResolvedValue({ status: "approved" });
    calls.recipeInstance.mockResolvedValue({ id: "recipe-1", revision: 3, storyGenerationEstimatedCostMicros: 12_500 });
    calls.runRecipeStory.mockResolvedValue({ id: "job-story-1" });
    calls.confirm.mockResolvedValue("confirm");
  });

  it.each([1, 2, 4, 5])("loads %s complete document candidates without Canvas v2", async (count) => {
    calls.scriptWorkspace.mockResolvedValue(workspace(count));
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();

    expect(calls.scriptWorkspace).toHaveBeenCalledWith("project-1", expect.any(AbortSignal));
    expect(wrapper.findAll("[aria-label='故事候选'] button")).toHaveLength(count);
    expect(wrapper.get("[aria-label='完整故事正文']").element).toHaveProperty("value", expect.stringContaining("完整正文"));
  });

  it("renders a healthy empty document state", async () => {
    calls.scriptWorkspace.mockResolvedValue(workspace(0));
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    expect(wrapper.text()).toContain("还没有故事候选");
    expect(wrapper.find("[role='alert']").exists()).toBe(false);
  });

  it("creates a story task only after the explicit measured-cost confirmation", async () => {
    calls.scriptWorkspace.mockResolvedValue(workspace(0));
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get(".state button").trigger("click");
    await flushPromises();

    expect(calls.confirm).toHaveBeenCalledWith(
      expect.stringContaining("Director 文本模型 1 次"),
      "生成故事候选",
      expect.any(Object),
    );
    expect(calls.runRecipeStory).toHaveBeenCalledWith(
      "recipe-1",
      12_500,
      expect.any(String),
    );
  });

  it("does not create a story task when cost is unmetered or confirmation is cancelled", async () => {
    calls.scriptWorkspace.mockResolvedValue(workspace(0));
    calls.recipeInstance.mockResolvedValueOnce({ id: "recipe-1", revision: 3, storyGenerationEstimatedCostMicros: null });
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get(".state button").trigger("click");
    await flushPromises();
    expect(calls.runRecipeStory).not.toHaveBeenCalled();
    expect(calls.warning).toHaveBeenCalledWith(expect.stringContaining("费用尚未计量"));

    calls.recipeInstance.mockResolvedValue({ id: "recipe-1", revision: 3, storyGenerationEstimatedCostMicros: 12_500 });
    calls.confirm.mockRejectedValueOnce("cancel");
    await wrapper.get(".state button").trigger("click");
    await flushPromises();
    expect(calls.runRecipeStory).not.toHaveBeenCalled();
  });

  it("keeps artistic warnings non-blocking and saves a new revision", async () => {
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get("[aria-label='完整故事正文']").setValue("更新后的完整 Markdown 正文");

    expect(wrapper.text()).toContain("节奏可继续打磨");
    expect(wrapper.emitted("dirty-change")?.at(-1)?.[0]).toMatchObject({ label: "剧情正文" });
    await wrapper.get("form.document-editor").trigger("submit");
    await flushPromises();

    expect(calls.editStoryRevision).toHaveBeenCalledWith("story-1", expect.objectContaining({
      body: "更新后的完整 Markdown 正文",
      expectedRevision: 1,
    }));
    expect(wrapper.text()).toContain("Revision 2");
  });

  it("preserves local text and exposes a revision conflict", async () => {
    calls.editStoryRevision.mockRejectedValueOnce(new Error("409 版本冲突"));
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get("[aria-label='完整故事正文']").setValue("不能丢失的正文");
    await wrapper.get("form.document-editor").trigger("submit");
    await flushPromises();
    expect(wrapper.get("[aria-label='完整故事正文']").element).toHaveProperty("value", "不能丢失的正文");
    expect(wrapper.text()).toContain("版本冲突");
  });

  it("uses one idempotency key when retrying the same failed save", async () => {
    calls.editStoryRevision.mockRejectedValueOnce(new Error("transport timeout"));
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get("[aria-label='完整故事正文']").setValue("重试时保持同一提交身份");
    await wrapper.get("form.document-editor").trigger("submit");
    await flushPromises();
    const firstKey = calls.editStoryRevision.mock.calls[0][1].idempotencyKey;
    await wrapper.get("form.document-editor").trigger("submit");
    await flushPromises();
    expect(calls.editStoryRevision.mock.calls[1][1].idempotencyKey).toBe(firstKey);
  });

  it("sets a selected candidate current only after explicit confirmation", async () => {
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.findAll("[aria-label='故事候选'] button")[1].trigger("click");
    await wrapper.get("button.current").trigger("click");
    await flushPromises();
    expect(calls.confirm).toHaveBeenCalledTimes(1);
    expect(calls.approveStory).toHaveBeenCalledWith("story-2");
    expect(wrapper.findAll("[aria-label='故事候选'] button")[1].text()).toContain("当前剧情");
  });

  it("stages background data instead of overwriting a dirty draft", async () => {
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get("[aria-label='完整故事正文']").setValue("必须保留的本地正文");
    const incoming = workspace();
    incoming.documents[2].body = "服务器新正文";
    calls.scriptWorkspace.mockResolvedValueOnce(incoming);
    await wrapper.get("[aria-label='刷新剧情工作区']").trigger("click");
    await flushPromises();
    expect(wrapper.get("[aria-label='完整故事正文']").element).toHaveProperty("value", "必须保留的本地正文");
    expect(wrapper.text()).toContain("当前未保存正文已保留");
  });
});
