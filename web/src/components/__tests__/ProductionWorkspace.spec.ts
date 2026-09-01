import { flushPromises, shallowMount } from "@vue/test-utils";
import { defineComponent, reactive } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProductionWorkspace from "../production/ProductionWorkspace.vue";

const calls = vi.hoisted(() => ({
  productionFlow: vi.fn(),
  saveLayout: vi.fn(),
  fitView: vi.fn(),
  getViewport: vi.fn(() => ({ x: 0, y: 0, zoom: 0.78 })),
  push: vi.fn(),
  replace: vi.fn(),
}));
const route = reactive({ query: {} as Record<string, string> });

vi.mock("@vue-flow/core", async () => {
  const { defineComponent } = await import("vue");
  return {
    VueFlow: defineComponent({ name: "VueFlow", props: ["nodes", "edges"], template: "<div data-testid='flow' />" }),
    useVueFlow: () => ({ fitView: calls.fitView, getViewport: calls.getViewport }),
  };
});
vi.mock("@vue-flow/background", async () => {
  const { defineComponent } = await import("vue");
  return { Background: defineComponent({ name: "Background", template: "<div />" }) };
});
vi.mock("vue-router", () => ({ useRoute: () => route, useRouter: () => ({ push: calls.push, replace: calls.replace }) }));
vi.mock("../../api/client", () => ({
  canvasApi: {
    productionFlow: calls.productionFlow,
    saveProductionFlowLayout: calls.saveLayout,
  },
}));

const kinds = ["script", "director_plan", "assets", "storyboard_table", "storyboard", "workbench"] as const;
function productionFlow() {
  return {
    revision: 7,
    nodes: kinds.map((kind, index) => ({
      id: `node-${kind}`,
      kind,
      title: ["剧本", "导演计划", "角色与素材", "分镜表", "分镜画面", "视频工作台"][index],
      subtitle: "当前产物摘要",
      status: "ready",
      position: { x: (index % 3) * 340, y: Math.floor(index / 3) * 300 },
      data: kind === "storyboard_table" ? { shots: [{ id: "shot-1", title: "镜头一", durationSeconds: 8 }] } : {},
    })),
    edges: kinds.slice(1).map((kind, index) => ({ id: `edge-${index}`, source: `node-${kinds[index]}`, target: `node-${kind}` })),
    viewport: { x: 0, y: 0, zoom: 0.78 },
    activeStoryboardRevisionId: "storyboard-1",
    activeTrackId: "shot-1",
    shotOrder: ["shot-1"],
  };
}

describe("ProductionWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    route.query = {};
    calls.productionFlow.mockResolvedValue(productionFlow());
    calls.saveLayout.mockResolvedValue({
      projectId: "project-1",
      layoutVersion: 8,
      syncStatus: "saved",
      viewport: { x: 40, y: 20, zoom: 0.9 },
      rebasedFromVersion: null,
    });
    calls.fitView.mockResolvedValue(undefined);
  });

  it("renders exactly six stable production artifacts without zone or system nodes", async () => {
    const wrapper = shallowMount(ProductionWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    const flow = wrapper.getComponent({ name: "VueFlow" });
    expect(calls.productionFlow).toHaveBeenCalledWith("project-1", expect.any(AbortSignal));
    expect(flow.props("nodes")).toHaveLength(6);
    expect(flow.props("nodes").map((node: { data: { artifact: { kind: string } } }) => node.data.artifact.kind)).toEqual(kinds);
    expect(wrapper.text()).not.toContain("重复阶段导航");
    expect(wrapper.text()).not.toContain("高级系统图");
  });

  it("opens the script workspace from the selected script artifact", async () => {
    const wrapper = shallowMount(ProductionWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get(".production-inspector button.primary").trigger("click");
    expect(calls.push).toHaveBeenCalledWith({ name: "project-script", params: { projectId: "project-1" }, query: {} });
  });

  it("restores the near-fullscreen video workbench from the URL", async () => {
    route.query = { workspace: "video", tab: "generate", shot: "shot-1" };
    const wrapper = shallowMount(ProductionWorkspace, {
      props: { projectId: "project-1" },
      global: { stubs: { VideoWorkbenchOverlay: defineComponent({ name: "VideoWorkbenchOverlay", props: ["tab", "shotId"], template: "<div data-testid='workbench' />" }) } },
    });
    await flushPromises();
    const overlay = wrapper.getComponent({ name: "VideoWorkbenchOverlay" });
    expect(overlay.props()).toMatchObject({ tab: "generate", shotId: "shot-1" });
    expect(wrapper.find(".node-floating-editor").exists()).toBe(false);
  });

  it("persists the production viewport through the dedicated flow layout boundary", async () => {
    const wrapper = shallowMount(ProductionWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    calls.getViewport.mockReturnValueOnce({ x: 40, y: 20, zoom: 0.9 });
    wrapper.getComponent({ name: "VueFlow" }).vm.$emit("viewportChangeEnd", { x: 40, y: 20, zoom: 0.9 });
    await flushPromises();

    expect(calls.saveLayout).toHaveBeenCalledWith("project-1", 7, expect.objectContaining({
      operations: [{ type: "viewport" }],
      viewport: { x: 40, y: 20, zoom: 0.9 },
    }));
  });

  it("fits all six production artifacts on load without generating a duplicate layout write", async () => {
    const wrapper = shallowMount(ProductionWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    wrapper.getComponent({ name: "VueFlow" }).vm.$emit("init");
    await flushPromises();

    expect(calls.fitView).toHaveBeenCalledWith(expect.objectContaining({ minZoom: 0.62, maxZoom: 0.86 }));
    expect(calls.saveLayout).not.toHaveBeenCalled();
  });
});
