import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import StoryImportView from "./StoryImportView.vue";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
const route = vi.hoisted(() => ({ params: {} as Record<string, string> }));
const client = vi.hoisted(() => ({
  previewStoryImport: vi.fn(),
  createStoryImport: vi.fn(),
  reanalyzeStoryImport: vi.fn(),
  storyImport: vi.fn(),
  storySeries: vi.fn(),
  projects: vi.fn(),
  confirmStoryImport: vi.fn(),
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
  duplicateDocumentId: null,
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
    global: { stubs: { RouterLink: { props: ["to"], template: "<a><slot /></a>" } } },
  });
}

describe("StoryImportView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    route.params = {};
    client.storySeries.mockResolvedValue([]);
    client.projects.mockResolvedValue([]);
    client.previewStoryImport.mockResolvedValue(preview);
    client.createStoryImport.mockResolvedValue({ document: analyzedDocument, analysisJob: null, reused: false });
  });

  it("moves an accepted import onto a stable document URL and blocks duplicate analysis while it runs", async () => {
    const analyzingDocument = {
      ...analyzedDocument,
      status: "analyzing",
      units: [],
      relationSuggestions: [],
    };
    client.createStoryImport.mockResolvedValue({
      document: analyzingDocument,
      analysisJob: { id: "job-1" },
      reused: false,
    });
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get("textarea[aria-label='故事来源文本']").setValue(analyzingDocument.rawText);
    await new Promise((resolve) => setTimeout(resolve, 450));
    await flushPromises();

    await wrapper.get("button.analyze-button").trigger("click");
    await flushPromises();

    expect(router.replace).toHaveBeenCalledWith("/story-imports/document-1");
    expect(wrapper.get("button.analyze-button").attributes("disabled")).toBeDefined();
    expect(wrapper.get("button.analyze-button").text()).toBe("分析任务进行中");
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
    expect(wrapper.get("button.analyze-button").attributes("disabled")).toBeDefined();
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
    client.createStoryImport.mockResolvedValue({ document: failedDocument, analysisJob: null, reused: true });
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
});
