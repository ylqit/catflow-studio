<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";

import { api } from "../../api/client";
import type { JobDto, ShotSpecDto, WorkspaceDto } from "../../api/types";
import { pendingIdempotencyKey, settleIdempotencyKey } from "../../idempotency";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const saving = ref(false);
const generating = ref(false);
const directorJob = ref<JobDto | null>(null);
const error = ref("");
const shots = reactive<ShotSpecDto[]>([]);
const totalDuration = computed(() => shots.reduce((sum, shot) => sum + shot.durationSeconds, 0));
const displayedDirectorJob = computed(() => directorJob.value ?? props.workspace.latestDirectorJob ?? null);

watch(
  () => props.workspace.activeShotPlan?.id,
  () => {
    const plan = props.workspace.activeShotPlan;
    shots.splice(0, shots.length, ...(plan ? JSON.parse(JSON.stringify(plan.shots)) as ShotSpecDto[] : []));
    if (plan) directorJob.value = null;
  },
  { immediate: true },
);

async function generateDirectorPlan() {
  if (generating.value) return;
  generating.value = true;
  error.value = "";
  const scope = `director:${props.projectId}`;
  const fingerprint = `${props.workspace.activeStory?.id ?? "missing-story"}:${props.workspace.selectionHash}`;
  try {
    directorJob.value = await api.generateShotPlan(
      props.projectId,
      pendingIdempotencyKey(scope, fingerprint),
    );
    settleIdempotencyKey(scope, fingerprint);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "专业导演分镜生成失败";
  } finally {
    generating.value = false;
  }
}

