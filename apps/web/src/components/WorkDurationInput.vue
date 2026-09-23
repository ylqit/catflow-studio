<script setup lang="ts">
import { productionCapabilities as capabilities } from '../productionCapabilities';
defineProps<{ modelValue:number; id?:string; label?:string }>();
const emit=defineEmits<{ 'update:modelValue':[number] }>();
</script>
<template><div class="work-duration"><label :for="id">{{ label ?? '作品目标长度' }}：{{ modelValue }} 秒</label><input :id="id" :value="modelValue" type="range" step="1" :min="capabilities.minimumWorkSeconds" :max="capabilities.maximumWorkSeconds" @input="emit('update:modelValue',Number(($event.target as HTMLInputElement).value))" /><div><button v-for="seconds in [15,30,45,60].filter(n=>n<=capabilities.maximumWorkSeconds)" :key="seconds" type="button" :aria-pressed="modelValue === seconds" @click="emit('update:modelValue',seconds)">{{ seconds }} 秒</button></div><small>按时长预计至少 {{ Math.ceil(modelValue/capabilities.maximumGenerationSeconds) }} 个生成单元；实际数量由场景、镜头和参考数量确定。</small></div></template>
<style scoped>.work-duration{display:grid;gap:.5rem}input{width:100%}button{margin-right:.5rem}button[aria-pressed=true]{font-weight:700;background:#e5eee8}small{color:#666}</style>
