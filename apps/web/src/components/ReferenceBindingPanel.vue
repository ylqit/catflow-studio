<script setup lang="ts">
import { ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api/client";
import type { CharacterRemakePreviewDto, ReferenceBindingDto } from "../api/types";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../idempotency";
import CatReferenceSelector from "./CatReferenceSelector.vue";
const props = defineProps<{ scope: 'project' | 'series'; objectId: string; revision?: string; readonly?: boolean }>();
const emit = defineEmits<{ changed: [] }>();
const router = useRouter();
const binding = ref<ReferenceBindingDto>();
const selected = ref<string>();
const mode = ref<'view' | 'change' | 'remake'>('view');
const preview = ref<CharacterRemakePreviewDto>();
const title = ref('');
const error = ref('');
const busy = ref(false);
async function load() {
  try { binding.value = await api.referenceBinding(props.scope, props.objectId); selected.value = binding.value.canonProfileId; }
  catch (reason) { error.value = String(reason); }
}
watch(() => [props.scope, props.objectId, props.revision], load, { immediate: true });
watch([selected, title], () => { preview.value = undefined; });
async function act() {
  if (!binding.value || !selected.value) return;
  busy.value = true; error.value = '';
  try {
    if (mode.value === 'change') {
      await api.changeReferenceBinding(props.scope, props.objectId, { canonProfileId: selected.value, expectedCanonProfileId: binding.value.canonProfileId });
      mode.value = 'view'; await load(); emit('changed');
    } else {
      preview.value = await api.previewCharacterRemake({ sourceType: props.scope, sourceId: props.objectId, canonProfileId: selected.value, title: title.value || null });
    }
  } catch (reason) { error.value = String(reason); await load(); }
  finally { busy.value = false; }
}
async function createRemake() {
  if (!preview.value) return;
  const value = preview.value;
  const scope = `character-remake:${props.scope}:${props.objectId}`;
  busy.value = true; error.value = '';
  try {
    const result = await api.createCharacterRemake({ sourceType: props.scope, sourceId: props.objectId, canonProfileId: value.canonProfileId, title: value.title, expectedInputHash: value.inputHash, idempotencyKey: pendingIdempotencyKey(scope, value.inputHash) });
    settleIdempotencyKey(scope, value.inputHash);
    mode.value = 'view'; preview.value = undefined;
    await router.push(props.scope === 'series' ? `/series/${result.targetId}` : `/projects/${result.targetId}/planner`);
  } catch (reason) { error.value = String(reason); }
  finally { busy.value = false; }
}
</script>
<template>
  <section class="reference-binding card">
    <header v-if="binding"><b>猫咪参考：{{ binding.label }}</b><span v-if="binding.ownerSeriesId">继承自本系列</span><button v-if="!readonly && binding.canChange" type="button" class="secondary" @click="mode = 'change'">更换猫咪</button><button v-if="!readonly" type="button" class="secondary" @click="mode = 'remake'">用其他猫咪重制</button><details><summary>当前参考</summary><CatReferenceSelector :model-value="binding.canonProfileId" readonly /><p>{{ binding.catIdentity }}</p></details></header>
    <p v-if="error" role="alert" class="notice error">{{ error }}</p>
    <div v-if="mode !== 'view' && binding">
      <p>{{ mode === 'change' ? '草稿修改后请重新查看生成预览。' : scope === 'series' ? '创建完整新系列，继承故事输入和参数，重新规划。' : '创建独立短片，继承原文和用户要求，重新规划。' }}</p>
      <CatReferenceSelector v-model="selected" />
      <label v-if="mode === 'remake'">新作品名称（可选）<input v-model="title" maxlength="160" placeholder="自动根据来源和猫咪方案命名" /></label>
      <p v-if="binding.blockedReason && mode === 'change'">{{ binding.blockedReason }}</p>
      <button type="button" class="primary" :disabled="busy || !selected || selected === binding.canonProfileId" @click="act">{{ mode === 'change' ? '保存猫咪选择' : '查看重制摘要' }}</button>
      <button type="button" class="secondary" @click="mode = 'view'; preview = undefined">取消</button>
      <section v-if="preview" class="remake-summary"><h3>{{ preview.title }}</h3><p>采用 {{ preview.targetLabel }}。继承：{{ preview.included?.join('、') }}。</p><p>重新制作：{{ preview.excluded?.join('、') }}。</p><details><summary>继承的原始输入</summary><pre>{{ JSON.stringify(preview.sourceSnapshot, null, 2) }}</pre></details><button type="button" class="primary" :disabled="busy" @click="createRemake">确认创建（不启动付费生成）</button></section>
    </div>
  </section>
</template>
<style scoped>
.reference-binding{padding:14px 18px;margin:12px auto;max-width:1424px}header{display:flex;align-items:center;gap:12px;flex-wrap:wrap}header b{margin-right:auto}header span,p{color:var(--muted);font-size:12px}header>details{width:100%}button{font-size:12px;margin-right:8px}label{display:grid;gap:8px;margin:12px 0}input{padding:10px;border:1px solid var(--line);border-radius:8px}.remake-summary{margin-top:18px;padding-top:12px;border-top:1px solid var(--line)}pre{white-space:pre-wrap;max-height:240px;overflow:auto;font-size:12px}
</style>
