import { reactive } from 'vue';
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceDto } from "../../api/types";
import VideoRepairWorkspace from "./VideoRepairWorkspace.vue";
import VideoEditPlanner from './VideoEditPlanner.vue';
import { ApiError } from '../../api/client';

const state = vi.hoisted(() => ({ query: { draftId: "draft-1" } as Record<string, string>, replace: vi.fn() }));
vi.mock("vue-router", () => ({ useRoute: () => reactive(state), useRouter: () => ({ replace: state.replace }) }));
const client = vi.hoisted(() => ({
  videoEditDrafts: vi.fn(), videoEditDraft: vi.fn(), videoDraftJobs: vi.fn(), videoReviews: vi.fn(),
  videoRepairs: vi.fn(), assets: vi.fn(), edits: vi.fn(), runtime: vi.fn(), previewVideoRepair: vi.fn(),
  createVideoRepair: vi.fn(), renderDraftPreview: vi.fn(), saveVideoDraft: vi.fn(), selectAsset: vi.fn(),
  prepareRepairResult: vi.fn(), job: vi.fn(), prepareSegmentReferences: vi.fn(), saveVideoEditInput: vi.fn(), planVideoEdit: vi.fn(), jobResult: vi.fn(),
}));
vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: client }));
const source = { id: "video-1", projectId: "project-1", role: "video", mediaType: "video", sha256: "a".repeat(64), byteSize: 1, metadata: { durationFrames: 289 }, createdAt: "2026-09-05T00:00:00Z" };
const workspace = { project: { id: "project-1", targetDurationSeconds: 12 }, selections: {}, eventCursor: 0 } as unknown as WorkspaceDto;
const head = { id: "edit-1", projectId: "project-1", editDraftId: "draft-1", revision: 1, timelineHash: "b".repeat(64), formatVersion: 2, edl: { videoSegments: [{ durationFrames: 289 }] }, createdAt: "2026-09-05T00:00:00Z" };
const draft = { id: "draft-1", sourceVideoAssetId: "video-1", headEditVersionId: "edit-1", referencesConfirmed: true };
const preview = { issueRange: { startFrame: 144, endFrame: 289 }, generationRange: { startFrame: 120, endFrame: 289 }, candidateCoreRange: { startFrame: 24, endFrame: 169 }, providerDurationSeconds: 8, editDraftId: "draft-1", endStatePolicy: "replace", desiredEndState: "饼干留在篮内", instruction: "不把饼干放到桌上", prompt: "片段内 1.000–7.042 秒", imageReferences: [], inputHash: "c".repeat(64) };
beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks(); state.query = { draftId: "draft-1" };
  state.replace.mockImplementation(async ({ query }) => { reactive(state).query = query; });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  client.videoEditDrafts.mockResolvedValue([draft]); client.videoEditDraft.mockResolvedValue(draft);
  client.assets.mockResolvedValue([source, { ...source, id: "base-preview", role: "edit_preview", metadata: { editVersionId: "edit-1" } }]);
  client.edits.mockResolvedValue([head]); client.videoRepairs.mockResolvedValue([]); client.videoDraftJobs.mockResolvedValue([]); client.videoReviews.mockResolvedValue([]);
  client.runtime.mockResolvedValue({ workerReady: true, worker: { ready: true }, provider: { name: "ark", apiKeyConfigured: true, paidCallsEnabled: true }, objectPublisher: { ready: true } });
  client.previewVideoRepair.mockResolvedValue(preview);
  client.saveVideoEditInput.mockImplementation(async (_project, _draft, command) => ({ ...draft, inputRevision: command.expectedRevision + 1, editingInput: command.editingInput }));
  client.prepareSegmentReferences.mockResolvedValue({ id: 'refs-1', kind: 'extract_continuity_frames', status: 'succeeded', createdAt: '2026-09-10T00:00:00Z', frozenInput: {} });
  client.prepareRepairResult.mockResolvedValue({ id: 'local-result', kind: 'render_edit_preview', status: 'queued', frozenInput: {} });
  client.job.mockResolvedValue({ id: 'job-1', status: 'submission_unknown', revision: 0, execution: { availableActions: ['prepare_replacement'], resultState: 'missing' } });
});
afterEach(() => { vi.useRealTimers(); });
describe("persistent video edit drafts", () => {
  it('keeps an explicit play-from-start position when an older view finishes loading', async () => {
    vi.useFakeTimers();
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    state.query = { draftId: 'draft-1', candidateId: 'repair-1', view: 'result' };
    client.videoRepairs.mockResolvedValue([{ id: 'repair-1', baseEditVersionId: 'edit-1', candidateAssetId: 'candidate-1', status: 'candidate_ready', issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.assets.mockResolvedValue([source, { ...source, id: 'candidate-1', role: 'repair_candidate', metadata: { durationFrames: 193, hasAudio: false } }, { ...source, id: 'trial-video', role: 'edit_preview', producingJobId: 'trial-1', metadata: { repairId: 'repair-1', durationFrames: 289 } }, ...['edit_result_before', 'edit_result_after'].map(role => ({ ...source, id: role, role, producingJobId: 'trial-1', metadata: { durationFrames: 145 } }))]);
    client.videoDraftJobs.mockResolvedValue([{ id: 'trial-1', kind: 'render_edit_preview', status: 'succeeded', createdAt: '2026-09-05T00:00:00Z', resultAssetIds: ['trial-video'], frozenInput: { repairId: 'repair-1', placement: { candidateSourceRange: { startFrame: 24, endFrame: 169 }, audioPolicy: 'preserve_current', fadeInMs: 0, fadeOutMs: 0 } } }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } });
    await flushPromises();
    const segment = view.get<HTMLVideoElement>('.player-stage video');
    segment.element.currentTime = 2;
    await segment.trigger('timeupdate');
    await view.findAll('button').find(button => button.text() === '查看完整效果')!.trigger('click');
    const full = view.get<HTMLVideoElement>('.player-stage video');
    await view.findAll('button').find(button => button.text() === '从头播放')!.trigger('click');
    await full.trigger('seeked');
    await flushPromises();
    // The late initial load must not restore the previous view's nonzero frame.
    await full.trigger('loadeddata');
    expect(full.element.currentTime).toBeLessThan(1 / 24);
    await vi.advanceTimersByTimeAsync(1500);
    view.unmount();
  });
  it('ignores a normal submission receipt after unmount', async () => {
    let resolvePost!: (job: unknown) => void;
    client.createVideoRepair.mockImplementationOnce(() => new Promise(resolve => { resolvePost = resolve; }));
    client.previewVideoRepair.mockResolvedValue({ ...preview, referencePreparationJobId: 'refs-1' });
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('环境渐渐明亮');
    await view.get('[data-testid="prepare-references"]').trigger('click'); await flushPromises();
    await view.findAll('button').find(button => button.text() === '生成修改结果（付费）')!.trigger('click'); await flushPromises();
    expect(client.createVideoRepair).toHaveBeenCalledTimes(1);
    view.unmount(); state.replace.mockClear();
    resolvePost({ id: 'late-job', projectId: 'project-1', kind: 'regenerate_video_segment', videoRepairId: 'late-repair', status: 'queued', frozenInput: preview });
    await flushPromises();
    expect(state.replace).not.toHaveBeenCalled();
  });
  it('unlocks after a definite 409 blocker completes without automatically resubmitting', async () => {
    const blocker = { id: 'blocking-job', kind: 'regenerate_video_segment', status: 'polling', createdAt: '2026-09-10T00:00:00Z' };
    client.job.mockResolvedValue({ id: 'refs-1', kind: 'extract_continuity_frames', status: 'succeeded', frozenInput: {} });
    client.previewVideoRepair.mockResolvedValue({ ...preview, referencePreparationJobId: 'refs-1' });
    client.createVideoRepair.mockImplementationOnce(async () => {
      client.videoDraftJobs.mockResolvedValue([blocker]);
      throw new ApiError(409, { code: 'video_edit_in_progress', message: 'An edit is already running', blockingJobId: blocker.id, editDraftId: draft.id, videoRepairId: 'blocking-repair' });
    });
    let view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace }, global: { stubs: { JobStatusCard: true } } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('环境渐渐明亮');
    await view.get('[data-testid="prepare-references"]').trigger('click'); await flushPromises();
    await view.findAll('button').find(button => button.text() === '生成修改结果（付费）')!.trigger('click'); await flushPromises();
    expect(client.createVideoRepair).toHaveBeenCalledTimes(1);
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeDefined();
    client.videoDraftJobs.mockResolvedValue([{ ...blocker, status: 'succeeded' }]);
    await view.setProps({ workspace: { ...workspace, eventCursor: 1 } }); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeUndefined();
    view.unmount();
    view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace }, global: { stubs: { JobStatusCard: true } } }); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeUndefined();
    expect(client.createVideoRepair).toHaveBeenCalledTimes(1);
    view.unmount();
  });
  it('isolates uncertain normal submissions by draft and restores them after navigation and remount', async () => {
    const second = { ...draft, id: 'draft-2', headEditVersionId: 'edit-2' };
    client.edits.mockResolvedValue([head, { ...head, id: 'edit-2', editDraftId: 'draft-2' }]);
    client.videoEditDraft.mockImplementation(async (_project, id) => id === second.id ? second : draft);
    client.previewVideoRepair.mockResolvedValue({ ...preview, referencePreparationJobId: 'refs-1' });
    client.createVideoRepair.mockRejectedValueOnce(new Error('receipt lost'));
    let view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace }, global: { stubs: { JobStatusCard: true } } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('环境渐渐明亮');
    await view.get('[data-testid="prepare-references"]').trigger('click'); await flushPromises();
    await view.findAll('button').find(button => button.text() === '生成修改结果（付费）')!.trigger('click'); await flushPromises();
    expect(client.createVideoRepair).toHaveBeenCalledTimes(1);
    const command = client.createVideoRepair.mock.calls[0][1];
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeDefined();
    await state.replace({ query: { draftId: second.id } }); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeUndefined();
    await state.replace({ query: { draftId: draft.id } }); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeDefined();
    view.unmount();
    view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace }, global: { stubs: { JobStatusCard: true } } }); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeDefined();
    expect(client.createVideoRepair).toHaveBeenCalledTimes(1);
    client.videoDraftJobs.mockResolvedValue([{ id: 'recovered-job', projectId: 'project-1', kind: 'regenerate_video_segment', videoRepairId: 'recovered-repair', status: 'succeeded', createdAt: '2026-09-10T00:00:00Z', idempotencyKey: command.idempotencyKey, inputHash: command.expectedInputHash, frozenInput: { ...preview, baseEditVersionId: 'edit-1' } }]);
    await view.setProps({ workspace: { ...workspace, eventCursor: 1 } }); await flushPromises();
    expect(state.query.candidateId).toBe('recovered-repair');
    await view.findAll('button').find(button => button.text() === '修改当前草稿其他区间')!.trigger('click'); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeUndefined();
    expect(client.createVideoRepair).toHaveBeenCalledTimes(1);
    view.unmount();
  });
  it.each(['stale-list', 'failed-list'])('selects an accepted replacement and retains its progress after %s', async failure => {
    state.query = { draftId:'draft-1', candidateId:'old-repair', view:'result' };
    const snapshot={...preview,baseEditVersionId:'edit-1',baseVideoAssetId:'video-1',imageReferences:[]};
    const original={id:'old-repair',baseEditVersionId:'edit-1',status:'generating',instruction:'old',issueRange:preview.issueRange,preview:snapshot};
    const oldJob={id:'old-job',kind:'regenerate_video_segment',status:'submission_unknown',videoRepairId:original.id,createdAt:'2026-09-10T00:00:00Z',frozenInput:snapshot};
    client.videoRepairs.mockResolvedValue([original]); client.videoDraftJobs.mockResolvedValue([oldJob]);
    const view=mount(VideoRepairWorkspace,{props:{projectId:'project-1',workspace},global:{stubs:{JobStatusCard:true}}}); await flushPromises();
    const created={...oldJob,id:'accepted-job',projectId:'project-1',videoRepairId:'accepted-repair',status:'queued',createdAt:'2026-09-10T01:00:00Z'};
    if (failure==='failed-list') client.videoRepairs.mockRejectedValue(new Error('offline'));
    view.getComponent({name:'JobStatusCard'}).vm.$emit('replacement',created); await flushPromises();
    expect(state.query.candidateId).toBe('accepted-repair'); expect(state.query.view).toBe('result');
    expect(view.getComponent({name:'JobStatusCard'}).props('jobId')).toBe('accepted-job');
    expect(view.get('[aria-label="局部修改执行进度"]').text()).toContain('accepted-job');
    expect(view.get('.candidate-card[aria-pressed="true"]').text()).not.toContain('提交结果待核实');
    expect(client.createVideoRepair).not.toHaveBeenCalled(); view.unmount();
  });
  it.each(['submission_unknown','polling'])('only releases historical superseded unknown locks: %s', async status => {
    client.videoDraftJobs.mockResolvedValue([{id:'old',kind:'regenerate_video_segment',status,successorJobIds:['done'],createdAt:'2026-09-10T00:00:00Z'},{id:'done',kind:'regenerate_video_segment',status:'succeeded',createdAt:'2026-09-10T01:00:00Z'}]);
    const view=mount(VideoRepairWorkspace,{props:{projectId:'project-1',workspace},global:{stubs:{JobStatusCard:true}}}); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')!==undefined).toBe(status==='polling'); view.unmount();
  });
  it('keeps an old URL reference and unsaved editor independent from the selected frozen 96-frame task', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    state.query = { draftId: 'draft-1', referenceJobId: 'old-83-reference' };
    const oldReference = { id: 'old-83-reference', status: 'succeeded', frozenInput: { command: { editContractVersion: 2, issueRange: { startFrame: 278, endFrame: 361 }, instruction: '旧参考对应文字' } } };
    const currentReference = { id: 'actual-96-reference', status: 'succeeded', frozenInput: {} };
    const result = { id: 'source-result', kind: 'render_edit_preview', status: 'succeeded', frozenInput: { timelineHash: 'source-timeline', resultRange: { startFrame: 265, endFrame: 361 } } };
    const frozen = { ...preview, baseEditVersionId: 'edit-1', baseVideoAssetId: 'video-1', sourceResultJobId: result.id, referencePreparationJobId: currentReference.id, issueRange: { startFrame: 265, endFrame: 361 }, generationRange: { startFrame: 265, endFrame: 361 }, candidateCoreRange: { startFrame: 0, endFrame: 96 }, durationSeconds: 4, videoReference: { assetId: 'actual-96-video' }, instruction: '冻结任务文字', compiledProviderPrompt: '冻结的最终指令', referenceRoles: ['anchor_in', 'environment'], imageReferences: [{ role: 'anchor_in', assetId: 'in-frame', frameNumber: 265 }, { role: 'environment', assetId: 'frozen-room' }] };
    client.edits.mockResolvedValue([{ ...head, edl: { videoSegments: [{ durationFrames: 361 }] } }]);
    client.job.mockImplementation(async id => id === oldReference.id ? oldReference : id === currentReference.id ? currentReference : result);
    client.assets.mockResolvedValue([source, { ...source, id: 'base-preview', role: 'edit_preview', metadata: { editVersionId: 'edit-1' } }, { ...source, id: 'old-83-video', producingJobId: oldReference.id, role: 'repair_context', metadata: { durationFrames: 83, sourceStartFrame: 278, sourceEndFrame: 361 } }, { ...source, id: 'actual-96-video', producingJobId: currentReference.id, role: 'repair_context', metadata: { durationFrames: 96, sourceStartFrame: 265, sourceEndFrame: 361 } }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace }, global: { stubs: { JobStatusCard: true } } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('尚未保存的当前输入');
    client.videoRepairs.mockResolvedValue([{ id: 'repair-new', baseEditVersionId: 'edit-1', status: 'generating', instruction: '错误的旁路文字', issueRange: frozen.issueRange, preview: { ...preview, instruction: '旁路历史文字' } }]);
    client.videoDraftJobs.mockResolvedValue([{ id: 'paid-job', videoRepairId: 'repair-new', kind: 'regenerate_video_segment', status: 'submission_unknown', revision: 4, createdAt: '2026-09-10T08:47:18Z', frozenInput: frozen }]);
    await view.setProps({ workspace: { ...workspace, eventCursor: 2 } }); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('尚未保存的当前输入');
    const details = view.get('[aria-label="所选任务冻结输入"]');
    expect(details.text()).toContain('[265, 361) · 96 帧');
    expect(details.text()).toContain('actual-96-reference');
    expect(details.text()).toContain('source-result');
    expect(details.text()).toContain('冻结任务文字');
    expect(details.get('video').attributes('src')).toContain('actual-96-video');
    expect(details.text()).not.toContain('old-83');
    expect(details.get('.compiled-provider-prompt').text()).toBe('冻结的最终指令');
    expect(view.get('.candidate-card').text()).toContain('提交结果待核实');
    await view.get('.candidate-card').trigger('click'); await flushPromises();
    expect(view.get('.viewing-area [role="status"]').text()).toContain('提交结果待核实');
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    await details.findAll('button').find(button => button.text() === '复用参数到当前修改方案')!.trigger('click'); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('冻结任务文字');
    expect(view.get('[aria-label="实际参考准备"]').get('video').attributes('src')).toContain('actual-96-video');
    await view.findAll('button').find(button => button.text() === '保存当前输入')!.trigger('click'); await flushPromises();
    expect(client.saveVideoEditInput).toHaveBeenLastCalledWith('project-1', 'draft-1', expect.objectContaining({ editingInput: expect.objectContaining({ sourceResultJobId: result.id, referencePreparationJobId: currentReference.id, referenceRoles: ['environment'], instruction: '冻结任务文字' }) }));
    view.unmount();
  });
  it.each([
    ['debounced', 'route'], ['debounced', 'button'], ['in-flight', 'route'], ['in-flight', 'button'],
  ])('persists the latest A input only to A through immediate navigation: %s / %s', async (timing, navigation) => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const second = { ...draft, id: 'draft-2', headEditVersionId: 'edit-2' };
    const stored = new Map([
      ['draft-1', { ...draft, inputRevision: 0, editingInput: {} as Record<string, unknown> }],
      ['draft-2', { ...second, inputRevision: 0, editingInput: {} as Record<string, unknown> }],
    ]);
    client.videoEditDrafts.mockResolvedValue([draft, second]);
    client.edits.mockResolvedValue([head, { ...head, id: 'edit-2', editDraftId: 'draft-2' }]);
    client.videoEditDraft.mockImplementation(async (_project, id) => stored.get(id));
    const persist = async (_project: string, id: string, command: { expectedRevision: number; editingInput: Record<string, unknown> }) => {
      const previous = stored.get(id)!;
      expect(command.expectedRevision).toBe(previous.inputRevision);
      const saved = { ...previous, editingInput: command.editingInput, inputRevision: previous.inputRevision + 1 };
      stored.set(id, saved); return saved;
    };
    client.saveVideoEditInput.mockImplementation(persist);
    let finishFirst!: () => Promise<void>;
    if (timing === 'in-flight') client.saveVideoEditInput.mockImplementationOnce((project, id, command) => new Promise(resolve => {
      finishFirst = async () => { resolve(await persist(project, id, command)); };
    }));
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('第一版文字');
    if (timing === 'in-flight') {
      await view.findAll('button').find(button => button.text() === '保存当前输入')!.trigger('click'); await flushPromises();
      expect(client.saveVideoEditInput).toHaveBeenCalledTimes(1);
    }
    await view.get('[aria-label="修改目标"]').setValue('离开前的最新文字');
    if (navigation === 'button') {
      await state.replace({ query: {} }); await flushPromises();
      await view.findAll('button').filter(button => button.text().startsWith('继续草稿'))[1].trigger('click');
    } else await state.replace({ query: { draftId: 'draft-2' } });
    await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('');
    expect(view.findAll('button').find(button => button.text() === '保存当前输入')!.attributes('disabled')).toBeUndefined();
    if (timing === 'in-flight') {
      await state.replace({ query: { draftId: 'draft-1' } }); await flushPromises();
      expect(view.find('[aria-label="修改目标"]').exists()).toBe(false);
      await finishFirst(); await flushPromises();
    }
    await vi.advanceTimersByTimeAsync(601); await flushPromises();
    await state.replace({ query: { draftId: 'draft-1' } }); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('离开前的最新文字');
    expect(stored.get('draft-1')!.editingInput.instruction).toBe('离开前的最新文字');
    expect(stored.get('draft-2')!.editingInput).toEqual({});
    expect(client.saveVideoEditInput.mock.calls.every(([project, id, command]) => project === 'project-1' && id === 'draft-1' && command.editingInput.editDraftId === 'draft-1' && command.editingInput.baseEditVersionId === 'edit-1')).toBe(true);
    expect(client.saveVideoEditInput).toHaveBeenCalledTimes(timing === 'in-flight' ? 2 : 1);
    expect(client.createVideoRepair).not.toHaveBeenCalled(); expect(client.planVideoEdit).not.toHaveBeenCalled();
    view.unmount();
  });
  it.each(['conflict', 'cancelled-read'])('retains and exposes a failed navigation save for recovery: %s', async failure => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const second = { ...draft, id: 'draft-2', headEditVersionId: 'edit-2' };
    client.edits.mockResolvedValue([head, { ...head, id: 'edit-2', editDraftId: 'draft-2' }]);
    client.videoEditDraft.mockImplementation(async (_project, id) => id === 'draft-2' ? second : draft);
    client.saveVideoEditInput.mockRejectedValueOnce(failure === 'conflict' ? new ApiError(409, 'input revision conflict') : new DOMException('request aborted', 'AbortError'));
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('保存失败也不能丢失的文字');
    await state.replace({ query: { draftId: 'draft-2' } }); await flushPromises();
    expect(view.text()).toContain('草稿 draft-1 的输入尚未保存');
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('');
    await state.replace({ query: { draftId: 'draft-1' } }); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('保存失败也不能丢失的文字');
    expect(client.saveVideoEditInput).toHaveBeenCalledTimes(1);
    if (failure === 'conflict') {
      expect(view.text()).toContain('另一处已更新这份编辑输入');
      client.videoEditDraft.mockResolvedValue({ ...draft, inputRevision: 4, editingInput: { instruction: '远端文字' } });
      await view.findAll('button').find(button => button.text() === '读取已保存输入进行比较')!.trigger('click'); await flushPromises();
      await view.findAll('button').find(button => button.text() === '保留当前输入并保存为新修订')!.trigger('click'); await flushPromises();
    } else {
      await view.findAll('button').find(button => button.text() === '保存当前输入')!.trigger('click'); await flushPromises();
    }
    expect(client.saveVideoEditInput).toHaveBeenLastCalledWith('project-1', 'draft-1', expect.objectContaining({ expectedRevision: failure === 'conflict' ? 4 : 0, editingInput: expect.objectContaining({ instruction: '保存失败也不能丢失的文字' }) }));
    expect(view.text()).not.toContain('草稿 draft-1 的输入尚未保存');
    expect(view.text()).toContain('编辑输入已保存');
    view.unmount();
  });
  it('locks the first form until delayed saved-input hydration finishes', async () => {
    let finishReviews!: (value: never[]) => void;
    client.videoReviews.mockReturnValue(new Promise(resolve => { finishReviews = resolve; }));
    client.videoEditDraft.mockResolvedValue({ ...draft, editingInput: { editContractVersion: 2, baseVideoAssetId: 'video-1', baseEditVersionId: 'edit-1', issueRange: { startFrame: 10, endFrame: 30 }, instruction: '已保存文字' } });
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeDefined();
    expect(view.get('[aria-label="生成方式"]').attributes('disabled')).toBeDefined();
    expect(view.text()).toContain('正在恢复草稿输入');
    finishReviews([]); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeUndefined();
    await view.get('[aria-label="修改目标"]').setValue('恢复后新写的文字');
    await view.findAll('button').find(button => button.text() === '保存当前输入')!.trigger('click'); await flushPromises();
    expect(client.saveVideoEditInput).toHaveBeenLastCalledWith('project-1', 'draft-1', expect.objectContaining({ editingInput: expect.objectContaining({ instruction: '恢复后新写的文字' }) }));
    view.unmount();
  });
  it('preserves new text and the range during a delayed same-draft refresh', async () => {
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    let finishRefresh!: (value: typeof draft) => void;
    client.videoEditDraft.mockReturnValueOnce(new Promise(resolve => { finishRefresh = resolve; }));
    await view.setProps({ workspace: { ...workspace, eventCursor: 1 } }); await flushPromises();
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeUndefined();
    await view.get('[aria-label="修改目标"]').setValue('刷新时新写的文字');
    await view.get('[aria-label="选区入点帧"]').setValue('25');
    finishRefresh({ ...draft, editingInput: { instruction: '服务器旧文字' } } as typeof draft); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('刷新时新写的文字');
    expect(view.get<HTMLInputElement>('[aria-label="选区入点帧"]').element.value).toBe('25');
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeUndefined();
    view.unmount();
  });
  it('resets the whole editing document when opening an empty second draft', async () => {
    const secondDraft = { ...draft, id: 'draft-2', headEditVersionId: 'edit-2' };
    client.edits.mockResolvedValue([head, { ...head, id: 'edit-2', editDraftId: 'draft-2' }]);
    client.videoEditDrafts.mockResolvedValue([draft, secondDraft]);
    client.videoEditDraft.mockImplementation(async (_project, id) => id === 'draft-2' ? secondDraft : { ...draft, editingInput: { editContractVersion: 1, baseVideoAssetId: 'video-1', baseEditVersionId: 'edit-1', issueRange: { startFrame: 14, endFrame: 39 }, instruction: '旧草稿目标', preserveContent: '旧保留', startState: '旧起点', actionProcess: '旧过程', desiredEndState: '旧结束', avoidProblems: '旧避免', generationMode: 'from_frame', endStatePolicy: 'replace', anchorStartFrame: 14, anchorEndFrame: 38, audioMode: 'generate_candidate', soundDescription: '旧声音', includeInAnchor: false, contextMode: 'custom', contextRange: { startFrame: 0, endFrame: 80 }, plannerJobId: 'old-plan', planSourceJobId: 'adopted-plan' } });
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace }, global: { stubs: { JobStatusCard: true } } }); await flushPromises();
    await state.replace({ query: {} }); await flushPromises();
    await view.findAll('button').filter(button => button.text().startsWith('继续草稿'))[1].trigger('click'); await flushPromises();
    for (const label of ['修改目标', '保留内容', '起始状态', '动作过程', '期望结束状态', '需要避免的问题']) expect(view.get<HTMLTextAreaElement>(`[aria-label="${label}"]`).element.value).toBe('');
    expect(view.get<HTMLSelectElement>('[aria-label="结束状态策略"]').element.value).toBe('follow_instruction');
    expect(view.getComponent(VideoEditPlanner).props('jobId')).toBe('');
    await view.get('[aria-label="修改目标"]').setValue('第二份草稿目标');
    await view.findAll('button').find(button => button.text() === '保存当前输入')!.trigger('click'); await flushPromises();
    expect(client.saveVideoEditInput).toHaveBeenLastCalledWith('project-1', 'draft-2', expect.objectContaining({ editingInput: expect.objectContaining({ editContractVersion: 2, generationMode: 'edit_existing', endStatePolicy: 'follow_instruction', planSourceJobId: null, plannerJobId: '', referencePreparationJobId: null, anchorStartFrame: null, anchorEndFrame: null, audioMode: 'preserve_current', soundDescription: '', includeInAnchor: true, contextMode: 'auto' }) }));
    view.unmount();
  });
  it('keeps the explicitly continued result range and later text through same-draft refreshes', async () => {
    state.query = { draftId: 'draft-1', candidateId: 'repair-1', view: 'result' };
    const resultRange = { startFrame: 144, endFrame: 289 };
    client.videoRepairs.mockResolvedValue([{ id: 'repair-1', baseEditVersionId: 'edit-1', candidateAssetId: 'candidate-1', status: 'candidate_ready', issueRange: resultRange, instruction: preview.instruction, preview }]);
    client.assets.mockResolvedValue([source, { ...source, id: 'candidate-1', role: 'repair_candidate', metadata: { durationFrames: 193, hasAudio: false } }, { ...source, id: 'trial-video', role: 'edit_preview', producingJobId: 'trial-1', metadata: { repairId: 'repair-1' } }, ...['edit_result_before', 'edit_result_after'].map(role => ({ ...source, id: role, role, producingJobId: 'trial-1' }))]);
    const completed = { id: 'trial-1', kind: 'render_edit_preview', status: 'succeeded', createdAt: '2026-09-05T00:00:00Z', resultAssetIds: ['trial-video'], frozenInput: { repairId: 'repair-1', resultRange, timelineHash: head.timelineHash, placement: { candidateSourceRange: { startFrame: 24, endFrame: 169 }, audioPolicy: 'preserve_current', fadeInMs: 0, fadeOutMs: 0 } } };
    client.videoDraftJobs.mockResolvedValue([completed]); client.job.mockResolvedValue(completed);
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    await view.findAll('button').find(button => button.text() === '继续修改此结果')!.trigger('click'); await flushPromises();
    expect(view.get<HTMLInputElement>('[aria-label="选区入点帧"]').element.value).toBe('144');
    expect(view.get<HTMLInputElement>('[aria-label="选区出点帧"]').element.value).toBe('289');
    await view.get('[aria-label="修改目标"]').setValue('继续结果的新目标');
    await view.setProps({ workspace: { ...workspace, eventCursor: 1 } }); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('继续结果的新目标');
    expect(view.get<HTMLInputElement>('[aria-label="选区入点帧"]').element.value).toBe('144');
    expect(view.get('[aria-label="修改目标"]').attributes('disabled')).toBeUndefined();
    expect(client.createVideoRepair).not.toHaveBeenCalled(); expect(client.planVideoEdit).not.toHaveBeenCalled();
    view.unmount();
  });
  it('ignores delayed old-draft jobs after navigating to another draft', async () => {
    let finishReference!: (value: unknown) => void;
    client.job.mockReturnValue(new Promise(resolve => { finishReference = resolve; }));
    client.videoEditDraft.mockResolvedValueOnce({ ...draft, editingInput: { editContractVersion: 2, baseVideoAssetId: 'video-1', baseEditVersionId: 'edit-1', issueRange: { startFrame: 10, endFrame: 30 }, instruction: '旧草稿已保存文字', referencePreparationJobId: 'old-refs' } });
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    client.edits.mockResolvedValue([{ ...head, id: 'edit-2', editDraftId: 'draft-2' }]);
    client.videoEditDraft.mockResolvedValue({ ...draft, id: 'draft-2', headEditVersionId: 'edit-2' });
    await state.replace({ query: { draftId: 'draft-2' } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('新草稿正在写');
    finishReference({ id: 'old-refs', status: 'succeeded', frozenInput: { command: { instruction: '旧参考文字' } } }); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('新草稿正在写');
    expect(view.find('[data-testid="refresh-edit-prompt"]').exists()).toBe(false);
    view.unmount();
  });
  it.each(['success', 'failure', 'newer-edit'])('recovers CAS input and its different job associations atomically: %s', async outcome => {
    client.saveVideoEditInput.mockRejectedValueOnce(new ApiError(409, 'input revision conflict'));
    const savedCommand = { editContractVersion: 2, baseVideoAssetId: 'video-1', baseEditVersionId: 'edit-1', issueRange: { startFrame: 10, endFrame: 30 }, instruction: '当前已保存', plannerJobId: 'plan-old', referencePreparationJobId: 'refs-old' };
    client.videoEditDraft.mockResolvedValue({ ...draft, inputRevision: 2, editingInput: savedCommand });
    client.job.mockImplementation(async id => ({ id, status: 'succeeded', frozenInput: {} }));
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace }, global: { stubs: { JobStatusCard: true } } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('我的当前输入');
    await view.findAll('button').find(button => button.text() === '保存当前输入')!.trigger('click'); await flushPromises();
    client.videoEditDraft.mockResolvedValue({ ...draft, inputRevision: 5, editingInput: { ...savedCommand, instruction: '选中的远端输入', plannerJobId: 'plan-remote', referencePreparationJobId: 'refs-remote' } });
    await view.findAll('button').find(button => button.text() === '读取已保存输入进行比较')!.trigger('click'); await flushPromises();
    let finish!: (value: unknown) => void, reject!: (reason: unknown) => void;
    client.job.mockReturnValueOnce(new Promise((resolve, fail) => { finish = resolve; reject = fail; }));
    await view.findAll('button').find(button => button.text() === '载入这份输入')!.trigger('click'); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('我的当前输入');
    if (outcome === 'newer-edit') await view.get('[aria-label="修改目标"]').setValue('恢复过程中继续编辑');
    if (outcome === 'failure') reject(new Error('reference temporarily unavailable'));
    else finish({ id: 'refs-remote', status: 'succeeded', frozenInput: {} });
    await flushPromises();
    if (outcome === 'success') {
      expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('选中的远端输入');
      expect(view.getComponent(VideoEditPlanner).props('jobId')).toBe('plan-remote');
      expect(view.find('[data-testid="refresh-edit-prompt"]').exists()).toBe(true);
      await view.get('[aria-label="修改目标"]').setValue('远端输入基础上的新修改');
      await view.findAll('button').find(button => button.text() === '保存当前输入')!.trigger('click'); await flushPromises();
      expect(client.saveVideoEditInput).toHaveBeenLastCalledWith('project-1', 'draft-1', expect.objectContaining({ expectedRevision: 5, editingInput: expect.objectContaining({ instruction: '远端输入基础上的新修改', plannerJobId: 'plan-remote', referencePreparationJobId: 'refs-remote' }) }));
    } else {
      expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe(outcome === 'failure' ? '我的当前输入' : '恢复过程中继续编辑');
      expect(view.getComponent(VideoEditPlanner).props('jobId')).toBe('plan-old');
      expect(view.findAll('button').find(button => button.text() === '保存当前输入')!.attributes('disabled')).toBeDefined();
    }
    expect(client.planVideoEdit).not.toHaveBeenCalled();
    view.unmount();
  });

  it('defaults to following the instruction and reuses prepared media after text changes', async () => {
    client.previewVideoRepair.mockResolvedValue({ ...preview, referencePreparationJobId: 'refs-1', mediaInputHash: 'm'.repeat(64) });
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } });
    await flushPromises();
    expect(view.get<HTMLSelectElement>('[aria-label="结束状态策略"]').element.value).toBe('follow_instruction');
    await view.get('[aria-label="修改目标"]').setValue('人物坐定，保持安静');
    await view.get('[data-testid="prepare-references"]').trigger('click'); await flushPromises();
    expect(client.prepareSegmentReferences).toHaveBeenCalledWith('project-1', expect.objectContaining({ editContractVersion: 2, endStatePolicy: 'follow_instruction' }));
    await view.get('[aria-label="修改目标"]').setValue('人物坐定，双手自然放下，保持安静');
    expect(view.find('.provider-prompt').exists()).toBe(false);
    await view.get('[data-testid="refresh-edit-prompt"]').trigger('click'); await flushPromises();
    expect(client.prepareSegmentReferences).toHaveBeenCalledTimes(1);
    expect(client.previewVideoRepair).toHaveBeenLastCalledWith('project-1', expect.objectContaining({ referencePreparationJobId: 'refs-1', instruction: '人物坐定，双手自然放下，保持安静' }));
    expect(client.planVideoEdit).not.toHaveBeenCalled();
    view.unmount();
  });
  it('restores saved editable text and a short range without adopting advice', async () => {
    client.videoEditDraft.mockResolvedValue({ ...draft, inputRevision: 3, editingInput: { editContractVersion: 2, baseVideoAssetId: 'video-1', baseEditVersionId: 'edit-1', issueRange: { startFrame: 206, endFrame: 289 }, instruction: '保留我最后写的文字', preserveContent: '灯光', endStatePolicy: 'follow_instruction' } });
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('保留我最后写的文字');
    expect(view.get<HTMLTextAreaElement>('[aria-label="保留内容"]').element.value).toBe('灯光');
    expect(view.get<HTMLInputElement>('[aria-label="选区入点帧"]').element.value).toBe('206');
    expect(client.createVideoRepair).not.toHaveBeenCalled(); expect(client.planVideoEdit).not.toHaveBeenCalled();
    view.unmount();
  });
  it('retains the current form on input revision conflicts and lets the user compare', async () => {
    client.saveVideoEditInput.mockRejectedValueOnce(new ApiError(409, 'input revision conflict'));
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('我的新修改');
    await view.findAll('button').find(button => button.text() === '保存当前输入')!.trigger('click'); await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('我的新修改');
    expect(view.text()).toContain('另一处已更新');
    client.videoEditDraft.mockResolvedValue({ ...draft, inputRevision: 4, editingInput: { instruction: '另一处输入' } });
    await view.findAll('button').find(button => button.text() === '读取已保存输入进行比较')!.trigger('click'); await flushPromises();
    expect(view.text()).toContain('另一处输入');
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('我的新修改');
    await view.findAll('button').find(button => button.text() === '保留当前输入并保存为新修订')!.trigger('click'); await flushPromises();
    expect(client.saveVideoEditInput).toHaveBeenLastCalledWith('project-1', 'draft-1', expect.objectContaining({ expectedRevision: 4, editingInput: expect.objectContaining({ instruction: '我的新修改' }) }));
    expect(client.saveVideoDraft).not.toHaveBeenCalled();
    view.unmount();
  });
  it('adopts only suggested text, preserving explicitly selected mode, references and range', async () => {
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('直接输入');
    view.getComponent(VideoEditPlanner).vm.$emit('adopt', { instruction: '建议目标', preserveContent: '保留环境', startState: '初始状态', actionProcess: '缓慢放下', desiredEndState: '保持静止', avoidProblems: '不要改变比例', recommendedGenerationMode: 'from_frame', recommendedEndStatePolicy: 'replace', recommendedReferenceRoles: ['environment'], notes: [] }, 'plan-1');
    await flushPromises();
    expect(view.get<HTMLTextAreaElement>('[aria-label="修改目标"]').element.value).toBe('建议目标');
    expect(view.get<HTMLSelectElement>('[aria-label="生成方式"]').element.value).toBe('edit_existing');
    expect(view.get<HTMLSelectElement>('[aria-label="结束状态策略"]').element.value).toBe('follow_instruction');
    expect(view.get<HTMLInputElement>('[aria-label="选区出点帧"]').element.value).toBe('96');
    await view.get('[aria-label="动作过程"]').setValue('采纳后自行补充');
    await view.get('[data-testid="prepare-references"]').trigger('click'); await flushPromises();
    expect(client.prepareSegmentReferences).toHaveBeenLastCalledWith('project-1', expect.objectContaining({ planSourceJobId: 'plan-1', actionProcess: '采纳后自行补充', referenceRoles: [] }));
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    view.unmount();
  });
  it('drops incompatible references when changing mode and invalidates prepared media', async () => {
    client.previewVideoRepair.mockResolvedValue({ ...preview, referencePreparationJobId: 'refs-1' });
    client.videoEditDraft.mockResolvedValue({ ...draft, references: [{ role: 'environment', assetId: 'old-environment' }] });
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    await view.get('[aria-label="修改目标"]').setValue('环境渐渐明亮');
    await view.get('[data-testid="prepare-references"]').trigger('click'); await flushPromises();
    expect(client.prepareSegmentReferences.mock.calls[0][1].referenceRoles).toEqual(['environment']);
    await view.get('[aria-label="生成方式"]').setValue('from_frame');
    await view.get('[aria-label="正确起始帧"]').setValue('0');
    expect(view.find('[data-testid="refresh-edit-prompt"]').exists()).toBe(false);
    await view.get('[data-testid="prepare-references"]').trigger('click'); await flushPromises();
    expect(client.prepareSegmentReferences).toHaveBeenLastCalledWith('project-1', expect.objectContaining({ generationMode: 'from_frame', referenceRoles: [], includeInAnchor: false, anchorStartFrame: 0, anchorEndFrame: null }));
    view.unmount();
  });
  it('lets the user move the candidate take without changing replacement length or applying', async () => {
    state.query = { draftId: 'draft-1', candidateId: 'repair-1', view: 'after' };
    client.videoRepairs.mockResolvedValue([{ id: 'repair-1', baseEditVersionId: 'edit-1', candidateAssetId: 'candidate-1', status: 'candidate_ready', issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.assets.mockResolvedValue([source, { ...source, id: 'candidate-1', role: 'repair_candidate', metadata: { durationFrames: 193, hasAudio: true } }, { ...source, id: 'trial-video', role: 'edit_preview', producingJobId: 'trial-1', metadata: { repairId: 'repair-1' } }, ...['edit_result_before', 'edit_result_after'].map(role => ({ ...source, id: role, role, producingJobId: 'trial-1' }))]);
    client.videoDraftJobs.mockResolvedValue([{ id: 'trial-1', kind: 'render_edit_preview', status: 'succeeded', createdAt: '2026-09-05T00:00:00Z', resultAssetIds: ['trial-video'], frozenInput: { repairId: 'repair-1', placement: { candidateSourceRange: { startFrame: 24, endFrame: 169 }, audioPolicy: 'preserve_current', fadeInMs: 0, fadeOutMs: 0 } } }]);
    client.renderDraftPreview.mockResolvedValue({ id: 'trial-2', status: 'queued', kind: 'render_edit_preview', frozenInput: { repairId: 'repair-1', placement: { candidateSourceRange: { startFrame: 30, endFrame: 175 }, audioPolicy: 'preserve_current' } } });
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } }); await flushPromises();
    await view.get('[aria-label="候选取用起点"]').setValue('80');
    expect(view.get('[data-testid="preview-candidate-take"]').attributes('disabled')).toBeDefined();
    await view.get('[aria-label="候选取用起点"]').setValue('30');
    expect(view.findAll('button').find(button => button.text() === '应用此修改到草稿')!.attributes('disabled')).toBeDefined();
    await view.get('[data-testid="preview-candidate-take"]').trigger('click'); await flushPromises();
    expect(client.renderDraftPreview).toHaveBeenCalledWith('project-1', 'draft-1', expect.objectContaining({ repairId: 'repair-1', placement: expect.objectContaining({ candidateSourceRange: { startFrame: 30, endFrame: 175 } }) }));
    expect(client.createVideoRepair).not.toHaveBeenCalled(); expect(client.saveVideoDraft).not.toHaveBeenCalled();
    view.unmount();
  });
  it.each([true, false])("reads a restored segment prompt only from its frozen preview (final recorded: %s)", async recorded => {
    const final = "冻结片段修改\n避免项：道具消失\n参考图 1：入点参考\n";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    client.videoRepairs.mockResolvedValue([{ id: "repair-1", baseEditVersionId: "edit-1", status: "generating", issueRange: preview.issueRange, instruction: preview.instruction,
      preview: { ...preview, compiledProviderPrompt: recorded ? final : undefined, warnings: [{ code: "RELATION_MISSING", message: "历史分镜缺少空间关系，请人工核对" }] },
    }]);
    client.videoDraftJobs.mockResolvedValue([{ id: "job-1", videoRepairId: "repair-1", kind: "regenerate_video_segment", status: "submission_unknown", createdAt: "2026-09-05T00:00:00Z", resultAssetIds: [] }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: "project-1", workspace } });
    await flushPromises();
    const prompt = view.get('.provider-prompt');
    expect(prompt.get('[aria-label="制作审查建议"]').text()).toContain("历史分镜缺少空间关系");
    if (recorded) {
      expect(prompt.get('.compiled-provider-prompt').element.textContent).toBe(final);
      await prompt.get('button').trigger('click');
      expect(writeText).toHaveBeenCalledWith(final);
    } else {
      expect(prompt.find('.compiled-provider-prompt').exists()).toBe(false);
      expect(prompt.text()).toContain("未记录最终模型指令");
      await prompt.get('button').trigger('click');
      expect(writeText).toHaveBeenCalledWith(preview.prompt);
    }
    expect(client.previewVideoRepair).not.toHaveBeenCalled();
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    view.unmount();
  });

  it("restores a source issue range without promoting its failed review", async () => {
    client.videoReviews.mockImplementation(async (_project, assetId) => assetId === "video-1" ? [{ id: "review-1", notes: "饼干被拿出", checks: { causalChainAndActiveEnding: "fail" }, issues: [{ range: { startFrame: 144, endFrame: 289 }, note: "饼干被拿出" }] }] : []);
    const view = mount(VideoRepairWorkspace, { props: { projectId: "project-1", workspace } });
    await flushPromises();
    expect(view.get('[role="slider"][aria-label="选区入点"]').attributes('aria-valuenow')).toBe('144');
    expect(view.get('[role="slider"][aria-label="选区出点"]').attributes('aria-valuenow')).toBe('289');
    expect(view.findAll("select").some(select => select.element.value === "fail")).toBe(true);
    expect(client.selectAsset).not.toHaveBeenCalled();
    view.unmount();
  });
  it("allows an unselected video to be edited without pretending quality passed", async () => {
    const view = mount(VideoRepairWorkspace, { props: { projectId: "project-1", workspace } });
    await flushPromises();
    expect(view.get('.acceptance-panel').text()).toContain("只有真正全部通过后才能正式选择");
    expect(view.get('[role="slider"][aria-label="选区出点"]').attributes("aria-valuemax")).toBe("289");
    expect(view.findAll('.frame-editor')).toHaveLength(1);
    expect(client.selectAsset).not.toHaveBeenCalled();
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    expect(view.findAll("video")).toHaveLength(1);
    view.unmount();
  });
  it("shows frozen selection separately from the editor without resubmitting", async () => {
    client.videoRepairs.mockResolvedValue([{ id: "repair-1", baseEditVersionId: "edit-1", status: "generating", issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.videoDraftJobs.mockResolvedValue([{ id: "job-1", videoRepairId: "repair-1", kind: "regenerate_video_segment", status: "submission_unknown", createdAt: "2026-09-05T00:00:00Z", resultAssetIds: [] }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: "project-1", workspace } });
    await flushPromises();
    expect(view.get('[role="slider"][aria-label="选区入点"]').attributes('aria-valuenow')).toBe('0');
    expect(view.get('[role="slider"][aria-label="选区出点"]').attributes('aria-valuenow')).toBe('96');
    expect(view.get('[role="slider"][aria-label="选区入点"]').attributes("aria-disabled")).toBe('true');
    expect(view.text()).toContain("提交结果待核实");
    expect(view.get('[aria-label="所选任务冻结输入"]').text()).toContain("[144, 289)");
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    view.unmount();
  });
  it("renders a complete local preview but never automatically applies a candidate", async () => {
    client.videoRepairs.mockResolvedValue([{ id: "repair-1", baseEditVersionId: "edit-1", status: "candidate_ready", candidateAssetId: "candidate-1", issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.assets.mockResolvedValue([source, { ...source, id: 'base-preview', role: 'edit_preview', metadata: { editVersionId: 'edit-1' } }, { ...source, id: "candidate-1", role: "repair_candidate", metadata: { durationFrames: 193, hasAudio: false } }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: "project-1", workspace } });
    await flushPromises();
    await view.get('.candidate-card').trigger('click');
    await flushPromises();
    expect(client.prepareRepairResult).toHaveBeenCalledWith("project-1", "draft-1", expect.objectContaining({ repairId: "repair-1", preserveOriginalAudio: false }));
    expect(client.prepareRepairResult).toHaveBeenCalledTimes(1);
    expect(client.saveVideoDraft).not.toHaveBeenCalled();
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    expect(client.selectAsset).not.toHaveBeenCalled();
    view.unmount();
  });

  it("freezes the result range until the user explicitly starts another edit, without a paid call", async () => {
    client.videoRepairs.mockResolvedValue([{ id: 'repair-1', baseEditVersionId: 'edit-1', candidateAssetId: 'candidate-1', status: 'candidate_ready', issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.assets.mockResolvedValue([source, { ...source, id: 'base-preview', role: 'edit_preview', metadata: { editVersionId: 'edit-1' } }, { ...source, id: 'candidate-1', role: 'repair_candidate', metadata: { durationFrames: 193, hasAudio: false } }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } });
    await flushPromises();
    expect(view.get('[role="slider"][aria-label="选区入点"]').attributes('aria-disabled')).toBe('false');
    await view.get('.candidate-card').trigger('click');
    await flushPromises();
    expect(view.get('[role="slider"][aria-label="选区入点"]').attributes('aria-disabled')).toBe('true');
    expect(view.get('.raw-inspection a').attributes('href')).toContain('candidate-1');
    expect(view.find('.trial-fields').exists()).toBe(false);
    await view.findAll('button').find(button => button.text() === '修改当前草稿其他区间')!.trigger('click');
    await flushPromises();
    expect(view.get('[role="slider"][aria-label="选区入点"]').attributes('aria-disabled')).toBe('false');
    expect(client.renderDraftPreview).not.toHaveBeenCalled();
    expect(client.createVideoRepair).not.toHaveBeenCalled();
    expect(client.saveVideoDraft).not.toHaveBeenCalled();
    view.unmount();
  });

  it('binds apply to the checked complete result without browser-provided EDL or a manual take', async () => {
    state.query = { draftId: 'draft-1', candidateId: 'repair-1', view: 'after' };
    client.videoRepairs.mockResolvedValue([{ id: 'repair-1', baseEditVersionId: 'edit-1', candidateAssetId: 'candidate-1', status: 'candidate_ready', issueRange: preview.issueRange, instruction: preview.instruction, preview }]);
    client.assets.mockResolvedValue([source, { ...source, id: 'candidate-1', role: 'repair_candidate', metadata: { durationFrames: 193, hasAudio: false } }, { ...source, id: 'trial-video', role: 'edit_preview', producingJobId: 'trial-1', metadata: { repairId: 'repair-1' } }, ...['edit_result_before', 'edit_result_after'].map(role => ({ ...source, id: role, role, producingJobId: 'trial-1' }))]);
    client.videoDraftJobs.mockResolvedValue([{ id: 'trial-1', kind: 'render_edit_preview', status: 'succeeded', createdAt: '2026-09-05T00:00:00Z', resultAssetIds: ['trial-video'], frozenInput: { repairId: 'repair-1', placement: { candidateSourceRange: { startFrame: 24, endFrame: 169 }, audioPolicy: 'preserve_current', fadeInMs: 0, fadeOutMs: 0 } } }]);
    const view = mount(VideoRepairWorkspace, { props: { projectId: 'project-1', workspace } });
    await flushPromises();
    const apply = view.findAll('button').find(button => button.text() === '应用此修改到草稿')!;
    expect(apply.attributes('disabled')).toBeUndefined();
    expect(view.find('.trial-fields').exists()).toBe(false);
    await apply.trigger('click'); await flushPromises();
    expect(client.saveVideoDraft).toHaveBeenCalledWith('project-1', 'draft-1', expect.objectContaining({ previewJobId: 'trial-1', expectedEditVersionId: 'edit-1' }));
    expect(client.saveVideoDraft.mock.calls[0][2]).not.toHaveProperty('edl');
    expect(client.selectAsset).not.toHaveBeenCalled();
    view.unmount();
  });
});
