import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import JobStatusCard from "./JobStatusCard.vue";
import { ApiError } from '../api/client';
import { pendingIdempotencyKey } from '../idempotency';

const calls = vi.hoisted(() => ({ job: vi.fn(), jobResult: vi.fn(), jobEvents: vi.fn(), prepareJobReplacement: vi.fn(), replaceUnknownJob: vi.fn(), recoverJob: vi.fn(), providerTasks: vi.fn() }));
const subscription = vi.hoisted(() => ({ refresh: async () => {} }));
vi.mock("../api/client", async original => ({ ...await original<typeof import('../api/client')>(), api: calls }));
vi.mock("../jobUpdates", () => ({ subscribeJobs: (_scope: unknown, refresh: () => Promise<void>) => { subscription.refresh = refresh; void refresh(); return () => {}; } }));

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
  calls.job.mockResolvedValue({ id: "unknown", status: "submission_unknown", revision: 3, execution: { availableActions: ["prepare_replacement"], usageUnconfirmed: true, resultState: "missing" }, frozenInput: {} });
  calls.jobResult.mockResolvedValue({ state: "missing" }); calls.jobEvents.mockResolvedValue({ items: [] });
  calls.prepareJobReplacement.mockResolvedValue({ executionInputHash: "frozen-current-input", model: "current-model", provider: "ark", input: { prompt: "Current inputs" } });
});

it('shows pending and definitive errors inside the dialog, preserving them across polling', async () => {
  let reject!: (reason: unknown) => void;
  calls.replaceUnknownJob.mockReturnValue(new Promise((_resolve, fail) => { reject = fail; }));
  const wrapper = mount(JobStatusCard, { props: { jobId: 'unknown' } }); await flushPromises();
  await wrapper.get('.task-actions button').trigger('click'); await flushPromises();
  await wrapper.get('dialog input[type="checkbox"]').setValue(true);
  await wrapper.get('dialog .task-actions button:last-child').trigger('click'); await flushPromises();
  expect(wrapper.get('dialog [role="status"]').text()).toContain('正在创建');
  reject(new ApiError(409, { code: 'video_edit_in_progress', message: '另一任务正在执行', blockingJobId: 'blocker', editDraftId: 'draft-1', videoRepairId: 'repair-1' })); await flushPromises();
  expect(wrapper.get('dialog [role="alert"]').text()).toContain('另一任务正在执行');
  await subscription.refresh(); await flushPromises();
  expect(wrapper.get('dialog [role="alert"]').text()).toContain('另一任务正在执行');
  expect(calls.replaceUnknownJob).toHaveBeenCalledTimes(1); wrapper.unmount();
});

it.each([
  [409, 'switch'], [422, 'switch'], [503, 'switch'], [0, 'switch'],
  [409, 'unmount'], [422, 'unmount'], [503, 'unmount'], [0, 'unmount'],
])('settles only definitive delayed rejection %s after %s without changing the destination', async (status, departure) => {
  let reject!: (reason: unknown) => void;
  calls.replaceUnknownJob.mockReturnValue(new Promise((_resolve, fail) => { reject = fail; }));
  calls.job.mockImplementation(async id => ({ id, status: id === 'unknown' ? 'submission_unknown' : 'succeeded', execution: { availableActions: id === 'unknown' ? ['prepare_replacement'] : [] }, frozenInput: {} }));
  let wrapper = mount(JobStatusCard, { props: { jobId: 'unknown' } }); await flushPromises();
  await wrapper.get('.task-actions button').trigger('click'); await flushPromises();
  await wrapper.get('dialog input[type="checkbox"]').setValue(true);
  await wrapper.get('dialog .task-actions button:last-child').trigger('click'); await flushPromises();
  const pending = sessionStorage.getItem('catflow:pending-idempotency:replacement:unknown');
  expect(pending).not.toBeNull();
  if (departure === 'switch') await wrapper.setProps({ jobId: 'other' });
  else { wrapper.unmount(); wrapper = mount(JobStatusCard, { props: { jobId: 'other' } }); }
  await flushPromises();
  const destination = wrapper.html();
  reject(status ? new ApiError(Number(status), { code: 'rejected', message: 'Definitively rejected' }) : new TypeError('connection lost'));
  await flushPromises();
  expect(wrapper.html()).toBe(destination);
  expect(wrapper.emitted('replacement')).toBeUndefined();
  expect(sessionStorage.getItem('catflow:pending-idempotency:replacement:unknown')).toBe(status ? null : pending);
  wrapper.unmount();
  wrapper = mount(JobStatusCard, { props: { jobId: 'unknown' } }); await flushPromises();
  await wrapper.get('.task-actions button').trigger('click'); await flushPromises();
  expect(calls.prepareJobReplacement).toHaveBeenCalledTimes(status ? 2 : 1);
  expect(wrapper.find('dialog h3').exists()).toBe(Boolean(status));
  expect(calls.replaceUnknownJob).toHaveBeenCalledTimes(1);
  wrapper.unmount();
});

