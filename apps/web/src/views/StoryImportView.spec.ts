import { flushPromises, mount } from "@vue/test-utils";
import { reactive } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import StoryImportView from "./StoryImportView.vue";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
const routeState = vi.hoisted(() => ({ params: {} as Record<string, string> }));
const route = reactive(routeState);
const client = vi.hoisted(() => ({ catReferenceOptions: vi.fn(), referenceBinding: vi.fn(),
  previewStoryImport: vi.fn(),
  createStoryImport: vi.fn(),
  reanalyzeStoryImport: vi.fn(),
  storyImport: vi.fn(),
  storyImports: vi.fn(),
  storySeries: vi.fn(),
  projects: vi.fn(),
  confirmStoryImport: vi.fn(),
  updateStoryProductionTargets: vi.fn(), job: vi.fn(),
}));

vi.mock("vue-router", async () => {
  const actual = await vi.importActual<typeof import("vue-router")>("vue-router");
  return { ...actual, useRoute: () => route, useRouter: () => router };
});
vi.mock("../api/client", () => ({ api: client }));

const now = "2026-09-04T08:00:00Z";
const preview = {
  contentHash: "a".repeat(64),
  inputHash: "b".repeat(64),
  characterCount: 30,
  prompt: "识别一个或多个故事单元。",
  outputSchema: {},
  promptRevision: "import-v1",
};
const analyzedDocument = {
  id: "document-1",
  contentHash: preview.contentHash,
  sourceFormat: "paste",
  fileName: null,
  rawText: "主题一：森林野餐\n剧本一：准备野餐\n主题二：下雨天",
  status: "analyzed",
  analysisJobId: "job-1",
  units: [
    { id: "unit-1", documentId: "document-1", ordinal: 1, title: "准备野餐", theme: "森林野餐", rawText: "准备野餐", analysis: {}, createdAt: now },
    { id: "unit-2", documentId: "document-1", ordinal: 2, title: "窗户上的画", theme: "下雨天", rawText: "窗户上的画", analysis: {}, createdAt: now },
  ],
  relationSuggestions: [
    { id: "suggestion-1", documentId: "document-1", relationType: "new_series", unitIds: ["unit-1"], title: "森林野餐", narrativeMode: "continuous", confidence: 90, rationale: "形成连续事件", status: "suggested", createdAt: now },
    { id: "suggestion-2", documentId: "document-1", relationType: "independent", unitIds: ["unit-2"], title: "窗户上的画", confidence: 85, rationale: "可独立制作", status: "suggested", createdAt: now },
  ],
  createdAt: now,
  updatedAt: now,
};

function mountView() {
  return mount(StoryImportView, {
    global: { stubs: { RouterLink: { props: ["to"], template: '<a :href="to"><slot /></a>' } } },
  });
}

