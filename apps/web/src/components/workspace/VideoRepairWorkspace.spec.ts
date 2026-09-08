import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceDto } from "../../api/types";
import VideoRepairWorkspace from "./VideoRepairWorkspace.vue";

const state = vi.hoisted(() => ({ query: { draftId: "draft-1" } as Record<string, string>, replace: vi.fn() }));
vi.mock("vue-router", () => ({ useRoute: () => state, useRouter: () => ({ replace: state.replace }) }));
const client = vi.hoisted(() => ({
  videoEditDrafts: vi.fn(), videoEditDraft: vi.fn(), videoDraftJobs: vi.fn(), videoReviews: vi.fn(),
  videoRepairs: vi.fn(), assets: vi.fn(), edits: vi.fn(), runtime: vi.fn(), previewVideoRepair: vi.fn(),
  createVideoRepair: vi.fn(), renderDraftPreview: vi.fn(), saveVideoDraft: vi.fn(), selectAsset: vi.fn(),
}));
vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: client }));
const source = { id: "video-1", projectId: "project-1", role: "video", mediaType: "video", sha256: "a".repeat(64), byteSize: 1, metadata: { durationFrames: 289 }, createdAt: "2026-09-05T00:00:00Z" };
const workspace = { project: { id: "project-1", targetDurationSeconds: 12 }, selections: {}, eventCursor: 0 } as unknown as WorkspaceDto;
const head = { id: "edit-1", projectId: "project-1", editDraftId: "draft-1", revision: 1, timelineHash: "b".repeat(64), formatVersion: 2, edl: { videoSegments: [{ durationFrames: 289 }] }, createdAt: "2026-09-05T00:00:00Z" };
const draft = { id: "draft-1", sourceVideoAssetId: "video-1", headEditVersionId: "edit-1", referencesConfirmed: true };
const preview = { issueRange: { startFrame: 144, endFrame: 289 }, generationRange: { startFrame: 120, endFrame: 289 }, candidateCoreRange: { startFrame: 24, endFrame: 169 }, providerDurationSeconds: 8, editDraftId: "draft-1", endStatePolicy: "replace", desiredEndState: "饼干留在篮内", instruction: "不把饼干放到桌上", prompt: "片段内 1.000–7.042 秒", imageReferences: [], inputHash: "c".repeat(64) };
beforeEach(() => {
  vi.clearAllMocks(); state.query = { draftId: "draft-1" };
  client.videoEditDrafts.mockResolvedValue([draft]); client.videoEditDraft.mockResolvedValue(draft);
  client.assets.mockResolvedValue([source, { ...source, id: "base-preview", role: "edit_preview", metadata: { editVersionId: "edit-1" } }]);
  client.edits.mockResolvedValue([head]); client.videoRepairs.mockResolvedValue([]); client.videoDraftJobs.mockResolvedValue([]); client.videoReviews.mockResolvedValue([]);
  client.runtime.mockResolvedValue({ workerReady: true, worker: { ready: true }, provider: { name: "ark", apiKeyConfigured: true, paidCallsEnabled: true }, objectPublisher: { ready: true } });
  client.previewVideoRepair.mockResolvedValue(preview);
});
describe("persistent video edit drafts", () => {
  it("restores a source issue range without promoting its failed review", async () => {
    client.videoReviews.mockImplementation(async (_project, assetId) => assetId === "video-1" ? [{ id: "review-1", notes: "饼干被拿出", checks: { causalChainAndActiveEnding: "fail" }, issues: [{ range: { startFrame: 144, endFrame: 289 }, note: "饼干被拿出" }] }] : []);
    const view = mount(VideoRepairWorkspace, { props: { projectId: "project-1", workspace } });
    await flushPromises();
    expect(view.get('input[aria-label="入点帧（包含）"]').element).toHaveProperty("value", "144");
    expect(view.get('input[aria-label="出点帧（不包含）"]').element).toHaveProperty("value", "289");
    expect(view.findAll("select").some(select => select.element.value === "fail")).toBe(true);
    expect(client.selectAsset).not.toHaveBeenCalled();
    view.unmount();
  });
  it("allows an unselected video to be edited without pretending quality passed", async () => {
    const view = mount(VideoRepairWorkspace, { props: { projectId: "project-1", workspace } });
    await flushPromises();
    expect(view.text()).toContain("进入编辑不等于验收通过");
    expect(view.get('input[aria-label="出点帧（不包含）"]').attributes("max")).toBe("289");
    expect(client.selectAsset).not.toHaveBeenCalled();
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    expect(view.findAll("video")).toHaveLength(1);
    view.unmount();
  });
  it("recovers the frozen selection and unknown provider state without resubmitting", async () => {
    client.videoRepairs.mockResolvedValue([{ id: "repair-1", baseEditVersionId: "edit-1", status: "generating", issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.videoDraftJobs.mockResolvedValue([{ id: "job-1", videoRepairId: "repair-1", kind: "regenerate_video_segment", status: "submission_unknown", createdAt: "2026-09-05T00:00:00Z", resultAssetIds: [] }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: "project-1", workspace } });
    await flushPromises();
    expect(view.get('input[aria-label="入点帧（包含）"]').element).toHaveProperty("value", "144");
    expect(view.get('input[aria-label="出点帧（不包含）"]').element).toHaveProperty("value", "289");
    expect(view.get('input[aria-label="入点帧（包含）"]').attributes("disabled")).toBeDefined();
    expect(view.text()).toContain("提交状态需要人工确认");
    expect(view.text()).toContain("出点接缝检查不适用");
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    view.unmount();
  });
  it("renders a complete local preview but never automatically applies a candidate", async () => {
    client.videoRepairs.mockResolvedValue([{ id: "repair-1", baseEditVersionId: "edit-1", status: "candidate_ready", candidateAssetId: "candidate-1", issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.assets.mockResolvedValue([source, { ...source, id: "candidate-1", role: "repair_candidate", metadata: { durationFrames: 193, hasAudio: false } }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: "project-1", workspace } });
    await flushPromises();
    await view.findAll("button").find(button => button.text() === "准备试装对比（本地免费）")!.trigger("click");
    await flushPromises();
    expect(client.renderDraftPreview).toHaveBeenCalledWith("project-1", "draft-1", expect.objectContaining({ repairId: "repair-1", expectedEditVersionId: "edit-1", placement: { candidateSourceRange: { startFrame: 24, endFrame: 169 }, audioPolicy: "preserve_current", fadeInMs: 0, fadeOutMs: 0 } }));
    expect(client.saveVideoDraft).not.toHaveBeenCalled();
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    expect(client.selectAsset).not.toHaveBeenCalled();
    view.unmount();
  });

  it("views a returned candidate without locking the next edit or creating any job", async () => {
    client.videoRepairs.mockResolvedValue([{ id: 'repair-1', baseEditVersionId: 'edit-1', candidateAssetId: 'candidate-1', status: 'candidate_ready', issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.assets.mockResolvedValue([source, { ...source, id: 'candidate-1', role: 'repair_candidate', metadata: { durationFrames: 193, hasAudio: false } }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } });
    await flushPromises();
    expect(view.get('input[aria-label="入点帧（包含）"]').attributes('disabled')).toBeUndefined();
    await view.get('.candidate-card').trigger('click');
    await flushPromises();
    expect(view.get('video').attributes('src')).toContain('candidate-1');
    expect(view.text()).toContain('模型实际返回 193 帧');
    expect(client.renderDraftPreview).not.toHaveBeenCalled();
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    expect(client.saveVideoDraft).not.toHaveBeenCalled();
    view.unmount();
  });

  it('binds apply to the completed trial and marks a changed take as stale', async () => {
    client.videoRepairs.mockResolvedValue([{ id: 'repair-1', baseEditVersionId: 'edit-1', candidateAssetId: 'candidate-1', status: 'candidate_ready', issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.assets.mockResolvedValue([source, { ...source, id: 'candidate-1', role: 'repair_candidate', metadata: { durationFrames: 193, hasAudio: false } }, { ...source, id: 'trial-video', role: 'edit_preview', producingJobId: 'trial-1', metadata: { repairId: 'repair-1' } }]);
    client.videoDraftJobs.mockResolvedValue([{ id: 'trial-1', kind: 'render_edit_preview', status: 'succeeded', createdAt: '2026-09-05T00:00:00Z', resultAssetIds: ['trial-video'], frozenInput: { repairId: 'repair-1', placement: { candidateSourceRange: { startFrame: 24, endFrame: 169 }, audioPolicy: 'preserve_current', fadeInMs: 0, fadeOutMs: 0 } } }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } });
    await flushPromises();
    const apply = view.findAll('button').find(button => button.text().startsWith('应用此试装'))!;
    expect(apply.attributes('disabled')).toBeUndefined();
    const take = view.get('.trial-fields input[type="number"]');
    await take.setValue(25);
    expect(view.text()).toContain('对应上一份试装');
    expect(apply.attributes('disabled')).toBeDefined();
    await take.setValue(24);
    await apply.trigger('click'); await flushPromises();
    expect(client.saveVideoDraft).toHaveBeenCalledWith('project-1', 'draft-1', expect.objectContaining({ previewJobId: 'trial-1', expectedEditVersionId: 'edit-1' }));
    expect(client.saveVideoDraft.mock.calls[0][2]).not.toHaveProperty('edl');
    expect(client.selectAsset).not.toHaveBeenCalled();
    view.unmount();
  });
});
