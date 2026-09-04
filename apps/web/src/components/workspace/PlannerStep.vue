<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "../../api/client";
import type { LifeStoryProposalDto, PlannerSnapshotDto } from "../../api/types";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";
import { billingPresentation, errorPresentation, jobPresentation, paidModelBlockedReason, type PaidModelRuntime } from "../../presentation";

const props = defineProps<{ projectId: string; runtime?: PaidModelRuntime | null }>();
const emit = defineEmits<{ changed: [] }>();
const snapshot = ref<PlannerSnapshotDto | null>(null);
const message = ref("");
const sending = ref(false);
const adopting = ref<string | null>(null);
const error = ref("");
const errorDetail = ref("");

const latestJobPresentation = computed(() => snapshot.value?.latestJob
  ? jobPresentation(snapshot.value.latestJob.status)
  : null);
const latestBillingPresentation = computed(() => snapshot.value?.latestJob
  ? billingPresentation(snapshot.value.latestJob.billingStatus, snapshot.value.latestJob.actualCostMicros, snapshot.value.latestJob.provider)
  : null);
const latestJobRunning = computed(() => Boolean(
  snapshot.value?.latestJob
  && !["succeeded", "failed", "cancelled"].includes(snapshot.value.latestJob.status),
));
const paidBlockedReason = computed(() => paidModelBlockedReason(props.runtime));

function proposalStatus(status: "draft" | "adopted" | "outdated") {
  return { draft: "待采用", adopted: "已采用", outdated: "已过期" }[status];
}

function compactTitle(title: string) {
  return title.length > 16 ? `${title.slice(0, 14)}…` : title;
}

