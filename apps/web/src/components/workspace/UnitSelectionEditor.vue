<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { api } from '../../api/client';
import type { AssetDto, JobDto } from '../../api/types';
import type { ProductionPlan, ProductionUnit, UnitPreview, UnitSelection, UnitSelectionCommand } from '../../api/productionTypes';
import { pendingIdempotencyKey, settleIdempotencyKey } from '../../idempotency';
const props = defineProps<{ projectId:string; plan:ProductionPlan; unit:ProductionUnit; preview?:UnitPreview; candidates:AssetDto[]; assets:AssetDto[]; selection?:UnitSelection }>();
const emit = defineEmits<{ refresh: []; saved: [] }>();
const player=ref<HTMLVideoElement>(), error=ref(''), busy=ref(false), viewedFrame=ref(0);
const shots=computed(() => props.plan.document.shots.filter(s => props.unit.shotIds.includes(s.id)));
const key=`catflow:unit-selection:${props.projectId}:${props.plan.id}:${props.unit.id}`;
let offset=0;
const form=ref<UnitSelectionCommand>({planId:props.plan.id, expectedDesignHash:props.preview?.unitDesignHash ?? '', expectedSelectionId:props.selection?.id ?? null,
  assetId:props.selection?.document.assetId ?? '', takes:shots.value.map(s => { const take={shotId:s.id,sourceInFrame:offset,durationFrames:s.durationFrames ?? s.durationSeconds*24};offset+=take.durationFrames;return take;}),
  events:shots.value.flatMap(s => (s.information?.keyEvents ?? []).map(e => ({shotId:s.id,eventId:e.id,verdict:'unknown' as const,sourceStartFrame:0,sourceEndFrame:1,notes:''}))),
  endState:{facts:[],unfinishedActions:'',evidenceFrame:offset-1,evidenceAssetId:'',confirmed:false},disposition:'accepted_with_issues',notes:'',audioPolicy:'native',idempotencyKey:'pending-selection'});
