import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, ref } from "vue";

import type { AssetDto, ProductionBoardDto } from "../../api/types";
import DeliveryWorkbench from "../director/DeliveryWorkbench.vue";

const calls = vi.hoisted(() => ({
  board: vi.fn(), build: vi.fn(), selectSequence: vi.fn(), tasks: vi.fn(),
  register: vi.fn(), push: vi.fn(), replace: vi.fn(), success: vi.fn(), error: vi.fn(), confirm: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  assetContentUrl: (id: string) => `/api/v1/assets/${id}/content`,
  api: {
    productionBoard: calls.board,
    projectTasks: calls.tasks,
    buildSequence: calls.build,
    selectSequence: calls.selectSequence,
  },
}));
vi.mock("../../tasks/taskCenter", () => ({
  registerTask: calls.register,
  useTaskCenter: () => ({
    projectSignals: ref<Record<string, { revision: number }>>({}),
    workspaceRefreshRequest: ref(),
  }),
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: calls.push, replace: calls.replace }) }));
vi.mock("element-plus", async (importOriginal) => ({
  ...(await importOriginal<typeof import("element-plus")>()),
  ElMessage: { success: calls.success, error: calls.error },
  ElMessageBox: { confirm: calls.confirm },
}));
vi.mock("../canvas/VideoEditWorkspace.vue", () => ({
  default: defineComponent({
    name: "VideoEditWorkspace",
    props: ["sourceAssetId", "references", "embedded"],
    emits: ["close", "submitted", "expand"],
    template: "<section data-testid='video-edit-workspace'>视频局部编辑</section>",
  }),
}));

enableAutoUnmount(afterEach);

function asset(
  id: string,
  role: string,
  mediaType: "image" | "video" = "video",
  status = "approved",
): AssetDto {
  return {
    id, role, mediaType, scope: "shot", status, projectId: "project-1", sceneId: "scene-1", shotId: "shot-1",
    producingStepId: null, sha256: `sha-${id}`, semanticKey: `${role}:${id}`, metadata: {}, contentReady: true,
    displayName: id, referencePurpose: null, visualProfileRevisionId: null, lookDraftRevision: null, createdAt: "2026-08-31T10:00:00Z",
  };
}

function board(): ProductionBoardDto {
  const video1 = asset("video-1", "shot_video");
  const video1History = asset("video-1-history", "shot_video");
  const video2 = { ...asset("video-2", "shot_video"), shotId: "shot-2" };
  const final = { ...asset("final-1", "final_sequence"), scope: "project", shotId: null };
  const styleBoard = { ...asset("style-board", "style_board", "image"), scope: "project", shotId: null, metadata: { referenceAuthority: { role: "style_board", providerEligible: true } } };
  const styleSource = { ...asset("style-source", "style_source", "image"), scope: "project", shotId: null, metadata: { referenceAuthority: { role: "style_source", providerEligible: false } } };
  const makeShot = (id: string, order: number, title: string, selected: AssetDto, versions: AssetDto[]) => ({
    id, sceneId: "scene-1", order, title, direction: `${title}完整镜头描述`, durationSeconds: 4, draftRevision: 3,
    anchorMode: "text_only" as const, referenceBindings: [], inheritProjectReferences: true, sceneLookUsage: "off" as const,
    useSceneLook: false, status: "approved", selectedAnchorAssetId: null, selectedVideoAssetId: selected.id, assets: versions, attempts: [],
  });
  const shot1 = makeShot("shot-1", 1, "发现纸星星", video1, [video1History, video1]);
  const shot2 = makeShot("shot-2", 2, "贴上纸星星", video2, [video2]);
  return {
    projectId: "project-1",
    projectGraph: {
      project: { id: "project-1", title: "一人一猫", contentDate: "2026-08-31", status: "active", selectedSequenceId: "sequence-1", contractVersion: 5, defaultReferenceBindings: [] },
      assets: [final, styleBoard, styleSource],
      scenes: [{ id: "scene-1", order: 1, title: "清晨窗边", sourceText: "故事", storyMode: "single", targetShotCount: 2, lookDraftRevision: 1, status: "ready", attempts: [], shots: [shot1, shot2] }],
      sequences: [{
        id: "sequence-1", projectId: "project-1", revision: 2, renderedAssetId: "final-1", status: "approved",
        plan: { duration_ms: 7800, clips: [
          { order: 1, shot_card_id: "shot-1", source_asset_id: "video-1", source_start_ms: 0, source_end_ms: 4000, timeline_start_ms: 0, timeline_end_ms: 4000 },
          { order: 2, shot_card_id: "shot-2", source_asset_id: "video-2", source_start_ms: 0, source_end_ms: 4000, timeline_start_ms: 3800, timeline_end_ms: 7800, transitionFromPrevious: { type: "cross_dissolve", durationMs: 200 } },
        ] },
      }],
    },
    scenes: [],
  };
}

