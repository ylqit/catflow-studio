<script setup lang="ts">
import { computed } from "vue";
import ActionBeatsEditor from './ActionBeatsEditor.vue';
import type { ActionBeatDto } from '../../api/types';

const props = defineProps<{ modelValue: string }>();
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();
const document = computed(() => {
  try { const value = JSON.parse(props.modelValue); return value && typeof value === 'object' ? value : null; }
  catch { return null; }
});
const shots = computed<Record<string, unknown>[]>(() => Array.isArray(document.value?.shots) ? document.value.shots : []);
const names: Record<string, string> = {
  framing: '景别与构图', cameraMovement: '运镜', childAction: '儿童动作摘要', catAction: '猫咪动作摘要',
  environmentChange: '画面变化摘要', childBlocking: '儿童动作', catBlocking: '猫咪动作',
  initialState: '开始状态', movementPath: '动作过程', endState: '结束状态', microMotions: '微动作（每行一项）',
  lens: '镜头设计', focalLengthEquivalent: '等效焦距', cameraHeight: '机位高度', cameraAngle: '拍摄角度', perspectiveIntent: '透视意图',
  composition: '构图', subjectPlacement: '主体位置', foreground: '前景', middleGround: '中景', background: '背景', screenDirection: '屏幕方向', eyeLine: '视线',
  cameraSpatialRelation: '机位与空间关系', interactionConstraints: '交互约束（每行一项）', visualExclusions: '画面排除项（每行一项）',
  physicalChange: '可见变化', subject: '变化主体', before: '变化前', after: '变化后',
  continuity: '连续性', incoming: '入场承接', outgoing: '结尾承接', sharedVisualElement: '共享视觉元素', finalFrame: '最后画面',
  lighting: '光照', direction: '方向', softness: '柔和程度', colorIntent: '色彩意图',
  sound: '声音', ambience: '环境声（每行一项）', objectEffects: '物件声（每行一项）', movementEffects: '动作声（每行一项）', musicIntent: '音乐意图', dialogue: '对白', directorIntent: '导演意图',
};
const requiredGroups: Record<string, string[]> = {
  childBlocking: ['initialState', 'movementPath', 'endState'], catBlocking: ['initialState', 'movementPath', 'endState'],
  lens: ['focalLengthEquivalent', 'cameraHeight', 'cameraAngle', 'perspectiveIntent'],
  composition: ['subjectPlacement', 'foreground', 'middleGround', 'background', 'screenDirection', 'eyeLine'],
  physicalChange: ['subject', 'before', 'after'], continuity: ['incoming', 'outgoing', 'sharedVisualElement', 'finalFrame'],
  lighting: ['direction', 'softness', 'colorIntent'], sound: ['musicIntent'],
};
type Field = { path: string[]; label: string; value: string; list: boolean; missing: boolean };
function editableBeats(shot: Record<string, unknown>): ActionBeatDto[] | undefined {
  if (!Array.isArray(shot.actionBeats)) return undefined;
  const readable = shot.actionBeats.every(beat => beat && typeof beat === 'object'
    && ['childAction', 'catAction', 'visibleChange'].every(key => typeof beat[key] === 'string')
    && typeof beat.startFrame === 'number' && typeof beat.endFrame === 'number'
    && (!beat.catPerformance || (typeof beat.catPerformance === 'object'
      && ['visibility', 'gazeFrom', 'gazeTo', 'eyelidAction'].every(key => typeof beat.catPerformance[key] === 'string'))));
  return readable ? shot.actionBeats as ActionBeatDto[] : undefined;
}
function fields(shot: Record<string, unknown>): Field[] {
  const result: Field[] = [];
  for (const key of ['framing', 'cameraMovement', 'childAction', 'catAction', 'environmentChange', 'cameraSpatialRelation', 'interactionConstraints', 'visualExclusions', ...Object.keys(requiredGroups), 'directorIntent']) {
    const children = requiredGroups[key];
    if (children) {
      const group = shot[key] && typeof shot[key] === 'object' ? shot[key] as Record<string, unknown> : {};
      for (const child of new Set([...children, ...Object.keys(group)])) add([key, child], group[child], children.includes(child));
    } else add([key], shot[key], true);
  }
  function add(path: string[], value: unknown, required: boolean) {
    if (Array.isArray(shot.actionBeats) && shot.actionBeats.length && (
      ['childAction', 'catAction', 'environmentChange'].includes(path[0])
      || (['childBlocking', 'catBlocking'].includes(path[0]) && ['movementPath', 'microMotions'].includes(path[1]))
    )) return;
    const list = Array.isArray(value) || ['interactionConstraints', 'visualExclusions'].includes(path[0]);
    result.push({ path, label: path.map(part => names[part] ?? part).join(' → '),
      value: value == null ? '' : Array.isArray(value) ? value.join('\n') : typeof value === 'object' ? JSON.stringify(value) : String(value),
      list, missing: required && (value == null || value === '') });
  }
  return result;
}
function updateBeats(index: number, beats: ActionBeatDto[]) {
  if (!document.value) return;
  const next = structuredClone(document.value);
  next.shots[index].actionBeats = beats;
  emit('update:modelValue', JSON.stringify(next, null, 2));
}
function update(index: number, path: string[], value: string, list = false) {
  if (!document.value) return;
  const next = structuredClone(document.value);
  let target = next.shots[index];
  for (const key of path.slice(0, -1)) {
    if (!target[key] || typeof target[key] !== 'object' || Array.isArray(target[key])) target[key] = {};
    target = target[key];
  }
  const key = path[path.length - 1];
  target[key] = list ? value.split('\n').filter(line => line.trim()) : key === 'durationSeconds' ? Number(value) : value;
  if (key === 'durationSeconds') target.durationFrames = Number(value) * 24;
  emit('update:modelValue', JSON.stringify(next, null, 2));
}
</script>

