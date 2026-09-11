import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProviderPrompt from "./ProviderPrompt.vue";

describe("ProviderPrompt", () => {
  const writeText = vi.fn();
  beforeEach(() => {
    writeText.mockReset().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  });

  it("displays and copies the recorded provider text verbatim, separately from summaries and review advice", async () => {
    const final = "  镜头保持低机位。\n\n避免项：不出现额外角色。\n参考图 1：只负责画风。\n";
    const wrapper = mount(ProviderPrompt, { props: {
      compiledProviderPrompt: final, prompt: "与最终文本不同的正文", negativePrompt: "单独存储的避免项",
      promptSections: [{ key: "shot", title: "摘要", content: "动作概要" }],
      warnings: [{ code: "relation_missing", message: "请人工核对接触关系" }],
    } });
    expect(wrapper.get(".compiled-provider-prompt").element.textContent).toBe(final);
    expect(wrapper.get('[aria-label="制作审查建议"]').text()).toContain("请人工核对接触关系");
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith(final);
    expect(wrapper.get('[role="status"]').text()).toBe("已复制最终模型指令。");
    wrapper.unmount();
  });

  it.each([undefined, null])("labels a historical snapshot with missing final text (%s) without reconstructing it", async missing => {
    const wrapper = mount(ProviderPrompt, { props: { historical: true, compiledProviderPrompt: missing, prompt: "旧正文", negativePrompt: "旧避免项" } });
    expect(wrapper.text()).toContain("旧任务未记录最终模型指令");
    expect(wrapper.find(".compiled-provider-prompt").exists()).toBe(false);
    expect(wrapper.get("button").text()).toBe("复制已记录正文（非完整指令）");
    await wrapper.get("button").trigger("click");
    expect(writeText).toHaveBeenCalledWith("旧正文");
    expect(writeText).not.toHaveBeenCalledWith("旧正文\n旧避免项");
    wrapper.unmount();
  });

  it("offers no copy action when the historical snapshot is entirely absent", () => {
    const wrapper = mount(ProviderPrompt, { props: { historical: true } });
    expect(wrapper.text()).toContain("系统不会用当前内容推测");
    expect(wrapper.find("button").exists()).toBe(false);
    wrapper.unmount();
  });

  it("does not invent readable advice from a warning code without a message", () => {
    const wrapper = mount(ProviderPrompt, { props: { warnings: [{ code: "MISSING_MESSAGE" }, { message: "  " }] } });
    expect(wrapper.find('[aria-label="制作审查建议"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("MISSING_MESSAGE");
    wrapper.unmount();
  });

  it("reports clipboard failures and clears stale copy feedback when the selected record changes", async () => {
    writeText.mockRejectedValueOnce(new Error("clipboard unavailable"));
    const wrapper = mount(ProviderPrompt, { props: { compiledProviderPrompt: "记录甲" } });
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="status"]').text()).toContain("复制失败");
    await wrapper.setProps({ compiledProviderPrompt: "记录乙" });
    expect(wrapper.find('[role="status"]').exists()).toBe(false);
    wrapper.unmount();
  });
});
