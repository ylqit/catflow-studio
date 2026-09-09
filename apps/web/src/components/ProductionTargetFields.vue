<script setup lang="ts">
import { computed } from "vue";
import type { StoryProductionTarget } from "../api/types";
const props = defineProps<{ modelValue: StoryProductionTarget; label: string }>();
const emit = defineEmits<{ "update:modelValue": [value: StoryProductionTarget] }>();
function change(patch: Partial<StoryProductionTarget>) { emit("update:modelValue", { ...props.modelValue, ...patch }); }
const keepText = computed({
  get: () => props.modelValue.mustKeep.join("\n"),
  set: (value: string) => change({ mustKeep: value.split("\n").map(item => item.trim()).filter(Boolean) }),
});
</script>
<template>
  <fieldset class="production-target">
    <legend>{{ label }}</legend>
    <label>系列长度<select :value="modelValue.lengthMode" @change="change(($event.target as HTMLSelectElement).value === 'ongoing' ? { lengthMode: 'ongoing', plannedEpisodeCount: null } : { lengthMode: 'fixed', plannedEpisodeCount: 3 })"><option value="fixed">固定集数</option><option value="ongoing">持续连载</option></select></label>
    <label v-if="modelValue.lengthMode === 'fixed'">计划集数<input :value="modelValue.plannedEpisodeCount" type="number" min="2" step="1" :aria-label="label + '计划集数'" @input="change({ plannedEpisodeCount: Number(($event.target as HTMLInputElement).value) })" /></label>
    <label>每集时长（秒）<input :value="modelValue.defaultEpisodeDurationSeconds" type="number" min="8" max="15" step="1" :aria-label="label + '每集时长'" @input="change({ defaultEpisodeDurationSeconds: Number(($event.target as HTMLInputElement).value) })" /></label>
    <label>叙事方式<select :value="modelValue.narrativeMode" @change="change({ narrativeMode: ($event.target as HTMLSelectElement).value as StoryProductionTarget['narrativeMode'] })"><option value="continuous">连续剧情</option><option value="lightly_serialized">轻连续</option><option value="anthology">单元故事</option></select></label>
    <p v-if="modelValue.lengthMode === 'fixed'"><strong>{{ modelValue.plannedEpisodeCount }} 集 × {{ modelValue.defaultEpisodeDurationSeconds }} 秒 = {{ (modelValue.plannedEpisodeCount ?? 0) * modelValue.defaultEpisodeDurationSeconds }} 秒</strong><br />固定系列至少 2 集；只制作 1 集时，在分析结果中选择“创建独立短片”。</p>
    <p v-else>持续连载按规划段确认，不预设总时长。</p>
    <label class="keep">必须保留（每行一项）<textarea v-model.lazy="keepText" :aria-label="label + '必须保留'" placeholder="例如：饼干始终留在篮内" /></label>
    <small>保留主线并精简：合并重复动作、简化次要过程；每项删改都在分集方案中说明。</small>
  </fieldset>
</template>
<style scoped>
.production-target { border:1px solid var(--line); border-radius:12px; padding:14px; margin:12px 0; display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; background:#fcf8f2; }
legend { font-weight:700; }label { display:grid; gap:6px; font-size:13px; } input,select,textarea { width:100%; min-width:0; padding:9px; border:1px solid var(--line); border-radius:7px; background:white; }p,small,.keep { grid-column:1/-1; }p,small { margin:0; line-height:1.6; font-size:12px; color:var(--muted); }.keep textarea { min-height:72px; resize:vertical; }
@media(max-width:600px) { .production-target { grid-template-columns:1fr; } }
</style>
