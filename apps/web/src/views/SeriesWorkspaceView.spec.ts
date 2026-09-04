import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SeriesWorkspaceView from "./SeriesWorkspaceView.vue";

const router = vi.hoisted(() => ({ push: vi.fn() }));
const client = vi.hoisted(() => ({
  storySeriesDetail: vi.fn(),
  seriesPlans: vi.fn(),
  seriesEpisodes: vi.fn(),
  seriesJobs: vi.fn(),
  runtime: vi.fn(),
  seriesAssets: vi.fn(),
  previewSeriesPlan: vi.fn(),
  generateSeriesPlan: vi.fn(),
  activateSeriesPlan: vi.fn(),
  rejectSeriesPlan: vi.fn(),
  materializeSeriesPlan: vi.fn(),
  materializeSeriesEpisode: vi.fn(),
  previewSeriesEpisodeStory: vi.fn(),
  generateSeriesEpisodeStory: vi.fn(),
  seriesEpisodeContinuity: vi.fn(),
  seriesEpisodeContinuityFrames: vi.fn(),
  selectSeriesEpisodeContinuityKeyframes: vi.fn(),
  confirmSeriesEpisodeContinuity: vi.fn(),
}));

vi.mock("vue-router", async () => {
  const actual = await vi.importActual<typeof import("vue-router")>("vue-router");
  return {
    ...actual,
    useRoute: () => ({ params: { seriesId: "series-1" } }),
    useRouter: () => router,
  };
});
vi.mock("../api/client", () => ({ api: client }));

const now = "2026-09-04T08:00:00Z";
const series = {
  id: "series-1",
  title: "森林野餐",
  premise: "孩子和猫咪从准备到返程。",
  narrativeMode: "continuous",
  plannedEpisodeCount: 30,
  defaultEpisodeDurationSeconds: 12,
  worldSetting: "家与森林",
  emotionalDirection: "期待到满足",
  endingGoal: "一起回家",
  recurringElements: [],
  mustKeep: [],
  mustAvoid: [],
  additionalNotes: null,
  canonProfileId: "canon-1",
  activePlanVersionId: "plan-1",
  plannedCount: 30,
  materializedCount: 0,
  completedCount: 0,
  createdAt: now,
  updatedAt: now,
};

function outline(order: number) {
  return {
    order,
    title: `第 ${order} 集`,
    targetDurationSeconds: 12,
    premise: `完成事件 ${order}`,
    openingState: "从上一状态开始",
    trigger: "发现变化",
    childIntent: "完成小事",
    childAction: "孩子开始、行动并停下",
    catResponse: "猫咪观察、回应并停下",
    visibleChange: "道具状态改变",
    endingState: "形成明确结尾",
    continuityCarryover: [],
    recurringLocationKeys: [],
    recurringPropKeys: [],
    productionWarnings: [],
  };
}

const bible = {
  logline: "一起完成一整天的野餐。",
  centralTheme: "陪伴",
  narrativeMode: "continuous",
  worldRules: [],
  emotionalArc: { opening: "期待", development: "准备", climax: "野餐", resolution: "返程" },
  recurringLocations: [],
  recurringProps: [],
  wardrobeRules: [],
  continuityRules: [],
  visualMotifs: [],
  soundMotifs: [],
  forbiddenChanges: [],
};
const acceptedPlan = {
  id: "plan-1",
  seriesId: "series-1",
  revision: 1,
  status: "accepted",
  active: true,
  disposition: "candidate_ready",
  plan: { seriesBible: bible, episodes: Array.from({ length: 30 }, (_, index) => outline(index + 1)) },
  inputHash: "a".repeat(64),
  promptRevision: "series-v1",
  issues: [],
  decidedAt: now,
  createdAt: now,
};
const candidatePlan = {
  ...acceptedPlan,
  id: "plan-2",
  revision: 2,
  status: "candidate",
  active: false,
  producingJobId: "job-finished",
  decidedAt: null,
};
const episodes = Array.from({ length: 30 }, (_, index) => ({
  id: `episode-${index + 1}`,
  seriesId: "series-1",
  order: index + 1,
  title: `第 ${index + 1} 集`,
  targetDurationSeconds: 12,
  status: "outline",
  projectId: null,
  activeOutlineVersionId: `outline-${index + 1}`,
  outline: outline(index + 1),
  createdAt: now,
  updatedAt: now,
}));

