<!--
  ActionBeatsEditor.vue —— 单镜头"节拍与表演"编辑器(v-model 组件)

  职责:
  1. 编辑一个镜头内的 actionBeats 数组:帧区间 / 作用(purpose) / 角色动作 / 可见变化
  2. 可选编辑 catPerformance(脸部可见性 / 目光转移 / 眼睑动作)
  3. 本地即时校验(issues):只列出错误不拦截输入,最终裁决由后端 shot plan 校验完成

  帧规则(与后端 DIRECTOR_BEAT_DIRECTION 提示词常量一致):
    - 24fps,startFrame/endFrame 为本镜头内整数帧,左闭右开 [start, end)
    - 按顺序、不重叠、不超出镜头总帧数 durationFrames
    - 每拍必填 visibleChange;childAction 与 catAction 至少填一个
    - eyelidAction 取 none/blink/slow_blink/squint_release,
      其中 blink/slow_blink 要求"闭合—重新睁开"过程清楚且脸部可读

  关联组件:
    - StoryboardStep.vue:分镜画布中对选中镜头 v-model="shot.actionBeats"
    - DirectorDraftEditor.vue:导演草稿的逐镜节拍编辑
    - 节拍数据随 shot_plan_versions.shots_json 入库,编译进 provider prompt
-->
<script setup lang="ts">
import { computed } from 'vue';
import type { ActionBeatDto, CatPerformanceDto } from '../../api/types';

const props = defineProps<{
  modelValue?: ActionBeatDto[] | null; // 当前镜头节拍数组(null = 历史分镜尚无节拍设计)
  durationFrames: number; // 镜头总帧数 = durationSeconds × 24
  disabled?: boolean;
  // 首次"添加节拍设计"时预填用,来自镜头级动作摘要(childAction/catAction/environmentChange)
  initialChildAction?: string;
  initialCatAction?: string;
  initialVisibleChange?: string;
}>();
const emit = defineEmits<{ 'update:modelValue': [value: ActionBeatDto[]] }>();
// 本地校验:逐拍检查帧区间(整数、递增、不重叠、不越界)与必填文字,产出中文错误列表
const issues = computed(() => {
  let end = 0; // 上一拍的 endFrame,用于验证"按顺序、不重叠"
  return (props.modelValue ?? []).flatMap((beat, index) => {
    // invalid:非整数帧 / 起点早于上一拍终点(重叠或乱序) / 起点为负 / 空区间 / 超出镜头
    const invalid = !Number.isInteger(beat.startFrame) || !Number.isInteger(beat.endFrame)
      || beat.startFrame < end || beat.startFrame < 0 || beat.endFrame <= beat.startFrame
      || beat.endFrame > props.durationFrames;
    end = beat.endFrame;
    // missing:可见变化必填;两个角色动作至少填一个(不在画面的角色可为空)
    const missing = !beat.visibleChange.trim() || (!beat.childAction.trim() && !beat.catAction.trim() && !beat.environmentAction?.trim());
    return invalid || missing ? [`节拍 ${index + 1}：${invalid ? '帧区间重叠、越界或顺序不正确' : '请填写动作和可见变化'}`] : [];
  });
});
// 末拍至少 2 帧才可再拆分(对半拆后各自 ≥1 帧);无节拍时也允许(走"从零添加"分支)
const canSplit = computed(() => {
  const last = props.modelValue?.at(-1);
  return !last || last.endFrame - last.startFrame >= 2;
});
// v-model 写回:给第 index 拍打补丁后整体 emit 新数组(不可变更新,不直接改 props)
function update(index: number, patch: Partial<ActionBeatDto>) {
  emit('update:modelValue', (props.modelValue ?? []).map((beat, i) => i === index ? { ...beat, ...patch } : beat));
}
// catPerformance 子对象补丁:该拍尚无眼神安排时,先用默认值初始化(可见 / 空目光 / 不指定眼睑)
function performance(index: number, patch: Partial<CatPerformanceDto>) {
  const current = props.modelValue?.[index]?.catPerformance
    ?? { visibility: 'visible', gazeFrom: '', gazeTo: '', eyelidAction: 'none' };
  update(index, { catPerformance: { ...current, ...patch } });
}
// 添加节拍:
// - 无节拍 → 生成一个覆盖整镜的 action 拍,用镜头级动作摘要预填(降低从零输入成本)
// - 有节拍 → 把末拍从中点对半拆,后半成为新的 reaction 拍(对应"行动→反应"叙事结构)
function add() {
  const beats = (props.modelValue ?? []).map(beat => ({ ...beat }));
  if (!beats.length) {
    emit('update:modelValue', [{ startFrame: 0, endFrame: props.durationFrames, purpose: 'action',
      childAction: props.initialChildAction ?? '', catAction: props.initialCatAction ?? '',
      visibleChange: props.initialVisibleChange ?? '', catPerformance: null }]);
    return;
  }
  const last = beats[beats.length - 1];
  const split = Math.floor((last.startFrame + last.endFrame) / 2); // 中点向下取整
  const end = last.endFrame;
  last.endFrame = split;
  beats.push({ startFrame: split, endFrame: end, purpose: 'reaction', childAction: '', catAction: '', visibleChange: '', catPerformance: null });
  emit('update:modelValue', beats);
}
// 按序号移除;模板侧限制至少保留 1 拍(仅剩 1 拍时"移除"按钮禁用)
function remove(index: number) {
  emit('update:modelValue', (props.modelValue ?? []).filter((_, i) => i !== index));
}
</script>

