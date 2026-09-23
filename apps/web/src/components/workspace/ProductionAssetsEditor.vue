<script setup lang="ts">
const crypto = globalThis.crypto;
import type { AssetDto } from '../../api/types';
import type { SceneBinding, PropBinding } from '../../api/productionTypes';
import ProductionPropImage from './ProductionPropImage.vue';
const props = defineProps<{ projectId:string; scenes: SceneBinding[]; props: PropBinding[]; assets: AssetDto[] }>();
const emit = defineEmits<{ 'update:scenes': [SceneBinding[]]; 'update:props': [PropBinding[]]; refresh:[] }>();
function addScene() { emit('update:scenes', [...props.scenes, { key: `scene-${props.scenes.length+1}`, name: '', assetId: '', axis: '', layout: [] }]); }
function addProp() { emit('update:props', [...props.props, { key: `prop-${props.props.length+1}`, name: '', assetId: '', identity: '', initialState: '', plannedChange: '', location: '', owner: 'environment' }]); }
</script>
<template>
  <section class="production-assets">
    <h3>场景与道具</h3><p>使用资产页导入或生成的图片。标识与分镜中的场景、道具标识对应；普通陈设由场景图片承担。</p>
    <article v-for="(scene, index) in scenes" :key="index">
      <header><b>场景 {{ index+1 }}</b><button @click="emit('update:scenes', scenes.filter((_, i) => i !== index))">移除</button></header>
      <label>标识<input v-model="scene.key" /></label><label>名称<input v-model="scene.name" /></label>
      <label>环境图片<select v-model="scene.assetId"><option value="">选择图片</option><option v-for="asset in assets" :key="asset.id" :value="asset.id">{{ asset.role }} · {{ asset.id.slice(0,8) }}</option></select></label>
      <img v-if="scene.assetId" :src="`/api/v1/assets/${scene.assetId}/content`" :alt="scene.name || '场景预览'" />
      <label>主要轴线与朝向<textarea v-model="scene.axis" /></label>
      <svg v-if="scene.layout?.length" viewBox="0 0 120 120" role="img" aria-label="场景平面示意"><rect x="0" y="0" width="120" height="120" fill="#f2eee7" /><g v-for="point in scene.layout" :key="point.key"><circle :cx="point.x+10" :cy="point.y+10" r="3" fill="#4d7a68" /><text :x="point.x+10" :y="point.y+6" font-size="5">{{ point.label }}</text></g></svg>
      <div v-for="(point, i) in scene.layout" :key="i" class="point">
        <input v-model="point.key" aria-label="位置标识" placeholder="位置标识" /><input v-model="point.label" aria-label="位置名称" placeholder="名称" />
        <select v-model="point.kind" aria-label="位置类型"><option v-for="kind in ['region','furniture','support','child','cat','prop','camera']" :key="kind">{{ kind }}</option></select>
        <input v-model.number="point.x" type="number" min="0" max="100" aria-label="横向位置" /><input v-model.number="point.y" type="number" min="0" max="100" aria-label="纵向位置" /><input v-model="point.direction" aria-label="朝向或支撑说明" placeholder="朝向或支撑说明" />
        <button @click="scene.layout?.splice(i,1)">移除位置</button>
      </div>
      <button @click="(scene.layout ??= []).push({ key: crypto.randomUUID(), label: '', kind: 'region', x: 50, y: 50, direction: '' })">添加区域、支撑面或机位</button><small>位置用于设计与检查，不能精确控制模型像素坐标。</small>
    </article><button @click="addScene">添加场景</button>
    <article v-for="(prop, index) in props.props" :key="index">
      <header><b>关键道具 {{ index+1 }}</b><button @click="emit('update:props', props.props.filter((_, i) => i !== index))">移除</button></header>
      <label>标识<input v-model="prop.key" /></label><label>名称<input v-model="prop.name" /></label>
      <label>道具图片<select v-model="prop.assetId"><option value="">选择图片</option><option v-for="asset in assets" :key="asset.id" :value="asset.id">{{ asset.role }} · {{ asset.id.slice(0,8) }}</option></select></label>
      <img v-if="prop.assetId" :src="`/api/v1/assets/${prop.assetId}/content`" :alt="prop.name || '道具预览'" />
      <ProductionPropImage :project-id="projectId" :prop="prop" @refresh="emit('refresh')" />
      <label>数量、颜色、轮廓<textarea v-model="prop.identity" /></label><label>起始状态<textarea v-model="prop.initialState" /></label><label>计划变化<textarea v-model="prop.plannedChange" /></label><label>位置<input v-model="prop.location" /></label><label>归属<select v-model="prop.owner"><option value="environment">环境</option><option value="child">儿童</option><option value="cat">猫咪</option></select></label>
    </article><button @click="addProp">添加关键道具</button>
  </section>
</template>
<style scoped>
article{border:1px solid #deded6;padding:1rem;border-radius:12px;margin:1rem 0}label{display:block;margin:.5rem 0}input,textarea,select{display:block;width:100%}img,svg{max-width:220px;max-height:180px}header,.point{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}.point input{max-width:130px}.point select{width:auto}small{display:block;color:#666}
</style>
