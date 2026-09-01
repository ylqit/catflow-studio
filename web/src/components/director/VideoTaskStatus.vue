<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed } from "vue";

import type { PersistentTaskDto } from "../../api/types";
import { cancelPersistentTask, useTaskCenter } from "../../tasks/taskCenter";

const props = defineProps<{ tasks: PersistentTaskDto[] }>();
const emit = defineEmits<{ changed: [task: PersistentTaskDto] }>();
const taskCenter = useTaskCenter();

const orderedTasks = computed(() => [...props.tasks].sort((left, right) => (
  String(right.updatedAt ?? right.createdAt ?? "").localeCompare(String(left.updatedAt ?? left.createdAt ?? ""))
)));

function lifecycleStep(task: PersistentTaskDto): number {
  const provider = task.cancellation?.providerStatus ?? task.progress?.providerStatus;
  if (["running", "succeeded", "failed"].includes(provider ?? "")) return 4;
  if (provider === "queued" || task.providerTaskId) return 3;
  if (["submitting", "submission_unknown"].includes(task.status)) return 2;
  return 1;
}

function statusLabel(task: PersistentTaskDto): string {
  if (task.status === "submission_unknown") return "提交状态待对账";
  if (task.status === "cancellation_unknown") return "取消状态待对账";
  if (task.status === "cancelling") return "正在取消 Provider 排队任务";
  if (task.status === "cancelled") return "已取消";
  if (task.status === "failed") return "生成失败";
  if (task.status === "succeeded") return "生成完成";
  return (["本地排队", "正在提交 Provider", "Provider 排队", "Provider 生成中"])[lifecycleStep(task) - 1];
}

async function cancel(task: PersistentTaskDto) {
  if (!task.cancellation?.allowed) {
    ElMessage.warning(task.cancellation?.disabledReason || "该任务当前不可取消");
    return;
  }
  const beforeProvider = task.cancellation.mode === "local_before_provider";
  try {
    await ElMessageBox.confirm(
      beforeProvider
        ? "该任务尚未提交 Provider。取消后 Provider 调用为 0，是否继续？"
        : "该任务已经提交 Provider。将尝试取消仍在排队的远端任务；费用是否产生以 Provider 账单为准。",
      task.cancellation.label,
      { confirmButtonText: task.cancellation.label, cancelButtonText: "保留任务", type: "warning" },
    );
  } catch {
    return;
  }
  try {
    const result = await cancelPersistentTask(task, "视频生成工作区人工取消");
    emit("changed", result);
    ElMessage.success(beforeProvider ? "已在提交 Provider 前取消" : "Provider 排队取消请求已完成");
  } catch (reason) {
    ElMessage.error(`取消失败：${reason instanceof Error ? reason.message : String(reason)}`);
  }
}
</script>

<template>
  <section class="video-task-status" aria-label="视频任务状态">
    <header><div><span>EXECUTION</span><b>生成任务</b></div><small>{{ orderedTasks.length }} 项</small></header>
    <p v-if="!orderedTasks.length" class="task-empty">当前镜头没有视频生成任务。</p>
    <article v-for="task in orderedTasks" :key="task.stepId" :data-status="task.status">
      <header><div><b>{{ statusLabel(task) }}</b><small>{{ task.model || task.provider || '等待运行配置' }}</small></div><mark>{{ task.status }}</mark></header>
      <ol class="task-lifecycle" :aria-label="`${statusLabel(task)}的执行阶段`">
        <li v-for="(label,index) in ['本地排队','提交 Provider','Provider 排队','Provider 生成']" :key="label" :class="{ reached: lifecycleStep(task) >= index + 1 }"><i />{{ label }}</li>
      </ol>
      <p>{{ task.progress?.message || task.cancellation?.disabledReason || task.operationKey }}</p>
      <dl>
        <div><dt>Provider Task</dt><dd>{{ task.providerTaskId || '尚未取得' }}</dd></div>
        <div><dt>费用边界</dt><dd>{{ task.cancellation?.costMayAlreadyApply ? '可能已经产生' : '尚未提交时为 0' }}</dd></div>
      </dl>
      <button
        v-if="task.cancellation"
        type="button"
        :disabled="!task.cancellation.allowed || taskCenter.cancellingStepIds.value.includes(task.stepId)"
        :title="task.cancellation.disabledReason || task.cancellation.label"
        @click="cancel(task)"
      >{{ task.cancellation.label }}</button>
    </article>
  </section>
</template>

<style scoped>
.video-task-status { display: grid; gap: 9px; }.video-task-status>header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }.video-task-status>header div { display: grid; gap: 2px; }.video-task-status>header span { color: #6b87a4; font-size: 9px; font-weight: 800; letter-spacing: .12em; }.video-task-status>header small,.task-empty { color: #7f8d9d; }.task-empty { margin: 0; padding: 12px; background: #171e26; border: 1px solid #2d3742; border-radius: 9px; }
.video-task-status article { display: grid; gap: 9px; padding: 11px; background: #171e26; border: 1px solid #303b47; border-radius: 10px; }.video-task-status article[data-status="failed"],.video-task-status article[data-status="submission_unknown"],.video-task-status article[data-status="cancellation_unknown"] { border-color: #72443d; }.video-task-status article>header { display: flex; justify-content: space-between; gap: 8px; }.video-task-status article>header div { display: grid; gap: 3px; }.video-task-status article>header small { color: #8090a1; }.video-task-status mark { height: fit-content; padding: 3px 6px; color: #9fb2c5; background: #29333e; border-radius: 999px; font-size: 9px; }.video-task-status article>p { margin: 0; color: #8796a7; line-height: 1.5; }
.task-lifecycle { display: grid; grid-template-columns: repeat(4,1fr); gap: 4px; margin: 0; padding: 0; list-style: none; }.task-lifecycle li { min-width: 0; display: grid; grid-template-columns: 8px minmax(0,1fr); align-items: center; gap: 4px; color: #617080; font-size: 8px; }.task-lifecycle i { width: 7px; height: 7px; background: #37424d; border-radius: 999px; }.task-lifecycle li.reached { color: #9fc1df; }.task-lifecycle li.reached i { background: #4c91c8; }
.video-task-status dl { margin: 0; display: grid; gap: 5px; }.video-task-status dl div { display: grid; grid-template-columns: 82px minmax(0,1fr); gap: 7px; }.video-task-status dt { color: #718092; }.video-task-status dd { margin: 0; overflow-wrap: anywhere; color: #aab8c7; }.video-task-status article>button { min-height: 44px; padding: 0 11px; color: #d8e2ed; background: #27313c; border: 1px solid #455463; border-radius: 8px; cursor: pointer; }.video-task-status article>button:disabled { opacity: .45; cursor: not-allowed; }
</style>
