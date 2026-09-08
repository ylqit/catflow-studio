<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '../../api/client';
import type { WorkspaceDto } from '../../api/types';
import type { PaidModelRuntime } from '../../presentation';
import { pendingIdempotencyKey, settleIdempotencyKey } from '../../idempotency';
import ShotProduction from './ShotProduction.vue';
const props = defineProps<{ projectId: string; workspace: WorkspaceDto; runtime?: PaidModelRuntime | null }>();
const choices = reactive<Record<string, { shotId: string; assetId: string; sourceInFrame: number } | null>>({});
const busy = ref(false); const error = ref(''); const router = useRouter();
const complete = computed(() => !!props.workspace.activeShotPlan?.shots.every(s => choices[s.id]));
async function assemble() {
  const plan = props.workspace.activeShotPlan; if (!plan || !complete.value) return;
  busy.value = true; error.value = '';
  const takes = plan.shots.map(s => choices[s.id]!); const fingerprint = JSON.stringify({ plan: plan.id, takes }); const scope = `shot-assembly:${props.projectId}`;
  try { const draft = await api.assembleShots(props.projectId, { shotPlanVersionId: plan.id, takes, idempotencyKey: pendingIdempotencyKey(scope, fingerprint) }); settleIdempotencyKey(scope, fingerprint); await router.push({ path: `/projects/${props.projectId}/delivery`, query: { draftId: draft.id } }); }
  catch (reason) { error.value = String(reason); } finally { busy.value = false; }
}
</script>
<template>
  <section class="card sequence-production"><h3>逐镜头生成与取用</h3><p>先在分镜画布确认各镜头起始画面。每个镜头各产生一次视频费用；没有批量提交。选齐后免费组装完整草稿，再播放和验收。</p><template v-if="workspace.activeShotPlan"><article v-for="shot in workspace.activeShotPlan.shots" :key="shot.id"><h4>镜头 {{ shot.order }} · {{ shot.durationSeconds }} 秒</h4><ShotProduction :project-id="projectId" :plan-id="workspace.activeShotPlan.id" :shot-id="shot.id" :runtime="runtime" video-mode @take="choices[shot.id] = $event" /></article><button class="primary" :disabled="busy || !complete" @click="assemble">组装完整编辑草稿（本地免费）</button></template><p v-if="error" role="alert">{{ error }}</p></section>
</template>
<style scoped>.sequence-production { padding: 22px; }article { border: 1px solid var(--line); border-radius: 12px; margin: 16px 0; }h4 { margin: 12px 16px; }</style>
