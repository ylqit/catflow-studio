<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api } from "../../api/client";
import type { PlannerSnapshotDto } from "../../api/types";

const props = defineProps<{ projectId: string }>();
const emit = defineEmits<{ changed: [] }>();
const snapshot = ref<PlannerSnapshotDto | null>(null);
const message = ref("");
const sending = ref(false);
const adopting = ref<string | null>(null);
const error = ref("");

async function load() {
  try {
    snapshot.value = await api.planner(props.projectId);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "生活灵感读取失败";
  }
}

async function sendMessage() {
  if (!snapshot.value || !message.value.trim()) return;
  sending.value = true;
  error.value = "";
  try {
    await api.plannerMessage(props.projectId, {
      text: message.value.trim(),
      expectedContextRevision: snapshot.value.contextRevision,
      idempotencyKey: crypto.randomUUID(),
    });
    message.value = "";
    await load();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "规划任务提交失败";
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
    error.value = reason instanceof Error ? reason.message : "提案采用失败";
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
        <div><p class="eyebrow">Life planner</p><h2>和生活灵感导演聊一聊</h2></div>
        <button class="ghost" @click="load">刷新</button>
      </header>
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
        <article v-for="item in snapshot?.messages" :key="item.id" class="message" :class="item.role">
          <span>{{ item.role === "assistant" ? "导" : "我" }}</span>
          <p>{{ item.content }}</p>
        </article>
      </div>
      <form class="composer" @submit.prevent="sendMessage">
        <textarea v-model="message" rows="3" maxlength="4000" placeholder="例如：雨停后，孩子发现猫咪在门口留下一串湿脚印……" @keydown.ctrl.enter="sendMessage" />
        <div><small>Ctrl + Enter 发送 · 首版使用本机 fake planner</small><button class="primary" :disabled="sending || !message.trim()"><span v-if="sending" class="spinner" />生成提案</button></div>
      </form>
    </div>

    <aside class="proposal-panel card">
      <header class="panel-head"><div><p class="eyebrow">Structured proposals</p><h2>生活微事件提案</h2></div><span class="pill">{{ snapshot?.proposals.length ?? 0 }} 个</span></header>
      <p v-if="error" class="notice error">{{ error }}</p>
      <div v-if="!snapshot?.proposals.length" class="empty">Worker 完成规划任务后，结构化提案会出现在这里。</div>
      <article v-for="proposal in snapshot?.proposals" :key="proposal.id" class="proposal">
        <div class="proposal-title"><h3>{{ proposal.title }}</h3><span class="pill" :class="{ good: proposal.status === 'adopted', warn: proposal.status === 'outdated' }">{{ proposal.status }}</span></div>
        <p class="proposal-summary">{{ proposal.summary }}</p>
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
.messages { flex: 1; min-height: 300px; padding: 24px; overflow-y: auto; }
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
