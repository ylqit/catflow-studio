<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { api } from "../api/client";
import type { AssetDto, CanonProfileDto, RuntimeBootstrapDto } from "../api/types";

type CanonRole = "episode_child" | "episode_cat" | "pair_scale" | "style_board";
const runtime = ref<RuntimeBootstrapDto | null>(null);
const canon = ref<CanonProfileDto | null>(null);
const uploaded = reactive<Partial<Record<CanonRole, AssetDto>>>({});
const busyRole = ref<CanonRole | null>(null);
const publishing = ref(false);
const error = ref("");
const canonSlots: Array<{ role: CanonRole; label: string }> = [
  { role: "episode_child", label: "儿童设计" },
  { role: "episode_cat", label: "猫咪设计" },
  { role: "pair_scale", label: "同框比例" },
  { role: "style_board", label: "净化画风板" },
];
const allUploaded = computed(() => canonSlots.every(({ role }) => uploaded[role]));

async function load() {
  error.value = "";
  try {
    [runtime.value, canon.value] = await Promise.all([api.runtime(), api.currentCanon()]);
    Object.assign(uploaded, canon.value.fixedAssets);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "运行状态读取失败";
  }
}

async function upload(role: CanonRole, event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  busyRole.value = role;
  error.value = "";
  try {
    uploaded[role] = await api.uploadCanonAsset(role, file);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "Canon 上传失败";
  } finally {
    busyRole.value = null;
  }
}

async function publish() {
  if (!allUploaded.value) return;
  publishing.value = true;
  try {
    canon.value = await api.publishCanon(Object.fromEntries(
      canonSlots.map(({ role }) => [role, uploaded[role]!.id]),
    ));
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "Canon 发布失败";
  } finally {
    publishing.value = false;
  }
}

onMounted(load);
</script>

<template>
  <main class="page settings-page">
    <section class="page-heading"><div><p class="eyebrow">Local runtime</p><h1>模型与运行设置</h1><p class="subtitle">密钥、数据库和媒体路径只存在于后端环境，浏览器只接收就绪状态。</p></div><RouterLink class="primary" to="/validation/first-three">进入首批真实验收</RouterLink></section>
    <p v-if="error" class="notice error">{{ error }}</p>
    <div v-if="runtime" class="settings-grid">
      <section class="card settings-card"><p class="eyebrow">Connection</p><h2>本机 Web 服务</h2><dl><div><dt>正式地址</dt><dd data-testid="runtime-address">{{ runtime.baseUrl }}</dd></div><div><dt>PostgreSQL</dt><dd><span class="pill" :class="{ good: runtime.databaseReady }">{{ runtime.databaseReady ? "Ready" : "Unavailable" }}</span></dd></div><div><dt>Worker</dt><dd><span class="pill" :class="{ good: runtime.workerReady }">{{ runtime.workerReady ? "Ready" : "Unavailable" }}</span></dd></div><div><dt>FFmpeg / ffprobe</dt><dd><span class="pill" :class="{ good: runtime.ffmpegReady && runtime.ffprobeReady }">{{ runtime.ffmpegReady && runtime.ffprobeReady ? "Ready" : "Unavailable" }}</span></dd></div></dl></section>
      <section class="card settings-card"><p class="eyebrow">Provider</p><h2>Ark typed gateway</h2><dl><div><dt>Provider</dt><dd><span class="pill good">{{ runtime.provider.name }}</span></dd></div><div><dt>Ark Key</dt><dd>{{ runtime.provider.apiKeyConfigured ? "已配置" : "未配置" }}</dd></div><div><dt>规划 / 诊断</dt><dd>{{ runtime.provider.planningModel }}</dd></div><div><dt>图片</dt><dd>{{ runtime.provider.imageModel }}</dd></div><div><dt>视频</dt><dd>{{ runtime.provider.videoModel }}</dd></div><div><dt>Capability</dt><dd>{{ runtime.provider.capabilityRevision }}</dd></div></dl></section>
      <section class="card settings-card wide canon-manager"><header><div><p class="eyebrow">Canon v4 publication</p><h2>四个固定全局资产</h2></div><span v-if="canon" class="pill good">Revision {{ canon.version }} · {{ Object.keys(canon.fixedAssets).length }}/4</span></header><p class="notice">正式权威：同一位 6–7 岁、约 1.2 米、约 4.5–5 头身的齐下颌短发儿童。发布前逐项显示 SHA256；发布后项目只读继承，普通项目不能覆盖四个槽位。</p><div v-if="canon" class="profile-hash"><span>当前 Profile SHA256</span><code>{{ canon.profileHash }}</code></div><div class="canon-grid"><label v-for="slot in canonSlots" :key="slot.role" class="canon-slot"><b>{{ slot.label }}</b><input :aria-label="`上传${slot.label}`" type="file" accept="image/png,image/jpeg,image/webp" :disabled="busyRole === slot.role" @change="upload(slot.role, $event)" /><code v-if="uploaded[slot.role]">{{ uploaded[slot.role]!.sha256 }}</code><span v-else>等待上传并校验</span></label></div><button class="primary publish-canon" :disabled="!allUploaded || publishing" @click="publish">{{ publishing ? "发布中…" : "核对 SHA256 并发布 Canon v4" }}</button></section>
    </div>
  </main>
</template>

<style scoped>
.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.settings-card { padding: 26px; }
.settings-card.wide { grid-column: span 2; }
.settings-card dl { display: grid; margin: 20px 0 0; }
.settings-card dl > div { display: grid; grid-template-columns: 150px 1fr; padding: 11px 0; border-bottom: 1px solid var(--line); font-size: 12px; }
.settings-card dt { color: var(--muted); }
.settings-card dd { margin: 0; font-weight: 650; overflow-wrap: anywhere; }
.canon-manager header { display: flex; justify-content: space-between; align-items: start; }
.canon-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 18px 0; }
.canon-slot { display: grid; gap: 9px; padding: 15px; border: 1px solid var(--line); border-radius: 13px; background: #fff; }
.canon-slot b { font-size: 13px; }
.canon-slot code { overflow: hidden; text-overflow: ellipsis; color: #617264; font-size: 10px; }
.canon-slot span { color: var(--muted); font-size: 11px; }
.profile-hash { display: grid; grid-template-columns: 150px 1fr; gap: 10px; margin: 12px 0; color: var(--muted); font-size: 10px; }
.profile-hash code { overflow: hidden; text-overflow: ellipsis; }
.publish-canon { width: 100%; }
</style>
