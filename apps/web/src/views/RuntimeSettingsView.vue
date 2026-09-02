<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";

import { api } from "../api/client";
import type { AssetDto, CanonProfileDto, RateCardItemDto, RateCardRevisionDto, RuntimeBootstrapDto } from "../api/types";

type CanonRole = "episode_child" | "episode_cat" | "pair_scale" | "style_board";
const runtime = ref<RuntimeBootstrapDto | null>(null);
const canon = ref<CanonProfileDto | null>(null);
const uploaded = reactive<Partial<Record<CanonRole, AssetDto>>>({});
const busyRole = ref<CanonRole | null>(null);
const publishing = ref(false);
const checkingPublisher = ref(false);
const publishingRate = ref(false);
const rateCards = ref<RateCardRevisionDto[]>([]);
const rateDraft = reactive({
  provider: "ark",
  model: "",
  revision: "",
  sourceUrl: "",
  rates: [{ metric: "inputTokens", unit: "million_tokens", unitPriceMicros: 0 }] as RateCardItemDto[],
});
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
    [runtime.value, canon.value, rateCards.value] = await Promise.all([
      api.runtime(),
      api.currentCanon(),
      api.rateCards(),
    ]);
    Object.assign(uploaded, canon.value.fixedAssets);
    if (!rateDraft.model) rateDraft.model = runtime.value.provider.planningModel;
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

async function checkPublisher() {
  if (!runtime.value?.objectPublisher.configured) return;
  checkingPublisher.value = true;
  error.value = "";
  try {
    const publisher = await api.checkObjectPublisher();
    const refreshed = await api.runtime();
    runtime.value = { ...refreshed, objectPublisher: publisher };
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "对象发布器预检失败";
  } finally {
    checkingPublisher.value = false;
  }
}

function addRate() {
  rateDraft.rates.push({ metric: "outputTokens", unit: "million_tokens", unitPriceMicros: 0 });
}