async function save() {
  const story = props.workspace.activeStory;
  const plan = props.workspace.activeShotPlan;
  if (!story || !plan) return;
  saving.value = true;
  error.value = "";
  try {
    await api.createShotPlan(props.projectId, {
      sourceStoryVersionId: story.id,
      sourceSelectionHash: props.workspace.selectionHash,
      clip: plan.clip,
      shots: shots.map((shot) => ({
        ...JSON.parse(JSON.stringify(shot)) as ShotSpecDto,
        durationFrames: shot.durationSeconds * 24,
      })),
      directorTreatment: plan.directorTreatment,
      directorPromptRevision: plan.directorPromptRevision,
      directorModel: plan.directorModel,
      directorInputHash: plan.directorInputHash,
    });
    emit("changed");
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "分镜保存失败";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section v-if="!workspace.activeStory" class="card empty missing-story">
    <div>✦</div><h2>先采用一个生活故事</h2><p>专业导演台只承接当前激活故事。</p><RouterLink class="primary" :to="`/projects/${projectId}/planner`">回到生活灵感</RouterLink>
  </section>

  <section v-else class="storyboard-layout">
    <aside class="story-source card">
      <p class="eyebrow">Active story · R{{ workspace.activeStory.revision }}</p>
      <h2>{{ workspace.activeStory.title }}</h2>
      <p class="story-body">{{ workspace.activeStory.body }}</p>
      <div class="story-rule"><b>{{ workspace.activeStory.targetDurationSeconds }} 秒</b><span>9:16</span><span>24 fps</span><span>{{ workspace.activeStory.dialoguePolicy === "none" ? "无对白" : "极少对白" }}</span></div>
      <ol>
        <li><b>触发</b>{{ workspace.activeStory.microEvent.trigger }}</li>
        <li><b>孩子动作</b>{{ workspace.activeStory.microEvent.childAction }}</li>
        <li><b>猫咪回应</b>{{ workspace.activeStory.microEvent.catResponse }}</li>
        <li><b>可见变化</b>{{ workspace.activeStory.microEvent.visibleChange }}</li>
        <li><b>温暖结尾</b>{{ workspace.activeStory.microEvent.warmEnding }}</li>
      </ol>
      <details v-if="workspace.activeShotPlan?.directorTreatment" class="treatment"><summary>故事导演解析</summary><pre>{{ JSON.stringify(workspace.activeShotPlan.directorTreatment, null, 2) }}</pre></details>
      <p v-if="workspace.activeShotPlan?.outdated" class="notice error">故事或角色选择已经变化，本版分镜只作历史参考。请重新生成专业导演分镜。</p>
    </aside>

    <div v-if="!workspace.activeShotPlan" class="director-empty card">
      <p class="eyebrow">Director Planner</p><h2>生成专业导演分镜</h2>
      <p>Director Planner 会把当前故事转换为 1–4 个可执行镜头，逐镜设计焦距、机位、构图、人物与猫咪走位、物理变化、连续性、声音和生成风险。模板不会直接写成正式分镜。</p>
      <div class="paid-note"><b>Ark · 专业规划模型 · Ark 付费模型</b><span>点击后直接创建一次分镜规划任务；完成后按 Provider usage 记录费用。</span></div>
      <button data-testid="generate-director-plan" class="primary" :disabled="generating" @click="generateDirectorPlan"><span v-if="generating" class="spinner" />生成专业导演分镜（Ark 付费）</button>
      <p v-if="displayedDirectorJob" class="notice">导演任务 {{ displayedDirectorJob.id }} · {{ displayedDirectorJob.status }}。任务完成后页面会从 PostgreSQL 读取正式版本。</p>
      <p v-if="error" class="notice error">{{ error }}</p>
    </div>

    <div v-else class="shot-editor card">
      <header class="editor-head">
        <div><p class="eyebrow">Professional shot plan · R{{ workspace.activeShotPlan.revision }}</p><h2>双层专业导演台</h2><small>{{ workspace.activeShotPlan.directorModel ?? "历史手工版本" }} · {{ workspace.activeShotPlan.directorPromptRevision ?? "无导演 Prompt 版本" }}</small></div>
        <div class="head-actions"><button class="secondary" :disabled="generating" @click="generateDirectorPlan">重新生成专业分镜</button><button class="primary" :disabled="saving" @click="save"><span v-if="saving" class="spinner" />保存新版本</button></div>
      </header>
      <p v-if="error" class="notice error editor-error">{{ error }}</p><p v-if="displayedDirectorJob" class="notice editor-error">导演任务 {{ displayedDirectorJob.id }} · {{ displayedDirectorJob.status }}</p>
      <div class="timeline-ruler"><span v-for="tick in 6" :key="tick">{{ Math.round(((tick - 1) / 5) * workspace.activeStory.targetDurationSeconds) }}s</span></div>

      <div class="shot-list">
        <article v-for="shot in shots" :key="shot.id" class="shot-card">
          <div class="shot-summary">
            <div class="shot-number">{{ String(shot.order).padStart(2, "0") }}<label><input v-model.number="shot.durationSeconds" type="number" min="2" max="15" /> 秒</label></div>
            <div class="shot-fields">
              <div class="field compact"><label>景别</label><input v-model="shot.framing" /></div><div class="field compact"><label>运镜</label><input v-model="shot.cameraMovement" /></div>
              <div class="field wide"><label>人物动作</label><input v-model="shot.childAction" /></div><div class="field wide"><label>猫咪动作</label><input v-model="shot.catAction" /></div>
              <div class="field wide"><label>环境变化</label><input v-model="shot.environmentChange" /></div><div class="field compact"><label>转场</label><select v-model="shot.transition"><option value="continuous">连续</option><option value="soft_cut">柔切</option><option value="hard_cut">硬切</option></select></div>
            </div>
          </div>

          <details data-testid="professional-shot-details" class="professional-details">
            <summary>查看详细导演设计</summary>
            <div v-if="shot.lens && shot.composition && shot.childBlocking && shot.catBlocking && shot.physicalChange && shot.continuity && shot.lighting && shot.sound" class="professional-grid">
              <fieldset><legend>焦距与机位</legend><label>等效焦距<input v-model="shot.lens.focalLengthEquivalent" /></label><label>机位高度<input v-model="shot.lens.cameraHeight" /></label><label>机位角度<input v-model="shot.lens.cameraAngle" /></label><label>透视意图<textarea v-model="shot.lens.perspectiveIntent" /></label></fieldset>
              <fieldset><legend>构图与轴线</legend><label>主体位置<input v-model="shot.composition.subjectPlacement" /></label><label>前景<input v-model="shot.composition.foreground" /></label><label>中景<input v-model="shot.composition.middleGround" /></label><label>背景<input v-model="shot.composition.background" /></label><label>运动方向<input v-model="shot.composition.screenDirection" /></label><label>视线<input v-model="shot.composition.eyeLine" /></label></fieldset>
              <fieldset><legend>人物走位</legend><label>初始状态<textarea v-model="shot.childBlocking.initialState" /></label><label>运动路径<textarea v-model="shot.childBlocking.movementPath" /></label><label>结束状态<textarea v-model="shot.childBlocking.endState" /></label><div class="tags"><span v-for="item in shot.childBlocking.microMotions" :key="item">{{ item }}</span></div></fieldset>
              <fieldset><legend>猫咪走位</legend><label>初始状态<textarea v-model="shot.catBlocking.initialState" /></label><label>运动路径<textarea v-model="shot.catBlocking.movementPath" /></label><label>结束状态<textarea v-model="shot.catBlocking.endState" /></label><div class="tags"><span v-for="item in shot.catBlocking.microMotions" :key="item">{{ item }}</span></div></fieldset>
              <fieldset><legend>物理状态变化</legend><label>对象<input v-model="shot.physicalChange.subject" /></label><label>变化前<textarea v-model="shot.physicalChange.before" /></label><label>变化后<textarea v-model="shot.physicalChange.after" /></label></fieldset>
              <fieldset><legend>镜头连续性</legend><label>承接状态<textarea v-model="shot.continuity.incoming" /></label><label>离开状态<textarea v-model="shot.continuity.outgoing" /></label><label>共享视觉元素<input v-model="shot.continuity.sharedVisualElement" /></label><label>最终帧<textarea v-model="shot.continuity.finalFrame" /></label></fieldset>
              <fieldset><legend>光线与色彩</legend><label>光线方向<input v-model="shot.lighting.direction" /></label><label>柔和度<input v-model="shot.lighting.softness" /></label><label>色彩意图<textarea v-model="shot.lighting.colorIntent" /></label></fieldset>
              <fieldset><legend>声音设计</legend><p><b>环境声</b>{{ shot.sound.ambience.join("、") }}</p><p><b>物件声</b>{{ shot.sound.objectEffects.join("、") }}</p><p><b>动作声</b>{{ shot.sound.movementEffects.join("、") }}</p><label>音乐意图<textarea v-model="shot.sound.musicIntent" /></label><label>对白<input v-model="shot.sound.dialogue" /></label></fieldset>
              <fieldset class="span-two"><legend>导演意图与生成风险</legend><label>导演意图<textarea v-model="shot.directorIntent" /></label><div v-for="risk in shot.generationRisks" :key="risk.code" class="risk"><code>{{ risk.code }}</code><span>{{ risk.message }}</span></div></fieldset>
            </div>
            <p v-else class="notice warn">这是旧版简化分镜，没有完整导演执行字段。建议通过 Director Planner 生成新版本。</p>
          </details>
        </article>
      </div>
      <footer class="duration-check"><span>镜头总时长</span><strong>{{ totalDuration }} / {{ workspace.activeStory.targetDurationSeconds }} 秒</strong><span class="pill" :class="{ good: totalDuration === workspace.activeStory.targetDurationSeconds }">{{ totalDuration === workspace.activeStory.targetDurationSeconds ? "帧数闭合" : "需要调整" }}</span></footer>
    </div>
  </section>
</template>

<style scoped>
.missing-story, .director-empty { padding: 70px; }.missing-story > div { font-size: 34px; color: var(--accent); }.missing-story p, .director-empty p { color: var(--muted); line-height: 1.7; }.missing-story .primary { display: inline-flex; align-items: center; }
.storyboard-layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 20px; align-items: start; }.story-source { position: sticky; top: 96px; padding: 24px; }.story-body { color: var(--muted); line-height: 1.7; font-size: 13px; }.story-rule { display: flex; flex-wrap: wrap; gap: 7px; margin: 16px 0; }.story-rule > * { padding: 6px 9px; border-radius: 8px; background: #f2ece4; color: #776e66; font-size: 11px; }.story-source ol { margin: 20px 0; padding: 0; list-style: none; display: grid; gap: 10px; }.story-source li { display: grid; gap: 3px; color: #766e67; font-size: 12px; line-height: 1.5; }.story-source li b { color: #b25e49; font-size: 10px; }.treatment pre { max-height: 260px; overflow: auto; white-space: pre-wrap; font-size: 9px; }
.director-empty { text-align: center; }.director-empty .paid-note { display: grid; gap: 5px; width: min(560px, 100%); margin: 22px auto; padding: 14px; border-radius: 12px; background: #fff4ea; color: var(--muted); font-size: 11px; }.director-empty .paid-note b { color: var(--ink); }.director-empty button { min-width: 260px; }
.shot-editor { overflow: hidden; }.editor-head { padding: 22px 24px; display: flex; justify-content: space-between; align-items: center; gap: 20px; border-bottom: 1px solid var(--line); }.editor-head h2 { margin: 0; font-size: 20px; }.editor-head small { color: var(--muted); }.head-actions { display: flex; gap: 8px; }.editor-error { margin: 16px 24px 0; }.timeline-ruler { display: flex; justify-content: space-between; margin: 20px 28px 0 100px; color: #aaa198; font-size: 9px; border-bottom: 1px solid #e4dbd2; padding-bottom: 5px; }
.shot-list { display: grid; gap: 14px; padding: 18px 24px; }.shot-card { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; background: #fff; }.shot-summary { display: grid; grid-template-columns: 65px 1fr; gap: 14px; align-items: center; padding: 16px; }.shot-number { font: 500 22px Georgia, serif; color: #c56d55; }.shot-number label { display: flex; align-items: center; gap: 3px; margin-top: 6px; color: #9c9289; font: 10px Inter, sans-serif; }.shot-number input { width: 35px; padding: 4px; }.shot-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px 12px; }.shot-fields .wide { grid-column: span 2; }.field { display: grid; gap: 4px; }.field.compact { grid-template-columns: 50px 1fr; align-items: center; }.field label { font-size: 10px; }.field input, .field select { padding: 7px 9px; font-size: 11px; }
.professional-details { border-top: 1px solid var(--line); background: #f8f3ed; }.professional-details > summary { padding: 12px 16px; cursor: pointer; color: #9d5845; font-size: 11px; font-weight: 800; }.professional-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 0 16px 16px; }.professional-grid fieldset { display: grid; gap: 8px; align-content: start; margin: 0; padding: 13px; border: 1px solid var(--line); border-radius: 11px; background: white; }.professional-grid legend { color: #a45e4c; font-size: 11px; font-weight: 800; }.professional-grid label { display: grid; gap: 4px; color: var(--muted); font-size: 9px; }.professional-grid input, .professional-grid textarea { padding: 7px 8px; border: 1px solid var(--line); border-radius: 7px; font-size: 10px; }.professional-grid p { display: grid; gap: 3px; margin: 0; color: var(--muted); font-size: 10px; }.professional-grid p b { color: var(--ink); }.professional-grid .span-two { grid-column: 1 / -1; }.tags { display: flex; flex-wrap: wrap; gap: 5px; }.tags span { padding: 4px 6px; border-radius: 6px; background: var(--sage-soft); color: #56705c; font-size: 9px; }.risk { display: grid; grid-template-columns: 120px 1fr; gap: 8px; padding: 7px; border-radius: 7px; background: #fff2e8; font-size: 10px; }
.duration-check { padding: 16px 24px; display: flex; gap: 12px; align-items: center; justify-content: flex-end; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }.duration-check strong { color: var(--ink); }
@media (max-width: 1050px) { .storyboard-layout { grid-template-columns: 1fr; }.story-source { position: static; }.professional-grid { grid-template-columns: 1fr; }.professional-grid .span-two { grid-column: auto; } }
</style>
