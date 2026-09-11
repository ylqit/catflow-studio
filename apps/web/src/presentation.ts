import type { JobDto } from "./api/types";

export interface PaidModelRuntime {
  worker?: {
    ready: boolean;
    state: "ready" | "offline" | "stale" | "restarting" | "degraded";
  };
  provider: {
    apiKeyConfigured: boolean;
    paidCallsEnabled: boolean;
    videoGeneration?: {
      maximumImageReferences: number;
      maximumVideoReferences: number;
      previousEpisodeVideoSupported: boolean;
    };
  };
  objectPublisher?: { ready: boolean };
}

export type PresentationTone = "neutral" | "active" | "good" | "warn" | "danger";

export interface StatusPresentation {
  label: string;
  description: string;
  tone: PresentationTone;
  terminal: boolean;
}

const JOB_PRESENTATIONS: Record<JobDto["status"], StatusPresentation> = {
  queued: {
    label: "等待生成",
    description: "任务已加入队列，可以离开此页面。",
    tone: "active",
    terminal: false,
  },
  submitting: {
    label: "正在准备",
    description: "正在准备并提交生成请求。",
    tone: "active",
    terminal: false,
  },
  submitted: {
    label: "已提交",
    description: "生成请求已提交。",
    tone: "active",
    terminal: false,
  },
  polling: {
    label: "正在生成",
    description: "正在生成，页面关闭后任务仍会继续。",
    tone: "active",
    terminal: false,
  },
  storing: {
    label: "正在保存",
    description: "结果已返回，正在安全保存。",
    tone: "active",
    terminal: false,
  },
  succeeded: {
    label: "已完成",
    description: "结果已经保存，可以继续下一步。",
    tone: "good",
    terminal: true,
  },
  failed: {
    label: "生成失败",
    description: "本次生成未完成，请查看原因后再决定是否重试。",
    tone: "danger",
    terminal: true,
  },
  submission_unknown: {
    label: "提交结果待核实",
    description: "提交状态需要人工确认，系统不会自动重复生成。",
    tone: "warn",
    terminal: true,
  },
  cancel_requested: {
    label: "正在取消",
    description: "正在请求取消任务。",
    tone: "active",
    terminal: false,
  },
  cancelled: {
    label: "已取消",
    description: "任务已取消。",
    tone: "neutral",
    terminal: true,
  },
};

export function jobPresentation(status: JobDto["status"]): StatusPresentation {
  return JOB_PRESENTATIONS[status];
}

/** Present execution facts consistently across task cards and media placeholders. */
export function jobExecutionPresentation(job: JobDto): StatusPresentation {
  const base = jobPresentation(job.status);
  const execution = job.execution;
  if (job.status === 'submission_unknown') return { ...base, description: '提交结果待核实，暂未收到可查询编号。系统不会自动重复生成。' };
  if (execution?.queryError) return {
    ...base, tone: 'warn',
    label: '外部进度待核实',
    description: execution.queryError.code === 'local_adapter_error'
      ? '本地查询组件异常，尚未取得火山最新状态。已暂停自动核实，修复后可重新核实原任务。'
      : `暂时无法查询最新进度；上次确认：${execution.providerStatus ?? '未取得外部状态'}。`,
  };
  if (job.status === 'cancelled' || job.status === 'cancel_requested') return base;
  const phase = job.kind === 'plan_video_edit' ? ['修改建议', '保存修改建议']
    : job.kind === 'extract_continuity_frames' ? ['参考素材', '裁切参考视频与提取参考图片']
    : job.kind === 'render_edit_preview' ? ['接回预览', '合成时间线与声音，准备接回预览']
    : ['结果', '保存结果'];
  if (job.status === 'succeeded') return { ...base, label: `${phase[0]}已保存`, description: `${phase[0]}已保存，等待人工查看。` };
  if (job.status === 'failed') {
    if (execution?.providerStatus === 'failed') return { ...base, description: '火山任务明确失败，返回详情已保存。' };
    if (execution?.stage === 'submit' && job.error?.submissionUnknown === false && job.error.httpStatus) return {
      ...base, label: '提交被拒绝', description: job.error.code === 'AccountOverdueError'
        ? '火山因账户欠费拒绝本次提交；未创建生成任务，拒绝回执已保存。'
        : `火山拒绝本次提交（HTTP ${job.error.httpStatus}），拒绝回执已保存。`,
    };
    const received = ['completed', 'succeeded'].includes(execution?.providerStatus ?? '') || execution?.resultState === 'complete';
    if (received && ['generate_video', 'regenerate_video_segment'].includes(job.kind)) return { ...base, label: '结果下载或保存中断', description: '外部已生成，结果下载或保存未完成。可恢复原任务的结果处理。' };
    return { ...base, label: `${phase[0]}未完成`, description: `${phase[1]}阶段中断，请查看错误后恢复。` };
  }
  if (job.kind === 'extract_continuity_frames' || job.kind === 'render_edit_preview') return {
    ...base, label: job.kind === 'extract_continuity_frames' ? '正在准备参考' : '正在准备接回预览',
    description: `${phase[1]}，不调用生成模型。`,
  };
  if (job.status === 'storing' || ['completed', 'succeeded'].includes(execution?.providerStatus ?? '')) {
    if (['generate_video', 'regenerate_video_segment'].includes(job.kind)) return { ...base, label: '正在下载并保存视频', description: '云端已生成，正在下载结果并保存完整候选。' };
    return { ...base, label: `正在${phase[1]}`, description: `${phase[1]}，完成后可查看。` };
  }
  if (job.kind === 'plan_video_edit') return { ...base, label: '正在整理修改建议', description: 'AI 正在整理文字方案，完成后由你决定是否填入。' };
  if (job.status === 'submitting') return { ...base, description: '正在提交或接收' };
  return base;
}

