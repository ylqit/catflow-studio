import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ShotSpecDto, WorkspaceDto } from "../../api/types";
import StoryboardStep from "./StoryboardStep.vue";

const client = vi.hoisted(() => ({
  generateShotPlan: vi.fn(),
  createShotPlan: vi.fn(),
  shotPlans: vi.fn(),
  shotPlanGenerationAttempts: vi.fn(),
  recoverShotPlanGeneration: vi.fn(),
  materializeShotPlanGeneration: vi.fn(),
  activateShotPlan: vi.fn(),
  rejectShotPlan: vi.fn(),
}));
vi.mock("../../api/client", () => ({ api: client }));

const professionalShot: ShotSpecDto = {
  id: "shot-1", order: 1, durationSeconds: 12, durationFrames: 288,
  framing: "中景", cameraMovement: "缓慢跟随", childAction: "孩子擦干猫爪",
  catAction: "猫咪抬爪后向前走", environmentChange: "湿爪印减少", transition: "continuous",
  lens: { focalLengthEquivalent: "35mm", cameraHeight: "儿童腰部", cameraAngle: "轻微俯拍", perspectiveIntent: "保持亲密距离" },
  composition: { subjectPlacement: "人物左、猫咪右", foreground: "门槛", middleGround: "一人一猫", background: "暖色室内", screenDirection: "由右向左", eyeLine: "孩子看向猫爪" },
  childBlocking: { initialState: "蹲在门边", movementPath: "手持毛巾逐只擦拭", endState: "起身折好毛巾", microMotions: ["重新握紧毛巾"] },
  catBlocking: { initialState: "前爪潮湿", movementPath: "依次抬爪配合", endState: "走上干燥脚垫", microMotions: ["尾巴轻摆"] },
  physicalChange: { subject: "猫爪与地面", before: "潮湿且有水印", after: "擦干且水印减少" },
  continuity: { incoming: "猫咪刚进门", outgoing: "猫咪走向室内", sharedVisualElement: "软毛巾", finalFrame: "孩子折好毛巾，猫咪继续迈步" },
  lighting: { direction: "窗外侧逆光", softness: "柔和漫射", colorIntent: "雨天暖灰" },
  sound: { ambience: ["轻雨声"], objectEffects: ["毛巾摩擦"], movementEffects: ["猫爪轻落"], musicIntent: "轻柔木琴" },
  directorIntent: "让擦爪的照顾感通过动作而非对白呈现",
  generationRisks: [{ code: "paw_contact", message: "避免手与猫爪融合" }],
};

function workspace(activeShotPlan: WorkspaceDto["activeShotPlan"]): WorkspaceDto {
  return {
    eventCursor: 0,
    project: { id: "project-1", title: "雨天擦爪", theme: "雨天擦爪", targetDurationSeconds: 12, aspectRatio: "9:16", canonProfileId: "canon-1", createdAt: "2026-09-01T00:00:00Z", updatedAt: "2026-09-01T00:00:00Z" },
    steps: [], selectionHash: "a".repeat(64), selections: {},
    activeStory: {
      id: "story-1", projectId: "project-1", revision: 1, title: "雨天擦爪", body: "猫咪进门，孩子为它擦爪。",
      microEvent: { trigger: "湿爪印", childAction: "擦猫爪", catResponse: "抬爪配合", visibleChange: "水印减少", warmEnding: "一起走进室内" },
      targetDurationSeconds: 12, dialoguePolicy: "none", environmentIntent: "雨天门廊", active: true, createdAt: "2026-09-01T00:00:00Z",
    },
    activeShotPlan,
  };
}

function mountStoryboard(activeShotPlan: WorkspaceDto["activeShotPlan"]) {
  return mount(StoryboardStep, {
    props: {
      projectId: "project-1",
      workspace: workspace(activeShotPlan),
      runtime: { provider: { apiKeyConfigured: true, paidCallsEnabled: true } },
    },
    global: { stubs: { RouterLink: true } },
  });
}

