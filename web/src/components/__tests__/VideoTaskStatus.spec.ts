import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import type { PersistentTaskDto } from "../../api/types";
import VideoTaskStatus from "../director/VideoTaskStatus.vue";

const calls = vi.hoisted(() => ({ cancel: vi.fn(), confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("../../tasks/taskCenter", () => ({
  cancelPersistentTask: calls.cancel,
  useTaskCenter: () => ({ cancellingStepIds: ref<string[]>([]) }),
}));
vi.mock("element-plus", async (importOriginal) => ({
  ...(await importOriginal<typeof import("element-plus")>()),
  ElMessageBox: { confirm: calls.confirm },
  ElMessage: { success: calls.success, error: calls.error, warning: calls.warning },
}));

enableAutoUnmount(afterEach);

function task(mode: "local_before_provider" | "provider_queued"): PersistentTaskDto {
  const queued = mode === "provider_queued";
  return {
    stepId: "task-1", projectId: "project-1", sceneId: "scene-1", shotId: "shot-1", kind: "generate_video",
    status: queued ? "running" : "queued", attempt: 1, operationKey: "video:shot", provider: "ark",
    providerTaskId: queued ? "provider-1" : null, model: "seedance", inputSnapshot: {},
    cancellation: {
      allowed: true, mode, label: queued ? "取消 Provider 排队任务" : "取消，尚未提交 Provider",
      disabledReason: null, providerStatus: queued ? "queued" : "not_submitted", costMayAlreadyApply: queued,
    },
  };
}

describe("VideoTaskStatus", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.confirm.mockResolvedValue("confirm");
    calls.cancel.mockImplementation(async (value) => ({ ...value, status: "cancelled", cancellation: { ...value.cancellation, allowed: false, providerStatus: "cancelled" } }));
  });

  it("explains and executes a zero-Provider local cancellation through the shared atomic boundary", async () => {
    const input = task("local_before_provider");
    const wrapper = mount(VideoTaskStatus, { props: { tasks: [input] } });
    await wrapper.get("article>button").trigger("click");
    await flushPromises();
    expect(calls.confirm).toHaveBeenCalledWith(expect.stringContaining("Provider 调用为 0"), "取消，尚未提交 Provider", expect.any(Object));
    expect(calls.cancel).toHaveBeenCalledWith(input, "视频生成工作区人工取消");
    expect(wrapper.emitted("changed")?.[0]?.[0]).toMatchObject({ status: "cancelled" });
  });

  it("warns that cost may apply before cancelling a queued Provider task", async () => {
    const input = task("provider_queued");
    const wrapper = mount(VideoTaskStatus, { props: { tasks: [input] } });
    expect(wrapper.text()).toContain("可能已经产生");
    await wrapper.get("article>button").trigger("click");
    await flushPromises();
    expect(calls.confirm).toHaveBeenCalledWith(expect.stringContaining("费用是否产生以 Provider 账单为准"), "取消 Provider 排队任务", expect.any(Object));
    expect(calls.cancel).toHaveBeenCalledTimes(1);
  });
});
