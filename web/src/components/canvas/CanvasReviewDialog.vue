<script setup lang="ts">
import { Close, FullScreen } from "@element-plus/icons-vue";

defineProps<{ title: string; fullscreenAvailable?: boolean }>();
const emit = defineEmits<{ close: []; fullscreen: [] }>();
</script>

<template>
  <div class="review-dialog-backdrop" role="presentation" @mousedown.self="emit('close')">
    <section class="canvas-review-dialog" role="dialog" aria-modal="true" :aria-label="`${title}审核`">
      <header>
        <div><span>媒体审核</span><b>{{ title }}</b></div>
        <nav>
          <button v-if="fullscreenAvailable" type="button" aria-label="全屏查看" @click="emit('fullscreen')"><FullScreen /></button>
          <button type="button" aria-label="关闭审核" @click="emit('close')"><Close /></button>
        </nav>
      </header>
      <div class="review-body"><slot /></div>
      <footer v-if="$slots.actions"><slot name="actions" /></footer>
    </section>
  </div>
</template>

<style scoped>
.review-dialog-backdrop { position: fixed; inset: 0; z-index: 1300; display: grid; place-items: center; padding: 30px; background: rgb(5 7 10 / 62%); backdrop-filter: blur(3px); }
.canvas-review-dialog { width: min(1120px, calc(100vw - 60px)); height: min(780px, calc(100vh - 60px)); display: grid; grid-template-rows: auto minmax(0, 1fr) auto; overflow: hidden; color: #eff4fa; background: #1d2026; border: 1px solid #505966; border-radius: 18px; box-shadow: 0 34px 90px rgb(0 0 0 / 62%); }
header { min-height: 62px; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 8px 12px 8px 20px; border-bottom: 1px solid #3d444f; background: #252930; } header div { min-width: 0; display: flex; align-items: baseline; gap: 12px; } header span { color: #8792a0; font-size: 11px; letter-spacing: .09em; } header b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } nav { display: flex; gap: 4px; } button { width: 44px; height: 44px; display: grid; place-items: center; color: #bdc7d4; background: transparent; border: 1px solid transparent; border-radius: 9px; cursor: pointer; } button:hover,button:focus-visible { color: #fff; background: #373d46; border-color: #535c68; outline: none; } button svg { width: 17px; }
.review-body { min-height: 0; overflow: auto; scrollbar-gutter: stable; } footer { min-height: 68px; display: flex; align-items: center; justify-content: flex-end; gap: 10px; padding: 10px 18px; border-top: 1px solid #3d444f; background: #252930; }
@media (max-width: 760px) { .review-dialog-backdrop { padding: 10px; align-items: end; } .canvas-review-dialog { width: 100%; height: min(88vh, calc(100vh - 20px)); border-radius: 16px; } }
</style>