async function publishRateCard() {
  if (!rateDraft.model.trim() || !rateDraft.revision.trim() || publishingRate.value) return;
  publishingRate.value = true;
  error.value = "";
  try {
    const card = await api.publishRateCard({
      provider: rateDraft.provider,
      model: rateDraft.model.trim(),
      revision: rateDraft.revision.trim(),
      sourceUrl: rateDraft.sourceUrl.trim() || null,
      effectiveFrom: new Date().toISOString(),
      rates: rateDraft.rates.map((rate) => ({ ...rate, unitPriceMicros: Number(rate.unitPriceMicros) })),
    });
    rateCards.value = [card, ...rateCards.value];
    rateDraft.revision = "";
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "费率版本发布失败";
  } finally {
    publishingRate.value = false;
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
      <section class="card settings-card"><p class="eyebrow">Provider</p><h2>Ark typed gateway</h2><dl><div><dt>Provider</dt><dd><span class="pill good">{{ runtime.provider.name }}</span></dd></div><div><dt>Ark Key</dt><dd>{{ runtime.provider.apiKeyConfigured ? "已配置" : "未配置" }}</dd></div><div><dt>规划 / 诊断</dt><dd>{{ runtime.provider.planningModel }}</dd></div><div><dt>图片</dt><dd>{{ runtime.provider.imageModel }}</dd></div><div><dt>视频</dt><dd>{{ runtime.provider.videoModel }}</dd></div><div><dt>Capability</dt><dd>{{ runtime.provider.capabilityRevision }}</dd></div><div><dt>片段修复</dt><dd><span class="pill" :class="{ good: runtime.provider.segmentRepair.supported }">{{ runtime.provider.segmentRepair.supported ? "Ready" : "Blocked" }}</span> · 1 视频 + 7/{{ runtime.provider.segmentRepair.maximumImageReferences }} 图片<span v-if="runtime.provider.segmentRepair.blockedReason" class="capability-reason">{{ runtime.provider.segmentRepair.blockedReason }}</span></dd></div></dl></section>
      <section class="card settings-card wide publisher-card"><header><div><p class="eyebrow">S3-compatible publisher</p><h2>Ark 参考视频临时发布</h2></div><span data-testid="object-publisher-status" class="pill" :class="{ good: runtime.objectPublisher.ready }">{{ runtime.objectPublisher.ready ? "Ready" : runtime.objectPublisher.configured ? "Needs check" : "Not configured" }}</span></header><p class="notice">私有对象只通过短时 HTTPS 签名供 Ark 读取；浏览器不会接收 AK、SK、对象键或签名 URL。</p><dl><div><dt>上传 Endpoint</dt><dd>{{ runtime.objectPublisher.endpointHost || "未配置" }}</dd></div><div><dt>Ark 公网 Host</dt><dd>{{ runtime.objectPublisher.publicHost || "未配置" }}</dd></div><div><dt>Bucket / Region</dt><dd>{{ runtime.objectPublisher.bucket || "—" }} / {{ runtime.objectPublisher.region || "—" }}</dd></div><div><dt>协议</dt><dd>{{ runtime.objectPublisher.backend }} · {{ runtime.objectPublisher.addressingStyle }} · URL {{ runtime.objectPublisher.presignTtlSeconds / 3600 }}h</dd></div><div><dt>临时对象</dt><dd>私有 · 保留 {{ runtime.objectPublisher.retentionDays }} 天</dd></div></dl><p v-if="runtime.objectPublisher.error" class="capability-reason">{{ runtime.objectPublisher.error.message }}</p><button data-testid="check-object-publisher" class="secondary publisher-check" :disabled="checkingPublisher || !runtime.objectPublisher.configured" @click="checkPublisher">{{ checkingPublisher ? "预检中…" : "验证上传、签名读取与删除" }}</button></section>
      <section class="card settings-card wide canon-manager"><header><div><p class="eyebrow">Canon v4 publication</p><h2>四个固定全局资产</h2></div><span v-if="canon" class="pill good">Revision {{ canon.version }} · {{ Object.keys(canon.fixedAssets).length }}/4</span></header><p class="notice">正式权威：同一位 6–7 岁、约 1.2 米、约 4.5–5 头身的齐下颌短发儿童。发布前逐项显示 SHA256；发布后项目只读继承，普通项目不能覆盖四个槽位。</p><div v-if="canon" class="profile-hash"><span>当前 Profile SHA256</span><code>{{ canon.profileHash }}</code></div><div class="canon-grid"><label v-for="slot in canonSlots" :key="slot.role" class="canon-slot"><b>{{ slot.label }}</b><input :aria-label="`上传${slot.label}`" type="file" accept="image/png,image/jpeg,image/webp" :disabled="busyRole === slot.role" @change="upload(slot.role, $event)" /><code v-if="uploaded[slot.role]">{{ uploaded[slot.role]!.sha256 }}</code><span v-else>等待上传并校验</span></label></div><button class="primary publish-canon" :disabled="!allUploaded || publishing" @click="publish">{{ publishing ? "发布中…" : "核对 SHA256 并发布 Canon v4" }}</button></section>
      <section class="card settings-card wide rate-card-manager">
        <header><div><p class="eyebrow">Immutable billing ledger</p><h2>Provider 费率版本</h2></div><span class="pill">历史版本只读</span></header>
        <p class="notice">Job 创建时冻结当前费率快照；新价格必须发布新 Revision，历史任务费用不会跟随修改。这里的结果是按费率表计算，不等同于 Provider 最终账单。</p>
        <div class="rate-form">
          <label>Provider<input v-model="rateDraft.provider" readonly /></label>
          <label>模型<input v-model="rateDraft.model" data-testid="rate-model" /></label>
          <label>Revision<input v-model="rateDraft.revision" data-testid="rate-revision" placeholder="例如 ark-planning-2026-09" /></label>
          <label>来源 URL<input v-model="rateDraft.sourceUrl" placeholder="官方费率页面" /></label>
          <div v-for="(rate, index) in rateDraft.rates" :key="index" class="rate-row">
            <select v-model="rate.metric"><option value="inputTokens">输入 tokens</option><option value="outputTokens">输出 tokens</option><option value="completionTokens">视频 completion tokens</option><option value="totalTokens">总 tokens</option><option value="generatedImages">生成图片</option><option value="generatedVideoSeconds">生成视频秒数</option></select>
            <select v-model="rate.unit"><option value="million_tokens">每百万 tokens</option><option value="image">每张图片</option><option value="video_second">每视频秒</option></select>
            <label>单价（微元）<input v-model.number="rate.unitPriceMicros" :data-testid="index === 0 ? 'rate-price' : undefined" type="number" min="0" /></label>
            <button v-if="rateDraft.rates.length > 1" class="ghost" @click="rateDraft.rates.splice(index, 1)">移除</button>
          </div>
          <div class="rate-actions"><button class="secondary" @click="addRate">增加计价指标</button><button data-testid="publish-rate-card" class="primary" :disabled="publishingRate || !rateDraft.model.trim() || !rateDraft.revision.trim()" @click="publishRateCard">{{ publishingRate ? "发布中…" : "发布新费率 Revision" }}</button></div>
        </div>
        <div class="rate-history"><article v-for="card in rateCards" :key="`${card.provider}-${card.model}-${card.revision}`"><div><b>{{ card.revision }}</b><span>{{ card.provider }} · {{ card.model }}</span></div><code v-for="rate in card.rates" :key="rate.metric">{{ rate.metric }} / {{ rate.unit }} = {{ rate.unitPriceMicros }} 微元</code><small>{{ card.effectiveFrom }} · {{ card.sourceUrl || "未记录来源" }}</small></article><p v-if="!rateCards.length" class="empty">尚未发布费率；Provider 返回 usage 后将显示“待核价”，不会显示 ¥0。</p></div>
      </section>
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
.capability-reason { display: block; margin-top: 6px; color: #a34f3f; font-weight: 500; line-height: 1.5; }
.canon-manager header { display: flex; justify-content: space-between; align-items: start; }
.publisher-card header { display: flex; justify-content: space-between; align-items: start; }
.publisher-check { width: 100%; margin-top: 16px; }
.canon-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 18px 0; }
.canon-slot { display: grid; gap: 9px; padding: 15px; border: 1px solid var(--line); border-radius: 13px; background: #fff; }
.canon-slot b { font-size: 13px; }
.canon-slot code { overflow: hidden; text-overflow: ellipsis; color: #617264; font-size: 10px; }
.canon-slot span { color: var(--muted); font-size: 11px; }
.profile-hash { display: grid; grid-template-columns: 150px 1fr; gap: 10px; margin: 12px 0; color: var(--muted); font-size: 10px; }
.profile-hash code { overflow: hidden; text-overflow: ellipsis; }
.publish-canon { width: 100%; }
.rate-card-manager header { display: flex; justify-content: space-between; align-items: start; }.rate-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 18px 0; }.rate-form > label, .rate-row label { display: grid; gap: 5px; color: var(--muted); font-size: 10px; }.rate-form input, .rate-form select { padding: 9px; border: 1px solid var(--line); border-radius: 8px; }.rate-row { grid-column: 1 / -1; display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 8px; align-items: end; padding: 10px; border-radius: 10px; background: #f6f0e9; }.rate-actions { grid-column: 1 / -1; display: flex; justify-content: flex-end; gap: 8px; }.rate-history { display: grid; gap: 9px; }.rate-history article { display: grid; grid-template-columns: 1.1fr 1fr 1fr; gap: 10px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; }.rate-history article div { display: grid; gap: 3px; }.rate-history span, .rate-history small { color: var(--muted); font-size: 9px; }.rate-history code { font-size: 9px; }
@media (max-width: 800px) { .settings-grid, .rate-form { grid-template-columns: 1fr; }.settings-card.wide, .rate-row, .rate-actions { grid-column: auto; }.rate-row, .rate-history article { grid-template-columns: 1fr; } }
</style>
