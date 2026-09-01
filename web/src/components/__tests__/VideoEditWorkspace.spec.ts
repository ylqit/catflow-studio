import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VideoEditWorkspace from "../canvas/VideoEditWorkspace.vue";

const calls = vi.hoisted(() => ({
  createVideoEditRecipe: vi.fn(),
  compileVideoEditRecipe: vi.fn(),
  submitVideoEditRecipe: vi.fn(),
  videoFilmstrip: vi.fn(),
  createVideoFilmstrip: vi.fn(),
}));

vi.mock("../../api/client", () => ({ canvasApi: calls }));

describe("VideoEditWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.videoFilmstrip.mockResolvedValue({
      assetId: "video-1",
      frameCount: 12,
      status: "ready",
      frames: Array.from({ length: 12 }, (_, index) => ({
        assetId: `frame-${index}`,
        timestampMs: index * 2_000,
        contentUrl: `/api/v1/assets/frame-${index}/content`,
      })),
    });
    calls.createVideoEditRecipe.mockResolvedValue({
      id: "recipe-1",
      revision: 1,
      projectId: "project-1",
      canvasNodeId: "edit-node",
      sourceAssetId: "video-1",
      startMs: 19_000,
      endMs: 26_000,
      instruction: "女主转身并轻触发簪",
      referenceAssetIds: ["person-2"],
      annotations: [],
      status: "draft",
    });
    calls.compileVideoEditRecipe.mockResolvedValue({
      recipeId: "recipe-1",
      mode: "direct",
      stages: [{ kind: "video_edit" }],
      imageCallCount: 0,
      videoCallCount: 1,
      estimatedCostMicros: 8_000,
      warnings: [],
      provider: "ark",
      model: "seedance",
    });
    calls.submitVideoEditRecipe.mockResolvedValue({ status: "queued" });
  });

  it("compiles one interval, annotations and visible provider inputs before paid submit", async () => {
    const wrapper = mount(VideoEditWorkspace, {
      props: {
        projectId: "project-1",
        sourceAssetId: "video-1",
        videoUrl: "/api/v1/assets/video-1/content",
        durationMs: 26_000,
        initialStartMs: 19_000,
        initialEndMs: 26_000,
        references: [
          { id: "person-2", title: "女主参考", thumbnailUrl: "/person.png" },
        ],
      },
      global: { stubs: { Teleport: true } },
    });

    expect(wrapper.text()).toContain("矩形");
    expect(wrapper.text()).toContain("画笔");
    expect(wrapper.text()).toContain("箭头");
    expect(wrapper.text()).toContain("时间点");
    expect(wrapper.text()).toContain("会进入供应商请求");
    await flushPromises();
    const frames = wrapper.findAll('[data-filmstrip-frame]');
    expect(frames).toHaveLength(12);
    expect(new Set(frames.map((item) => item.attributes("src"))).size).toBe(12);
    expect(wrapper.get('[data-range-handle="start"]')).toBeDefined();
    expect(wrapper.get('[data-range-handle="end"]')).toBeDefined();
    expect(wrapper.text()).toContain("编辑区间入口帧");
    expect(wrapper.text()).toContain("编辑区间出口帧");

    await wrapper.get("textarea").setValue("女主转身并轻触发簪");
    await wrapper.get('[data-tool="rectangle"]').trigger("click");
    expect(wrapper.get('[data-tool="rectangle"]').classes()).toContain("active");
    await wrapper.get('[data-action="compile"]').trigger("click");
    await flushPromises();

    expect(calls.createVideoEditRecipe).toHaveBeenCalledWith(expect.objectContaining({
      startMs: 19_000,
      endMs: 26_000,
      referenceAssetIds: ["person-2"],
    }));
    expect(calls.compileVideoEditRecipe).toHaveBeenCalledWith("recipe-1");
    expect(wrapper.text()).toContain("0 次图片调用");
    expect(wrapper.text()).toContain("1 次视频调用");

    await wrapper.get('[data-action="submit"]').trigger("click");
    await flushPromises();
    expect(calls.submitVideoEditRecipe).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("取消，不创建任务");
    expect(wrapper.text()).toContain("编译输入哈希");
    await wrapper.get('[data-action="cancel-cost"]').trigger("click");
    expect(calls.submitVideoEditRecipe).not.toHaveBeenCalled();

    await wrapper.get('[data-action="submit"]').trigger("click");
    await wrapper.get('[data-action="confirm-cost"]').trigger("click");
    await flushPromises();
    expect(calls.submitVideoEditRecipe).toHaveBeenCalledWith(
      "recipe-1",
      expect.any(String),
      8_000,
    );
  });
});
