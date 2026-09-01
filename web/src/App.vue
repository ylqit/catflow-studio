<script setup lang="ts">
import { ElMessage, ElMessageBox, ElNotification } from "element-plus";
import { onBeforeUnmount, onMounted, provide, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "./api/client";
import {
  startRuntimeStatus,
  stopRuntimeStatus,
} from "./runtimeStatus";
import {
  clearCompletedTasks,
  cancelPersistentTask,
  recoverPersistentTask,
  registerTask,
  requestWorkspaceRefresh,
  startTaskCenter,
  stopTaskCenter,
  useTaskCenter,
} from "./tasks/taskCenter";
import type { TaskCenterItem } from "./tasks/taskCenter";
import ProjectRail from "./components/director/ProjectRail.vue";

const router = useRouter();
const taskDrawerVisible = ref(false);
const taskCenter = useTaskCenter();
provide("openGlobalTasks", () => {
  taskDrawerVisible.value = true;
});

function taskStatusText(task: Pick<
  TaskCenterItem,
  "status" | "kind" | "providerTaskId" | "cancellation"
>): string {
  if (task.status === "running" && [
    "story_diagnosis",
    "story_rewrite",
    "shot_suggestions",
    "shot_assistance",
  ].includes(task.kind)) return "Ark 分析中";
  if (task.status === "queued" && task.providerTaskId) return "Provider 排队";
  return ({
    queued: "排队中",
    pending: "等待提交",
    submitting: "提交 Provider 中",
    running: "Provider 生成中",
    awaiting_review: "等待人工审核",
    succeeded: "成功",
    failed: "失败",
    submission_unknown: "提交状态待对账",
    cancelling: "正在取消 Provider 排队任务",
    cancellation_unknown: "取消状态待对账",
    restart_pending: "服务重启后待恢复",
    cancelled: "已取消",
  } as Record<string, string>)[task.status] ?? task.status;
}

function taskStatusType(status: string): "success" | "warning" | "danger" | "info" {
  if (status === "succeeded") return "success";
  if (["failed", "submission_unknown", "cancellation_unknown"].includes(status)) return "danger";
  if (["awaiting_review", "restart_pending"].includes(status)) return "warning";
  return "info";
}

async function openTask(projectId?: string, shotId?: string, canvasNodeId?: string) {
  if (!projectId) return;
  taskDrawerVisible.value = false;
  await router.push({
    name: "project-production",
    params: { projectId },
    query: shotId
      ? { workspace: "video", tab: "generate", shot: shotId }
      : canvasNodeId
        ? { item: canvasNodeId }
        : {},
  });
  requestWorkspaceRefresh(projectId, shotId);
}

async function resumeTask(stepId: string, projectId?: string, shotId?: string) {
  try {
    const submitted = await api.resumeStep(stepId);
    registerTask(submitted.jobId, {
      kind: "resume_step",
      label: "恢复 Provider 任务查询",
      projectId,
      shotId,
      operationKey: "resume",
    });
    ElMessage.success("已登记恢复查询，页面可继续操作");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

function lifecycleStage(task: TaskCenterItem): number {
  const providerStatus = task.cancellation?.providerStatus ?? task.progress?.providerStatus;
  if (["running", "succeeded", "failed"].includes(providerStatus ?? "")) return 3;
  if (providerStatus === "queued" || task.providerTaskId) return 2;
  if (["submitting", "submission_unknown"].includes(task.status)) return 1;
  return 0;
}

function cancellationVisible(task: TaskCenterItem): boolean {
  return Boolean(task.cancellation && [
    "pending",
    "queued",
    "submitting",
    "running",
    "submission_unknown",
    "cancelling",
    "cancellation_unknown",
  ].includes(task.status));
}

async function recoverTask(task: TaskCenterItem) {
  if (!task.recovery?.allowed || !task.stepId) {
    ElMessage.error(task.recovery?.disabledReason || "该任务当前不允许安全恢复");
    return;
  }
  const resumesProviderTracking = task.recovery.mode === "resume_provider_tracking";
  try {
    await ElMessageBox.confirm(
      resumesProviderTracking
        ? "将继续查询已经提交的同一个 Provider 任务号；不会创建第二个视频任务，也不会再次产生生成调用。"
        : "将复用原冻结输入、输入哈希与费用确认，继续当前角色设计阶段中尚未提交 Provider 的图片任务；不会重新生成创意、事件、剧情或视频。",
      resumesProviderTracking ? "确认继续跟踪原 Provider 任务" : "确认从调度失败步骤继续",
      {
        confirmButtonText: task.recovery.label || "从失败步骤继续",
        cancelButtonText: "取消",
        type: "warning",
      },
    );
    await recoverPersistentTask(task);
    ElMessage.success(
      resumesProviderTracking
        ? "已恢复原 Provider 任务跟踪，没有创建新的生成调用"
        : "同一角色设计任务已重新排队",
    );
  } catch (error) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

async function cancelTask(task: TaskCenterItem) {
  if (!task.cancellation?.allowed || !task.stepId) {
    ElMessage.error(task.cancellation?.disabledReason || "该任务当前不允许取消");
    return;
  }
  const beforeProvider = task.cancellation.mode === "local_before_provider";
  try {
    await ElMessageBox.confirm(
      beforeProvider
        ? "该任务尚未提交 Provider。确认后只取消本地排队任务，Provider 调用为 0 次。"
        : "系统会先查询 Provider；只有仍处于 queued 才发送远端取消。任务已经提交过，费用是否产生以 Provider 账单为准。",
      beforeProvider ? "确认提交前取消" : "确认取消 Provider 排队任务",
      {
        confirmButtonText: task.cancellation.label,
        cancelButtonText: "返回",
        type: "warning",
      },
    );
    const cancelled = await cancelPersistentTask(task, "Web 任务中心人工取消");
    ElMessage.success(
      cancelled.progress?.message
      ?? (beforeProvider ? "已在提交 Provider 前取消" : "Provider 排队任务已取消"),
    );
  } catch (error) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

onMounted(() => {
  startRuntimeStatus();
  startTaskCenter();
});
onBeforeUnmount(() => {
  stopRuntimeStatus();
  stopTaskCenter();
});

watch(() => taskCenter.lastNotification.value, (event) => {
  if (!event || ![
    "awaiting_review",
    "succeeded",
    "failed",
    "submission_unknown",
    "cancellation_unknown",
  ].includes(event.item.status)) return;
  const tagType = taskStatusType(event.item.status);
  const notificationType = tagType === "danger" ? "error" : tagType;
  ElNotification({
    title: event.item.label,
    message: taskStatusText(event.item),
    type: notificationType,
    duration: ["failed", "submission_unknown", "cancellation_unknown"].includes(
      event.item.status,
    )
      ? 0
      : event.item.status === "awaiting_review"
        ? 6500
        : 4500,
    onClick: () => void openTask(
      event.item.projectId,
      event.item.shotId,
      event.item.canvasNodeId,
    ),
  });
});
</script>

<template>
  <div class="shell">
    <ProjectRail
      :task-count="taskCenter.activeCount.value + taskCenter.attentionCount.value"
    />
    <main class="content"><router-view /></main>
    <el-drawer v-model="taskDrawerVisible" title="全局任务中心" size="min(430px, 100vw)">
      <div class="task-actions">
        <span>长任务在后台继续执行。清理已完成只隐藏浏览器记录，不删除服务端任务、Provider 任务或媒体资产。</span>
        <el-button size="small" @click="clearCompletedTasks">清理已完成</el-button>
      </div>
      <el-alert v-if="taskCenter.connectionError.value" type="warning" :title="`任务状态暂不可达：${taskCenter.connectionError.value}`" :closable="false" />
      <el-empty v-if="!taskCenter.items.value.length" description="暂无任务" />
      <article v-for="task in taskCenter.items.value" :key="task.key" class="task-card">
        <div class="task-head">
          <b>{{ task.label }}<template v-if="task.attempt"> V{{ task.attempt }}</template></b>
          <el-tag :type="taskStatusType(task.status)">{{ taskStatusText(task) }}</el-tag>
        </div>
        <small>{{ task.model || task.kind }} · {{ task.createdAt ? new Date(task.createdAt).toLocaleString() : '刚刚' }}</small>
        <ol v-if="['pending', 'queued', 'submitting', 'running', 'cancelling'].includes(task.status)" class="task-lifecycle" aria-label="Provider 任务阶段">
          <li v-for="(stage, index) in ['本地排队', '正在提交 Provider', 'Provider 排队', 'Provider 生成中']" :key="stage" :class="{ current: lifecycleStage(task) === index, done: lifecycleStage(task) > index }">
            <i />{{ stage }}
          </li>
        </ol>
        <el-progress
          v-if="task.progress && typeof task.progress.percent === 'number' && ['queued', 'pending', 'running', 'submitting'].includes(task.status)"
          :percentage="task.progress.percent"
        />
        <p v-if="task.progress?.message" class="task-progress-message">{{ task.progress.message }}</p>
        <p v-if="task.resultSummary?.message" class="task-result-message">{{ String(task.resultSummary.message) }}</p>
        <p v-if="task.error">{{ String(task.error.message ?? task.error.code ?? '任务失败') }}</p>
        <section v-if="task.recovery" class="task-recovery" aria-label="任务恢复策略">
          <b>{{ task.recovery.label || '从失败步骤继续' }}</b>
          <span v-if="task.error?.failedStep">失败步骤：{{ task.error.failedStep }}</span>
          <span>供应商状态：{{ task.recovery.mode === 'resume_pre_provider' ? '尚未提交' : task.recovery.mode === 'resume_provider_tracking' ? '已提交，复用原任务号' : '需要后端确认' }}</span>
          <small v-if="!task.recovery.allowed">{{ task.recovery.disabledReason }}</small>
        </section>
        <section v-if="cancellationVisible(task)" class="task-cancellation" aria-label="任务取消策略">
          <b>{{ task.cancellation?.label }}</b>
          <span>Provider 状态：{{ task.cancellation?.providerStatus }}</span>
          <span v-if="task.cancellation?.costMayAlreadyApply">任务已经提交过，费用可能已经产生。</span>
          <small v-if="!task.cancellation?.allowed">{{ task.cancellation?.disabledReason }}</small>
        </section>
        <div class="task-card-actions">
          <el-button v-if="task.projectId" size="small" @click="openTask(task.projectId, task.shotId, task.canvasNodeId)">打开对应节点</el-button>
          <el-button
            v-if="task.stepId && task.recovery"
            type="primary"
            size="small"
            :disabled="!task.recovery.allowed || taskCenter.recoveringStepIds.value.includes(task.stepId)"
            :loading="taskCenter.recoveringStepIds.value.includes(task.stepId)"
            :title="task.recovery.disabledReason || task.recovery.label || '从失败步骤继续'"
            @click="recoverTask(task)"
          >{{ task.recovery.label || '从失败步骤继续' }}</el-button>
          <el-button
            v-if="task.stepId && cancellationVisible(task)"
            type="warning"
            size="small"
            :disabled="!task.cancellation?.allowed || taskCenter.cancellingStepIds.value.includes(task.stepId)"
            :loading="taskCenter.cancellingStepIds.value.includes(task.stepId)"
            :title="task.cancellation?.disabledReason || task.cancellation?.label"
            @click="cancelTask(task)"
          >{{ task.cancellation?.label }}</el-button>
          <el-button v-if="task.stepId && task.providerTaskId && ['submission_unknown', 'restart_pending'].includes(task.status)" size="small" @click="resumeTask(task.stepId, task.projectId, task.shotId)">按 Provider ID 对账</el-button>
        </div>
      </article>
    </el-drawer>
  </div>
</template>

<style scoped>
.shell { width: 100%; height: 100%; display: grid; grid-template-columns: 64px minmax(0, 1fr); overflow: hidden; }.content { min-width: 0; min-height: 0; overflow: hidden; background: #0d1016; }
.task-actions, .task-card { display: grid; gap: 8px; }.task-actions { margin-bottom: 12px; color: #8d9ab0; }.task-card { padding: 12px; margin-bottom: 10px; border: 1px solid #303c4f; border-radius: 8px; background: #111824; }.task-head, .task-card-actions { display: flex; gap: 8px; align-items: center; justify-content: space-between; flex-wrap: wrap; }.task-card small { color: #8592a6; }.task-card p { margin: 0; color: #f3a6a6; white-space: pre-wrap; }
.task-card .task-progress-message { color: #9eb4cc; }.task-card .task-result-message { color: #8ed4ad; }
.task-recovery { display: grid; gap: 4px; padding: 10px; color: #dbc59e; background: #30271b; border: 1px solid #765a2e; border-radius: 7px; }.task-recovery b { color: #ffd69a; }.task-recovery small { color: #f0ad98; }
.task-cancellation { display: grid; gap: 4px; padding: 10px; color: #cbd6e5; background: #202936; border: 1px solid #4a5d75; border-radius: 8px; }.task-cancellation b { color: #f2c77d; }.task-cancellation small { color: #f0ad98; }
.task-lifecycle { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin: 2px 0; padding: 0; list-style: none; }.task-lifecycle li { display: grid; grid-template-columns: 8px minmax(0, 1fr); gap: 5px; align-items: center; color: #657286; font-size: 10px; line-height: 1.3; }.task-lifecycle i { width: 7px; height: 7px; border-radius: 50%; background: #3b4656; }.task-lifecycle li.done { color: #829db7; }.task-lifecycle li.done i { background: #4f7da5; }.task-lifecycle li.current { color: #f2d39d; font-weight: 700; }.task-lifecycle li.current i { background: #f1b85e; box-shadow: 0 0 0 3px rgb(241 184 94 / 14%); }
@media (max-width: 720px) {
  .shell { position: relative; display: block; padding-bottom: 58px; }
  .content { width: 100%; height: 100%; }
}
</style>
