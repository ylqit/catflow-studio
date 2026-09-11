import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ShotProduction from "./ShotProduction.vue";

const client = vi.hoisted(() => ({ shotProductionContext: vi.fn(), assets: vi.fn(), previewShotMedia: vi.fn(), generateShotMedia: vi.fn() }));
vi.mock("../../api/client", async original => ({ ...await original<typeof import("../../api/client")>(), api: client }));

describe("ShotProduction prompt records", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.shotProductionContext.mockResolvedValue({
      shot: { id: "shot-1", order: 1 }, frameCurrent: true, targetDurationFrames: 96, designHash: "design-1", jobs: [],
    });
    client.assets.mockResolvedValue([]);
  });

  it.each(["shot_frame", "shot_video"] as const)("shows and copies the exact %s preview with review advice kept separate", async purpose => {
    const final = "严格使用本镜头设计。\n避免项：额外肢体\n参考图 1：起点状态\n";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    client.previewShotMedia.mockResolvedValue({
      purpose, compiledProviderPrompt: final, prompt: "仅正文摘要", negativePrompt: "仅避免项摘要",
      generationMode: purpose === "shot_frame" ? "reference_images" : "from_frame", generateAudio: purpose === "shot_video",
      references: [], warnings: [{ code: "RELATION_REVIEW", message: "人工检查接触状态" }],
    });
    const wrapper = mount(ShotProduction, { props: { projectId: "project-1", planId: "plan-1", shotId: "shot-1", videoMode: purpose === "shot_video" } });
    if (purpose === "shot_frame") await wrapper.get("button").trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find(button => button.text().includes(purpose === "shot_frame" ? "检查镜头图片生成输入" : "检查本镜头视频输入"))!.trigger("click");
    await flushPromises();
    expect(client.previewShotMedia).toHaveBeenCalledWith("project-1", "plan-1", "shot-1", purpose);
    expect(wrapper.get(".compiled-provider-prompt").element.textContent).toBe(final);
    expect(wrapper.get('[aria-label="制作审查建议"]').text()).toContain("人工检查接触状态");
    await wrapper.findAll("button").find(button => button.text() === "复制最终模型指令")!.trigger("click");
    expect(writeText).toHaveBeenCalledWith(final);
    expect(client.generateShotMedia).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
