import type { JobDto } from "./api/types";

export interface PaidModelRuntime {
  worker?: {
    ready: boolean;
    state: "ready" | "offline" | "stale" | "restarting" | "degraded";
  };
  provider: {
    apiKeyConfigured: boolean;
    paidCallsEnabled: boolean;
  };
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
    label: "提交状态待确认",
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
