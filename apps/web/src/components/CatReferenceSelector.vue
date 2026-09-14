<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import type { CatReferenceOptionDto } from "../api/types";
const props = defineProps<{ modelValue?: string | null; readonly?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: string]; ready: [value: boolean] }>();
const options = ref<CatReferenceOptionDto[]>([]);
const error = ref("");
const broken = ref(new Set<string>());
function imageFailed(id: string) { broken.value.add(id); if (id === props.modelValue) emit("ready", false); }
function select(option: CatReferenceOptionDto) {
  if (props.readonly || !option.available || !option.canonProfileId || broken.value.has(option.canonProfileId)) return;
  emit("update:modelValue", option.canonProfileId); emit("ready", true);
}
onMounted(async () => {
  try {
    options.value = await api.catReferenceOptions();
    const chosen = options.value.find(item => props.modelValue ? item.canonProfileId === props.modelValue : item.key === "gray-original");
    if (chosen?.available && chosen.canonProfileId) {
      if (!props.modelValue && !props.readonly) emit("update:modelValue", chosen.canonProfileId);
      emit("ready", true);
    } else emit("ready", false);
  } catch (reason) { error.value = reason instanceof Error ? reason.message : "参考目录无法读取"; emit("ready", false); }
});
</script>
<template>
  <section class="cat-selector" aria-label="猫咪参考选择">
    <p class="hint">选择猫咪参考 · 儿童与画风共用固定设定</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div class="options">
      <article v-for="option in options.filter(item => !readonly || item.canonProfileId === modelValue)" :key="option.key" :class="{ selected: option.canonProfileId === modelValue }">
        <button type="button" class="choice" :aria-pressed="option.canonProfileId === modelValue" :disabled="readonly || !option.available || broken.has(option.canonProfileId ?? '')" @click="select(option)">
          <img v-if="option.fixedAssets?.episode_cat" :src="`/api/v1/assets/${option.fixedAssets.episode_cat.id}/content`" :alt="option.label + '生成主图'" @error="imageFailed(option.canonProfileId ?? '')" />
          <b>{{ option.label }}</b><span v-if="option.canonProfileId === modelValue">已选用</span>
        </button>
        <p v-if="!option.available || broken.has(option.canonProfileId ?? '')" role="alert">{{ option.unavailableReason || '图片无法读取，请检查参考资料。' }}</p>
        <details><summary>查看参考</summary>
          <p>{{ option.catIdentity }}</p>
          <a v-if="option.fixedAssets?.pair_scale" :href="`/api/v1/assets/${option.fixedAssets.pair_scale.id}/content`" target="_blank" rel="noopener"><img :src="`/api/v1/assets/${option.fixedAssets.pair_scale.id}/content`" :alt="option.label + '人猫比例图'" @error="imageFailed(option.canonProfileId ?? '')" />人猫比例图</a>
          <div class="auxiliary"><a v-for="view in option.auxiliary" :key="view.asset.id" :href="`/api/v1/assets/${view.asset.id}/content`" target="_blank" rel="noopener"><img :src="`/api/v1/assets/${view.asset.id}/content`" :alt="view.view" />{{ { front: '正面', rear: '背面', 'three-view': '三视图' }[view.view] ?? view.view }}</a></div>
          <small>辅助视图仅供查阅。生成使用主图和同套比例图。</small>
          <details><summary>技术详情</summary><small>Canon 内部版本 {{ option.version }} · {{ option.profileHash }}</small></details>
        </details>
      </article>
    </div>
  </section>
</template>
<style scoped>
.cat-selector{margin:16px 0;grid-column:1/-1}.hint{font-size:13px;color:var(--muted)}.options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.options article{border:1px solid var(--line);border-radius:12px;padding:12px;min-width:0;background:var(--paper)}.options article.selected{border:2px solid var(--accent-dark);padding:11px}.choice{display:flex;align-items:center;gap:12px;width:100%;background:transparent;border:0;text-align:left;padding:0;color:inherit}.choice:disabled{opacity:1}.choice img{width:80px;height:80px;object-fit:contain;border-radius:8px}.choice b{flex:1}.choice span,small{font-size:11px;color:var(--muted)}details{margin-top:10px;font-size:12px}summary{cursor:pointer}details img{height:130px;max-width:100%;object-fit:contain;display:block}details a{display:inline-block;margin:8px 8px 8px 0}.auxiliary{display:flex;flex-wrap:wrap}small{overflow-wrap:anywhere}@media(max-width:620px){.options{grid-template-columns:1fr}}
</style>