it('renders derived references in their actual SDK order, without zipping incompatible legacy lists', async () => {
  calls.prepareJobReplacement.mockResolvedValue({ kind: 'regenerate_video_segment', executionInputHash:'frozen-current-input', input: {
    imageReferences: [{ role:'anchor_in',assetId:'anchor',frameNumber:265,derived:true },{role:'episode_child',assetId:'child'}],
    referenceRoles:['anchor_in','episode_child'],referenceAssetIds:['child'],
    videoReference:{assetId:'clip'},issueRange:{startFrame:265,endFrame:361},generationRange:{startFrame:265,endFrame:361},candidateCoreRange:{startFrame:0,endFrame:96},providerDurationSeconds:4,
  }});
  const wrapper=mount(JobStatusCard,{props:{jobId:'unknown'}}); await flushPromises();
  await wrapper.get('.task-actions button').trigger('click'); await flushPromises();
  const refs=wrapper.findAll('dialog figure');
  expect(refs).toHaveLength(2); expect(refs[0].get('img').attributes('src')).toContain('/anchor/');
  expect(refs[0].text()).toContain('265'); expect(refs[1].get('img').attributes('src')).toContain('/child/');
  expect(wrapper.get('dialog video').attributes('src')).toContain('/clip/');
  expect(wrapper.get('dialog').text()).toContain('[265, 361)'); wrapper.unmount();
});

it('reconciles a lost response after remount without issuing another paid POST', async () => {
  const key=pendingIdempotencyKey('replacement:unknown','same-input');
  const created={id:'accepted',status:'queued',idempotencyKey:key,frozenInput:{executionInputHash:'same-input'}};
  calls.job.mockImplementation(async id=> id==='unknown' ? {id:'unknown',status:'submission_unknown',successorJobIds:['accepted'],execution:{availableActions:[]}} : created);
  const wrapper=mount(JobStatusCard,{props:{jobId:'unknown'}}); await flushPromises();
  expect(wrapper.emitted('replacement')?.[0]).toEqual([created]);
  expect(calls.replaceUnknownJob).not.toHaveBeenCalled();
  expect(sessionStorage.getItem('catflow:pending-idempotency:replacement:unknown')).toBeNull(); wrapper.unmount();
});

it('keeps an uncertain request identity and only rechecks records when no successor is confirmed', async () => {
  calls.replaceUnknownJob.mockRejectedValue(new TypeError('connection lost'));
  const wrapper=mount(JobStatusCard,{props:{jobId:'unknown'}}); await flushPromises();
  await wrapper.get('.task-actions button').trigger('click'); await flushPromises();
  await wrapper.get('dialog input[type="checkbox"]').setValue(true);
  await wrapper.get('dialog .task-actions button:last-child').trigger('click'); await flushPromises();
  expect(wrapper.get('dialog [role="alert"]').text()).toContain('尚未确定');
  expect(wrapper.get('dialog .task-actions button:last-child').attributes('disabled')).toBeDefined();
  const pending=sessionStorage.getItem('catflow:pending-idempotency:replacement:unknown');
  await wrapper.findAll('dialog button').find(b=>b.text()==='重新核对本次提交（不生成）')!.trigger('click'); await flushPromises();
  expect(sessionStorage.getItem('catflow:pending-idempotency:replacement:unknown')).toBe(pending);
  expect(calls.replaceUnknownJob).toHaveBeenCalledTimes(1); wrapper.unmount();
});

