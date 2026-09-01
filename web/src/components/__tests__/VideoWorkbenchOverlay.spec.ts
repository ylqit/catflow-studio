import { flushPromises, shallowMount } from "@vue/test-utils";
import { defineComponent } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VideoWorkbenchOverlay from "../production/VideoWorkbenchOverlay.vue";

const calls = vi.hoisted(() => ({ videoWorkbench: vi.fn() }));
vi.mock("../../api/client", () => ({ canvasApi: { videoWorkbench: calls.videoWorkbench } }));

function data() {
  return {
    activeTrackId: "track-1",
    approvedReferences: [],
    timeline: null,
    exportSummary: null,
    tracks: [{
      id: "track-1",
      title: "窗边镜头",
      shotIds: ["shot-1"],
      durationSeconds: 8,
      orderedReferences: [
        { assetId: "child", title: "本集儿童", semanticRole: "episode_appearance", ordinal: 1, providerEligible: true, contentUrl: "/child.png" },
        { assetId: "source", title: "叶片来源", semanticRole: "style_source", ordinal: 2, providerEligible: false, contentUrl: "/leaf.png" },
        { assetId: "style", title: "净化画风板", semanticRole: "style_board", ordinal: 3, providerEligible: true, contentUrl: "/style.png" },
      ],
      prompt: "生成一个 9:16、8 秒的原创一人一猫短片。",
      providerConfig: { model: "seedance", mode: "reference_media" },
      task: null,
      versions: [],
      selectedVersionId: null,
    }],
  };
}

const GenerationStub = defineComponent({ name: "VideoGenerationWorkspace", template: "<div data-testid='generation-panel' />" });
const DeliveryStub = defineComponent({ name: "DeliveryWorkbench", template: "<div data-testid='delivery-panel' />" });

describe("VideoWorkbenchOverlay", () => {
  beforeEach(() => { vi.clearAllMocks(); calls.videoWorkbench.mockResolvedValue(data()); });

  it("loads preview directly and keeps style_source out of the visible Provider references", async () => {
    const wrapper = shallowMount(VideoWorkbenchOverlay, {
      props: { projectId: "project-1", tab: "preview" },
      global: { stubs: { VideoGenerationWorkspace: GenerationStub, DeliveryWorkbench: DeliveryStub } },
    });
    await flushPromises();
    expect(calls.videoWorkbench).toHaveBeenCalledWith("project-1", expect.any(AbortSignal));
    expect(wrapper.text()).toContain("窗边镜头");
    expect(wrapper.text()).not.toContain("叶片来源");
  });

  it("uses the production-safe generation implementation without an extra video-workbench read", async () => {
    const wrapper = shallowMount(VideoWorkbenchOverlay, {
      props: { projectId: "project-1", tab: "generate", shotId: "shot-1" },
      global: { stubs: { VideoGenerationWorkspace: GenerationStub, DeliveryWorkbench: DeliveryStub } },
    });
    await flushPromises();
    expect(wrapper.find("[data-testid='generation-panel']").exists()).toBe(true);
    expect(calls.videoWorkbench).not.toHaveBeenCalled();
  });

  it("mounts the single delivery implementation for edit", async () => {
    const wrapper = shallowMount(VideoWorkbenchOverlay, {
      props: { projectId: "project-1", tab: "edit", shotId: "shot-1" },
      global: { stubs: { VideoGenerationWorkspace: GenerationStub, DeliveryWorkbench: DeliveryStub } },
    });
    await flushPromises();
    expect(wrapper.find("[data-testid='delivery-panel']").exists()).toBe(true);
    expect(calls.videoWorkbench).not.toHaveBeenCalled();
  });
});
