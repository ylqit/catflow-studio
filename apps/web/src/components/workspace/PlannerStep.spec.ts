import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PlannerStep from "./PlannerStep.vue";

const client = vi.hoisted(() => ({
  planner: vi.fn(),
  runtime: vi.fn(),
  plannerMessage: vi.fn(),
  adoptProposal: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: client }));

describe("PlannerStep", () => {
  const runtime = { provider: { apiKeyConfigured: true, paidCallsEnabled: true } };
  beforeEach(() => {
    client.planner.mockResolvedValue({
      sessionId: "session-1",
      projectId: "project-1",
      contextRevision: 1,
      messages: [],
      proposals: [],
    });
    client.runtime.mockResolvedValue({
      provider: {
        name: "ark",
        planningModel: "doubao-seed-2-1-pro-260628",
      },
    });
    client.plannerMessage.mockResolvedValue({ id: "job-1", status: "queued" });
  });

  it("submits the paid Ark planning job directly without a validation quota prompt", async () => {
    const wrapper = mount(PlannerStep, { props: { projectId: "project-1", runtime } });
    await flushPromises();

    await wrapper.get("textarea").setValue("雨天擦爪");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(client.plannerMessage).toHaveBeenCalledWith("project-1", {
      text: "雨天擦爪",
      expectedContextRevision: 1,
      idempotencyKey: expect.any(String),
    });
    expect(wrapper.text()).toContain("本次会使用付费模型，完成后显示实际用量");
    expect(wrapper.text()).toContain("故事灵感");
    expect(wrapper.text()).toContain("故事候选");
    expect(wrapper.text()).not.toContain("规划付费确认");
    expect(wrapper.text()).not.toContain("plan_story");
    expect(wrapper.text()).not.toContain("额度");
  });

  it("shows reported usage without inventing a zero cost", async () => {
    client.planner.mockResolvedValue({
      sessionId: "session-1",
      projectId: "project-1",
      contextRevision: 1,
      messages: [],
      proposals: [],
      latestJob: {
        id: "job-1",
        status: "succeeded",
        provider: "ark",
        model: "doubao-seed-2-1-pro-260628",
        actualUsage: { inputTokens: 321, outputTokens: 87, totalTokens: 408 },
        billingStatus: "unpriced",
        createdAt: "2026-09-01T00:00:00Z",
        updatedAt: "2026-09-01T00:00:01Z",
      },
    });

    const wrapper = mount(PlannerStep, { props: { projectId: "project-1", runtime } });
    await flushPromises();

    expect(wrapper.get('[data-testid="planner-job-summary"]').text()).toContain("已完成");
    expect(wrapper.get('[data-testid="planner-job-summary"]').text()).not.toContain("job-1");
    expect(wrapper.get('[data-testid="planner-job-details"]').text()).toContain("inputTokens");
    expect(wrapper.get('[data-testid="planner-job-details"]').text()).toContain("321");
    expect(wrapper.get('[data-testid="planner-job-details"]').text()).toContain("费用待核价");
    expect(wrapper.text()).not.toContain("¥0");
  });

  it("keeps completed conversation history collapsed once a story candidate exists", async () => {
    client.planner.mockResolvedValue({
      sessionId: "session-1",
      projectId: "project-1",
      contextRevision: 1,
      messages: [
        { id: "message-1", role: "user", content: "雨天擦爪", ordinal: 1, createdAt: "2026-09-01T00:00:00Z" },
        { id: "message-2", role: "assistant", content: "旧版重复回复", ordinal: 2, createdAt: "2026-09-01T00:00:01Z" },
      ],
      proposals: [{
        id: "proposal-1", projectId: "project-1", status: "adopted", title: "擦干小猫爪",
        summary: "门边的湿爪被逐只擦干。", body: "孩子替猫咪擦干爪子。",
        microEvent: { trigger: "猫咪留下湿爪印", childAction: "用毛巾擦爪", catResponse: "逐只抬爪", visibleChange: "水印减少", warmEnding: "猫咪走进屋内" },
        targetDurationSeconds: 12, dialoguePolicy: "none", environmentIntent: "雨天门廊", contextHash: "hash", warnings: [],
      }],
    });

    const wrapper = mount(PlannerStep, { props: { projectId: "project-1", runtime } });
    await flushPromises();

    const history = wrapper.get('[data-testid="planner-conversation-history"]');
    expect(history.attributes("open")).toBeUndefined();
    expect(history.get("summary").text()).toContain("查看历史对话");
    expect(wrapper.text()).toContain("擦干小猫爪");
  });

  it("does not submit when Ark credentials are unavailable", async () => {
    const wrapper = mount(PlannerStep, {
      props: {
        projectId: "project-1",
        runtime: { provider: { apiKeyConfigured: false, paidCallsEnabled: true } },
      },
    });
    await flushPromises();
    await wrapper.get("textarea").setValue("雨天擦爪");

    expect(wrapper.get("button.primary").attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("尚未配置模型服务密钥");
  });
});
