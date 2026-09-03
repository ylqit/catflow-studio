import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import type { ShotSpecDto, WorkspaceDto } from "../../api/types";
import StoryboardStep from "./StoryboardStep.vue";

const client = vi.hoisted(() => ({ generateShotPlan: vi.fn(), createShotPlan: vi.fn() }));
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
      active: true, outdated: false, createdAt: "2026-09-01T00:00:00Z",
    };
    const wrapper = mountStoryboard(plan);

    expect(wrapper.text()).toContain("景别");
    expect(wrapper.text()).toContain("人物动作");
    const details = wrapper.get('[data-testid="professional-shot-details"]');
    expect(details.get("summary").text()).toBe("查看镜头细节");
    expect(details.attributes("open")).toBeUndefined();
    expect(details.findAll("input").some((input) => input.element.value === "35mm")).toBe(true);
    expect(details.findAll("textarea").some((input) => input.element.value === "蹲在门边")).toBe(true);
    expect(details.findAll("textarea").some((input) => input.element.value === "孩子折好毛巾，猫咪继续迈步")).toBe(true);
    expect(details.text()).toContain("轻雨声");
    expect(details.text()).toContain("避免手与猫爪融合");
  });

  it("hydrates the formal plan when the SSE-backed workspace projection changes", async () => {
    const wrapper = mountStoryboard(null);
    expect(wrapper.text()).not.toContain("分镜设计");

    const plan = {
      id: "plan-after-worker", projectId: "project-1", revision: 1, sourceStoryVersionId: "story-1", sourceSelectionHash: "a".repeat(64),
      clip: {}, shots: [professionalShot], totalDurationSeconds: 12,
      directorTreatment: { logline: "雨天门边的一次温柔照顾", theme: "日常照顾" },
      directorPromptRevision: "catflow-director-v1", directorModel: "planner", directorInputHash: "b".repeat(64),
      active: true, outdated: false, createdAt: "2026-09-01T00:00:00Z",
    };
    await wrapper.setProps({ workspace: workspace(plan) });
    await flushPromises();

    expect(wrapper.text()).toContain("分镜设计");
    expect(wrapper.findAll('[data-testid="professional-shot-details"] input').some((input) => (input.element as HTMLInputElement).value === "35mm")).toBe(true);
  });
});