it('does not claim a successor with a different submitted input', async () => {
  const key=pendingIdempotencyKey('replacement:unknown','expected-input');
  calls.job.mockImplementation(async id=>id==='unknown' ? {id:'unknown',status:'submission_unknown',successorJobIds:['different'],execution:{availableActions:[]}} : {id:'different',status:'queued',idempotencyKey:key,frozenInput:{executionInputHash:'other-input'}});
  const wrapper=mount(JobStatusCard,{props:{jobId:'unknown'}}); await flushPromises();
  expect(wrapper.emitted('replacement')).toBeUndefined();
  expect(wrapper.get('[aria-label="重建提交状态"]').text()).toContain('未确认');
  expect(calls.replaceUnknownJob).not.toHaveBeenCalled(); wrapper.unmount();
});

it('keeps a failed post-create reload separate from an accepted replacement', async () => {
  const wrapper=mount(JobStatusCard,{props:{jobId:'unknown'}}); await flushPromises();
  await wrapper.get('.task-actions button').trigger('click'); await flushPromises();
  await wrapper.get('dialog input[type="checkbox"]').setValue(true);
  calls.replaceUnknownJob.mockResolvedValue({id:'accepted'}); calls.job.mockRejectedValue(new Error('read interrupted'));
  await wrapper.get('dialog .task-actions button:last-child').trigger('click'); await flushPromises();
  expect(wrapper.emitted('replacement')?.[0]).toEqual([{id:'accepted'}]);
  expect(wrapper.find('dialog h3').exists()).toBe(false);
  expect(calls.replaceUnknownJob).toHaveBeenCalledTimes(1); wrapper.unmount();
});

it('ignores an old preparation response after switching the inspected job', async () => {
  let resolve!: (value: unknown) => void;
  calls.prepareJobReplacement.mockReturnValue(new Promise(done=>{resolve=done;}));
  const wrapper=mount(JobStatusCard,{props:{jobId:'unknown'}}); await flushPromises();
  await wrapper.get('.task-actions button').trigger('click');
  calls.job.mockResolvedValue({id:'other',status:'succeeded',execution:{availableActions:[]}});
  await wrapper.setProps({jobId:'other'}); await flushPromises();
  resolve({executionInputHash:'old',input:{prompt:'stale'}}); await flushPromises();
  expect(wrapper.find('dialog h3').exists()).toBe(false); wrapper.unmount();
});

it.each(['query_failed', 'not_found', 'candidates'])('keeps cloud lookup read-only and reports %s distinctly', async status => {
  calls.job.mockResolvedValue({ id: 'unknown', kind: 'regenerate_video_segment', status: 'submission_unknown', revision: 3, execution: { availableActions: ['lookup_provider_tasks'] } });
  calls.providerTasks.mockResolvedValue({ status, model: 'frozen-model', windowStart: '2026-09-10T08:47:00Z', windowEnd: '2026-09-10T08:50:00Z', candidates: [], coverageComplete: status !== 'query_failed', message: '核对说明' });
  const wrapper = mount(JobStatusCard, { props: { jobId: 'unknown' } }); await flushPromises();
  expect(calls.providerTasks).not.toHaveBeenCalled();
  await wrapper.get('.task-actions button').trigger('click'); await flushPromises();
  expect(wrapper.get('[aria-label="云端任务核对结果"]').text()).toContain(status === 'query_failed' ? '云端查询失败' : status === 'not_found' ? '查询窗口内未找到任务' : '找到可能相关任务，尚未关联');
  expect(wrapper.text()).toContain('提交结果待核实');
  expect(calls.recoverJob).not.toHaveBeenCalled(); expect(calls.replaceUnknownJob).not.toHaveBeenCalled();
  wrapper.unmount();
});

it('requires selecting a cloud task and explicit attestation, then recovers without creating generation', async () => {
  calls.job.mockResolvedValue({ id: 'unknown', kind: 'regenerate_video_segment', status: 'submission_unknown', revision: 3, execution: { availableActions: ['lookup_provider_tasks'] } });
  calls.providerTasks.mockResolvedValue({ status: 'candidates', model: 'frozen-model', candidates: [{ providerTaskId: 'found-task', model: 'frozen-model', status: 'succeeded', parameters: { duration: 4 }, mismatches: [], canAssociate: true }], coverageComplete: true });
  const wrapper = mount(JobStatusCard, { props: { jobId: 'unknown' } }); await flushPromises();
  await wrapper.get('.task-actions button').trigger('click'); await flushPromises();
  expect(wrapper.get<HTMLInputElement>('input[type="radio"]').element.checked).toBe(false);
  await wrapper.get('input[type="radio"]').setValue();
  const confirm = wrapper.get('.cloud-task-lookup button'); expect(confirm.attributes('disabled')).toBeDefined();
  expect(calls.recoverJob).not.toHaveBeenCalled();
  await wrapper.get('input[type="checkbox"]').setValue(true);
  calls.recoverJob.mockResolvedValue({ id: 'unknown', status: 'polling', revision: 4, providerTaskId: 'found-task', execution: { availableActions: [], providerStatus: 'succeeded' } });
  await confirm.trigger('click'); await flushPromises();
  expect(calls.recoverJob).toHaveBeenCalledWith('unknown', { action: 'associate_provider_task', providerTaskId: 'found-task', confirmAssociation: true, acknowledgeUnverifiedParameters: true, expectedRevision: 3, idempotencyKey: expect.any(String) });
  expect(calls.replaceUnknownJob).not.toHaveBeenCalled(); expect(calls.prepareJobReplacement).not.toHaveBeenCalled();
  wrapper.unmount();
});

