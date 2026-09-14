import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SeriesCreateView from "./SeriesCreateView.vue";

const router = vi.hoisted(() => ({ push: vi.fn() }));
const client = vi.hoisted(() => ({ catReferenceOptions: vi.fn(), referenceBinding: vi.fn(), createStorySeries: vi.fn() }));

vi.mock("vue-router", async () => {
  const actual = await vi.importActual<typeof import("vue-router")>("vue-router");
  return { ...actual, useRouter: () => router };
});
vi.mock("../api/client", () => ({ api: client }));

function mountView() {
  return mount(SeriesCreateView, {
    global: { stubs: { RouterLink: { props: ["to"], template: "<a><slot /></a>" } } },
  });
}

describe("SeriesCreateView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.catReferenceOptions.mockResolvedValue([{ key: "gray-original", label: "原版灰猫", canonProfileId: "gray-canon", available: true, fixedAssets: {}, auxiliary: [] }, { key: "white-v4", label: "V4 校色白猫", canonProfileId: "white-canon", available: true, fixedAssets: {}, auxiliary: [] }]);
    client.referenceBinding.mockResolvedValue({ canonProfileId: "gray-canon", label: "原版灰猫", canChange: true });
    client.createStorySeries.mockResolvedValue({ id: "series-1" });
  });

  it("supports a fixed series beyond thirty episodes without treating the provider batch as a product limit", async () => {
    const wrapper = mountView();
    await wrapper.get("input[aria-label='系列名称']").setValue("百集生活");
    await wrapper.get("textarea[aria-label='核心故事']").setValue("孩子和猫咪的长期日常。");
    await wrapper.get("input[aria-label='计划集数']").setValue(100);
    await wrapper.get("textarea[aria-label='世界与环境']").setValue("家与社区");
    await wrapper.get("textarea[aria-label='情绪方向']").setValue("温暖成长");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(client.createStorySeries).toHaveBeenCalledWith(expect.objectContaining({
      lengthMode: "fixed",
      plannedEpisodeCount: 100,
    }));
  });

  it("creates an ongoing series without a total episode count", async () => {
    const wrapper = mountView();
    await wrapper.get("input[aria-label='系列名称']").setValue("持续日常");
    await wrapper.get("textarea[aria-label='核心故事']").setValue("孩子和猫咪的长期日常。");
    await wrapper.get("input[value='ongoing']").setValue();
    await wrapper.get("textarea[aria-label='世界与环境']").setValue("家与社区");
    await wrapper.get("textarea[aria-label='情绪方向']").setValue("温暖成长");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(client.createStorySeries).toHaveBeenCalledWith(expect.objectContaining({
      lengthMode: "ongoing",
      plannedEpisodeCount: null,
    }));
  });
});
