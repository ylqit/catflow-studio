import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FirstValidationView from "./FirstValidationView.vue";

const canonHashes = ["1".repeat(64), "2".repeat(64), "3".repeat(64), "4".repeat(64)];
let projects: Array<Record<string, unknown>> = [];
const preview = {
  manifestHash: "a".repeat(64),
  topics: ["雨天擦爪", "浇花", "寻找滚落线团"],
  durationSeconds: 12,
  resolution: "480p",
  aspectRatio: "9:16",
  targetBudgetCny: 50,
  callLimits: { plan_story: 3, generate_image: 1, diagnose_image: 1, generate_video: 3, diagnose_video: 1, regenerate_video_segment: 1 },
  totalCallLimit: 10,
  maximumVideoCalls: 4,
  provider: "ark",
  models: { planning: "planning", image: "image", diagnostic: "diagnostic", video: "video" },
  capabilityRevision: "ark-seedance-2.0-v1",
  costEstimateStatus: "unmetered_paid",
  authorizationReady: true,
  blockingReasons: [] as string[],
  repair: {
    topic: "雨天擦爪",
    issueRange: { startFrame: 96, endFrame: 192 },
    prompt: "孩子蹲下，用软毛巾逐只擦干猫爪；猫咪自然抬爪配合，湿爪和地面水印明显减少。",
  },
  canon: {
    profileId: "11111111-1111-4111-8111-111111111111",
    version: 6,
    profileHash: "b".repeat(64),
    childAge: "6-7",
    childHeightCm: 120,
    references: ["episode_child", "episode_cat", "pair_scale", "style_board"].map((role, index) => ({
      role,
      assetId: `${index + 1}1111111-1111-4111-8111-111111111111`,
      sha256: canonHashes[index],
    })),
  },
};

vi.mock("../api/client", () => ({
  api: {
    previewValidationRun: vi.fn(async () => preview),
    currentValidationRun: vi.fn(async () => null),
    projects: vi.fn(async () => projects),
    authorizeValidationRun: vi.fn(),
    pauseValidationRun: vi.fn(),
  },
}));

describe("FirstValidationView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    projects = [];
    preview.authorizationReady = true;
    preview.blockingReasons = [];
  });

  it("shows the exact Canon authority frozen into the paid manifest", async () => {
    const wrapper = mount(FirstValidationView, {
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("6–7 岁 · 120 cm");
    expect(wrapper.text()).toContain("Revision 6");
    expect(wrapper.text()).toContain("b".repeat(64));
    for (const hash of canonHashes) expect(wrapper.text()).toContain(hash);
  });

  it("never links a same-topic project that belongs to an older Canon", async () => {
    projects = [{
      id: "old-project",
      title: "雨天擦爪",
      theme: "雨天擦爪",
      targetDurationSeconds: 12,
      aspectRatio: "9:16",
      canonProfileId: "old-canon-profile",
      createdAt: "2026-01-01T00:00:00Z",
      updatedAt: "2026-01-01T00:00:00Z",
    }];
    const wrapper = mount(FirstValidationView, {
      global: { stubs: { RouterLink: { props: ["to"], template: "<a><slot /></a>" } } },
    });
    await flushPromises();

    expect(wrapper.text()).not.toContain("继续普通五步流程");
    expect(wrapper.text()).toContain("去新建短片");
  });

  it("shows the Ark reference-publication blocker and disables paid authorization", async () => {
    preview.authorizationReady = false;
    preview.blockingReasons = ["需要受管 HTTPS 视频发布器"];
    const wrapper = mount(FirstValidationView, {
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("付费授权已阻断：需要受管 HTTPS 视频发布器");
    expect(wrapper.get("button.authorize").attributes("disabled")).toBeDefined();
  });
});