describe("StoryboardStep", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    client.shotPlans.mockResolvedValue([]);
    client.shotPlanGenerationAttempts.mockResolvedValue([]);
  });

  it("settles the request key as soon as the server returns a durable job", async () => {
    let resolveJob!: (value: { id: string; status: string }) => void;
    client.generateShotPlan.mockReturnValue(new Promise((resolve) => { resolveJob = resolve; }));
    const wrapper = mountStoryboard(null);
    await flushPromises();
    const storageKey = "catflow:pending-idempotency:director:project-1";
    const fingerprint = `story-1:${"a".repeat(64)}:no-plan`;
    window.sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, key: "director-request-key" }));

    await wrapper.get('[data-testid="generate-director-plan"]').trigger("click");
    expect(window.sessionStorage.getItem(storageKey)).not.toBeNull();
    resolveJob({ id: "director-job-1", status: "queued" });
    await flushPromises();

    expect(client.generateShotPlan).toHaveBeenCalledWith("project-1", "director-request-key");
    expect(window.sessionStorage.getItem(storageKey)).toBeNull();
  });

  it("clears a stale request key after an input conflict without retrying automatically", async () => {
    const conflict = Object.assign(new Error("idempotency key already belongs to different input"), {
      status: 409,
      detail: {
        code: "idempotency_input_conflict",
        message: "当前生成输入已经变化，旧请求标识不能继续使用。",
        retryable: false,
      },
    });
    client.generateShotPlan
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce({ id: "director-job-new", status: "queued" });
    const wrapper = mountStoryboard(null);
    await flushPromises();
    const storageKey = "catflow:pending-idempotency:director:project-1";
    const fingerprint = `story-1:${"a".repeat(64)}:no-plan`;
    window.sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, key: "stale-director-key" }));

    await wrapper.get('[data-testid="generate-director-plan"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("生成输入已经更新，本次没有创建任务。请再次点击“重新生成分镜”");
    expect(client.generateShotPlan).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem(storageKey)).toBeNull();

    await wrapper.get('[data-testid="generate-director-plan"]').trigger("click");
    await flushPromises();

    expect(client.generateShotPlan).toHaveBeenCalledTimes(2);
    expect(client.generateShotPlan.mock.calls[1]?.[1]).not.toBe("stale-director-key");
  });

  it("reconciles a stale request key when the latest persisted job is terminal", async () => {
    const terminalWorkspace = workspace(null);
    terminalWorkspace.latestDirectorJob = {
      id: "director-job-failed", projectId: "project-1", kind: "plan_shots", status: "failed",
      inputHash: "f".repeat(64), frozenInput: { storyVersionId: "story-1" }, resultAssetIds: [],
      error: { code: "result_storage_failed", message: "invalid payload", retryable: false },
      createdAt: "2026-09-03T00:00:00Z", updatedAt: "2026-09-03T00:02:00Z",
    };
    const storageKey = "catflow:pending-idempotency:director:project-1";
    const fingerprint = `story-1:${"a".repeat(64)}:no-plan`;
    window.sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, key: "orphaned-director-key" }));

    mount(StoryboardStep, {
      props: {
        projectId: "project-1",
        workspace: terminalWorkspace,
        runtime: { provider: { apiKeyConfigured: true, paidCallsEnabled: true } },
      },
      global: { stubs: { RouterLink: true } },
    });
    await flushPromises();

    expect(window.sessionStorage.getItem(storageKey)).toBeNull();
  });

  it("keeps the request key when the request outcome is unknown", async () => {
    client.generateShotPlan.mockRejectedValue(new TypeError("Failed to fetch"));
    const wrapper = mountStoryboard(null);
    await flushPromises();
    const storageKey = "catflow:pending-idempotency:director:project-1";
    const fingerprint = `story-1:${"a".repeat(64)}:no-plan`;
    window.sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, key: "unknown-request-key" }));

    await wrapper.get('[data-testid="generate-director-plan"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("暂时无法确认任务是否已经创建，请先刷新生成记录，不要重复点击");
    expect(window.sessionStorage.getItem(storageKey)).not.toBeNull();
  });

  it("does not discard the request key for a different conflict", async () => {
    const conflict = Object.assign(new Error("a shot plan generation job is already running"), {
      status: 409,
      detail: "a shot plan generation job is already running",
    });
    client.generateShotPlan.mockRejectedValue(conflict);
    const wrapper = mountStoryboard(null);
    await flushPromises();
    const storageKey = "catflow:pending-idempotency:director:project-1";
    const fingerprint = `story-1:${"a".repeat(64)}:no-plan`;
    window.sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, key: "running-conflict-key" }));

    await wrapper.get('[data-testid="generate-director-plan"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("已有一条分镜任务正在处理，请等待当前任务完成");
    expect(window.sessionStorage.getItem(storageKey)).not.toBeNull();
  });

  it("submits only once while the first request is pending", async () => {
    let resolveJob!: (value: { id: string; status: string }) => void;
    client.generateShotPlan.mockReturnValue(new Promise((resolve) => { resolveJob = resolve; }));
    const wrapper = mountStoryboard(null);
    await flushPromises();

    const firstClick = wrapper.get('[data-testid="generate-director-plan"]').trigger("click");
    const secondClick = wrapper.get('[data-testid="generate-director-plan"]').trigger("click");
    await Promise.all([firstClick, secondClick]);

    expect(client.generateShotPlan).toHaveBeenCalledTimes(1);
    resolveJob({ id: "director-job-1", status: "queued" });
    await flushPromises();
  });

  it("does not turn a placeholder template into a formal shot plan", async () => {
    client.generateShotPlan.mockResolvedValue({ id: "director-job-1", status: "queued" });
    const wrapper = mountStoryboard(null);

    expect(wrapper.text()).not.toContain("保存新版本");
    await wrapper.get('[data-testid="generate-director-plan"]').trigger("click");
    await flushPromises();

    expect(client.generateShotPlan).toHaveBeenCalledWith("project-1", expect.any(String));
    expect(wrapper.text()).toContain("生成分镜");
    expect(wrapper.text()).toContain("本次会使用付费模型，完成后显示实际用量");
  });

  it("keeps core fields concise and exposes the professional director design", async () => {
    const plan = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      directorTreatment: { logline: "雨天门边的一次温柔照顾", theme: "日常照顾" },
      directorPromptRevision: "catflow-director-v1", directorModel: "planner", directorInputHash: "b".repeat(64),
      reviewStatus: "accepted" as const, active: true, outdated: false, createdAt: "2026-09-01T00:00:00Z",
    };
    const wrapper = mountStoryboard(plan);

    expect(wrapper.text()).toContain("景别");
    expect(wrapper.text()).toContain("人物动作");
    expect(wrapper.get('[data-testid="shot-child-summary"]').text()).toContain(
      "蹲在门边 → 手持毛巾逐只擦拭 → 起身折好毛巾",
    );
    expect(wrapper.get('[data-testid="shot-child-summary"]').find("input").exists()).toBe(false);
    expect(wrapper.get('[data-testid="shot-cat-summary"]').text()).toContain(
      "前爪潮湿 → 依次抬爪配合 → 走上干燥脚垫",
    );
    expect(wrapper.get('[data-testid="shot-change-summary"]').text()).toContain(
      "猫爪与地面 · 潮湿且有水印 → 擦干且水印减少",
    );
    expect(wrapper.get('[data-testid="shot-ending-summary"]').text()).toContain(
      "孩子折好毛巾，猫咪继续迈步",
    );
    const details = wrapper.get('[data-testid="professional-shot-details"]');
    expect(details.get("summary").text()).toBe("查看镜头细节");
    expect(details.attributes("open")).toBeUndefined();
    expect(details.text()).toContain("动作与状态");
    expect(details.text()).toContain("镜头画面");
    expect(details.text()).toContain("连续性与结尾");
    expect(details.text()).toContain("光线与声音");
    expect(details.text()).toContain("导演意图与风险");
    expect(details.findAll("input").some((input) => input.element.value === "35mm")).toBe(true);
    expect(details.findAll("textarea").some((input) => input.element.value === "蹲在门边")).toBe(true);
    expect(details.findAll("textarea").some((input) => input.element.value === "孩子折好毛巾，猫咪继续迈步")).toBe(true);
    expect(details.text()).toContain("轻雨声");
    expect(details.text()).toContain("避免手与猫爪融合");

    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    await wrapper.get('[data-testid="shot-child-summary"] button').trigger("click");
    await flushPromises();
    expect((details.element as HTMLDetailsElement).open).toBe(true);
    expect(focus).toHaveBeenCalled();
    focus.mockRestore();
  });

  it("saves compatibility summaries derived from the professional shot details", async () => {
    const plan = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [{
        ...professionalShot,
        childAction: "这份旧人物摘要不应成为保存权威",
        catAction: "这份旧猫咪摘要不应成为保存权威",
        environmentChange: "这份旧变化摘要不应成为保存权威",
      }], totalDurationSeconds: 12,
      directorTreatment: { logline: "雨天门边的一次温柔照顾", theme: "日常照顾" },
      directorPromptRevision: "catflow-director-v1", directorModel: "planner", directorInputHash: "b".repeat(64),
      reviewStatus: "accepted" as const, active: true, outdated: false, createdAt: "2026-09-01T00:00:00Z",
    };
    client.createShotPlan.mockResolvedValue({ ...plan, id: "plan-2", revision: 2 });
    const wrapper = mountStoryboard(plan);
    await flushPromises();

    await wrapper.get('[data-testid="shot-1-child-movement"]').setValue("抱住猫咪并逐只擦拭");
    expect(wrapper.get('[data-testid="shot-child-summary"]').text()).toContain(
      "蹲在门边 → 抱住猫咪并逐只擦拭 → 起身折好毛巾",
    );
    await wrapper.get(".head-actions .primary").trigger("click");
    await flushPromises();

    const command = client.createShotPlan.mock.calls[0]?.[1];
    expect(command.shots[0].childAction).toBe(
      "蹲在门边 → 抱住猫咪并逐只擦拭 → 起身折好毛巾",
    );
    expect(command.shots[0].catAction).toBe(
      "前爪潮湿 → 依次抬爪配合 → 走上干燥脚垫",
    );
    expect(command.shots[0].environmentChange).toBe(
      "猫爪与地面 · 潮湿且有水印 → 擦干且水印减少",
    );
  });

  it("lets the creator remove a stale micro motion before saving a new version", async () => {
    const plan = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {},
      shots: [{
        ...professionalShot,
        childBlocking: {
          ...professionalShot.childBlocking!,
          microMotions: ["重新握紧毛巾", "伸向下一样物品"],
        },
      }],
      totalDurationSeconds: 12,
      directorTreatment: { logline: "雨天门边的一次温柔照顾", theme: "日常照顾" },
      directorPromptRevision: "catflow-director-v1", directorModel: "planner", directorInputHash: "b".repeat(64),
      reviewStatus: "accepted" as const, active: true, outdated: false, createdAt: "2026-09-01T00:00:00Z",
    };
    client.createShotPlan.mockResolvedValue({ ...plan, id: "plan-2", revision: 2 });
    const wrapper = mountStoryboard(plan);
    await flushPromises();

    await wrapper.get('button[aria-label="移除人物微动作 2"]').trigger("click");
    await wrapper.get(".head-actions .primary").trigger("click");
    await flushPromises();

    const command = client.createShotPlan.mock.calls[0]?.[1];
    expect(command.shots[0].childBlocking.microMotions).toEqual(["重新握紧毛巾"]);
  });

  it("lets the creator remove a stale generation risk before saving a new version", async () => {
    const plan = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {},
      shots: [{
        ...professionalShot,
        generationRisks: [
          { code: "SINGLE_SHOT_TIMING", message: "原动作节拍过多" },
          { code: "HAND_SCALE", message: "保持儿童手部比例" },
        ],
      }],
      totalDurationSeconds: 12,
      directorTreatment: { logline: "雨天门边的一次温柔照顾", theme: "日常照顾" },
      directorPromptRevision: "catflow-director-v1", directorModel: "planner", directorInputHash: "b".repeat(64),
      reviewStatus: "accepted" as const, active: true, outdated: false, createdAt: "2026-09-01T00:00:00Z",
    };
    client.createShotPlan.mockResolvedValue({ ...plan, id: "plan-2", revision: 2 });
    const wrapper = mountStoryboard(plan);
    await flushPromises();

    await wrapper.get('button[aria-label="移除生成风险 SINGLE_SHOT_TIMING"]').trigger("click");
    await wrapper.get(".head-actions .primary").trigger("click");
    await flushPromises();

    const command = client.createShotPlan.mock.calls[0]?.[1];
    expect(command.shots[0].generationRisks).toEqual([
      { code: "HAND_SCALE", message: "保持儿童手部比例" },
    ]);
  });

  it("hydrates the formal plan when the SSE-backed workspace projection changes", async () => {
    const wrapper = mountStoryboard(null);
    expect(wrapper.text()).not.toContain("分镜设计");

    const plan = {
      id: "plan-after-worker", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      directorTreatment: { logline: "雨天门边的一次温柔照顾", theme: "日常照顾" },
      directorPromptRevision: "catflow-director-v1", directorModel: "planner", directorInputHash: "b".repeat(64),
      reviewStatus: "accepted" as const, active: true, outdated: false, createdAt: "2026-09-01T00:00:00Z",
    };
    await wrapper.setProps({ workspace: workspace(plan) });
    await flushPromises();

    expect(wrapper.text()).toContain("分镜设计");
    expect(wrapper.findAll('[data-testid="professional-shot-details"] input').some((input) => (input.element as HTMLInputElement).value === "35mm")).toBe(true);
  });

  it("keeps the accepted version visible while a generated candidate waits for review", async () => {
    const accepted = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      reviewStatus: "accepted" as const, active: true, outdated: false, createdAt: "2026-09-01T00:00:00Z",
    };
    const candidate = {
      ...accepted,
      id: "plan-2",
      revision: 2,
      shots: [{ ...professionalShot, cameraMovement: "缓慢推近" }],
      reviewStatus: "candidate" as const,
      producingJobId: "director-job-2",
      baseShotPlanVersionId: "plan-1",
      active: false,
      createdAt: "2026-09-03T00:00:00Z",
    };
    client.shotPlans.mockResolvedValue([candidate, accepted]);
    const wrapper = mountStoryboard(accepted);
    await flushPromises();

    expect(wrapper.text()).toContain("版本 1 · 当前使用");
    expect(wrapper.text()).toContain("版本 2 · 新生成 · 待确认");
    await wrapper.get('[data-testid="shot-plan-version-plan-2"]').trigger("click");
    expect(wrapper.text()).toContain("采用新版");
    await wrapper.get('[data-testid="compare-shot-plan"]').trigger("click");
    expect(wrapper.get('[data-testid="shot-plan-compare-drawer"]').text()).toContain("缓慢推近");
    expect(wrapper.get('[data-testid="shot-plan-compare-drawer"]').text()).toContain("缓慢跟随");
    expect(wrapper.get('[data-testid="shot-plan-compare-drawer"]').find("pre").exists()).toBe(false);
    client.activateShotPlan.mockResolvedValue({ ...candidate, reviewStatus: "accepted", active: true });
    await wrapper.get(".head-actions .primary").trigger("click");
    await flushPromises();
    expect(client.activateShotPlan).toHaveBeenCalledWith(
      "project-1",
      "plan-2",
      "plan-1",
      expect.any(String),
    );
  });

  it("shows honest running and incomplete states without inventing a new version", async () => {
    const accepted = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      reviewStatus: "accepted" as const, active: true, outdated: true, createdAt: "2026-09-01T00:00:00Z",
    };
    client.shotPlans.mockResolvedValue([accepted]);
    const runningWorkspace = workspace(accepted);
    runningWorkspace.latestDirectorJob = {
      id: "director-job-2", projectId: "project-1", kind: "plan_shots", status: "submitting",
      inputHash: "c".repeat(64), frozenInput: { storyVersionId: "story-1" }, resultAssetIds: [],
      createdAt: "2026-09-03T00:00:00Z", updatedAt: "2026-09-03T00:00:01Z",
    };
    const wrapper = mount(StoryboardStep, {
      props: { projectId: "project-1", workspace: runningWorkspace, runtime: { provider: { apiKeyConfigured: true, paidCallsEnabled: true } } },
      global: { stubs: { RouterLink: true } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("正在基于当前故事生成新分镜，版本 1 仍在使用");
    expect(wrapper.get('[data-testid="regenerate-director-plan"]').attributes("disabled")).toBeDefined();

    const failedWorkspace = workspace(accepted);
    failedWorkspace.latestDirectorJob = {
      ...runningWorkspace.latestDirectorJob,
      status: "failed",
      error: { code: "response_not_completed", message: "Ark response status is 'incomplete'", retryable: false, incompleteReason: "max_output_tokens" },
      updatedAt: "2026-09-03T00:00:05Z",
    };
    await wrapper.setProps({ workspace: failedWorkspace });
    await flushPromises();

    expect(wrapper.text()).toContain("模型没有完整写完分镜，本次没有生成新版本。当前仍保留版本 1");
    expect(wrapper.text()).not.toContain("版本 2");
    expect(wrapper.text()).toContain("版本 1 已因故事、角色或环境变化而过期");
  });

  it("distinguishes an offline queue from model processing", async () => {
    const accepted = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      reviewStatus: "accepted" as const, active: true, outdated: true, createdAt: "2026-09-01T00:00:00Z",
    };
    const queuedWorkspace = workspace(accepted);
    queuedWorkspace.latestDirectorJob = {
      id: "director-job-queued", projectId: "project-1", kind: "plan_shots", status: "queued",
      inputHash: "d".repeat(64), frozenInput: { storyVersionId: "story-1" }, resultAssetIds: [],
      createdAt: "2026-09-03T00:00:00Z", updatedAt: "2026-09-03T00:00:00Z",
    };
    const wrapper = mount(StoryboardStep, {
      props: {
        projectId: "project-1",
        workspace: queuedWorkspace,
        runtime: {
          worker: { ready: false, state: "restarting" },
          provider: { apiKeyConfigured: true, paidCallsEnabled: true },
        },
      },
      global: { stubs: { RouterLink: true } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("后台任务暂时离线，原任务已保存，尚未提交模型");
    expect(wrapper.text()).toContain("系统恢复后会继续同一任务");
    expect(wrapper.text()).toContain("排队时间");
    expect(wrapper.text()).not.toContain("正在基于当前故事生成新分镜");
    expect(wrapper.get('[data-testid="regenerate-director-plan"]').attributes("disabled")).toBeDefined();
  });

  it("explains historical director validation failures without rewriting the job", async () => {
    const accepted = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      reviewStatus: "accepted" as const, active: true, outdated: true, createdAt: "2026-09-01T00:00:00Z",
    };
    const failedWorkspace = workspace(accepted);
    failedWorkspace.latestDirectorJob = {
      id: "director-job-failed", projectId: "project-1", kind: "plan_shots", status: "failed",
      inputHash: "f".repeat(64), frozenInput: { storyVersionId: "story-1" }, resultAssetIds: [],
      error: {
        code: "result_storage_failed",
        message: "1 validation error for DirectorPlanPayload: shots list has at most 4 items",
        retryable: false,
      },
      createdAt: "2026-09-03T00:00:00Z", updatedAt: "2026-09-03T00:02:00Z",
    };
    const wrapper = mount(StoryboardStep, {
      props: {
        projectId: "project-1",
        workspace: failedWorkspace,
        runtime: {
          worker: { ready: true, state: "ready" },
          provider: { apiKeyConfigured: true, paidCallsEnabled: true },
        },
      },
      global: { stubs: { RouterLink: true } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("模型返回的分镜结构未通过校验，本次没有生成新版本。当前仍保留版本 1");
    expect(wrapper.text()).not.toContain("版本 2");
  });

  it("recovers a warning-only paid result without generating again", async () => {
    const accepted = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      reviewStatus: "accepted" as const, active: true, outdated: true, createdAt: "2026-09-01T00:00:00Z",
    };
    const candidate = {
      ...accepted,
      id: "plan-2",
      revision: 2,
      shots: [{
        ...professionalShot,
        sound: {
          ...professionalShot.sound!,
          objectEffects: ["水流声", "水壶轻碰声", "水滴声", "托盘摩擦声"],
        },
      }],
      reviewStatus: "candidate" as const,
      producingJobId: "director-job-recoverable",
      baseShotPlanVersionId: "plan-1",
      active: false,
      outdated: false,
      createdAt: "2026-09-04T00:00:00Z",
    };
    const recoverableAttempt = {
      jobId: "director-job-recoverable",
      status: "failed" as const,
      storyVersionId: "story-1",
      baseShotPlanVersionId: "plan-1",
      provider: "ark",
      model: "planning",
      createdAt: "2026-09-04T00:00:00Z",
      updatedAt: "2026-09-04T00:01:00Z",
      billingStatus: "usage_reported" as const,
      result: {
        disposition: "candidate_ready" as const,
        recoverable: true,
        draft: { targetDurationSeconds: 12, directorTreatment: {}, shots: [] },
        issues: [
          { code: "sound_detail_dense", severity: "warning" as const, path: "shots.0.sound.objectEffects", message: "该镜头的声音细节较多（4 条），已全部保留。" },
          { code: "unknown_provider_field", severity: "warning" as const, path: "shots.0.blocking_note", message: "模型附带了一项额外说明，已保存在生成记录中。" },
        ],
      },
      error: { code: "director_output_validation_failed", message: "legacy strict failure", retryable: false, submissionUnknown: false },
    };
    client.shotPlans.mockResolvedValueOnce([accepted]).mockResolvedValue([candidate, accepted]);
    client.shotPlanGenerationAttempts
      .mockResolvedValueOnce([recoverableAttempt])
      .mockResolvedValue([{ ...recoverableAttempt, resultShotPlanVersionId: "plan-2", result: { ...recoverableAttempt.result, resultShotPlanVersionId: "plan-2" } }]);
    client.recoverShotPlanGeneration.mockResolvedValue(candidate);
    const failedWorkspace = workspace(accepted);
    failedWorkspace.latestDirectorJob = {
      id: "director-job-recoverable", projectId: "project-1", kind: "plan_shots", status: "failed",
      inputHash: "f".repeat(64), frozenInput: { storyVersionId: "story-1" }, resultAssetIds: [],
      error: { code: "director_output_validation_failed", message: "legacy strict failure", retryable: false },
      createdAt: "2026-09-04T00:00:00Z", updatedAt: "2026-09-04T00:01:00Z",
    };
    const wrapper = mount(StoryboardStep, {
      props: {
        projectId: "project-1",
        workspace: failedWorkspace,
        runtime: { worker: { ready: true, state: "ready" }, provider: { apiKeyConfigured: true, paidCallsEnabled: true } },
      },
      global: { stubs: { RouterLink: true } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("新版分镜已经返回，包含 2 项制作提示");
    expect(wrapper.text()).toContain("声音细节较多（4 条），已全部保留");
    expect(wrapper.text()).not.toContain("本次没有生成新版本");
    await wrapper.get('[data-testid="recover-director-result"]').trigger("click");
    await flushPromises();

    expect(client.recoverShotPlanGeneration).toHaveBeenCalledWith(
      "project-1",
      "director-job-recoverable",
      expect.any(String),
    );
    expect(client.generateShotPlan).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("版本 2 · 新生成 · 待确认");
    expect(wrapper.text()).toContain("已有结果已恢复");
    await wrapper.get('[data-testid="professional-shot-details"] summary').trigger("click");
    expect(wrapper.text()).toContain("水流声、水壶轻碰声、水滴声、托盘摩擦声");
  });

  it("materializes a corrected blocking draft without calling the model again", async () => {
    const accepted = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      reviewStatus: "accepted" as const, active: true, outdated: true, createdAt: "2026-09-01T00:00:00Z",
    };
    const correctedCandidate = {
      ...accepted,
      id: "plan-2",
      revision: 2,
      reviewStatus: "candidate" as const,
      producingJobId: "director-job-needs-input",
      baseShotPlanVersionId: "plan-1",
      active: false,
      outdated: false,
      createdAt: "2026-09-04T00:00:00Z",
    };
    const needsInputAttempt = {
      jobId: "director-job-needs-input",
      status: "succeeded" as const,
      storyVersionId: "story-1",
      baseShotPlanVersionId: "plan-1",
      provider: "ark",
      model: "planning",
      createdAt: "2026-09-04T00:00:00Z",
      updatedAt: "2026-09-04T00:01:00Z",
      billingStatus: "usage_reported" as const,
      result: {
        disposition: "needs_input" as const,
        recoverable: true,
        draft: { targetDurationSeconds: 12, directorTreatment: {}, shots: [{ id: "shot-1" }] },
        issues: [
          { code: "missing_required_field", severity: "blocking" as const, path: "shots.0.catBlocking.endState", message: "请补充猫咪动作的结束状态。" },
        ],
      },
    };
    client.shotPlans.mockResolvedValueOnce([accepted]).mockResolvedValue([correctedCandidate, accepted]);
    client.shotPlanGenerationAttempts
      .mockResolvedValueOnce([needsInputAttempt])
      .mockResolvedValue([{ ...needsInputAttempt, resultShotPlanVersionId: "plan-2" }]);
    client.materializeShotPlanGeneration.mockResolvedValue(correctedCandidate);
    const needsInputWorkspace = workspace(accepted);
    needsInputWorkspace.latestDirectorJob = {
      id: "director-job-needs-input", projectId: "project-1", kind: "plan_shots", status: "succeeded",
      inputHash: "f".repeat(64), frozenInput: { storyVersionId: "story-1" }, resultAssetIds: [],
      createdAt: "2026-09-04T00:00:00Z", updatedAt: "2026-09-04T00:01:00Z",
    };
    const wrapper = mount(StoryboardStep, {
      props: {
        projectId: "project-1",
        workspace: needsInputWorkspace,
        runtime: { worker: { ready: true, state: "ready" }, provider: { apiKeyConfigured: true, paidCallsEnabled: true } },
      },
      global: { stubs: { RouterLink: true } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("分镜已经返回，还需要补充 1 项重要内容");
    expect(wrapper.text()).toContain("本次结果已经保存，不需要重新调用模型");
    const correctedPayload = { targetDurationSeconds: 12, directorTreatment: {}, shots: [professionalShot] };
    await wrapper.get('textarea[aria-label="待补充的分镜草稿"]').setValue(JSON.stringify(correctedPayload));
    await wrapper.get('[data-testid="materialize-director-result"]').trigger("click");
    await flushPromises();

    expect(client.materializeShotPlanGeneration).toHaveBeenCalledWith(
      "project-1",
      "director-job-needs-input",
      correctedPayload,
      expect.any(String),
    );
    expect(client.generateShotPlan).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("版本 2 · 新生成 · 待确认");
  });

  it("shows a ready queued job as waiting for worker pickup", async () => {
    const accepted = {
      id: "plan-1", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      reviewStatus: "accepted" as const, active: true, outdated: false, createdAt: "2026-09-01T00:00:00Z",
    };
    const queuedWorkspace = workspace(accepted);
    queuedWorkspace.latestDirectorJob = {
      id: "director-job-queued", projectId: "project-1", kind: "plan_shots", status: "queued",
      inputHash: "e".repeat(64), frozenInput: { storyVersionId: "story-1" }, resultAssetIds: [],
      createdAt: "2026-09-03T00:00:00Z", updatedAt: "2026-09-03T00:00:00Z",
    };
    const wrapper = mount(StoryboardStep, {
      props: {
        projectId: "project-1",
        workspace: queuedWorkspace,
        runtime: {
          worker: { ready: true, state: "ready" },
          provider: { apiKeyConfigured: true, paidCallsEnabled: true },
        },
      },
      global: { stubs: { RouterLink: true } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("等待后台任务领取");
    expect(wrapper.text()).toContain("排队时间");
    expect(wrapper.text()).not.toContain("正在基于当前故事生成新分镜");
  });
});
