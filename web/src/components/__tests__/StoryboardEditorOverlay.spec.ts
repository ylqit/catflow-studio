import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StoryboardEditorOverlay from "../production/StoryboardEditorOverlay.vue";

const calls = vi.hoisted(() => ({
  confirm: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  saveManualStoryboard: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  canvasApi: { saveManualStoryboard: calls.saveManualStoryboard },
}));
vi.mock("element-plus", async (importOriginal) => ({
  ...(await importOriginal<typeof import("element-plus")>()),
  ElMessageBox: { confirm: calls.confirm },
  ElMessage: {
    error: calls.error,
    success: calls.success,
    warning: calls.warning,
  },
}));

enableAutoUnmount(afterEach);

const shot = {
  id: "shot-1",
  sceneId: "scene-window",
  storyboardRevisionId: "storyboard-r2",
  generationClipId: null,
  order: 1,
  revision: 2,
  referenceBindingRevision: 1,
  referenceBindings: [],
  title: "窗边纸星星",
  durationSeconds: 8,
  direction: "孩子发现纸星星，猫咪推回，最后一起贴到玻璃上。",
  visualDescription: "窗边连续动作",
  childAction: "发现并贴起纸星星",
  catAction: "用鼻尖推回",
  spatialRelation: "孩子在窗边，猫咪位于手边",
  contactOcclusion: "无关键遮挡",
  shotSize: "中近景",
  camera: "稳定轻推",
  lighting: "清晨柔光",
  dialogue: "",
  soundEffect: "室内环境声",
  musicIntent: "轻柔",
  wardrobeState: "本集服装保持一致",
  propState: "纸星星",
  continuityIn: "纸星星落在窗台",
  continuityOut: "纸星星贴在玻璃上",
  cutIntent: "continuous",
  status: "approved",
  staleReason: null,
  promptId: "prompt-1",
};

function flow() {
  return {
    revision: 4,
    nodes: [{
      id: "storyboard-table",
      kind: "storyboard_table",
      title: "分镜表",
      subtitle: "1 镜 · 8 秒",
      status: "complete",
      position: { x: 0, y: 0 },
      data: { storyboardRevision: 2, durationSeconds: 8, shots: [shot] },
    }],
    edges: [],
    viewport: { x: 0, y: 0, zoom: 1 },
    activeStoryboardRevisionId: "storyboard-r2",
    activeTrackId: "track-1",
    shotOrder: ["shot-1"],
  } as const;
}

describe("StoryboardEditorOverlay", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    calls.confirm.mockResolvedValue("confirm");
    calls.saveManualStoryboard.mockResolvedValue({ revision: 3 });
  });

  it("edits the complete direction in a dedicated workspace and saves a new revision", async () => {
    const wrapper = mount(StoryboardEditorOverlay, { props: { projectId: "project-1", flow: flow() as any } });
    await wrapper.get("[aria-label='完整镜头描述']").setValue("更新后的完整镜头方向");
    await wrapper.get(".overlay-footer .primary").trigger("click");
    await flushPromises();

    expect(calls.confirm).toHaveBeenCalledWith(
      expect.stringContaining("Prompt、视频和时间线引用失效"),
      "保存分镜新版本",
      expect.any(Object),
    );
    expect(calls.saveManualStoryboard).toHaveBeenCalledWith(
      "project-1",
      2,
      [expect.objectContaining({
        id: "shot-1",
        order: 1,
        direction: "更新后的完整镜头方向",
        action: "更新后的完整镜头方向",
        durationSeconds: 8,
      })],
      true,
    );
    expect(wrapper.emitted("saved")).toHaveLength(1);
  });

  it("splits a valid shot without changing the total duration", async () => {
    const wrapper = mount(StoryboardEditorOverlay, { props: { projectId: "project-1", flow: flow() as any } });
    await wrapper.findAll(".shot-actions button").find((button) => button.text().includes("拆分"))!.trigger("click");
    expect(wrapper.findAll(".shot-list > button")).toHaveLength(2);
    expect(wrapper.get(".overlay-footer").text()).toContain("总时长 8s");
    expect(wrapper.get(".overlay-footer").text()).not.toContain("与目标相差");
  });

  it("keeps optional advanced director fields non-blocking", async () => {
    const wrapper = mount(StoryboardEditorOverlay, { props: { projectId: "project-1", flow: flow() as any } });
    await wrapper.get("[aria-label='完整镜头描述']").setValue("只保留最小可执行描述");
    const advancedTextareas = wrapper.findAll(".advanced textarea");
    for (const textarea of advancedTextareas) await textarea.setValue("");
    await wrapper.get(".overlay-footer .primary").trigger("click");
    await flushPromises();
    expect(calls.saveManualStoryboard).toHaveBeenCalledTimes(1);
    expect(calls.error).not.toHaveBeenCalled();
  });
});