export function paidModelBlockedReason(
  runtime: PaidModelRuntime | null | undefined,
): string {
  if (!runtime) return "正在检查模型服务，请稍候。";
  const workerReason = backgroundTaskBlockedReason(runtime);
  if (workerReason) return workerReason;
  if (!runtime.provider.apiKeyConfigured) return "尚未配置模型服务密钥，请先前往运行设置。";
  if (!runtime.provider.paidCallsEnabled) return "新的模型调用当前已关闭，请先在运行设置中启用。";
  return "";
}

export function backgroundTaskBlockedReason(
  runtime: Pick<PaidModelRuntime, "worker"> | null | undefined,
): string {
  if (!runtime) return "正在检查后台任务，请稍候。";
  if (!runtime.worker || runtime.worker.ready) return "";
  return runtime.worker.state === "degraded"
    ? "后台任务需要检查，恢复前不能开始新的任务。"
    : "后台任务暂时不可用，系统正在自动恢复。";
}

export type BillingStatus = NonNullable<JobDto["billingStatus"]>;

export interface BillingPresentation {
  label: string;
  detail: string;
}

export interface ErrorPresentation {
  message: string;
  technicalMessage: string;
}

const ERROR_MESSAGES: Array<{ matches: string; message: string }> = [
  {
    matches: "confirm the episode's incoming continuity",
    message: "请先确认本集连续性：决定哪些角色状态、地点和道具需要继承、调整或重置。此条件尚未满足，视频请求未提交。",
  },
  {
    matches: "director_output_validation_failed",
    message: "模型返回的分镜结构不完整，本次没有生成新版本；当前版本保持不变。",
  },
  {
    matches: "worker_unavailable",
    message: "后台任务暂时不可用，系统正在自动恢复，请稍后再试。",
  },
  {
    matches: "shot plan asset selection is outdated",
    message: "角色或环境已经更新，请重新生成分镜后再生成视频。",
  },
  {
    matches: "shot plan is outdated",
    message: "当前分镜已经过期，请重新生成或保存分镜后再继续。",
  },
  {
    matches: "planner context revision changed",
    message: "故事内容已经更新，请刷新页面后再试。",
  },
  {
    matches: "input hash",
    message: "页面内容已经变化，请等待预览更新后再试。",
  },
  {
    matches: "reference publisher",
    message: "临时视频发布尚未就绪，请到运行设置完成检查。",
  },
];

export function errorPresentation(reason: unknown, fallback: string): ErrorPresentation {
  const message = reason instanceof Error ? reason.message : typeof reason === "string" ? reason : fallback;
  if (message.startsWith("环境草稿已在其他窗口更新")) return {
    message: "环境草稿已在其他窗口更新。本窗口文字已保留，请先复制需要的修改，再读取最新草稿核对。",
    technicalMessage: message,
  };
  if (message.startsWith("故事来源已变化")) return { message, technicalMessage: message };
  const normalized = message.toLowerCase();
  const known = ERROR_MESSAGES.find((item) => normalized.includes(item.matches));
  return {
    message: known?.message ?? `${fallback}，请查看技术详情后重试。`,
    technicalMessage: message,
  };
}

export function billingPresentation(
  status: BillingStatus | undefined,
  costMicros: number | null | undefined,
  _provider?: string,
): BillingPresentation {
  const formattedCost = costMicros == null ? null : `¥${(costMicros / 1_000_000).toFixed(4)}`;

  if (status === "calculated" && formattedCost) {
    return { label: formattedCost, detail: `已按任务创建时的费率表计算：${formattedCost}。` };
  }
  if (status === "provider_adjusted" && formattedCost) {
    return { label: formattedCost, detail: `已按模型服务返回的最终费用记录：${formattedCost}。` };
  }
  if (status === "unpriced") {
    return { label: "费用待核价", detail: "任务已记录实际用量，待补充对应费率。" };
  }
  if (status === "usage_reported") {
    return { label: "用量已记录", detail: "模型服务已返回实际用量，费用仍在计算。" };
  }
  return { label: "费用计算中", detail: "任务完成后会根据实际用量更新费用。" };
}
