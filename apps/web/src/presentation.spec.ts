import { describe, expect, it } from "vitest";

import { billingPresentation, errorPresentation, jobPresentation, paidModelBlockedReason } from "./presentation";

describe("creator-facing presentation", () => {
  it.each([
    ["queued", "等待生成"],
    ["submitting", "正在准备"],
    ["submitted", "已提交"],
    ["polling", "正在生成"],
    ["storing", "正在保存"],
    ["succeeded", "已完成"],
    ["failed", "生成失败"],
    ["submission_unknown", "提交状态待确认"],
    ["cancel_requested", "正在取消"],
    ["cancelled", "已取消"],
  ] as const)("presents %s as %s", (status, label) => {
    expect(jobPresentation(status).label).toBe(label);
  });

  it("keeps actionable recovery copy out of raw provider language", () => {
    const presentation = jobPresentation("submission_unknown");

    expect(presentation.description).toContain("确认");
    expect(presentation.description).not.toContain("Provider");
    expect(presentation.tone).toBe("warn");
  });

  it("does not represent an unpriced paid call as zero", () => {
    expect(billingPresentation("unpriced", null)).toEqual({
      label: "费用待核价",
      detail: "任务已记录实际用量，待补充对应费率。",
    });
  });

  it("keeps pending billing provider-neutral", () => {
    expect(billingPresentation(undefined, null, "ark")).toEqual({
      label: "费用计算中",
      detail: "任务完成后会根据实际用量更新费用。",
    });
  });

  it("explains why a real model submission is unavailable", () => {
    expect(paidModelBlockedReason({ provider: { apiKeyConfigured: false, paidCallsEnabled: true } })).toContain("密钥");
    expect(paidModelBlockedReason({ provider: { apiKeyConfigured: true, paidCallsEnabled: false } })).toContain("已关闭");
    expect(paidModelBlockedReason({ provider: { apiKeyConfigured: true, paidCallsEnabled: true } })).toBe("");
  });

  it("turns stale production input into a creator action", () => {
    expect(errorPresentation("shot plan asset selection is outdated", "生成预览失败")).toEqual({
      message: "角色或环境已经更新，请重新生成分镜后再生成视频。",
      technicalMessage: "shot plan asset selection is outdated",
    });
  });

  it("keeps an unknown failure visible without inventing a cause", () => {
    expect(errorPresentation("unexpected response", "生成预览失败")).toEqual({
      message: "生成预览失败，请查看技术详情后重试。",
      technicalMessage: "unexpected response",
    });
  });
});
