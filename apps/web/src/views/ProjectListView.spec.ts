import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProjectListView from "./ProjectListView.vue";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
const route = vi.hoisted(() => ({ query: {} as Record<string, string | string[]> }));
const client = vi.hoisted(() => ({
  projectLibrary: vi.fn(),
  projectCollections: vi.fn(),
  createProject: vi.fn(),
  createProjectCollection: vi.fn(),
  projectLibraryAction: vi.fn(),
  organizeProject: vi.fn(),
}));

vi.mock("vue-router", async () => {
  const actual = await vi.importActual<typeof import("vue-router")>("vue-router");
  return { ...actual, useRouter: () => router, useRoute: () => route };
});
vi.mock("../api/client", () => ({ api: client }));

const now = "2026-09-03T10:00:00Z";
const items = Array.from({ length: 36 }, (_, index) => ({
  id: `project-${index}`,
  title: `生活短片 ${index}`,
  themeSummary: "孩子和猫咪完成一个小小的生活动作。",
  targetDurationSeconds: 12,
  aspectRatio: "9:16",
  coverAssetId: index === 0 ? "cover-1" : null,
  collection: index < 3 ? { id: "home", name: "居家日常", colorKey: "sage", sortOrder: 0, archived: false, createdAt: now, updatedAt: now } : null,
  tags: [{ name: "室内", normalizedName: "室内" }],
  stage: index === 0 ? "editing" : "story",
  attention: index === 1 ? "needs_attention" : "normal",
  attentionReasons: index === 1 ? ["video_candidate_ready"] : [],
  pinned: index === 0,
  archived: false,
  lastActivityAt: now,
  createdAt: now,
}));

const facets = {
  systemViews: { all: 500, recent: 18, in_progress: 420, needs_attention: 7, completed: 80, pinned: 3, archived: 12 },
  stages: { story: 200, assets: 80, storyboard: 60, generation: 45, editing: 35, completed: 80 },
  collections: [{ id: "home", name: "居家日常", count: 120 }],
  tags: [{ name: "室内", count: 160 }],
};

describe("ProjectListView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    route.query = {};
    window.localStorage.clear();
    client.projectCollections.mockResolvedValue([{ id: "home", name: "居家日常", colorKey: "sage", sortOrder: 0, archived: false, createdAt: now, updatedAt: now }]);
    client.projectLibrary.mockResolvedValue({ items, nextCursor: "page-2", total: 500, facets });
  });

  it("renders only the first compact page and creator-facing management controls", async () => {
    const wrapper = mount(ProjectListView, {
      global: { stubs: { RouterLink: { props: ["to"], template: "<a><slot /></a>" } } },
    });
    await flushPromises();

    expect(wrapper.get("h1").text()).toBe("项目库");
    expect(wrapper.findAll("[data-project-card]")).toHaveLength(36);
    expect(wrapper.text()).toContain("500 个项目");
    expect(wrapper.text()).toContain("最近更新");
    expect(wrapper.text()).toContain("居家日常");
    expect(wrapper.text()).toContain("视频待选择");
    expect(wrapper.find("[aria-label='搜索项目']").exists()).toBe(true);
    expect(wrapper.find("[aria-label='切换为列表']").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("Provider task ID");
  });

  it("loads a cursor page and keeps API filters in the URL", async () => {
    client.projectLibrary
      .mockResolvedValueOnce({ items: items.slice(0, 2), nextCursor: "page-2", total: 3, facets })
      .mockResolvedValueOnce({ items: [{ ...items[2], id: "last-project" }], nextCursor: null, total: 3, facets })
      .mockResolvedValueOnce({ items: items.slice(0, 1), nextCursor: null, total: 1, facets });
    const wrapper = mount(ProjectListView, {
      global: { stubs: { RouterLink: { props: ["to"], template: "<a><slot /></a>" } } },
    });
    await flushPromises();

    await wrapper.get("button.load-more").trigger("click");
    await flushPromises();
    expect(client.projectLibrary).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "page-2" }));
    expect(wrapper.findAll("[data-project-card]")).toHaveLength(3);

    await wrapper.get("[aria-label='搜索项目']").setValue("雨天");
    await new Promise((resolve) => setTimeout(resolve, 350));
    await flushPromises();
    expect(router.replace).toHaveBeenCalledWith(expect.objectContaining({ query: expect.objectContaining({ q: "雨天" }) }));
  });

  it("keeps selection separate from navigation and performs one batch action", async () => {
    const wrapper = mount(ProjectListView, {
      global: { stubs: { RouterLink: { props: ["to"], template: "<a class='project-link'><slot /></a>" } } },
    });
    await flushPromises();

    const checkbox = wrapper.get("input[aria-label='选择生活短片 0']");
    expect(checkbox.element.closest("a")).toBeNull();
    await checkbox.setValue(true);
    client.projectLibraryAction.mockResolvedValue({ updatedCount: 1 });
    await wrapper.get("button[data-action='pin']").trigger("click");
    await flushPromises();

    expect(client.projectLibraryAction).toHaveBeenCalledWith({ action: "pin", projectIds: ["project-0"] });
  });

  it("provides creator-facing row actions without nesting them in project links", async () => {
    window.localStorage.setItem("catflow.library.layout", "list");
    client.organizeProject.mockResolvedValue(items[0]);
    const wrapper = mount(ProjectListView, {
      global: {
        stubs: {
          RouterLink: {
            props: ["to"],
            template: "<a class='list-project' :data-to='to'><slot /></a>",
          },
        },
      },
    });
    await flushPromises();

    const menu = wrapper.get("summary[aria-label='生活短片 0的更多操作']");
    expect(menu.element.closest("a")).toBeNull();
    expect(wrapper.get("a.list-project").attributes("data-to")).toBe(
      "/projects/project-0/delivery",
    );
    await menu.trigger("click");
    await wrapper.get(".row-actions button").trigger("click");
    await flushPromises();

    expect(client.organizeProject).toHaveBeenCalledWith("project-0", { pinned: false });
  });
});