<template>
  <fieldset class="beats-editor" :disabled="disabled">
    <legend>节拍与表演</legend>
    <p>按本镜头内的时间安排触发、行动、反应和回报。24 帧为 1 秒；动作摘要由这里同步，眼型固定不等于眼睑和视线固定。</p>
    <p v-if="!modelValue?.length">历史分镜尚无节拍设计，原动作仍有效。可主动添加后再编辑。</p>
    <!-- 每个节拍一张卡片:头部显示秒区间(帧 ÷ 24),下方依次是帧区间/作用/动作/可见变化/眼神表演 -->
    <article v-for="(beat, index) in modelValue" :key="index" class="beat">
      <label>道具或环境动作（画外角色留空）<textarea :value="beat.environmentAction ?? ''" @input="update(index,{environmentAction:($event.target as HTMLTextAreaElement).value})" /></label>
      <header><b>节拍 {{ index + 1 }} · {{ (beat.startFrame / 24).toFixed(2) }}–{{ (beat.endFrame / 24).toFixed(2) }} 秒</b><button type="button" :disabled="(modelValue?.length ?? 0) < 2" @click="remove(index)">移除</button></header>
      <div class="beat-time">
        <label>开始帧<input type="number" :aria-label="`节拍 ${index + 1} 开始帧`" min="0" :max="durationFrames - 1" :value="beat.startFrame" @input="update(index, { startFrame: Number(($event.target as HTMLInputElement).value) })" /></label>
        <label>结束帧（不含）<input type="number" :aria-label="`节拍 ${index + 1} 结束帧`" min="1" :max="durationFrames" :value="beat.endFrame" @input="update(index, { endFrame: Number(($event.target as HTMLInputElement).value) })" /></label>
        <label>作用<select :value="beat.purpose" @change="update(index, { purpose: ($event.target as HTMLSelectElement).value as ActionBeatDto['purpose'] })"><option value="trigger">触发</option><option value="action">行动</option><option value="reaction">反应</option><option value="payoff">回报</option></select></label>
      </div>
      <label>儿童动作<textarea :aria-label="`节拍 ${index + 1} 儿童动作`" :value="beat.childAction" @input="update(index, { childAction: ($event.target as HTMLTextAreaElement).value })" /></label>
      <label>猫咪动作<textarea :aria-label="`节拍 ${index + 1} 猫咪动作`" :value="beat.catAction" @input="update(index, { catAction: ($event.target as HTMLTextAreaElement).value })" /></label>
      <label>可见的新变化<textarea :aria-label="`节拍 ${index + 1} 可见变化`" :value="beat.visibleChange" @input="update(index, { visibleChange: ($event.target as HTMLTextAreaElement).value })" /></label>
      <button v-if="!beat.catPerformance" type="button" @click="performance(index, {})">添加猫咪眼神表演</button>
      <div v-if="beat.catPerformance" class="performance">
        <label>脸部可见性<select :value="beat.catPerformance.visibility" @change="performance(index, { visibility: ($event.target as HTMLSelectElement).value as CatPerformanceDto['visibility'] })"><option value="visible">清楚可见</option><option value="partial">部分可见</option><option value="hidden">不可见</option></select></label>
        <label>起初关注<input :value="beat.catPerformance.gazeFrom" @input="performance(index, { gazeFrom: ($event.target as HTMLInputElement).value })" /></label>
        <label>随后关注<input :value="beat.catPerformance.gazeTo" @input="performance(index, { gazeTo: ($event.target as HTMLInputElement).value })" /></label>
        <label>眼睑动作<select :aria-label="`节拍 ${index + 1} 眼睑动作`" :value="beat.catPerformance.eyelidAction" @change="performance(index, { eyelidAction: ($event.target as HTMLSelectElement).value as CatPerformanceDto['eyelidAction'] })"><option value="none">不特别指定</option><option value="blink">闭合后自然睁开</option><option value="slow_blink">缓慢闭合再睁开</option><option value="squint_release">半眯后恢复</option></select></label>
        <button type="button" @click="update(index, { catPerformance: null })">移除眼神安排</button>
      </div>
    </article>
    <!-- 本地校验错误列表(issues);底部按钮:无节拍时"添加",有节拍时"拆分末拍并补充反应" -->
    <p v-for="issue in issues" :key="issue" role="alert" class="beat-error">{{ issue }}</p>
    <button type="button" :disabled="!canSplit" @click="add">{{ modelValue?.length ? '拆分末节拍并补充反应' : '添加节拍设计' }}</button>
  </fieldset>
</template>

<style scoped>
.beats-editor { min-width: 0; border: 1px solid var(--line, #d9c8b8); border-radius: 12px; margin: 14px 0; padding: 16px; }
legend { font-weight: 700; } p { line-height: 1.6; font-size: 12px; color: var(--muted, #655b52); }
.beat { padding: 12px; margin: 12px 0; background: #faf7f2; border-radius: 8px; }
header { display: flex; justify-content: space-between; gap: 12px; } label { display: block; font-size: 12px; margin: 8px 0; }
input, select, textarea { display: block; box-sizing: border-box; width: 100%; font: inherit; margin-top: 4px; }
textarea { min-height: 64px; resize: vertical; } .beat-time, .performance { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.beat-error { color: #a4402c; } @media (max-width: 720px) { .beat-time, .performance { grid-template-columns: 1fr; } }
</style>
