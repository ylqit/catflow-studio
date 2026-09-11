<script setup lang="ts">
import { computed } from 'vue';
import type { AssetDto, JobDto, SegmentRepairPreviewDto } from '../../api/types';
import ProviderPrompt from '../ProviderPrompt.vue';
const props = defineProps<{ snapshot: SegmentRepairPreviewDto; job?: JobDto; assets: AssetDto[]; reuseDisabled?: boolean }>();
defineEmits<{ reuse: [] }>();
const video = computed(() => props.assets.find(asset => asset.id === props.snapshot.videoReference?.assetId));
const names: Record<string, string> = { anchor_in: '入点衔接参考', anchor_out: '出点衔接参考', first_frame: '严格首帧', last_frame: '严格尾帧', episode_child: '角色外观', episode_cat: '搭档外观', pair_scale: '相对比例', environment: '环境', style_board: '画风' };
</script>
<template>
  <section class="frozen-edit-details" aria-label="所选任务冻结输入">
    <h3>所选任务实际输入 · 只读</h3>
    <p>以下内容来自提交时保存的快照。当前修改方案单独保留。</p>
    <dl>
      <dt>生成任务</dt><dd>{{ job?.id ?? '历史记录' }} · {{ job?.model ?? snapshot.model }}</dd>
      <dt>来源结果</dt><dd>{{ snapshot.sourceResultJobId ?? '父草稿时间线' }}</dd>
      <dt>来源草稿版本</dt><dd>{{ snapshot.baseEditVersionId }}</dd>
      <dt>参考准备任务</dt><dd>{{ snapshot.referencePreparationJobId ?? '历史记录未提供' }}</dd>
      <dt>替换区间</dt><dd>[{{ snapshot.issueRange.startFrame }}, {{ snapshot.issueRange.endFrame }}) · {{ snapshot.issueRange.endFrame - snapshot.issueRange.startFrame }} 帧</dd>
      <dt>来源参考区间</dt><dd>[{{ snapshot.generationRange.startFrame }}, {{ snapshot.generationRange.endFrame }}) · {{ snapshot.generationRange.endFrame - snapshot.generationRange.startFrame }} 帧</dd>
      <dt>输出与默认取用</dt><dd>生成 {{ snapshot.providerDurationSeconds }} 秒 · 取用 [{{ snapshot.candidateCoreRange.startFrame }}, {{ snapshot.candidateCoreRange.endFrame }})</dd>
      <dt>生成方式</dt><dd>{{ snapshot.generationMode === 'from_frame' ? '从正确起点重新生成' : '修改现有片段' }}</dd>
      <dt>结束策略</dt><dd>{{ snapshot.endStatePolicy === 'match_original' ? '保留原结束状态' : snapshot.endStatePolicy === 'replace' ? '使用新的结束状态' : '按修改描述结束' }}</dd>
      <dt>修改目标</dt><dd>{{ snapshot.instruction }}</dd>
    </dl>
    <template v-if="snapshot.videoReference">
      <video v-if="video" :src="`/api/v1/assets/${video.id}/content`" controls preload="metadata" />
      <p>实际参考视频：{{ snapshot.videoReference.assetId ?? '历史素材编号未提供' }}<template v-if="video"> · {{ video.metadata.durationFrames }} 帧 / {{ (Number(video.metadata.durationFrames) / 24).toFixed(3) }} 秒</template></p>
    </template>
    <p v-else>此任务没有发送参考视频。</p>
    <ProviderPrompt historical :compiled-provider-prompt="snapshot.compiledProviderPrompt" :prompt="snapshot.prompt" :negative-prompt="snapshot.negativePrompt" :warnings="snapshot.warnings" />
    <details :open="snapshot.endStatePolicy === 'match_original'"><summary>实际发送图片（按提交顺序）</summary><div class="frozen-images">
      <figure v-for="(item, index) in snapshot.imageReferences" :key="`${item.role}:${index}`"><img v-if="item.assetId" :src="`/api/v1/assets/${item.assetId}/content`" :alt="names[item.role] ?? item.role" /><figcaption>图片 {{ index + 1 }} · {{ names[item.role] ?? item.role }}<template v-if="item.frameNumber != null"> · 草稿第 {{ item.frameNumber }} 帧</template></figcaption></figure>
    </div></details>
    <button class="secondary" :disabled="reuseDisabled" @click="$emit('reuse')">复用参数到当前修改方案</button>
    <p v-if="reuseDisabled">该任务属于其他草稿版本或输入正在恢复，请先打开对应版本。</p>
  </section>
</template>
<style scoped>
.frozen-edit-details { padding:20px 24px; border-block:1px solid var(--line); overflow-wrap:anywhere; } dl { display:grid; grid-template-columns:150px 1fr; gap:8px; } dd { margin:0; } video { max-height:250px; max-width:100%; } .frozen-images { display:flex; flex-wrap:wrap; gap:12px; } figure { margin:12px 0; max-width:130px; } img { width:100px; height:100px; object-fit:contain; } figcaption { font-size:12px; } button { margin-top:14px; } @media(max-width:600px) { dl { grid-template-columns:1fr; } }
</style>
