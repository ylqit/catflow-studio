<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import type { SeriesCreateCommand } from "../api/types";

const router = useRouter();
const saving = ref(false);
const error = ref("");
const form = reactive<SeriesCreateCommand>({
  title: "",
  premise: "",
  narrativeMode: "continuous",
  plannedEpisodeCount: 6,
  defaultEpisodeDurationSeconds: 12,
  worldSetting: "",
  emotionalDirection: "",
  endingGoal: "",
  recurringElements: [],
  mustKeep: [],
  mustAvoid: [],
  additionalNotes: "",
});
const recurringText = ref("");
const keepText = ref("");
const avoidText = ref("");

function lines(value: string): string[] {
  return value.split(/[\n，,]/).map((item) => item.trim()).filter(Boolean);
}

async function create() {
  saving.value = true;
  error.value = "";
  try {
    const created = await api.createStorySeries({
      ...form,
      recurringElements: lines(recurringText.value),
      mustKeep: lines(keepText.value),
      mustAvoid: lines(avoidText.value),
      endingGoal: form.endingGoal || null,
      additionalNotes: form.additionalNotes || null,
    });
    await router.push(`/series/${created.id}`);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "系列没有保存，请检查输入。";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <main class="page series-create-page">
    <nav class="wizard-steps" aria-label="新建系列步骤"><b>1 系列构想</b><span>2 规划预览</span><span>3 确认整季方案</span><span>4 开始制作</span></nav>
    <form class="card series-form" @submit.prevent="create">
      <header><div><h1>新建系列</h1><p>填写系列方向。本步骤只保存构想，不调用模型。</p></div><RouterLink to="/series">返回系列</RouterLink></header>
      <div class="form-grid">
        <label class="field"><span>系列名称</span><input v-model="form.title" required maxlength="160" placeholder="森林野餐" /></label>
        <label class="field"><span>叙事方式</span><select v-model="form.narrativeMode"><option value="continuous">连续剧情</option><option value="lightly_serialized">轻连续</option><option value="anthology">单元故事</option></select></label>
        <label class="field wide"><span>核心故事</span><textarea v-model="form.premise" required maxlength="4000" placeholder="孩子和猫咪从准备野餐到返程的连续一天…" /></label>
        <label class="field"><span>计划集数：{{ form.plannedEpisodeCount }} 集</span><input v-model.number="form.plannedEpisodeCount" type="range" min="2" max="30" /></label>
        <label class="field"><span>每集时长：{{ form.defaultEpisodeDurationSeconds }} 秒</span><input v-model.number="form.defaultEpisodeDurationSeconds" type="range" min="8" max="15" /></label>
        <label class="field wide"><span>世界与环境</span><textarea v-model="form.worldSetting" required maxlength="2000" placeholder="故事发生在哪里，时间、季节和环境有哪些稳定规则" /></label>
        <label class="field"><span>情绪方向</span><textarea v-model="form.emotionalDirection" required maxlength="1000" placeholder="从期待到满足，再温暖返程" /></label>
        <label class="field"><span>最终目标（可选）</span><textarea v-model="form.endingGoal" maxlength="1000" placeholder="最后一集希望抵达什么状态" /></label>
        <label class="field"><span>贯穿元素</span><textarea v-model="recurringText" placeholder="野餐篮，毛线球" /></label>
        <label class="field"><span>必须保留</span><textarea v-model="keepText" placeholder="每行一项" /></label>
        <label class="field"><span>必须避免</span><textarea v-model="avoidText" placeholder="危险动作，身份变化" /></label>
        <label class="field"><span>补充说明</span><textarea v-model="form.additionalNotes" placeholder="可留空" /></label>
      </div>
      <p v-if="error" class="notice error">{{ error }}</p>
      <footer><p>固定 9:16、24 fps；儿童、猫咪和基础画风继承当前固定设定。</p><button class="primary" :disabled="saving">{{ saving ? "正在保存" : "保存构想并查看规划" }}</button></footer>
    </form>
  </main>
</template>

<style scoped>
.wizard-steps { margin-bottom: 18px; display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; background: #fffaf4; }.wizard-steps > * { padding: 13px; text-align: center; color: var(--muted); font-size: 12px; border-right: 1px solid var(--line); }.wizard-steps > *:last-child { border-right: 0; }.wizard-steps b { color: var(--accent-dark); background: #fae8df; }.series-form { padding: 30px; }.series-form > header, .series-form > footer { display: flex; justify-content: space-between; align-items: start; gap: 30px; }.series-form header p, .series-form footer p { color: var(--muted); }.form-grid { margin: 24px 0; display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }.wide { grid-column: 1 / -1; }.series-form footer { align-items: center; }.series-form footer p { margin: 0; }.series-form footer button { min-width: 210px; }
</style>