describe("StoryImportView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.catReferenceOptions.mockResolvedValue([{ key: "gray-original", label: "原版灰猫", canonProfileId: "gray-canon", available: true, fixedAssets: {}, auxiliary: [] }, { key: "white-v4", label: "V4 校色白猫", canonProfileId: "white-canon", available: true, fixedAssets: {}, auxiliary: [] }]);
    client.referenceBinding.mockResolvedValue({ canonProfileId: "gray-canon", label: "原版灰猫", canChange: true });
    route.params = {};
    client.storySeries.mockResolvedValue([]);
    client.projects.mockResolvedValue([]);
    client.storyImports.mockResolvedValue([]);
    client.previewStoryImport.mockResolvedValue(preview);
    client.createStoryImport.mockResolvedValue({ document: analyzedDocument, analysisJob: null, idempotencyReplayed: false });
    client.updateStoryProductionTargets.mockImplementation(async (_id, command) => ({ ...analyzedDocument, productionTargets: command.productionTargets }));
    client.job.mockResolvedValue({ id: "job-1", status: "succeeded", revision: 0, execution: { availableActions: [], resultState: "complete" } });
  });

  it("lists prior source records so an analyzed result can be resumed without reanalysis", async () => {
    client.storyImports.mockResolvedValue([analyzedDocument]);
    const wrapper = mountView();
    await flushPromises();

    const history = wrapper.get(".import-history");
    expect(history.text()).toContain("森林野餐");
    expect(history.text()).toContain("2 个剧情节拍");
    expect(history.get("a").attributes("href")).toBe("/story-imports/document-1");
  });

  it("loads a saved analysis when the reused route component changes document id", async () => {
    client.storyImport.mockResolvedValue(analyzedDocument);
    const wrapper = mountView();
    await flushPromises();

    route.params = { documentId: "document-1" };
    await flushPromises();

    expect(client.storyImport).toHaveBeenCalledWith("document-1");
    expect(wrapper.text()).toContain("识别到 2 个剧情节拍");
  });

  it("allows an accepted source suggestion to explicitly create another independent series", async () => {
    const accepted = {
      ...analyzedDocument,
      relationSuggestions: analyzedDocument.relationSuggestions.map((item, index) => ({
        ...item,
        status: index === 0 ? "accepted" : item.status,
        episodeCountRecommendation: index === 0
          ? { minimumRecommended: 2, preferred: 2, maximumRecommended: 3, rationale: "建议" }
          : null,
      })),
    };
    route.params = { documentId: "document-1" };
    client.storyImport.mockResolvedValue(accepted);
    client.confirmStoryImport.mockResolvedValue({
      series: { id: "series-new" },
      projects: [],
    });
    const wrapper = mountView();
    await flushPromises();

    const button = wrapper.findAll("button.confirm-relation")[0];
    expect(button.text()).toContain("创建另一个新系列");
    expect(button.attributes("disabled")).toBeUndefined();
    await button.trigger("click");
    await flushPromises();

    expect(client.confirmStoryImport).toHaveBeenCalledWith(
      "document-1",
      expect.objectContaining({
        suggestionId: "suggestion-1",
        target: "new_series",
        seriesLengthMode: "fixed",
        plannedEpisodeCount: 3,
        defaultEpisodeDurationSeconds: 15,
      }),
    );
    expect(router.push).toHaveBeenCalledWith("/series/series-new");
  });

  it("moves an accepted import onto a stable document URL and blocks another import while it runs", async () => {
    const analyzingDocument = {
      ...analyzedDocument,
      status: "analyzing",
      units: [],
      relationSuggestions: [],
    };
    client.createStoryImport.mockResolvedValue({
      document: analyzingDocument,
      analysisJob: { id: "job-1" },
      idempotencyReplayed: false,
    });
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get("textarea[aria-label='故事来源文本']").setValue(analyzingDocument.rawText);
    await new Promise((resolve) => setTimeout(resolve, 450));
    await flushPromises();

    await wrapper.get("button.analyze-button").trigger("click");
    await flushPromises();

    expect(router.replace).toHaveBeenCalledWith("/story-imports/document-1");
    expect(wrapper.find("button.analyze-button").exists()).toBe(false);
    expect(wrapper.text()).toContain("正在理解故事结构");
    expect(client.createStoryImport).toHaveBeenCalledTimes(1);
  });

  it("restores the persisted import from a document URL after a reload", async () => {
    const analyzingDocument = {
      ...analyzedDocument,
      status: "analyzing",
      units: [],
      relationSuggestions: [],
    };
    route.params = { documentId: "document-1" };
    client.storyImport.mockResolvedValue(analyzingDocument);

    const wrapper = mountView();
    await flushPromises();

    expect(client.storyImport).toHaveBeenCalledWith("document-1");
    expect(
      (wrapper.get("textarea[aria-label='故事来源文本']").element as HTMLTextAreaElement).value,
    ).toBe(analyzingDocument.rawText);
    expect(wrapper.text()).toContain("正在理解故事结构");
    expect(wrapper.find("button.analyze-button").exists()).toBe(false);
    expect(client.createStoryImport).not.toHaveBeenCalled();
  });

  it("accepts mixed source text and leaves every suggested relationship for confirmation", async () => {
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get("textarea[aria-label='故事来源文本']").setValue(analyzedDocument.rawText);
    await new Promise((resolve) => setTimeout(resolve, 450));
    await flushPromises();
    await wrapper.get("button.analyze-button").trigger("click");
    await flushPromises();

    expect(client.createStoryImport).toHaveBeenCalledTimes(1);
    expect(wrapper.findAll(".source-unit")).toHaveLength(2);
    expect(wrapper.findAll(".relation-card")).toHaveLength(2);
    expect(client.confirmStoryImport).not.toHaveBeenCalled();
  });

  it("retries a failed analysis against the same stored document", async () => {
    const failedDocument = { ...analyzedDocument, status: "failed", units: [], relationSuggestions: [] };
    client.createStoryImport.mockResolvedValue({ document: failedDocument, analysisJob: null, idempotencyReplayed: false });
    client.reanalyzeStoryImport.mockResolvedValue({ id: "job-2" });
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get("textarea[aria-label='故事来源文本']").setValue(failedDocument.rawText);
    await new Promise((resolve) => setTimeout(resolve, 450));
    await flushPromises();
    await wrapper.get("button.analyze-button").trigger("click");
    await flushPromises();
    await wrapper.get(".import-result .primary").trigger("click");
    await flushPromises();

    expect(client.reanalyzeStoryImport).toHaveBeenCalledWith(
      "document-1",
      expect.objectContaining({ expectedInputHash: preview.inputHash }),
    );
    expect(client.createStoryImport).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("正在理解故事结构");
  });

  it("offers a paid reanalysis for an unconfirmed result and keeps the document id", async () => {
    route.params = { documentId: "document-1" };
    client.storyImport.mockResolvedValue(analyzedDocument);
    client.reanalyzeStoryImport.mockResolvedValue({ id: "job-2" });
    const wrapper = mountView();
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 450));
    await flushPromises();

    const button = wrapper.get("button.reanalyze-button");
    expect(button.text()).toContain("重新分析并拆分");
    await button.trigger("click");
    await flushPromises();

    expect(client.reanalyzeStoryImport).toHaveBeenCalledWith(
      "document-1",
      expect.objectContaining({ expectedInputHash: preview.inputHash }),
    );
    expect(wrapper.text()).toContain("正在理解故事结构");
  });

  it("falls back to creating a new series when an append suggestion has no target series", async () => {
    const appendWithoutTarget = {
      ...analyzedDocument,
      relationSuggestions: [
        {
          ...analyzedDocument.relationSuggestions[0],
          relationType: "append_series",
          suggestedSeriesId: null,
        },
      ],
    };
    route.params = { documentId: "document-1" };
    client.storyImport.mockResolvedValue(appendWithoutTarget);

    const wrapper = mountView();
    await flushPromises();

    const target = wrapper.get("select[aria-label='森林野餐的处理方式']");
    expect((target.element as HTMLSelectElement).value).toBe("new_series");
    expect(wrapper.get(".relation-action button").attributes("disabled")).toBeUndefined();
  });

  it("always presents an intentional import as a new paid analysis", async () => {
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get("textarea[aria-label='故事来源文本']").setValue(analyzedDocument.rawText);
    await new Promise((resolve) => setTimeout(resolve, 450));
    await flushPromises();

    expect(wrapper.text()).toContain("每次导入都会新建来源记录");
    expect(wrapper.get("button.analyze-button").text()).toBe("导入并分析（付费）");
    expect(wrapper.text()).not.toContain("打开已有分析");
    expect(wrapper.text()).not.toContain("已有相同内容");
  });

  it("keeps eleven source beats while confirming a separate three by fifteen production target", async () => {
    const elevenBeatDocument = {
      ...analyzedDocument,
      units: Array.from({ length: 11 }, (_, index) => ({
        id: `unit-${index + 1}`,
        documentId: "document-1",
        ordinal: index + 1,
        title: `剧情节拍 ${index + 1}`,
        theme: "森林野餐",
        rawText: `原文事件 ${index + 1}`,
        analysis: {},
        createdAt: now,
      })),
      relationSuggestions: [{
        ...analyzedDocument.relationSuggestions[0],
        unitIds: Array.from({ length: 11 }, (_, index) => `unit-${index + 1}`),
        episodeCountRecommendation: {
          minimumRecommended: 6,
          preferred: 8,
          maximumRecommended: 11,
          rationale: "相邻节拍可以组合为单集。",
        },
      }],
    };
    route.params = { documentId: "document-1" };
    client.storyImport.mockResolvedValue(elevenBeatDocument);
    client.confirmStoryImport.mockResolvedValue({
      series: { id: "series-new" },
      projects: [],
    });

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain("识别到 11 个剧情节拍");
    expect(wrapper.text()).toContain("故事分析中的集数建议未考虑当前时长");
    expect((wrapper.get("input[aria-label='森林野餐生产目标计划集数']").element as HTMLInputElement).value).toBe("3");

    await wrapper.get("button.confirm-relation").trigger("click");
    await flushPromises();

    expect(client.confirmStoryImport).toHaveBeenCalledWith(
      "document-1",
      expect.objectContaining({
        target: "new_series",
        seriesLengthMode: "fixed",
        plannedEpisodeCount: 3,
        defaultEpisodeDurationSeconds: 15,
      }),
    );
  });

  it("settles a successful import request so the next explicit import gets a new key", async () => {
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get("textarea[aria-label='故事来源文本']").setValue(analyzedDocument.rawText);
    await new Promise((resolve) => setTimeout(resolve, 450));
    await flushPromises();

    await wrapper.get("button.analyze-button").trigger("click");
    await flushPromises();
    const firstKey = client.createStoryImport.mock.calls[0][0].idempotencyKey;
    wrapper.unmount();
    route.params = {};
    const next = mountView(); await flushPromises();
    await next.get("textarea[aria-label='故事来源文本']").setValue(analyzedDocument.rawText);
    await new Promise(resolve => setTimeout(resolve, 450)); await flushPromises();
    await next.get("button.analyze-button").trigger("click");
    await flushPromises();
    const secondKey = client.createStoryImport.mock.calls[1][0].idempotencyKey;

    expect(secondKey).not.toBe(firstKey);
  });

  it("submits only once when the paid import button is clicked twice quickly", async () => {
    let resolveRequest: ((value: unknown) => void) | undefined;
    client.createStoryImport.mockImplementation(() => new Promise((resolve) => {
      resolveRequest = resolve;
    }));
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get("textarea[aria-label='故事来源文本']").setValue(analyzedDocument.rawText);
    await new Promise((resolve) => setTimeout(resolve, 450));
    await flushPromises();

    const button = wrapper.get("button.analyze-button");
    const firstClick = button.trigger("click");
    const secondClick = button.trigger("click");
    await Promise.all([firstClick, secondClick]);

    expect(client.createStoryImport).toHaveBeenCalledTimes(1);
    resolveRequest?.({ document: analyzedDocument, analysisJob: null, idempotencyReplayed: false });
    await flushPromises();
  });
  it("persists a white choice per group and confirms it without another analysis", async () => {
    route.params = { documentId: "document-1" };
    client.storyImport.mockResolvedValue(analyzedDocument);
    client.confirmStoryImport.mockResolvedValue({ series: { id: "white-series" }, projects: [] });
    const wrapper = mountView();
    await flushPromises();
    const group = wrapper.findAll(".relation-card")[0];
    await group.findAll(".cat-selector .choice")[1].trigger("click");
    await group.get(".confirm-relation").trigger("click");
    await flushPromises();
    expect(client.updateStoryProductionTargets).toHaveBeenCalledWith("document-1", expect.objectContaining({
      productionTargets: expect.objectContaining({ "suggestion-1": expect.objectContaining({ canonProfileId: "white-canon" }) }),
    }));
    expect(client.confirmStoryImport).toHaveBeenCalledWith("document-1", expect.objectContaining({ canonProfileId: "white-canon" }));
    expect(client.createStoryImport).not.toHaveBeenCalled();
    expect(client.reanalyzeStoryImport).not.toHaveBeenCalled();
  });

});
