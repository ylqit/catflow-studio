import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { AssetDto } from "../../api/types";
import AssetImageViewer from "./AssetImageViewer.vue";

function asset(id: string, role: string): AssetDto {
  return {
    id,
    projectId: "project-1",
    role,
    mediaType: "image",
    sha256: id.padEnd(64, "a").slice(0, 64),
    byteSize: 1024,
    metadata: {},
    createdAt: "2026-09-03T00:00:00Z",
  };
}

describe("AssetImageViewer", () => {
  it("shows original images, switches candidates, and exposes fixed-reference comparison", async () => {
    const environments = [asset("environment-1", "environment"), asset("environment-2", "environment")];
    const comparisons = [
      { label: "固定儿童", asset: asset("child", "episode_child") },
      { label: "固定猫咪", asset: asset("cat", "episode_cat") },
      { label: "人猫比例", asset: asset("scale", "pair_scale") },
      { label: "固定画风板", asset: asset("style", "style_board") },
    ];
    const wrapper = mount(AssetImageViewer, {
      props: {
        open: true,
        title: "当前环境参考",
        assets: environments,
        activeAssetId: "environment-1",
        comparisons,
      },
      attachTo: document.body,
    });

    expect(wrapper.get('[role="dialog"]').attributes("aria-modal")).toBe("true");
    expect(wrapper.get(".viewer-main-image").attributes("src")).toContain("environment-1");

    await wrapper.get('[aria-label="下一张候选"]').trigger("click");
    expect(wrapper.get(".viewer-main-image").attributes("src")).toContain("environment-2");

    await wrapper.get("button[data-view='comparison']").trigger("click");
    expect(wrapper.findAll(".comparison-item")).toHaveLength(4);
    expect(wrapper.text()).toContain("固定儿童");
    expect(wrapper.text()).toContain("固定画风板");

    await wrapper.get('[aria-label="放大图片"]').trigger("click");
    expect(wrapper.get(".zoom-value").text()).toBe("125%");

    await wrapper.get('[role="dialog"]').trigger("keydown", { key: "Escape" });
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("shows a retry action when the original image cannot be loaded", async () => {
    const wrapper = mount(AssetImageViewer, {
      props: {
        open: true,
        title: "固定儿童",
        assets: [asset("child", "episode_child")],
        activeAssetId: "child",
        comparisons: [],
      },
    });

    await wrapper.get(".viewer-main-image").trigger("error");
    expect(wrapper.text()).toContain("图片暂时无法读取");
    expect(wrapper.get("button[data-action='retry']").text()).toBe("重试");
  });

  it("keeps old environment warnings visible without inventing a missing prompt", () => {
    const environment = asset("legacy-environment", "environment");
    const wrapper = mount(AssetImageViewer, {
      props: {
        open: true,
        title: "当前环境参考",
        assets: [environment],
        activeAssetId: environment.id,
        comparisons: [],
        promptUnavailable: true,
        qualityReport: {
          intentMatch: "warning",
          characterFree: "fail",
          styleMatch: "warning",
          stagingSpace: "pass",
          technical: "pass",
          warnings: [{ code: "unexpected_subject", message: "画面中出现了猫咪。" }],
        },
      },
    });

    expect(wrapper.text()).toContain("画面检查建议");
    expect(wrapper.text()).toContain("发现人物或动物");
    expect(wrapper.text()).toContain("画面中出现了猫咪");
    expect(wrapper.text()).toContain("旧任务未记录完整生成指令");
  });

  it("restores focus to the opening control after candidate navigation", async () => {
    const opener = document.createElement("button");
    opener.textContent = "查看大图";
    document.body.appendChild(opener);
    opener.focus();
    const environments = [asset("environment-1", "environment"), asset("environment-2", "environment")];
    const wrapper = mount(AssetImageViewer, {
      props: {
        open: true,
        title: "当前环境参考",
        assets: environments,
        activeAssetId: environments[0].id,
        comparisons: [],
      },
      attachTo: document.body,
    });

    await wrapper.get('[aria-label="下一张候选"]').trigger("click");
    await wrapper.setProps({ activeAssetId: environments[1].id });
    await wrapper.get('[role="dialog"]').trigger("keydown", { key: "Escape" });
    await new Promise((resolve) => setTimeout(resolve));

    expect(document.activeElement).toBe(opener);
    wrapper.unmount();
    opener.remove();
  });
});
