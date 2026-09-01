<script setup lang="ts">
import { reactive, ref } from "vue";

import { api } from "../../api/client";
import type { ShotSpecDto, WorkspaceDto } from "../../api/types";

const props = defineProps<{ projectId: string; workspace: WorkspaceDto }>();
const emit = defineEmits<{ changed: [] }>();
const saving = ref(false);
const error = ref("");

function defaultShots(): ShotSpecDto[] {
  const duration = props.workspace.activeStory?.targetDurationSeconds ?? props.workspace.project.targetDurationSeconds;
  const count = 3;
  const base = Math.floor(duration / count);
  const remainder = duration % count;
  const micro = props.workspace.activeStory?.microEvent;
  return [
    { id: "shot-1", order: 1, durationSeconds: base, framing: "中景", cameraMovement: "固定观察", childAction: micro?.childAction ?? "孩子注意到眼前的小事", catAction: micro?.catResponse ?? "猫咪停下观察", environmentChange: micro?.trigger ?? "生活事件被触发", transition: "continuous" },
    { id: "shot-2", order: 2, durationSeconds: base, framing: "近景", cameraMovement: "轻微下移", childAction: micro?.childAction ?? "孩子完成一个简单动作", catAction: micro?.catResponse ?? "猫咪安静回应", environmentChange: micro?.visibleChange ?? "画面出现可见变化", transition: "soft_cut" },
    { id: "shot-3", order: 3, durationSeconds: base + remainder, framing: "中近景", cameraMovement: "缓慢推进", childAction: "孩子停下来陪在猫咪身边", catAction: micro?.warmEnding ?? "猫咪靠近孩子", environmentChange: "柔和暖光落在一人一猫身上", transition: "continuous" },
  ];
}

const shots = reactive<ShotSpecDto[]>(props.workspace.activeShotPlan?.shots.map((shot) => ({ ...shot })) ?? defaultShots());