<template>
  <div class="director-draft-fields">
    <p v-if="!document">正文暂时不能解析，请在高级编辑中检查 JSON；已有输入仍保留。</p>
    <article v-for="(shot, index) in shots" :key="index" class="draft-shot">
      <h3>镜头 {{ index + 1 }} · 已返回内容</h3>
      <ActionBeatsEditor v-if="shot.actionBeats == null || editableBeats(shot)" :model-value="editableBeats(shot)" :duration-frames="Number(shot.durationSeconds ?? 2) * 24" :initial-child-action="String(shot.childAction ?? '')" :initial-cat-action="String(shot.catAction ?? '')" :initial-visible-change="String(shot.environmentChange ?? '')" @update:model-value="updateBeats(index, $event)" />
      <p v-else role="alert">节拍字段结构不完整，原结果已保留，请在高级编辑中修正 actionBeats 后使用表单。</p>
      <label>时长（秒）<input type="number" min="2" max="15" :aria-label="`镜头 ${index + 1} 时长`" :value="shot.durationSeconds" @input="update(index, ['durationSeconds'], ($event.target as HTMLInputElement).value)" /></label>
      <details open><summary>动作、画面与声音</summary>
        <label v-for="field in fields(shot)" :key="field.path.join('.')" :class="{ missing: field.missing }">
          {{ field.label }} <strong v-if="field.missing">待补充</strong>
          <textarea rows="2" :aria-label="`镜头 ${index + 1} ${field.label}`" :value="field.value" @input="update(index, field.path, ($event.target as HTMLTextAreaElement).value, field.list)" />
          <button v-if="field.list && field.missing" type="button" class="secondary" :aria-label="`镜头 ${index + 1} ${field.label} 标记为空列表`" @click="update(index, field.path, '', true)">无适用项，记录为空列表</button>
        </label>
      </details>
    </article>
    <details><summary>高级编辑：完整 JSON</summary><textarea class="json" aria-label="待补充的分镜草稿" :value="modelValue" spellcheck="false" @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)" /></details>
  </div>
</template>

<style scoped>
.draft-shot { border: 1px solid #d9c8b8; border-radius: 12px; padding: 16px; margin: 12px 0; min-width: 0; }
label { display: block; margin: 12px 0; font-size: 14px; }
textarea { display: block; width: 100%; min-height: 70px; resize: vertical; font: inherit; overflow-wrap: anywhere; }
.missing { color: #a4402c; } .missing textarea { border-color: #b95239; }
.json { min-height: 240px; font-family: monospace; }
</style>
