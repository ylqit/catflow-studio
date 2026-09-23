<script setup lang="ts">
import { ref } from 'vue';
import { api } from '../../api/client';
import { pendingIdempotencyKey, settleIdempotencyKey } from '../../idempotency';
import type { JobDto } from '../../api/types';
import type { PropBinding } from '../../api/productionTypes';
import JobStatusCard from '../JobStatusCard.vue';
const props=defineProps<{ projectId:string; prop:PropBinding }>();
const emit=defineEmits<{ refresh:[] }>();
const preview=ref<{inputHash:string;compiledProviderPrompt:string;model:string}>(),job=ref<JobDto>(),busy=ref(false),error=ref('');
async function prepare(){busy.value=true;error.value='';try{preview.value=await api.json(`/api/v1/projects/${props.projectId}/production-props/preview`,'POST',{key:props.prop.key,name:props.prop.name,appearance:props.prop.identity});}catch(reason){error.value=String(reason);}finally{busy.value=false;}}
async function generate(){if(!preview.value)return;busy.value=true;error.value='';const hash=preview.value.inputHash,scope=`prop-image:${props.projectId}:${props.prop.key}`;try{job.value=await api.json(`/api/v1/projects/${props.projectId}/production-props/generations`,'POST',{key:props.prop.key,name:props.prop.name,appearance:props.prop.identity,expectedInputHash:hash,idempotencyKey:pendingIdempotencyKey(scope,hash)});settleIdempotencyKey(scope,hash);}catch(reason){error.value=String(reason);}finally{busy.value=false;}}
</script>
<template><details><summary>缺少道具图片时生成候选（付费）</summary><p>使用道具名称、外观要求和本片固定画风板，生成一张无角色的道具参考。也可在资产页导入已有图片。</p><button type="button" :disabled="busy" @click="prepare">预览指令（免费）</button><template v-if="preview"><pre>{{ preview.compiledProviderPrompt }}</pre><p>{{ preview.model }} · 费用待核价 · 不自动重抽</p><button type="button" :disabled="busy || Boolean(job)" @click="generate">生成一张道具图</button></template><JobStatusCard v-if="job" :job-id="job.id" title="道具图片任务" /><button type="button" @click="emit('refresh')">刷新图片列表</button><p v-if="error" class="notice error">{{ error }}</p></details></template>
<style scoped>pre{white-space:pre-wrap}details{padding:1rem;background:#f4f1eb}</style>
