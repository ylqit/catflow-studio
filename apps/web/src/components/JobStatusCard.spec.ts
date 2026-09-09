import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import JobStatusCard from "./JobStatusCard.vue";

const calls = vi.hoisted(() => ({ job: vi.fn(), jobResult: vi.fn(), jobEvents: vi.fn(), prepareJobReplacement: vi.fn(), replaceUnknownJob: vi.fn(), recoverJob: vi.fn() }));
const subscription = vi.hoisted(() => ({ refresh: async () => {} }));
vi.mock("../api/client", () => ({ api: calls }));
vi.mock("../jobUpdates", () => ({ subscribeJobs: (_scope: unknown, refresh: () => Promise<void>) => { subscription.refresh = refresh; void refresh(); return () => {}; } }));

beforeEach(() => {
  vi.clearAllMocks();
  calls.job.mockResolvedValue({ id: "unknown", status: "submission_unknown", revision: 3, execution: { availableActions: ["prepare_replacement"], usageUnconfirmed: true, resultState: "missing" }, frozenInput: {} });
  calls.jobResult.mockResolvedValue({ state: "missing" }); calls.jobEvents.mockResolvedValue({ items: [] });
  calls.prepareJobReplacement.mockResolvedValue({ executionInputHash: "frozen-current-input", model: "current-model", provider: "ark", input: { prompt: "Current inputs" } });
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