it.each([
  ['plan_video_edit', 'storing', 'completed', '保存修改建议'],
  ['extract_continuity_frames', 'submitting', null, '裁切参考视频与提取参考图片'],
  ['regenerate_video_segment', 'storing', 'succeeded', '正在下载结果'],
  ['regenerate_video_segment', 'failed', 'succeeded', '结果下载或保存未完成'],
  ['render_edit_preview', 'failed', null, '准备接回预览阶段中断'],
])('shows the concrete %s phase for %s', async (kind, status, providerStatus, expected) => {
  calls.job.mockResolvedValue({ id: 'phase', kind, status, revision: 1, execution: { providerStatus, availableActions: ['process_result'] } });
  const wrapper = mount(JobStatusCard, { props: { jobId: 'phase' } }); await flushPromises();
  expect(wrapper.text()).toContain(expected); expect(wrapper.text()).not.toContain('本地处理');
  expect(calls.recoverJob).not.toHaveBeenCalled(); wrapper.unmount();
});

it("prepares unknown replacement without submitting and requires scoped explicit confirmation", async () => {
  const wrapper = mount(JobStatusCard, { props: { jobId: "unknown" } });
  await flushPromises();
  expect(wrapper.text()).toContain("费用待核实");
  await wrapper.get(".task-actions button").trigger("click"); await flushPromises();
  expect(calls.prepareJobReplacement).toHaveBeenCalledWith("unknown");
  const submit = wrapper.get("dialog .task-actions button:last-child");
  expect(submit.attributes("disabled")).toBeDefined();
  expect(calls.replaceUnknownJob).not.toHaveBeenCalled();
  await wrapper.get('input[type="checkbox"]').setValue(true);
  calls.replaceUnknownJob.mockResolvedValue({ id: "new-job" });
  await submit.trigger("click"); await flushPromises();
  expect(calls.replaceUnknownJob).toHaveBeenCalledWith("unknown", "frozen-current-input", expect.any(String));
  expect(wrapper.emitted("replacement")?.[0]).toEqual([{ id: "new-job" }]);
  wrapper.unmount();
});

it("shows and copies a replacement's compiled media prompt without submitting a replacement", async () => {
  const final = "重新准备的模型文本\n避免项：额外对象\n参考图 1：身份\n";
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  calls.prepareJobReplacement.mockResolvedValue({
    kind: "generate_video", executionInputHash: "frozen-current-input", provider: "ark", model: "current-model",
    input: { compiledProviderPrompt: final, prompt: "正文摘要", negativePrompt: "避免项摘要" },
  });
  const wrapper = mount(JobStatusCard, { props: { jobId: "unknown" } });
  await flushPromises();
  await wrapper.get(".task-actions button").trigger("click");
  await flushPromises();
  const dialog = wrapper.get("dialog");
  expect(dialog.get(".compiled-provider-prompt").element.textContent).toBe(final);
  await dialog.findAll("button").find(button => button.text() === "复制最终模型指令")!.trigger("click");
  expect(writeText).toHaveBeenCalledWith(final);
  expect(calls.replaceUnknownJob).not.toHaveBeenCalled();
  wrapper.unmount();
});

it("renders complete result facts while details load, and never fabricates a task ID", async () => {
  calls.job.mockResolvedValue({ id: "complete", status: "succeeded", revision: 4, execution: { resultState: "complete", availableActions: [] }, frozenInput: {} });
  const wrapper = mount(JobStatusCard, { props: { jobId: "complete" } });
  await flushPromises();
  expect(wrapper.text()).toContain("完整回执");
  expect(wrapper.text()).not.toContain("没有收到正文");
  expect(wrapper.text()).toContain("未收到／此接口不提供");
  wrapper.unmount();
});

