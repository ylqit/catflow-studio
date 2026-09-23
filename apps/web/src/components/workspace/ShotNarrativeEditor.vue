<script setup lang="ts">
const crypto = globalThis.crypto;
import type { ShotSpecDto } from '../../api/types';
const props=defineProps<{ shot:ShotSpecDto; disabled?:boolean }>();
function duration(event:Event) {const frames=Number((event.target as HTMLInputElement).value);props.shot.durationFrames=frames;props.shot.durationSeconds=frames/24;}
</script>
<template>
  <fieldset v-if="shot.formatVersion === 2 && shot.information" :disabled="disabled" class="shot-narrative">
    <legend>本镜信息与叙事</legend><label>取用帧数（24 帧 = 1 秒）<input type="number" min="24" max="360" :value="shot.durationFrames" @input="duration" /></label>
    <label>信息职责<select v-model="shot.information.role"><option value="establish">建立期待</option><option value="action">行动</option><option value="reaction">反应</option><option value="reveal">揭示</option><option value="payoff">回报</option><option value="transition">衔接</option></select></label>
    <label>观众新增的信息<textarea v-model="shot.information.newInformation" /></label><label>对应的期待、意外或回应<textarea v-model="shot.information.narrativeLink" /></label>
    <label v-for="subject in (['child','cat','prop','environment'] as const)" :key="subject"><input v-model="shot.information.visibleSubjects" type="checkbox" :value="subject" />{{ { child:'儿童', cat:'猫咪', prop:'道具', environment:'环境' }[subject] }}</label>
    <label>场景标识<input v-model="shot.information.sceneKey" /></label><label>道具标识（逗号分隔）<input :value="shot.information.propKeys?.join(',')" @change="shot.information.propKeys=($event.target as HTMLInputElement).value.split(/[,，]/).map(v=>v.trim()).filter(Boolean)" /></label>
    <label><input v-model="shot.information.isolateGeneration" type="checkbox" />此镜需要独立生成</label>
    <article v-for="(event,index) in shot.information.keyEvents" :key="event.id"><label>关键事件<input v-model="event.description" /></label><label>本镜起帧<input v-model.number="event.startFrame" type="number" min="0" /></label><label>本镜止帧（不含）<input v-model.number="event.endFrame" type="number" min="1" :max="shot.durationFrames ?? 360" /></label><label><input v-model="event.required" type="checkbox" />必须达到</label><button @click="shot.information.keyEvents?.splice(index,1)">移除事件</button></article>
    <button @click="(shot.information.keyEvents ??= []).push({id:crypto.randomUUID(),description:'',startFrame:0,endFrame:shot.durationFrames ?? 24,required:true})">添加关键事件</button>
  </fieldset>
</template>
<style scoped>.shot-narrative{padding:1rem;margin:1rem 0}label{display:block;margin:.6rem 0}textarea{display:block;width:100%}article{border-top:1px solid #ddd;margin-top:1rem}</style>
