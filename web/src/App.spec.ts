import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { computed, defineComponent, reactive, ref } from "vue";

import App from "./App.vue";
const calls = vi.hoisted(() => ({ push: vi.fn() }));
const route = reactive({
  name: "projects" as string,
  path: "/projects",
  params: {} as Record<string, string>,
  query: {} as Record<string, string>,
});

enableAutoUnmount(afterEach);

vi.mock("vue-router", () => ({
  useRoute: () => route,
  useRouter: () => ({ push: calls.push }),
}));

vi.mock("./runtimeStatus", () => ({
  startRuntimeStatus: vi.fn(),
  stopRuntimeStatus: vi.fn(),
  useRuntimeStatus: () => ({
    health: ref(null),
    unreachable: ref(false),
  }),
}));

vi.mock("./tasks/taskCenter", () => ({
  clearCompletedTasks: vi.fn(),
  cancelPersistentTask: vi.fn(),
  recoverPersistentTask: vi.fn(),
  registerTask: vi.fn(),
  requestWorkspaceRefresh: vi.fn(),
  startTaskCenter: vi.fn(),
  stopTaskCenter: vi.fn(),
  useTaskCenter: () => ({
    items: computed(() => []),
    activeCount: computed(() => 2),
    attentionCount: computed(() => 3),
    lastNotification: ref(null),
    connectionError: ref(""),
    recoveringStepIds: ref([]),
    cancellingStepIds: ref([]),
  }),
}));

vi.mock("./api/client", () => ({
  api: { resumeStep: vi.fn() },
}));

describe("App global shell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    route.name = "projects";
    route.path = "/projects";
    route.params = {};
    route.query = {};
  });

  it("keeps one 64px application rail across every canonical workspace and legacy redirect target", async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          RouterView: { template: "<main data-testid='route-view' />" },
          ElProgress: { template: "<div />" },
        },
      },
    });

    expect(wrapper.findAll("[aria-label='应用导航']")).toHaveLength(1);
    expect(wrapper.find(".sidebar").exists()).toBe(false);
    expect(wrapper.find(".shell-mobile-nav").exists()).toBe(false);
    expect(wrapper.get("[aria-label='全局任务，5 个运行或待处理任务']").text()).toContain("5");
    expect(wrapper.find("[aria-label='项目列表']").exists()).toBe(true);
    expect(wrapper.find("[aria-label='重复项目概览']").exists()).toBe(false);

    for (const next of [
      { name: "project-script", path: "/projects/p1/script", params: { projectId: "p1" }, query: {} },
      { name: "project-assets", path: "/projects/p1/assets", params: { projectId: "p1" }, query: {} },
      { name: "project-production", path: "/projects/p1/production", params: { projectId: "p1" }, query: {} },
      { name: "project-production", path: "/projects/p1/production", params: { projectId: "p1" }, query: { workspace: "video", tab: "edit" } },
      { name: "settings", path: "/settings", params: {}, query: {} },
    ]) {
      Object.assign(route, next);
      await flushPromises();
      expect(wrapper.findAll("[aria-label='应用导航']")).toHaveLength(1);
    }
  });

  it("keeps the global rail limited to projects, tasks, and settings", async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          RouterView: defineComponent({ template: "<div />" }),
          ElProgress: { template: "<div />" },
        },
      },
    });
    expect(wrapper.get("[aria-label='项目列表']").element).toBeTruthy();
    expect(wrapper.get("[aria-label^='全局任务']").element).toBeTruthy();
    expect(wrapper.get("[aria-label='设置']").element).toBeTruthy();
    expect(wrapper.find("[aria-label='高级系统图']").exists()).toBe(false);
    expect(wrapper.find("[aria-label='独立兼容生产入口']").exists()).toBe(false);
  });
});
