<script setup lang="ts">
import { ref, watch } from 'vue';
import { api } from '../../api/client';
import type { StoryVersionDto } from '../../api/types';
import type { NarrativeDesign } from '../../api/productionTypes';
const props=defineProps<{projectId:string;story:StoryVersionDto}>();
const emit=defineEmits<{saved:[]}>();
const fields: Array<[keyof NarrativeDesign,string]>=[['characterGoal','角色目标'],['audienceExpectation','观众期待'],['smallDisruption','小意外（可空）'],['reveal','可见揭示（可空）'],['response','角色回应'],['payoff','结尾回报'],['adaptationNotes','新增改编说明']];
const draft=ref<NarrativeDesign>({characterGoal:'',audienceExpectation:'',smallDisruption:'',reveal:'',response:'',payoff:'',adaptationNotes:''});
const error=ref(''),busy=ref(false);
watch(()=>props.story,story=>{draft.value={characterGoal:story.microEvent.childAction,audienceExpectation:'',smallDisruption:'',reveal:'',response:story.microEvent.catResponse,payoff:story.microEvent.warmEnding,adaptationNotes:'',...story.narrativeDesign};},{immediate:true});
async function save(){
  busy.value=true;error.value='';
  try {await api.json(`/api/v1/projects/${props.projectId}/stories`,'POST',{
    title:props.story.title,body:props.story.body,microEvent:props.story.microEvent,
    targetDurationSeconds:props.story.targetDurationSeconds,dialoguePolicy:props.story.dialoguePolicy,
    environmentIntent:props.story.environmentIntent,narrativeDesign:draft.value});emit('saved');}
  catch(reason){error.value=String(reason);}finally{busy.value=false;}
}
</script>
<template><details class="narrative-design"><summary>期待—意外—回应 · 故事版本 {{ story.revision }}</summary><p v-if="!story.narrativeDesign">历史故事没有这项设计；不会自动补造反转。</p><label v-for="[key,label] in fields" :key="key">{{ label }}<textarea v-model="draft[key]" /></label><p>保存创建并采用新故事版本，原文保持不变，已有分镜与生产计划保留为历史版本；需要重新确认分镜。此操作不调用模型。</p><p v-if="error" class="notice error">{{ error }}</p><button :disabled="busy" @click="save">保存为新故事版本</button></details></template>
<style scoped>.narrative-design{padding:1rem}label{display:block;margin:.6rem 0}textarea{display:block;width:100%}</style>