const factKeys=new Set(shots.value.flatMap(s => [...(s.information?.visibleSubjects ?? []).filter(a => ['child','cat'].includes(a)), ...(s.information?.propKeys ?? []).map(k => `prop:${k}`)]));
form.value.endState.facts=[...factKeys].map(k => ({key:k,value:'未确认',certainty:'uncertain',required:true}));
onMounted(() => {
  try { const saved=sessionStorage.getItem(key); if(saved)form.value=JSON.parse(saved); else if(props.selection) {
    const d=props.selection.document; form.value={...form.value,assetId:d.assetId,takes:d.takes,events:d.events?.map(e=>({shotId:e.shotId,eventId:e.eventId,verdict:e.verdict,sourceStartFrame:e.sourceStartFrame,sourceEndFrame:e.sourceEndFrame,notes:e.notes})),endState:d.endState,disposition:d.disposition,notes:d.notes,audioPolicy:d.audioPolicy};
  }} catch { error.value='本地选片草稿无法读取，请重新填写。'; }
});
watch(form,value => sessionStorage.setItem(key,JSON.stringify(value)),{deep:true});
const candidate=computed(() => props.candidates.find(a=>a.id===form.value.assetId));
const lastFrame=computed(() => {const t=form.value.takes.at(-1);return t?t.sourceInFrame+t.durationFrames-1:0;});
const evidence=computed(() => props.assets.filter(a => a.mediaType==='image' && a.metadata.sourceVideoAssetId===form.value.assetId && a.metadata.sourceFrame===lastFrame.value));
watch(() => [form.value.assetId,lastFrame.value],()=>{form.value.endState.confirmed=false;form.value.endState.evidenceAssetId='';form.value.endState.evidenceFrame=lastFrame.value;});
let frameCallback:number|undefined, frameOwner:HTMLVideoElement|undefined;
function observeDecodedFrames() {
  if(frameCallback!==undefined)frameOwner?.cancelVideoFrameCallback?.(frameCallback);
  const video=player.value;if(!video)return;frameOwner=video;
  const update=(_now:number,meta:VideoFrameCallbackMetadata)=>{
    viewedFrame.value=Math.floor(meta.mediaTime*24+.01);
    if(player.value===video)frameCallback=video.requestVideoFrameCallback(update);
  };
  if(video.requestVideoFrameCallback)frameCallback=video.requestVideoFrameCallback(update);
}
onBeforeUnmount(()=>{if(frameCallback!==undefined)frameOwner?.cancelVideoFrameCallback?.(frameCallback);});
function seek(frame:number) { if(!player.value)return; player.value.pause();player.value.currentTime=(Math.max(0,Math.min(frame,Number(candidate.value?.metadata.durationFrames ?? 1)-1))+.001)/24; }
function step(delta:number) {seek(viewedFrame.value+delta);}
function observe() {if(player.value && !player.value.requestVideoFrameCallback)viewedFrame.value=Math.floor(player.value.currentTime*24+.01);}
function speed(event:Event) {if(player.value)player.value.playbackRate=Number((event.target as HTMLSelectElement).value);}
async function extract() {
  busy.value=true;error.value='';
  try {await api.json<JobDto>(`/api/v1/projects/${props.projectId}/production-units/${props.unit.id}/evidence`,'POST',{planId:props.plan.id,assetId:form.value.assetId,sourceFrame:lastFrame.value});emit('refresh');}
  catch(reason){error.value=String(reason);}finally{busy.value=false;}
}
async function save() {
  if(!props.preview){error.value='请先查看最新生成输入，确认计划与上游状态。';return;}
  busy.value=true;error.value='';
  const command={...form.value,expectedDesignHash:props.preview.unitDesignHash,expectedSelectionId:props.selection?.id ?? null};
  const hash=JSON.stringify(command),scope=`unit-selection:${props.projectId}:${props.unit.id}`;
  try {await api.json(`/api/v1/projects/${props.projectId}/production-units/${props.unit.id}/selections`,'POST',{...command,idempotencyKey:pendingIdempotencyKey(scope,hash)});settleIdempotencyKey(scope,hash);sessionStorage.removeItem(key);emit('saved');}
  catch(reason){error.value=String(reason);}finally{busy.value=false;}
}
</script>
<template>
  <section class="selection-editor"><h4>选片、事件与实际状态</h4><p>模型任务成功只表示返回了素材。请播放并听查，再记录关键事件和实际结束状态。</p>
    <label>候选<select v-model="form.assetId"><option value="">选择候选</option><option v-for="a in candidates" :key="a.id" :value="a.id">{{ a.id.slice(0,8) }} · {{ a.metadata.durationFrames }} 帧{{ a.metadata.unitDesignHash !== preview?.unitDesignHash ? ' · 需校验适用性' : '' }}</option></select></label>
    <template v-if="candidate"><video ref="player" controls preload="metadata" :src="`/api/v1/assets/${candidate.id}/content`" @loadeddata="observeDecodedFrames" @timeupdate="observe" @seeked="observe" /><p>实际尺寸 {{ candidate.metadata.width }}×{{ candidate.metadata.height }} · {{ candidate.metadata.durationFrames }} 帧 · {{ candidate.metadata.hasAudio ? '含音轨，需听查音乐与对白' : '无音轨' }}</p>
      <button @click="step(-1)">前一帧</button><span>素材帧 {{ viewedFrame }}</span><button @click="step(1)">后一帧</button><select aria-label="播放速度" @change="speed"><option value="1">原速</option><option value="0.5">0.5 倍</option><option value="0.25">0.25 倍</option></select>
    </template>
    <table><thead><tr><th>镜头</th><th>素材入点帧</th><th>采用帧数</th></tr></thead><tbody><tr v-for="take in form.takes" :key="take.shotId"><td>{{ take.shotId }}</td><td><input v-model.number="take.sourceInFrame" type="number" min="0" /><button @click="seek(take.sourceInFrame)">定位</button></td><td>{{ take.durationFrames }}</td></tr></tbody></table>
    <article v-for="event in form.events" :key="event.shotId+event.eventId"><b>{{ shots.find(s=>s.id===event.shotId)?.information?.keyEvents?.find(e=>e.id===event.eventId)?.description }}</b>
      <label>实际起帧<input v-model.number="event.sourceStartFrame" type="number" min="0" /></label><label>实际止帧（不含）<input v-model.number="event.sourceEndFrame" type="number" min="1" /></label><button @click="seek(event.sourceStartFrame)">跳转检查</button>
      <select v-model="event.verdict" aria-label="事件检查结论"><option value="pass">通过</option><option value="warning">有差异</option><option value="fail">未达到</option><option value="unknown">无法判断</option><option value="not_applicable">不适用</option></select><textarea v-model="event.notes" placeholder="实际结果与差异" />
    </article>
    <h4>实际结束状态 · 素材帧 {{ lastFrame }}</h4><button :disabled="busy || !candidate" @click="extract">本地保存结束帧（免费）</button><button @click="emit('refresh')">读取结束帧</button>
    <label>结束帧证据<select v-model="form.endState.evidenceAssetId"><option value="">提取完成后选择</option><option v-for="a in evidence" :key="a.id" :value="a.id">{{ a.id.slice(0,8) }}</option></select></label><img v-if="form.endState.evidenceAssetId" :src="`/api/v1/assets/${form.endState.evidenceAssetId}/content`" alt="所选素材的实际结束帧" />
    <label v-for="fact in form.endState.facts" :key="fact.key">{{ fact.key }}<textarea v-model="fact.value" placeholder="位置、姿态、道具状态和归属" /><select v-model="fact.certainty"><option value="observed">已观察</option><option value="unobserved">未观察</option><option value="uncertain">仍不确定</option></select></label>
    <label>尚未完成的动作<textarea v-model="form.endState.unfinishedActions" /></label><label><input v-model="form.endState.confirmed" type="checkbox" />我已核对结束帧与片段，确认上述状态</label>
    <label>采用结论<select v-model="form.disposition"><option value="accepted">内容通过</option><option value="accepted_with_issues">带问题采用</option><option value="rejected">拒绝</option></select></label><label>声音<select v-model="form.audioPolicy"><option value="native">采用原生声音</option><option value="mute">静音</option></select></label><label>身份、肢体、空间、声音及已知差异<textarea v-model="form.notes" /></label>
    <p v-if="error" class="notice error">{{ error }}</p><button :disabled="busy || !candidate" @click="save">保存选片与检查记录</button>
  </section>
</template>
<style scoped>.selection-editor{padding:1rem;background:#f8f7f3;border-radius:12px}video{display:block;width:100%;max-height:480px;background:#222}img{max-height:240px;max-width:100%}label{display:block;margin:.6rem 0}textarea{display:block;width:100%;min-height:3rem}table{width:100%;margin:1rem 0}input[type=number]{width:110px}article{padding:.8rem 0;border-bottom:1px solid #ddd}button{margin:.3rem}</style>