async function save() {
  const story = props.workspace.activeStory;
  if (!story) return;
  saving.value = true;
  error.value = "";
  try {
    await api.createShotPlan(props.projectId, {
      sourceStoryVersionId: story.id,
      sourceSelectionHash: props.workspace.selectionHash,
      clip: {
        durationSeconds: story.targetDurationSeconds,
        aspectRatio: "9:16",
        microEvent: story.title,
        childAction: story.microEvent.childAction,
        catActionOrObservation: story.microEvent.catResponse,
        visibleCauseAndEffect: story.microEvent.visibleChange,
        warmEnding: story.microEvent.warmEnding,
        dialoguePolicy: story.dialoguePolicy,
        environmentIntent: story.environmentIntent,
      },
      shots,
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
    <div>✦</div><h2>先采用一个生活故事</h2><p>分镜只承接当前激活故事，不再建立 Scene、ShotCard 或 Generation Plan。</p><RouterLink class="primary" :to="`/projects/${projectId}/planner`">回到生活灵感</RouterLink>
  </section>
  <section v-else class="storyboard-layout">
    <aside class="story-source card">
      <p class="eyebrow">Active story · R{{ workspace.activeStory.revision }}</p>
      <h2>{{ workspace.activeStory.title }}</h2>
      <p class="story-body">{{ workspace.activeStory.body }}</p>
      <div class="story-rule"><b>{{ workspace.activeStory.targetDurationSeconds }} 秒</b><span>9:16</span><span>{{ workspace.activeStory.dialoguePolicy === "none" ? "无对白" : "极少对白" }}</span></div>
      <ol>
        <li><b>触发</b>{{ workspace.activeStory.microEvent.trigger }}</li>
        <li><b>孩子动作</b>{{ workspace.activeStory.microEvent.childAction }}</li>
        <li><b>猫咪回应</b>{{ workspace.activeStory.microEvent.catResponse }}</li>
        <li><b>可见变化</b>{{ workspace.activeStory.microEvent.visibleChange }}</li>
        <li><b>温暖结尾</b>{{ workspace.activeStory.microEvent.warmEnding }}</li>
      </ol>
      <p v-if="workspace.activeShotPlan?.outdated" class="notice error">角色选择或故事已变化，这一版分镜已过期。保存后将创建新 Revision。</p>
    </aside>
    <div class="shot-editor card">
      <header class="editor-head"><div><p class="eyebrow">Shot plan</p><h2>用 1–4 个镜头讲清因果链</h2></div><button class="primary" :disabled="saving" @click="save"><span v-if="saving" class="spinner" />保存新版本</button></header>
      <p v-if="error" class="notice error editor-error">{{ error }}</p>
      <div class="timeline-ruler"><span v-for="tick in 6" :key="tick">{{ Math.round(((tick - 1) / 5) * workspace.activeStory.targetDurationSeconds) }}s</span></div>
      <div class="shot-list">
        <article v-for="shot in shots" :key="shot.id" class="shot-card">
          <div class="shot-number">{{ String(shot.order).padStart(2, "0") }}<span>{{ shot.durationSeconds }}s</span></div>
          <div class="shot-fields">
            <div class="field compact"><label>景别</label><input v-model="shot.framing" /></div>
            <div class="field compact"><label>运镜</label><input v-model="shot.cameraMovement" /></div>
            <div class="field wide"><label>孩子动作</label><input v-model="shot.childAction" /></div>
            <div class="field wide"><label>猫咪动作</label><input v-model="shot.catAction" /></div>
            <div class="field wide"><label>环境可见变化</label><input v-model="shot.environmentChange" /></div>
          </div>
          <select v-model="shot.transition" aria-label="转场"><option value="continuous">连续</option><option value="soft_cut">柔切</option><option value="hard_cut">硬切</option></select>
        </article>
      </div>
      <footer class="duration-check"><span>镜头总时长</span><strong>{{ shots.reduce((sum, shot) => sum + shot.durationSeconds, 0) }} / {{ workspace.activeStory.targetDurationSeconds }} 秒</strong><span class="pill good">一个主要事件</span></footer>
    </div>
  </section>
</template>

<style scoped>
.missing-story { padding: 90px; }
.missing-story > div { font-size: 34px; color: var(--accent); }
.missing-story p { color: var(--muted); }
.missing-story .primary { display: inline-flex; align-items: center; }
.storyboard-layout { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 20px; align-items: start; }
.story-source { position: sticky; top: 96px; padding: 24px; }
.story-body { color: var(--muted); line-height: 1.7; font-size: 13px; }
.story-rule { display: flex; gap: 7px; margin: 16px 0; }
.story-rule > * { padding: 6px 9px; border-radius: 8px; background: #f2ece4; color: #776e66; font-size: 11px; }
.story-source ol { margin: 20px 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.story-source li { display: grid; gap: 3px; color: #766e67; font-size: 12px; line-height: 1.5; }
.story-source li b { color: #b25e49; font-size: 10px; }
.shot-editor { overflow: hidden; }
.editor-head { padding: 22px 24px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--line); }
.editor-head h2 { margin: 0; font-size: 20px; }
.editor-error { margin: 16px 24px 0; }
.timeline-ruler { display: flex; justify-content: space-between; margin: 20px 28px 0 100px; color: #aaa198; font-size: 9px; border-bottom: 1px solid #e4dbd2; padding-bottom: 5px; }
.shot-list { display: grid; gap: 12px; padding: 18px 24px; }
.shot-card { display: grid; grid-template-columns: 52px 1fr 82px; gap: 14px; align-items: center; padding: 16px; border: 1px solid var(--line); border-radius: 14px; background: #fff; }
.shot-number { font: 500 22px Georgia, serif; color: #c56d55; }
.shot-number span { display: block; margin-top: 5px; color: #9c9289; font: 10px Inter, sans-serif; }
.shot-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px 12px; }
.shot-fields .wide { grid-column: span 2; }
.field.compact { grid-template-columns: 43px 1fr; align-items: center; }
.field.compact label { font-size: 10px; }
.field input { padding: 7px 9px; font-size: 11px; }
.shot-card > select { padding: 8px; border: 1px solid var(--line); border-radius: 9px; background: #f6f1ea; color: #6d645d; font-size: 10px; }
.duration-check { padding: 16px 24px; display: flex; gap: 12px; align-items: center; justify-content: flex-end; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
.duration-check strong { color: var(--ink); }
</style>
