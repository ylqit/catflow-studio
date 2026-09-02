import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import RuntimeSettingsView from "./RuntimeSettingsView.vue";

const blockedReason = "需要先把本地上下文视频发布为受管 HTTPS URL";
const { checkObjectPublisher } = vi.hoisted(() => ({
  checkObjectPublisher: vi.fn(async () => ({
    configured: true,
    ready: true,
    backend: "s3",
    endpointHost: "tos-s3-cn-beijing.volces.com",
    publicHost: "test-vedio-ylq.tos-s3-cn-beijing.volces.com",
    bucket: "test-vedio-ylq",
    region: "cn-beijing",
    addressingStyle: "virtual",
    presignTtlSeconds: 7200,
    retentionDays: 7,
  })),
}));

vi.mock("../api/client", () => ({
  api: {
    runtime: vi.fn(async () => ({
      csrfToken: "csrf",
      baseUrl: "http://127.0.0.1:8877",
      localOnly: true,
      databaseReady: true,
      workerReady: true,
      ffmpegReady: true,
      ffprobeReady: true,
      objectPublisher: {
        configured: true,
        ready: false,
        backend: "s3",
        endpointHost: "tos-s3-cn-beijing.volces.com",
        publicHost: "test-vedio-ylq.tos-s3-cn-beijing.volces.com",
        bucket: "test-vedio-ylq",
        region: "cn-beijing",
        addressingStyle: "virtual",
        presignTtlSeconds: 7200,
        retentionDays: 7,
        error: { code: "not_checked", message: "尚未完成发布器往返预检" },
      },
      provider: {
        name: "ark",
        planningModel: "planning",
        imageModel: "image",
        videoModel: "video",
        diagnosticModel: "diagnostic",
        capabilityRevision: "ark-seedance-2.0-v1",
        paidCallsEnabled: true,
        apiKeyConfigured: true,
        segmentRepair: {
          supported: false,
          blockedReason,
          maximumImageReferences: 9,
          maximumVideoReferences: 1,
        },
      },
    })),
    checkObjectPublisher,
    currentCanon: vi.fn(async () => ({
      id: "11111111-1111-4111-8111-111111111111",
      version: 4,
      specVersion: 4,
      active: true,
      profileKey: "canon-v4",
      profileHash: "a".repeat(64),
      childAge: "6-7",
      childHeightCm: 120,
      childHeightRangeCm: [115, 125],
      childBodyProportion: "4.5-5-heads",
      childHair: "jaw-length-short",
      catPattern: "gray-white-tabby",
      fixedAssets: {},
      createdAt: "2026-01-01T00:00:00Z",
    })),
  },
}));

describe("RuntimeSettingsView", () => {
  it("shows why Ark segment repair is blocked instead of presenting a false Ready state", async () => {
    const wrapper = mount(RuntimeSettingsView, {
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("片段修复Blocked");
    expect(wrapper.text()).toContain(blockedReason);
  });

  it("runs the non-paid publisher roundtrip from the settings page and shows safe status", async () => {
    const wrapper = mount(RuntimeSettingsView, {
      global: { stubs: { RouterLink: { template: "<a><slot /></a>" } } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("test-vedio-ylq.tos-s3-cn-beijing.volces.com");
    await wrapper.get('[data-testid="check-object-publisher"]').trigger("click");
    await flushPromises();

    expect(checkObjectPublisher).toHaveBeenCalledOnce();
    expect(wrapper.get('[data-testid="object-publisher-status"]').text()).toContain("Ready");
    expect(wrapper.text()).not.toContain("X-Amz-Signature");
  });
});