function isRedundantSummary(proposal: LifeStoryProposalDto) {
  const normalized = proposal.summary.replace(/[“”"'，。；：、\s]/g, "");
  const title = proposal.title.replace(/[“”"'，。；：、\s]/g, "");
  return normalized === title || (normalized.includes(title) && /^(围绕|通过|以)/.test(proposal.summary));
}

async function load() {
  error.value = "";
  try {
    snapshot.value = await api.planner(props.projectId);
  } catch (reason) {
    const failure = errorPresentation(reason, "故事灵感暂时无法读取");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  }
}

async function sendMessage() {
  if (!snapshot.value || !message.value.trim() || paidBlockedReason.value || latestJobRunning.value || sending.value) return;
  sending.value = true;
  error.value = "";
  const text = message.value.trim();
  const scope = `planner:${props.projectId}`;
  const fingerprint = `${snapshot.value.contextRevision}:${text}`;
  try {
    await api.plannerMessage(props.projectId, {
      text,
      expectedContextRevision: snapshot.value.contextRevision,
      idempotencyKey: pendingIdempotencyKey(scope, fingerprint),
    });
    settleIdempotencyKey(scope, fingerprint);
    message.value = "";
    await load();
  } catch (reason) {
    const failure = errorPresentation(reason, "故事没有成功开始生成");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    sending.value = false;
  }
}

async function adopt(proposalId: string) {
  adopting.value = proposalId;
  try {
    await api.adoptProposal(props.projectId, proposalId);
    await load();
    emit("changed");
  } catch (reason) {
    const failure = errorPresentation(reason, "故事没有成功采用");
    error.value = failure.message;
    errorDetail.value = failure.technicalMessage;
  } finally {
    adopting.value = null;
  }
}

defineExpose({ reload: load });
onMounted(load);
</script>

<template>
  <section class="planner-layout">
    <div class="conversation card">
      <header class="panel-head">
        <div><p class="eyebrow">故事灵感</p><h2>聊聊这个小故事</h2></div>
        <button class="ghost" @click="load">刷新</button>
      </header>
      <section v-if="snapshot?.latestJob && latestJobPresentation" class="job-status" :class="latestJobPresentation.tone">
        <div data-testid="planner-job-summary" class="job-summary">
          <b>{{ latestJobPresentation.label }}</b>
          <span>{{ snapshot.latestJob.error?.message || latestJobPresentation.description }}</span>
        </div>
        <details data-testid="planner-job-details" class="job-record">
          <summary>查看生成记录</summary>
          <dl>
            <div><dt>任务编号</dt><dd><code>{{ snapshot.latestJob.id }}</code></dd></div>
            <div><dt>模型服务</dt><dd>{{ snapshot.latestJob.provider || "旧任务未记录" }} · {{ snapshot.latestJob.model || "旧任务未记录" }}</dd></div>
            <div><dt>原始状态</dt><dd>{{ snapshot.latestJob.status }}</dd></div>
            <div v-if="snapshot.latestJob.actualUsage"><dt>实际用量</dt><dd>{{ JSON.stringify(snapshot.latestJob.actualUsage) }}</dd></div>
            <div v-if="latestBillingPresentation"><dt>费用</dt><dd>{{ latestBillingPresentation.label }} · {{ latestBillingPresentation.detail }}</dd></div>
            <div v-if="snapshot.latestJob.error?.code"><dt>错误代码</dt><dd>{{ snapshot.latestJob.error.code }}</dd></div>
            <div v-if="snapshot.latestJob.error?.requestId"><dt>请求编号</dt><dd>{{ snapshot.latestJob.error.requestId }}</dd></div>
          </dl>
        </details>
      </section>
      <div class="messages">
        <div v-if="!snapshot?.messages.length" class="welcome-message">
          <span class="director-avatar">导</span>
          <div>
            <strong>先告诉我一个很小的日常瞬间。</strong>
            <p>我会把它收敛成一个触发、一个人猫互动、一个可见变化和一个温暖落点。</p>
            <div class="suggestions">
              <button v-for="idea in ['雨天擦爪', '折叠毛巾', '分享窗边阳光']" :key="idea" @click="message = idea">{{ idea }}</button>
            </div>
          </div>
        </div>
        <details v-if="snapshot?.messages.length && snapshot.proposals.length" data-testid="planner-conversation-history" class="conversation-history"><summary>查看历史对话（{{ snapshot.messages.length }} 条）</summary><article v-for="item in snapshot.messages" :key="item.id" class="message" :class="item.role"><span>{{ item.role === "assistant" ? "导" : "我" }}</span><p>{{ item.content }}</p></article></details>
        <template v-else><article v-for="item in snapshot?.messages" :key="item.id" class="message" :class="item.role"><span>{{ item.role === "assistant" ? "导" : "我" }}</span><p>{{ item.content }}</p></article></template>
      </div>
      <form class="composer" @submit.prevent="sendMessage">
        <textarea v-model="message" rows="3" maxlength="4000" placeholder="例如：雨停后，孩子发现猫咪在门口留下一串湿脚印……" @keydown.ctrl.enter="sendMessage" />
        <div><small>{{ latestJobRunning ? "当前故事任务正在处理，完成前不会创建第二条任务。" : paidBlockedReason || "Ctrl + Enter · 本次会使用付费模型，完成后显示实际用量。" }}</small><button class="primary" :disabled="sending || latestJobRunning || !message.trim() || Boolean(paidBlockedReason)"><span v-if="sending" class="spinner" />生成提案</button></div>
      </form>
    </div>

    <aside class="proposal-panel card">
      <header class="panel-head"><div><p class="eyebrow">故事候选</p><h2>选择一个故事</h2></div><span class="pill">{{ snapshot?.proposals.length ?? 0 }} 个</span></header>
      <div v-if="error" class="notice error creator-error"><p>{{ error }}</p><details v-if="errorDetail && errorDetail !== error"><summary>技术详情</summary><code>{{ errorDetail }}</code></details></div>
      <div v-if="!snapshot?.proposals.length" class="empty">故事生成后会出现在这里。</div>
      <article v-for="proposal in snapshot?.proposals" :key="proposal.id" class="proposal">
        <div class="proposal-title"><h3 :title="proposal.title">{{ compactTitle(proposal.title) }}</h3><span class="pill" :class="{ good: proposal.status === 'adopted', warn: proposal.status === 'outdated' }">{{ proposalStatus(proposal.status) }}</span></div>
        <p v-if="!isRedundantSummary(proposal)" class="proposal-summary">{{ proposal.summary }}</p><details v-else class="legacy-summary"><summary>查看原摘要</summary><p class="proposal-summary">{{ proposal.summary }}</p></details>
        <ol class="micro-chain">
          <li><b>触发</b><span>{{ proposal.microEvent.trigger }}</span></li>
          <li><b>孩子</b><span>{{ proposal.microEvent.childAction }}</span></li>
          <li><b>猫咪</b><span>{{ proposal.microEvent.catResponse }}</span></li>
          <li><b>变化</b><span>{{ proposal.microEvent.visibleChange }}</span></li>
          <li><b>结尾</b><span>{{ proposal.microEvent.warmEnding }}</span></li>
        </ol>
        <div class="proposal-foot"><span>{{ proposal.targetDurationSeconds }} 秒 · {{ proposal.dialoguePolicy === "none" ? "无对白" : "极少对白" }}</span><button v-if="proposal.status === 'draft'" class="secondary" :disabled="adopting === proposal.id" @click="adopt(proposal.id)">{{ adopting === proposal.id ? "采用中" : "采用为故事" }}</button></div>
      </article>
    </aside>
  </section>
</template>

<style scoped>
.planner-layout { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(390px, .8fr); gap: 20px; min-height: calc(100vh - 215px); }
.conversation, .proposal-panel { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.panel-head { display: flex; align-items: center; justify-content: space-between; padding: 22px 24px 17px; border-bottom: 1px solid var(--line); }
.panel-head h2 { margin: 0; font-size: 20px; }
.job-status { display: grid; gap: 9px; margin: 14px 20px 0; padding: 12px 14px; border: 1px solid #d9cdbf; border-radius: 12px; background: #faf7f2; font-size: 11px; }
.job-summary { display: grid; grid-template-columns: auto 1fr; gap: 10px; min-width: 0; }
.job-summary span { color: var(--muted); }
.job-status code { overflow: hidden; color: var(--muted); text-overflow: ellipsis; }
.job-status.warn, .job-status.danger { border-color: #d8aaa2; background: #fff3f1; }
.job-status.good { border-color: #bed0c1; background: var(--sage-soft); }
.job-record summary { cursor: pointer; color: #7d6d62; font-weight: 700; }
.job-record dl { display: grid; gap: 6px; margin: 10px 0 0; padding-top: 10px; border-top: 1px solid var(--line); }
.job-record dl div { display: grid; grid-template-columns: 70px minmax(0, 1fr); gap: 8px; }
.job-record dt { color: var(--muted); }.job-record dd { margin: 0; overflow-wrap: anywhere; }
.messages { flex: 1; min-height: 300px; padding: 24px; overflow-y: auto; }
.conversation-history { padding: 12px 14px; border: 1px solid var(--line); border-radius: 12px; background: #faf7f2; }.conversation-history > summary, .legacy-summary > summary { cursor: pointer; color: var(--muted); font-size: 11px; font-weight: 700; }.legacy-summary { margin: 9px 0 12px; }.legacy-summary .proposal-summary { margin-bottom: 0; }
.welcome-message { display: flex; gap: 15px; padding: 18px; border-radius: 16px; background: #f8f1e7; line-height: 1.65; }
.welcome-message p { color: var(--muted); margin: 6px 0 13px; font-size: 13px; }
.director-avatar, .message > span { flex: 0 0 auto; width: 34px; height: 34px; border-radius: 12px; display: grid; place-items: center; color: white; background: #7c917e; font-size: 12px; }
.suggestions { display: flex; flex-wrap: wrap; gap: 8px; }
.suggestions button { padding: 7px 10px; border: 1px solid #dfd2c1; border-radius: 999px; background: #fffaf4; color: #7e6551; cursor: pointer; font-size: 12px; }
.message { display: flex; gap: 11px; margin-top: 18px; align-items: flex-start; }
.message.user { flex-direction: row-reverse; }
.message.user > span { background: #d77b5f; }
.message p { max-width: 74%; padding: 11px 14px; margin: 0; border-radius: 4px 15px 15px; background: #f2ece4; line-height: 1.65; font-size: 13px; }
.message.user p { border-radius: 15px 4px 15px 15px; background: #f8e8e1; }
.composer { margin: 0 20px 20px; padding: 12px; border: 1px solid #ded4ca; border-radius: 16px; background: white; }
.composer textarea { width: 100%; padding: 4px; border: 0; outline: 0; resize: none; color: var(--ink); }
.composer > div { display: flex; justify-content: space-between; align-items: center; }
.composer small { color: #999087; }
.proposal-panel { max-height: calc(100vh - 215px); overflow-y: auto; }
.proposal { margin: 16px; padding: 17px; border: 1px solid var(--line); border-radius: 15px; background: #fff; }
.proposal + .proposal { margin-top: 0; }
.proposal-title, .proposal-foot { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
.proposal-title h3 { margin: 0; font: 600 17px Georgia, "Songti SC", serif; }
.proposal-summary { margin: 10px 0 14px; color: var(--muted); line-height: 1.55; font-size: 12px; }
.micro-chain { list-style: none; display: grid; gap: 7px; margin: 0 0 15px; padding: 0; }
.micro-chain li { display: grid; grid-template-columns: 38px 1fr; gap: 8px; color: #6b635d; font-size: 11px; line-height: 1.45; }
.micro-chain b { color: #b4634d; }
.proposal-foot { padding-top: 13px; border-top: 1px dashed var(--line); color: var(--muted); font-size: 11px; }
.proposal-foot button { min-height: 34px; padding: 0 12px; }
</style>
