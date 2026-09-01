import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({
  taskCenter: vi.fn(),
  taskCenterEventsUrl: vi.fn(),
  resumeStep: vi.fn(),
  recoverPersistentTask: vi.fn(),
  cancelPersistentTask: vi.fn(),
  runRecipeCharacterDesign: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: calls }));

describe("global task center", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("keeps a persistent running provider step visible without resubmitting it", async () => {
    calls.taskCenter.mockResolvedValue({ runtimeJobs: [], persistentTasks: [{
      stepId: "step-1",
      projectId: "project-1",
      sceneId: "scene-1",
      shotId: "shot-1",
      kind: "video",
      status: "running",
      attempt: 2,
      operationKey: "video:shot",
      provider: "fake",
      providerTaskId: "provider-1",
      model: "fake-seedance",
      inputSnapshot: {},
      error: null,
      createdAt: "2026-08-14T01:00:00Z",
    }] });
    calls.resumeStep.mockResolvedValue({ jobId: "resume-job-1" });
    const center = await import("../taskCenter");

    await center.refreshTaskCenter();

    const item = center.useTaskCenter().items.value[0];
    expect(item.stepId).toBe("step-1");
    expect(item.status).toBe("running");
    expect(item.providerTaskId).toBe("provider-1");
    expect(calls.resumeStep).not.toHaveBeenCalled();
  });

  it("labels the non-recipe story strategy as one candidate batch without automatic review", async () => {
    const center = await import("../taskCenter");

    expect(center.taskKindLabel("story_strategy")).toBe("故事候选批次生成");
  });

  it("registers a submitted job immediately without waiting for completion", async () => {
    calls.taskCenter.mockResolvedValue({ runtimeJobs: [{
      jobId: "job-1",
      kind: "generate_video",
      status: "running",
      context: { projectId: "project-1", shotId: "shot-1", operationKey: "video:shot" },
      result: null,
      error: null,
    }], persistentTasks: [] });
    const center = await import("../taskCenter");

    center.registerTask("job-1", {
      kind: "generate_video",
      label: "视频片段",
      projectId: "project-1",
      shotId: "shot-1",
      operationKey: "video:shot",
    });

    expect(center.useTaskCenter().items.value[0].status).toBe("queued");
    await center.refreshTaskCenter();
    expect(center.useTaskCenter().items.value[0].status).toBe("running");
  });

  it("recovers the same persistent task without starting a new character-design run", async () => {
    calls.recoverPersistentTask.mockResolvedValue({
      stepId: "character-parent-1",
      projectId: "project-1",
      kind: "director",
      status: "queued",
      attempt: 2,
      operationKey: "recipe:character_design",
      recipeInstanceId: "recipe-1",
      businessObjectId: "character-revision-1",
      inputSnapshot: {
        revision: 1,
        canonProfileId: "canon-v3-healing-child-cat-line-texture",
      },
      error: null,
      recovery: null,
      updatedAt: "2026-08-26T02:00:00Z",
    });
    const center = await import("../taskCenter");

    const recovered = await center.recoverPersistentTask({
      stepId: "character-parent-1",
      recovery: {
        allowed: true,
        mode: "resume_pre_provider",
        label: "从失败步骤继续",
      },
    });

    expect(calls.recoverPersistentTask).toHaveBeenCalledTimes(1);
    expect(calls.recoverPersistentTask).toHaveBeenCalledWith("character-parent-1");
    expect(calls.runRecipeCharacterDesign).not.toHaveBeenCalled();
    expect(recovered.stepId).toBe("character-parent-1");
    expect(recovered.attempt).toBe(2);
    const stored = center.useTaskCenter().items.value.find((item) => item.stepId === "character-parent-1");
    expect(stored?.status).toBe("queued");
    expect(stored?.businessObjectId).toBe("character-revision-1");
  });

  it("rejects a duplicate recovery while the original request is still pending", async () => {
    let resolveRecovery!: (value: Record<string, unknown>) => void;
    calls.recoverPersistentTask.mockImplementation(() => new Promise((resolve) => {
      resolveRecovery = resolve;
    }));
    const center = await import("../taskCenter");
    const task = {
      key: "step:character-parent-2",
      stepId: "character-parent-2",
      kind: "director",
      label: "角色设计",
      status: "failed" as const,
      updatedAt: "2026-08-26T01:00:00Z",
      source: "workflow" as const,
      recovery: {
        allowed: true,
        mode: "resume_pre_provider" as const,
        label: "从失败步骤继续",
      },
    };

    const first = center.recoverPersistentTask(task);
    await expect(center.recoverPersistentTask(task)).rejects.toThrow("恢复请求正在提交");
    expect(calls.recoverPersistentTask).toHaveBeenCalledTimes(1);
    resolveRecovery({
      stepId: "character-parent-2",
      projectId: "project-1",
      kind: "director",
      status: "queued",
      attempt: 2,
      operationKey: "recipe:character_design",
      inputSnapshot: {},
    });
    await first;
  });

  it("cancels an unsubmitted task with an expected-state snapshot", async () => {
    calls.cancelPersistentTask.mockResolvedValue({
      stepId: "video-local-1",
      projectId: "project-1",
      kind: "video",
      status: "cancelled",
      attempt: 1,
      operationKey: "media:video:batch:1:candidate:1",
      providerTaskId: null,
      inputSnapshot: {},
      progress: {
        providerStatus: "not_submitted",
        message: "已在提交 Provider 前取消，Provider 调用 0 次",
      },
      cancellation: {
        allowed: false,
        mode: "unavailable",
        label: "当前不可取消",
        disabledReason: "任务已经取消",
        providerStatus: "cancelled",
        costMayAlreadyApply: false,
      },
    });
    const center = await import("../taskCenter");

    const result = await center.cancelPersistentTask({
      stepId: "video-local-1",
      status: "queued",
      providerTaskId: null,
      cancellation: {
        allowed: true,
        mode: "local_before_provider",
        label: "取消，尚未提交 Provider",
        providerStatus: "not_submitted",
        costMayAlreadyApply: false,
      },
    }, "Web 任务中心人工取消");

    expect(calls.cancelPersistentTask).toHaveBeenCalledWith("video-local-1", {
      expectedStatus: "queued",
      expectedProviderTaskId: null,
      reason: "Web 任务中心人工取消",
    });
    expect(result.status).toBe("cancelled");
    const stored = center.useTaskCenter().items.value.find(
      (item) => item.stepId === "video-local-1",
    );
    expect(stored?.status).toBe("cancelled");
    expect(stored?.progress?.providerStatus).toBe("not_submitted");
  });

  it("does not issue a second cancellation while the first is unresolved", async () => {
    let resolveCancellation!: (value: Record<string, unknown>) => void;
    calls.cancelPersistentTask.mockImplementation(() => new Promise((resolve) => {
      resolveCancellation = resolve;
    }));
    const center = await import("../taskCenter");
    const task = {
      stepId: "provider-queued-1",
      status: "queued" as const,
      providerTaskId: "provider-task-1",
      cancellation: {
        allowed: true,
        mode: "provider_queued" as const,
        label: "取消 Provider 排队任务",
        providerStatus: "queued" as const,
        costMayAlreadyApply: true,
      },
    };

    const first = center.cancelPersistentTask(task);
    await expect(center.cancelPersistentTask(task)).rejects.toThrow("取消请求正在提交");
    expect(calls.cancelPersistentTask).toHaveBeenCalledTimes(1);
    resolveCancellation({
      stepId: "provider-queued-1",
      projectId: "project-1",
      kind: "video",
      status: "cancelled",
      attempt: 1,
      operationKey: "video:shot",
      inputSnapshot: {},
    });
    await first;
  });

  it("does not treat a completed sequence business status as a running task", async () => {
    calls.taskCenter.mockResolvedValue({ runtimeJobs: [{
      jobId: "sequence-job-1",
      kind: "build_sequence",
      status: "succeeded",
      context: { projectId: "project-1", operationKey: "sequence:build" },
      result: { id: "sequence-1", status: "content_review" },
      error: null,
    }], persistentTasks: [] });
    const center = await import("../taskCenter");

    center.registerTask("sequence-job-1", {
      kind: "build_sequence",
      label: "本地成片合成",
      projectId: "project-1",
      operationKey: "sequence:build",
    });
    await center.refreshTaskCenter();

    expect(center.useTaskCenter().items.value[0].status).toBe("succeeded");
  });

  it("applies durable SSE progress once and refreshes the affected project projection", async () => {
    class FakeEventSource {
      static latest: FakeEventSource;
      readonly listeners = new Map<string, (event: MessageEvent<string>) => void>();
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      closed = false;

      constructor(readonly url: string) {
        FakeEventSource.latest = this;
      }

      addEventListener(type: string, listener: EventListener) {
        this.listeners.set(type, listener as (event: MessageEvent<string>) => void);
      }

      emit(type: string, sequence: number, data: Record<string, unknown>) {
        this.listeners.get(type)?.(new MessageEvent(type, {
          data: JSON.stringify(data),
          lastEventId: String(sequence),
        }));
      }

      close() {
        this.closed = true;
      }
    }

    vi.stubGlobal("EventSource", FakeEventSource);
    calls.taskCenter.mockResolvedValue({ runtimeJobs: [], persistentTasks: [] });
    calls.taskCenterEventsUrl.mockReturnValue("/api/v1/task-center/events?afterEventId=0");
    const center = await import("../taskCenter");

    center.startTaskCenter();
    await vi.waitFor(() => expect(calls.taskCenter).toHaveBeenCalled());
    FakeEventSource.latest.onopen?.();
    FakeEventSource.latest.emit("task_queued", 41, {
      stepId: "step-sse",
      projectId: "project-sse",
      operationKey: "recipe:story",
      kind: "director",
      status: "queued",
    });
    FakeEventSource.latest.emit("task_progress", 42, {
      stepId: "step-sse",
      projectId: "project-sse",
      operationKey: "recipe:story",
      kind: "director",
      status: "running",
      progress: { currentStep: 1, totalSteps: 3, percent: 33, message: "生成候选" },
    });
    FakeEventSource.latest.emit("task_failed", 42, {
      stepId: "step-sse",
      projectId: "project-sse",
      status: "failed",
    });

    const task = center.useTaskCenter().items.value[0];
    expect(task.status).toBe("running");
    expect(task.progress?.percent).toBe(33);
    expect(window.localStorage.getItem("cvg.v5.task-event-cursor")).toBe("42");

    FakeEventSource.latest.emit("canvas_projection_changed", 43, {
      projectId: "project-sse",
      operationKey: "recipe:story",
    });
    expect(center.useTaskCenter().projectSignals.value["project-sse"]?.revision).toBe(1);
    expect(window.localStorage.getItem("cvg.v5.task-event-cursor")).toBe("43");

    center.stopTaskCenter();
    expect(FakeEventSource.latest.closed).toBe(true);
    vi.unstubAllGlobals();
  });
});
