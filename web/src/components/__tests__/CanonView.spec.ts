import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import type { AssetDto } from "../../api/types";
import CanonView from "../../views/CanonView.vue";

const apiMocks = vi.hoisted(() => ({
  canon: vi.fn(),
  projects: vi.fn(),
  project: vi.fn(),
  visualProfile: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api: apiMocks,
  assetContentUrl: (assetId: string) => `/api/v1/assets/${assetId}/content`,
}));

const personAsset: AssetDto = {
  id: "canon-person",
  role: "reference",
  mediaType: "image",
  scope: "canon",
  status: "approved",
  projectId: null,
  sceneId: null,
  shotId: null,
  sha256: "a".repeat(64),
  semanticKey: "person:headshot",
  metadata: {},
  contentReady: true,
  displayName: "人物大头照",
  referencePurpose: "person_identity",
};

const stubs = {
  ElAlert: { props: ["title"], template: "<div>{{ title }}</div>" },
  ElButton: { template: "<button><slot /></button>" },
  ElDialog: { template: "<div><slot /></div>" },
};

describe("CanonView", () => {
  it("presents only the global Canon library and does not load project settings", async () => {
    apiMocks.canon.mockResolvedValue([personAsset]);
    apiMocks.projects.mockResolvedValue([{ id: "project-1", title: "湖泊钓鱼" }]);

    const wrapper = mount(CanonView, { global: { stubs } });
    await flushPromises();

    expect(wrapper.text()).toContain("全局 Canon 资产");
    expect(wrapper.text()).toContain("人物大头照");
    expect(wrapper.text()).toContain("项目生成的场景视觉基准、锚点和视频帧不会进入 Canon");
    expect(wrapper.text()).not.toContain("选择项目");
    expect(wrapper.text()).not.toContain("保存新 Revision");
    expect(apiMocks.canon).toHaveBeenCalledOnce();
    expect(apiMocks.projects).not.toHaveBeenCalled();
    expect(apiMocks.project).not.toHaveBeenCalled();
    expect(apiMocks.visualProfile).not.toHaveBeenCalled();
  });
});
