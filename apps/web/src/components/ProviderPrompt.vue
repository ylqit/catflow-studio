<script setup lang="ts">
import { computed, ref, watch } from "vue";

const props = defineProps<{
  compiledProviderPrompt?: string | null;
  prompt?: string | null;
  negativePrompt?: string | null;
  promptSections?: Array<{ key: string; title: string; content: string }> | null;
  warnings?: ReadonlyArray<Record<string, string>> | null;
  historical?: boolean;
}>();
const recorded = computed(() => typeof props.compiledProviderPrompt === "string");
const reviewWarnings = computed(() => props.warnings?.filter(warning => typeof warning.message === "string" && warning.message.trim()) ?? []);
const copyStatus = ref("");
watch(() => [props.compiledProviderPrompt, props.prompt], () => { copyStatus.value = ""; });

async function copyPrompt() {
  const text = recorded.value ? props.compiledProviderPrompt : props.prompt;
  if (typeof text !== "string") return;
  try {
    await navigator.clipboard.writeText(text);
    copyStatus.value = recorded.value ? "已复制最终模型指令。" : "已复制已记录正文；此记录不包含完整发送内容。";
  } catch {
    copyStatus.value = "复制失败，请在下方选中文本手动复制。";
  }
}
</script>

<template>
  <section class="provider-prompt" aria-label="模型指令记录">
    <section v-if="reviewWarnings.length" class="prompt-advice" aria-label="制作审查建议">
      <b>制作审查建议</b>
      <p>以下建议供人工检查，不属于最终模型指令。</p>
      <ul><li v-for="warning in reviewWarnings" :key="`${warning.code ?? ''}:${warning.message}`">{{ warning.message }}</li></ul>
    </section>
    <template v-if="recorded">
      <b>最终模型指令</b>
      <p class="prompt-note">包含避免项与参考图职责，按本次记录原样展示和复制。</p>
      <button type="button" class="secondary" @click="copyPrompt">复制最终模型指令</button>
      <pre class="compiled-provider-prompt">{{ compiledProviderPrompt }}</pre>
    </template>
    <template v-else>
      <p class="prompt-history-note">{{ historical ? '旧任务未记录最终模型指令，无法确认完整发送内容。系统不会用当前工作区推测。' : '此预览未提供最终模型指令，无法确认完整发送内容。' }}</p>
      <button v-if="prompt != null" type="button" class="secondary" @click="copyPrompt">复制已记录正文（非完整指令）</button>
      <p v-if="!prompt && !promptSections?.length" class="prompt-note">{{ historical ? '旧任务未记录完整生成指令，系统不会用当前内容推测。' : '暂无可展示的指令正文。' }}</p>
    </template>
    <p v-if="copyStatus" role="status">{{ copyStatus }}</p>
    <details v-if="prompt != null || negativePrompt || promptSections?.length" class="prompt-summary">
      <summary>查看正文与避免项摘要</summary>
      <div v-if="promptSections?.length" class="prompt-sections">
        <section v-for="section in promptSections" :key="section.key" class="prompt-section"><h3>{{ section.title }}</h3><p>{{ section.content }}</p></section>
      </div>
      <template v-else-if="prompt != null"><b>已记录正文</b><pre>{{ prompt }}</pre></template>
      <template v-if="negativePrompt"><b>需要避免的问题</b><pre>{{ negativePrompt }}</pre></template>
    </details>
  </section>
</template>

<style scoped>
.provider-prompt { min-width: 0; display: grid; gap: 10px; margin-block: 12px; overflow-wrap: anywhere; }
.provider-prompt p { margin: 0; line-height: 1.65; white-space: pre-wrap; }
.provider-prompt button { justify-self: start; }
.provider-prompt pre { margin: 0; padding: 12px; border-radius: 8px; background: #f7f2eb; max-height: 440px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.65; }
.prompt-note, .prompt-history-note { color: var(--muted); font-size: 12px; }
.prompt-advice { padding: 12px; border-radius: 8px; background: #fff4e8; color: #805c36; }
.prompt-advice ul { margin-bottom: 0; padding-left: 20px; }
.prompt-summary { display: grid; gap: 8px; }.prompt-summary summary { cursor: pointer; font-weight: 700; }
.prompt-sections { display: grid; gap: 10px; }.prompt-section h3 { margin: 10px 0 5px; font-size: 13px; }
</style>
