import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import VideoEditPlanner from './VideoEditPlanner.vue';
import type { SegmentRepairPreviewCommand } from '../../api/types';

const client = vi.hoisted(() => ({ planVideoEdit: vi.fn(), jobResult: vi.fn(), job: vi.fn() }));
vi.mock('../../api/client', async original => ({ ...await original<typeof import('../../api/client')>(), api: client }));
const command = { baseVideoAssetId: 'video-1', baseEditVersionId: 'edit-1', editDraftId: 'draft-1', issueRange: { startFrame: 278, endFrame: 361 }, instruction: '容器放稳之后保持静止' } as SegmentRepairPreviewCommand;
const plan = { instruction: '将物体放入容器后保持静止', preserveContent: '场景与光线', startState: '', actionProcess: '放下后松手', desiredEndState: '保持静止', avoidProblems: '', recommendedGenerationMode: 'from_frame', recommendedEndStatePolicy: 'replace', recommendedReferenceRoles: [], notes: ['容器深度不确定，需人工确认。'] };
const statusStub = { props: ['jobId'], emits: ['changed', 'replacement'], template: '<div class="plan-status" />' };
beforeEach(() => {
  vi.clearAllMocks();
  client.planVideoEdit.mockResolvedValue({ id: 'plan-1', kind: 'plan_video_edit', status: 'queued', frozenInput: { command } });
  client.jobResult.mockResolvedValue({ result: { editPlan: plan } });
});
describe('optional video edit planning', () => {
  it.each(['pending', 'failed-read'])('blocks paid creation until a saved job has a known terminal status: %s', async outcome => {
    let finish!: (value: unknown) => void, reject!: (reason: unknown) => void;
    client.job.mockReturnValue(new Promise((resolve, fail) => { finish = resolve; reject = fail; }));
    const view = mount(VideoEditPlanner, { props: { projectId: 'p', command, jobId: 'saved-plan' } }); await flushPromises();
    expect(view.get('[data-testid="plan-edit"]').attributes('disabled')).toBeDefined();
    expect(view.text()).toContain('确认原任务结束前暂不能新建付费方案');
    await view.get('[data-testid="plan-edit"]').trigger('click');
    if (outcome === 'failed-read') reject(new Error('status unavailable'));
    else finish({ id: 'saved-plan', status: 'submission_unknown', revision: 1, execution: { availableActions: [] } });
    await flushPromises();
    expect(view.get('[data-testid="plan-edit"]').attributes('disabled')).toBeDefined();
    expect(client.planVideoEdit).not.toHaveBeenCalled();
    if (outcome === 'failed-read') expect(view.text()).toContain('暂时无法读取本地任务记录');
    view.getComponent({ name: 'JobStatusCard' }).vm.$emit('changed', { id: 'saved-plan', status: 'failed', revision: 2 }); await flushPromises();
    expect(view.get('[data-testid="plan-edit"]').attributes('disabled')).toBeUndefined();
    view.unmount();
  });
  it('retries an unresolved create with the exact original payload and key after text changes', async () => {
    client.planVideoEdit.mockRejectedValueOnce(new Error('connection lost'));
    const view = mount(VideoEditPlanner, { props: { projectId: 'p', command }, global: { stubs: { JobStatusCard: statusStub } } });
    await view.get('[data-testid="plan-edit"]').trigger('click'); await flushPromises();
    const original = client.planVideoEdit.mock.calls[0];
    await view.setProps({ command: { ...command, instruction: '请求未确认时的新文字' } });
    await view.get('[data-testid="plan-edit"]').trigger('click'); await flushPromises();
    expect(client.planVideoEdit.mock.calls[1]).toEqual(original);
    expect(window.sessionStorage.getItem('catflow:pending-idempotency:video-edit-plan:p:draft-1')).toBeNull();
    view.unmount();
  });

  it('calls only when clicked and presents advice without applying or generating', async () => {
    const view = mount(VideoEditPlanner, { props: { projectId: 'p', command }, global: { stubs: { JobStatusCard: statusStub } } });
    await flushPromises();
    expect(client.planVideoEdit).not.toHaveBeenCalled();
    await view.get('[data-testid="plan-edit"]').trigger('click');
    expect(client.planVideoEdit).toHaveBeenCalledWith('p', expect.objectContaining({ instruction: command.instruction, issueRange: command.issueRange }));
    await flushPromises();
    view.getComponent(statusStub).vm.$emit('changed', { id: 'plan-1', status: 'succeeded', frozenInput: { command } });
    await flushPromises();
    expect(view.text()).toContain(plan.instruction);
    expect(view.text()).toContain(plan.notes[0]);
    expect(view.emitted('adopt')).toBeUndefined();
    await view.get('[data-testid="adopt-plan"]').trigger('click');
    expect(view.emitted('adopt')?.[0]).toEqual([plan, 'plan-1']);
    view.unmount();
  });
  it('retains newer text and disallows adoption when the source range changes', async () => {
    const view = mount(VideoEditPlanner, { props: { projectId: 'p', command, jobId: 'plan-1' }, global: { stubs: { JobStatusCard: statusStub } } });
    view.getComponent(statusStub).vm.$emit('changed', { id: 'plan-1', status: 'succeeded', frozenInput: { command } });
    await flushPromises();
    await view.setProps({ command: { ...command, instruction: '我刚补充的内容' } });
    expect(view.text()).toContain('文字已经修改');
    expect(view.emitted('adopt')).toBeUndefined();
    await view.setProps({ command: { ...command, issueRange: { startFrame: 360, endFrame: 361 } } });
    expect(view.get('[data-testid="adopt-plan"]').attributes('disabled')).toBeDefined();
    expect(client.planVideoEdit).not.toHaveBeenCalled();
    view.unmount();
  });
  it('keeps direct editing available after a planning failure', async () => {
    client.planVideoEdit.mockRejectedValue(new Error('planning unavailable'));
    const view = mount(VideoEditPlanner, { props: { projectId: 'p', command }, global: { stubs: { JobStatusCard: statusStub } } });
    await view.get('[data-testid="plan-edit"]').trigger('click'); await flushPromises();
    expect(view.text()).toContain('仍可直接填写');
    expect(view.emitted('adopt')).toBeUndefined();
    view.unmount();
  });
});
