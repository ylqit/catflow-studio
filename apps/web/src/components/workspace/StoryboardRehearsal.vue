<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import type { ShotSpecDto } from '../../api/types';
const props=defineProps<{shots:ShotSpecDto[]}>();
const index=ref(0),playing=ref(false);let timer:ReturnType<typeof setTimeout>|undefined;
const shot=computed(()=>props.shots[index.value]);
function stop(){clearTimeout(timer);playing.value=false;}
function tick(){if(!playing.value||!shot.value)return;timer=setTimeout(()=>{if(index.value+1>=props.shots.length){stop();return;}index.value++;tick();},(shot.value.durationFrames ?? shot.value.durationSeconds*24)/24*1000);}
function play(){stop();index.value=0;playing.value=true;tick();}
onBeforeUnmount(stop);
</script>
<template><details class="rehearsal"><summary>本地分镜预演（免费）</summary><button @click="play">按镜头时长播放</button><button @click="stop">停止</button><div class="thumbs"><button v-for="(s,i) in shots" :key="s.id" @click="stop();index=i">{{ s.order }} · {{ (s.durationFrames ?? s.durationSeconds*24)/24 }} 秒</button></div><template v-if="shot"><img v-if="shot.confirmedFrame" :src="`/api/v1/assets/${shot.confirmedFrame.assetId}/content`" alt="已确认分镜图片" /><div v-else class="placeholder">本镜尚无图片 · {{ shot.framing }}</div><p>{{ shot.information?.newInformation ?? shot.environmentChange }}</p></template><small>仅使用已有图片；缺图显示占位，不创建图片任务。</small></details></template>
<style scoped>.rehearsal{padding:1rem}.thumbs{display:flex;gap:.5rem;flex-wrap:wrap;margin:1rem 0}img,.placeholder{height:260px;max-width:100%;object-fit:contain}.placeholder{display:grid;place-items:center;background:#eee}button{margin:.3rem}</style>