function mountView() {
  return mount(SeriesWorkspaceView, {
    global: {
      stubs: {
        RouterLink: { props: ["to"], template: "<a><slot /></a>" },
        AssetImageViewer: true,
      },
    },
  });
}

describe("SeriesWorkspaceView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.storySeriesDetail.mockResolvedValue(series);
    client.seriesPlans.mockResolvedValue([candidatePlan, acceptedPlan]);
    client.seriesEpisodes.mockResolvedValue(episodes);
    client.seriesJobs.mockResolvedValue([]);
    client.runtime.mockResolvedValue({
      worker: { ready: true, state: "ready", restartCount: 0, retryingAutomatically: false },
      provider: { apiKeyConfigured: true, paidCallsEnabled: true },
    });
    client.seriesAssets.mockResolvedValue([]);
    client.previewSeriesPlan.mockResolvedValue({
      seriesId: "series-1",
      provider: "ark",
      model: "planning",
      capabilityRevision: "v1",
      inputHash: "b".repeat(64),
      prompt: "只规划整季路线。",
      outputSchema: {},
      plannedEpisodeCount: 30,
      defaultEpisodeDurationSeconds: 12,
      promptRevision: "series-v1",
    });
  });

  it("keeps a 30-episode series lightweight and does not auto-adopt a candidate", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.findAll(".episode-rail article")).toHaveLength(10);
    expect(wrapper.findAll(".episode-list article")).toHaveLength(12);
    expect(wrapper.text()).toContain("新方案 · 待确认");
    expect(client.activateSeriesPlan).not.toHaveBeenCalled();

    await wrapper.get(".episode-list .load-more").trigger("click");
    expect(wrapper.findAll(".episode-list article")).toHaveLength(24);
  });

  it("creates one explicit planning job and keeps the action disabled while queued", async () => {
    client.generateSeriesPlan.mockResolvedValue({
      id: "job-1",
      seriesId: "series-1",
      projectId: null,
      kind: "plan_series",
      status: "queued",
      inputHash: "b".repeat(64),
      idempotencyKey: "request-1",
      frozenInput: {},
      resultAssetIds: [],
      createdAt: now,
      updatedAt: now,
    });
    const wrapper = mountView();
    await flushPromises();

    const button = wrapper.get(".planning-section .primary");
    await button.trigger("click");
    await flushPromises();

    expect(client.generateSeriesPlan).toHaveBeenCalledTimes(1);
    expect(button.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("等待后台任务领取");
  });

  it("shows a project-scoped episode story job and prevents a duplicate paid submission", async () => {
    const materializedEpisodes = [
      { ...episodes[0], projectId: "project-1", status: "story_review" },
      ...episodes.slice(1),
    ];
    client.seriesEpisodes.mockResolvedValue(materializedEpisodes);
    client.seriesJobs.mockResolvedValue([{
      id: "episode-story-job-1",
      seriesId: null,
      projectId: "project-1",
      kind: "plan_series_episode",
      status: "submitting",
      inputHash: "c".repeat(64),
      idempotencyKey: "episode-request-1",
      frozenInput: {},
      resultAssetIds: [],
      createdAt: now,
      updatedAt: now,
    }]);
    client.previewSeriesEpisodeStory.mockResolvedValue({
      seriesId: "series-1",
      episodeId: "episode-1",
      projectId: "project-1",
      inputHash: "d".repeat(64),
      prompt: "只扩写第一集。",
    });

    const wrapper = mountView();
    await flushPromises();
    await wrapper.get(".episode-list article button.secondary").trigger("click");
    await flushPromises();

    const panel = wrapper.get(".episode-story-panel");
    expect(panel.text()).toContain("正在生成本集故事");
    expect(panel.text()).toContain("当前任务完成前不会创建第二条任务");
    const button = panel.get("button.primary");
    expect(button.attributes("disabled")).toBeDefined();
    await button.trigger("click");
    expect(client.generateSeriesEpisodeStory).not.toHaveBeenCalled();
  });

  it("opens a provider candidate safely and repairs an unambiguous zero-based episode order", async () => {
    const needsInputPlan = {
      ...candidatePlan,
      disposition: "needs_input",
      plan: {
        seriesBible: bible,
        episodes: Array.from({ length: 30 }, (_, index) => outline(index)),
      },
      issues: [
        {
          code: "episode_order_invalid",
          severity: "blocking",
          path: "episodes",
          message: "集数必须从 1 开始并连续。",
        },
      ],
    };
    client.seriesPlans.mockResolvedValue([needsInputPlan, acceptedPlan]);
    client.materializeSeriesPlan.mockResolvedValue({ ...candidatePlan, id: "plan-3", revision: 3 });
    const wrapper = mountView();
    await flushPromises();

    await wrapper.get(".candidate-actions .ghost").trigger("click");

    expect(wrapper.find(".plan-editor").exists()).toBe(true);
    expect(wrapper.text()).toContain("第 0 集");
    const repair = wrapper.get("button.renumber-episodes");
    expect(repair.text()).toContain("按当前顺序编号为 1–30");

    await repair.trigger("click");
    await wrapper.get(".editor-actions .primary").trigger("click");
    await flushPromises();

    expect(client.materializeSeriesPlan).toHaveBeenCalledWith(
      "series-1",
      "plan-2",
      expect.objectContaining({
        plan: expect.objectContaining({
          episodes: expect.arrayContaining([
            expect.objectContaining({ order: 1 }),
            expect.objectContaining({ order: 30 }),
          ]),
        }),
      }),
    );
  });

  it("confirms each continuity field as inherit, adjust, or reset", async () => {
    const state = {
      wardrobe: "同一套夏季服装",
      location: "森林草地",
      weather: "晴天",
      timeOfDay: "午后",
      lighting: "树荫下的柔光",
      childState: "孩子坐在野餐垫旁",
      catState: "猫咪抱着毛线球",
      spatialPositions: "孩子在左，猫咪在右",
      props: [{ key: "basket", name: "野餐篮", state: "已经打开", owner: "child" }],
      unfinishedActions: ["收起野餐垫"],
      endingImage: "两者准备开始收拾",
    };
    client.seriesEpisodeContinuity
      .mockResolvedValueOnce({
        episodeId: "episode-2",
        previousEpisodeId: "episode-1",
        incoming: { id: "snapshot-1", episodeId: "episode-2", direction: "incoming", source: "planned", state, decisions: {}, confirmed: false, active: true, createdAt: now },
        outgoing: null,
      })
      .mockResolvedValueOnce({
        episodeId: "episode-2",
        previousEpisodeId: "episode-1",
        incoming: { id: "snapshot-2", episodeId: "episode-2", direction: "incoming", source: "confirmed", state: { ...state, weather: "傍晚微风" }, decisions: { weather: "adjust" }, confirmed: true, active: true, createdAt: now },
        outgoing: null,
      });
    client.seriesEpisodeContinuityFrames.mockResolvedValue({ episodeId: "episode-1", sourceVideoAssetId: null, lastFrame: null, candidates: [], selectedKeyframes: [] });
    client.confirmSeriesEpisodeContinuity.mockResolvedValue({});
    const wrapper = mountView();
    await flushPromises();

    await wrapper.findAll(".episode-list article")[1].get("button.ghost").trigger("click");
    await flushPromises();
    await wrapper.get("select[aria-label='天气的连续性处理']").setValue("adjust");
    await wrapper.get("textarea[aria-label='天气状态']").setValue("傍晚微风");
    await wrapper.get("select[aria-label='地点的连续性处理']").setValue("reset");
    await wrapper.get("button.continuity-confirm").trigger("click");
    await flushPromises();

    expect(client.confirmSeriesEpisodeContinuity).toHaveBeenCalledWith(
      "series-1",
      "episode-2",
      expect.objectContaining({
        state: expect.objectContaining({ weather: "傍晚微风" }),
        decisions: expect.objectContaining({ weather: "adjust", location: "reset", wardrobe: "inherit" }),
      }),
    );
    expect(wrapper.text()).toContain("已确认");
  });
});