describe("DeliveryWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.board.mockResolvedValue(board());
    calls.tasks.mockResolvedValue([]);
    calls.build.mockResolvedValue({ jobId: "job-build-1" });
    calls.selectSequence.mockImplementation(async (_projectId: string, sequenceId: string, approve: boolean) => ({
      ...board().projectGraph.sequences.find((sequence) => sequence.id === sequenceId)!,
      status: approve ? "approved" : "rejected",
    }));
    calls.confirm.mockResolvedValue("confirm");
  });

  it("loads delivery independently and renders the approved master, sequence history and real timeline clips", async () => {
    const wrapper = mount(DeliveryWorkbench, {
      props: { projectId: "project-1", focusedItemId: "sequence-1", panel: "main" },
    });
    await flushPromises();

    expect(calls.board).toHaveBeenCalledWith("project-1");
    expect(wrapper.get(".delivery-player video").attributes("src")).toBe("/api/v1/assets/final-1/content");
    expect(wrapper.get(".delivery-player").text()).toContain("总片 Revision 2");
    expect(wrapper.findAll(".delivery-timeline-clip")).toHaveLength(2);
    expect(wrapper.get(".delivery-timeline").text()).toContain("叠化 0.2s");
    expect(wrapper.findAll(".delivery-sequence-history article")).toHaveLength(1);
  });

  it("compares immutable shot versions and opens the existing local editor with Provider-eligible references only", async () => {
    const wrapper = mount(DeliveryWorkbench, {
      props: { projectId: "project-1", focusedItemId: "sequence-1", panel: "main" },
    });
    await flushPromises();

    await wrapper.findAll(".delivery-timeline-clip")[0].trigger("click");
    expect(wrapper.get(".delivery-player video").attributes("src")).toBe("/api/v1/assets/video-1/content");
    expect(wrapper.findAll(".delivery-video-history article")).toHaveLength(2);
    const compare = wrapper.findAll(".delivery-video-history button").find((button) => button.text().includes("对比"));
    await compare!.trigger("click");
    expect(wrapper.findAll(".delivery-comparison video")).toHaveLength(2);

    await wrapper.get("[data-action='open-local-edit']").trigger("click");
    const editor = wrapper.getComponent({ name: "VideoEditWorkspace" });
    expect(editor.props("sourceAssetId")).toBe("video-1");
    expect(editor.props("embedded")).toBe(true);
    expect(editor.props("references")).toHaveLength(1);
    expect(editor.props("references")[0].id).toBe("style-board");

    editor.vm.$emit("expand");
    await flushPromises();
    expect(wrapper.getComponent({ name: "VideoEditWorkspace" }).props("embedded")).toBe(false);
    wrapper.getComponent({ name: "VideoEditWorkspace" }).vm.$emit("close");
    await flushPromises();
    expect(wrapper.findComponent({ name: "VideoEditWorkspace" }).exists()).toBe(false);
  });

  it("builds the approved timeline locally with explicit intro, outro and between-shot transitions", async () => {
    const wrapper = mount(DeliveryWorkbench, {
      props: { projectId: "project-1", focusedItemId: "sequence-1", panel: "main" },
    });
    await flushPromises();

    await wrapper.get("[data-action='open-compose']").trigger("click");
    expect(wrapper.findAll(".delivery-compose-shot")).toHaveLength(2);
    await wrapper.get("[data-transition-shot='shot-2']").setValue("cross_dissolve");
    await wrapper.get("[data-action='build-sequence']").trigger("click");
    await flushPromises();

    expect(calls.confirm).toHaveBeenCalledOnce();
    expect(calls.build).toHaveBeenCalledWith("project-1", {
      transitions: [{ afterShotId: "shot-2", transition: { type: "cross_dissolve", durationMs: 300 } }],
      introTransition: { type: "fade_black", durationMs: 400 },
      outroTransition: { type: "fade_black", durationMs: 400 },
    });
    expect(calls.register).toHaveBeenCalledWith("job-build-1", {
      kind: "build_sequence",
      label: "本地成片合成",
      operationKey: "sequence:build",
      projectId: "project-1",
    });
  });

  it("keeps rendered output in review until the user explicitly approves it as the current master", async () => {
    const pending = board();
    pending.projectGraph.project.selectedSequenceId = null;
    pending.projectGraph.sequences[0].status = "content_review";
    calls.board.mockResolvedValue(pending);
    const wrapper = mount(DeliveryWorkbench, {
      props: { projectId: "project-1", focusedItemId: "sequence-1", panel: "main" },
    });
    await flushPromises();

    expect(wrapper.get(".delivery-sequence-review").text()).toContain("等待成片审核");
    await wrapper.get("[data-action='approve-sequence']").trigger("click");
    await flushPromises();

    expect(calls.confirm).toHaveBeenCalledOnce();
    expect(calls.selectSequence).toHaveBeenCalledWith("project-1", "sequence-1", true);
    expect(calls.board).toHaveBeenCalledTimes(2);
  });

  it("only enables final download for the approved sequence selected by the project", async () => {
    const approved = mount(DeliveryWorkbench, {
      props: { projectId: "project-1", focusedItemId: "sequence-1", panel: "main" },
    });
    await flushPromises();
    expect(approved.get("[data-action='download-current']").attributes("disabled")).toBeUndefined();
    approved.unmount();

    const pending = board();
    pending.projectGraph.project.selectedSequenceId = null;
    pending.projectGraph.sequences[0].status = "content_review";
    calls.board.mockResolvedValue(pending);
    const review = mount(DeliveryWorkbench, {
      props: { projectId: "project-1", focusedItemId: "sequence-1", panel: "main" },
    });
    await flushPromises();
    expect(review.get("[data-action='download-current']").attributes("disabled")).toBeDefined();
  });

  it("resolves the legacy current-shot focus from the approved sequence without a second parent request", async () => {
    const wrapper = mount(DeliveryWorkbench, {
      props: { projectId: "project-1", focusedItemId: "current-shot", panel: "main" },
    });
    await flushPromises();

    expect(wrapper.get(".delivery-player").text()).toContain("发现纸星星");
    expect(wrapper.get(".delivery-player video").attributes("src")).toBe("/api/v1/assets/video-1/content");
  });
});
