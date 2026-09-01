import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProjectListView from "../ProjectListView.vue";

const calls = vi.hoisted(() => ({
  projects: vi.fn(),
  visualPresets: vi.fn(),
  createChildCatProject: vi.fn(),
  push: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: { projects: calls.projects },
  canvasApi: {
    visualPresets: calls.visualPresets,
    createChildCatProject: calls.createChildCatProject,
  },
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: calls.push }) }));
vi.mock("element-plus", async (importOriginal) => ({
  ...(await importOriginal<typeof import("element-plus")>()),
  ElMessage: { success: calls.success, error: calls.error },
}));

const v4Preset = {
  key: "healing_child_cat_style_board_v4",
  title: "Canon v4",
  ready: true,
  canonProfileId: "canon-v4",
  slots: [{
    semanticKey: "style:healing_line_texture_v4",
    title: "净化画风板",
    assetId: "style-board-v4",
    authority: { providerEligible: true },
  }],
};

describe("ProjectListView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.projects.mockResolvedValue([{ id: "project-1", title: "一人一猫", contentDate: "2026-08-29", status: "active" }]);
    calls.visualPresets.mockResolvedValue([v4Preset]);
    calls.createChildCatProject.mockResolvedValue({ projectId: "project-new", providerCallCount: 0 });
  });

  it("offers one unambiguous project entry into the production flow", async () => {
    const wrapper = mount(ProjectListView);
    await flushPromises();

    const project = wrapper.get("[data-testid='project-project-1']");
    expect(project.findAll("button")).toHaveLength(1);
    expect(project.text()).not.toContain("旧版生产");
    expect(project.text()).not.toContain("重复项目概览");
    await project.get("button.open-project").trigger("click");
    expect(calls.push).toHaveBeenCalledWith({ name: "project-production", params: { projectId: "project-1" } });
  });

  it("atomically creates a Canon v4 child-cat project without a Provider task", async () => {
    const wrapper = mount(ProjectListView, {
      global: { stubs: { ElDialog: { template: "<div><slot/><slot name='footer'/></div>" }, ElButton: { template: "<button @click='$emit(\"click\")'><slot/></button>" } } },
    });
    await flushPromises();
    const fields = wrapper.findAll(".create-form input");
    await fields[0].setValue("窗边纸星星");
    await wrapper.get(".create-form textarea").setValue("清晨窗边，孩子和猫咪一起贴好纸星星。无对白。 ");
    const createButton = wrapper.findAll("button").find((button) => button.text().includes("创建并进入剧本"));
    expect(createButton).toBeTruthy();
    await createButton!.trigger("click");
    await flushPromises();

    expect(calls.createChildCatProject).toHaveBeenCalledWith(expect.objectContaining({
      title: "窗边纸星星",
      childCanonProfileId: "canon-v4",
      catCanonProfileId: "canon-v4",
      styleBoardAssetId: "style-board-v4",
      brief: expect.objectContaining({ durationSeconds: 8, aspectRatio: "9:16", qualityTier: "quick" }),
    }));
    expect(calls.push).toHaveBeenCalledWith({ name: "project-script", params: { projectId: "project-new" } });
    expect(calls.success).toHaveBeenCalledWith(expect.stringContaining("未调用 Provider"));
  });

  it("blocks project creation when the provider-eligible style board is missing", async () => {
    calls.visualPresets.mockResolvedValue([{ ...v4Preset, slots: [] }]);
    const wrapper = mount(ProjectListView, {
      global: { stubs: { ElDialog: { template: "<div><slot/><slot name='footer'/></div>" }, ElButton: { template: "<button v-bind='$attrs' @click='$emit(\"click\")'><slot/></button>" } } },
    });
    await flushPromises();
    const createButton = wrapper.findAll("button").find((button) => button.text().includes("创建并进入剧本"));
    expect(createButton?.attributes("disabled")).toBeDefined();
    expect(createButton?.attributes("title")).toContain("画风板");
    expect(calls.createChildCatProject).not.toHaveBeenCalled();
  });
});