it("blocks paid replacement while the environment editor contains unsaved input", async () => {
  const wrapper = mount(JobStatusCard, { props: { jobId: 'unknown', preparationBlockedReason: '环境修改尚未保存' } });
  await flushPromises();
  expect(wrapper.get('.task-actions button').attributes('disabled')).toBeDefined();
  await wrapper.get('.task-actions button').trigger('click');
  expect(calls.prepareJobReplacement).not.toHaveBeenCalled();
  await wrapper.setProps({ preparationBlockedReason: '' });
  await wrapper.get('.task-actions button').trigger('click'); await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true);
  await wrapper.setProps({ preparationBlockedReason: '环境修改尚未保存' });
  const submit = wrapper.get('dialog .task-actions button:last-child');
  expect(submit.attributes('disabled')).toBeDefined();
  await submit.trigger('click');
  expect(calls.replaceUnknownJob).not.toHaveBeenCalled();
  wrapper.unmount();
});

it('does not render or fetch event history and only reloads a body when availability changes', async () => {
  const current = { id: 'video', status: 'polling', revision: 1, providerTaskId: 'real-task-id', execution: { resultState: 'partial', providerStatus: 'running', availableActions: [] }, frozenInput: { internal: 'do-not-display' } };
  calls.job.mockResolvedValue(current);
  const wrapper = mount(JobStatusCard, { props: { jobId: 'video' } });
  await flushPromises();
  const details = wrapper.findAll('details').find(d => d.get('summary').text() === '任务详情与返回内容')!;
  (details.element as HTMLDetailsElement).open = true;
  await details.trigger('toggle'); await flushPromises();
  expect(calls.jobResult).toHaveBeenCalledTimes(1);
  calls.job.mockResolvedValue({ ...current, revision: 2 });
  await subscription.refresh();
  expect(calls.jobResult).toHaveBeenCalledTimes(1);
  calls.job.mockResolvedValue({ ...current, status: 'succeeded', revision: 3, execution: { ...current.execution, resultState: 'complete', providerStatus: 'succeeded' } });
  await subscription.refresh();
  expect(calls.jobResult).toHaveBeenCalledTimes(2);
  expect(calls.jobEvents).not.toHaveBeenCalled();
  expect(wrapper.text()).not.toContain('冻结输入');
  expect(wrapper.text()).not.toContain('do-not-display');
  expect(wrapper.text()).not.toContain('job.polling');
  expect(wrapper.text()).toContain('real-task-id');
  wrapper.unmount();
});

it("distinguishes local adapter failure from provider failure and transient network errors", async () => {
  calls.job.mockResolvedValue({ id: "accepted", status: "submitted", revision: 5, execution: { providerStatus: "submitted", queryError: { code: "local_adapter_error" }, availableActions: ["query_provider"] }, frozenInput: {} });
  const wrapper = mount(JobStatusCard, { props: { jobId: "accepted" } });
  await flushPromises();
  expect(wrapper.text()).toContain("本地查询组件异常");
  expect(wrapper.text()).toContain("已暂停自动核实");
  expect(wrapper.text()).not.toContain("火山任务明确失败");
  expect(wrapper.text()).toContain("重新核实外部进度");
  wrapper.unmount();
});

it.each([
  [403, "AccountOverdueError", "火山因账户欠费拒绝本次提交"],
  [400, "InvalidParameter", "火山拒绝本次提交（HTTP 400）"],
  [429, "RateLimitExceeded", "火山拒绝本次提交（HTTP 429）"],
])("shows confirmed submission rejection separately from local failure (%s)", async (httpStatus, code, message) => {
  calls.job.mockResolvedValue({ id: "rejected", status: "failed", revision: 7, execution: { stage: "submit", availableActions: [] }, error: { code, httpStatus, submissionUnknown: false, message: "Provider rejection" }, frozenInput: {} });
  const wrapper = mount(JobStatusCard, { props: { jobId: "rejected" } });
  await flushPromises();
  expect(wrapper.text()).toContain(message);
  expect(wrapper.text()).not.toContain("本地处理或接收阶段中断");
  expect(wrapper.text()).not.toContain("火山任务明确失败");
  expect(calls.recoverJob).not.toHaveBeenCalled();
  wrapper.unmount();
});
