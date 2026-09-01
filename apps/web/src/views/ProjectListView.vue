<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import type { ProjectDto } from "../api/types";

const router = useRouter();
const projects = ref<ProjectDto[]>([]);
const loading = ref(true);
const creating = ref(false);
const showCreate = ref(false);
const error = ref("");
const draft = reactive({ title: "", theme: "", targetDurationSeconds: 10 });

async function loadProjects() {
  loading.value = true;
  error.value = "";
  try {
    projects.value = await api.projects();
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "项目读取失败";
  } finally {
    loading.value = false;
  }
}

async function createProject() {
  creating.value = true;
  error.value = "";
  try {
    const project = await api.createProject(draft);
    await router.push(`/projects/${project.id}/planner`);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : "项目创建失败";
  } finally {
    creating.value = false;
  }
}

onMounted(loadProjects);
</script>

<template>
  <main class="page projects-page">
    <section class="page-heading">
      <div>
        <p class="eyebrow">Original life clips</p>
        <h1>生活短片项目</h1>
        <p class="subtitle">一个项目，只讲清一个 8–15 秒的一人一猫生活微事件。</p>
      </div>
      <button class="primary new-project" @click="showCreate = true">＋ 新建短片</button>
    </section>

    <p v-if="error" class="notice error">{{ error }}</p>
    <section v-if="loading" class="card empty">正在从 PostgreSQL 恢复项目…</section>
    <section v-else-if="projects.length" class="project-grid">
      <RouterLink
        v-for="(project, index) in projects"
        :key="project.id"
        class="project-card card"
        :to="`/projects/${project.id}/planner`"
      >
        <div class="project-cover" :class="`cover-${(index % 4) + 1}`">
          <span class="cover-cat">◖ ᵔᴗᵔ ◗</span>
          <span class="duration">{{ project.targetDurationSeconds }}s</span>
        </div>
        <div class="project-content">
          <div>
            <h2>{{ project.title }}</h2>
            <p>{{ project.theme }}</p>
          </div>
          <div class="project-meta">
            <span>9:16</span>
            <span>Canon v4</span>
            <time>{{ new Date(project.updatedAt).toLocaleDateString("zh-CN") }}</time>
          </div>
        </div>
      </RouterLink>
    </section>
    <section v-else class="card empty empty-projects">
      <div class="empty-illustration">☁︎　🐾　☀︎</div>
      <h2>从一个很小的日常开始</h2>
      <p>例如雨天擦爪、折叠毛巾，或分享窗边的阳光。</p>
      <button class="primary" @click="showCreate = true">创建第一个项目</button>
    </section>

    <div v-if="showCreate" class="modal-backdrop" @click.self="showCreate = false">
      <form class="create-modal card" @submit.prevent="createProject">
        <div class="modal-head">
          <div><p class="eyebrow">New clip</p><h2>新建生活短片</h2></div>
          <button type="button" class="modal-close" @click="showCreate = false">×</button>
        </div>
        <div class="field">
          <label for="project-title">短片名称</label>
          <input id="project-title" v-model="draft.title" required maxlength="160" placeholder="雨天擦爪" />
        </div>
        <div class="field">
          <label for="project-theme">最初的生活灵感</label>
          <textarea id="project-theme" v-model="draft.theme" required placeholder="孩子替刚回家的猫咪擦干湿爪…" />
        </div>
        <div class="field">
          <label for="project-duration">目标时长：{{ draft.targetDurationSeconds }} 秒</label>
          <input id="project-duration" v-model.number="draft.targetDurationSeconds" type="range" min="8" max="15" />
        </div>
        <p class="notice">固定 9:16，采用 Canon v4 的同一位短发儿童、同一只灰白虎斑猫与柔和数字插画画风。</p>
        <button class="primary modal-submit" :disabled="creating || !draft.title || !draft.theme">
          <span v-if="creating" class="spinner" />{{ creating ? "正在创建" : "进入生活灵感" }}
        </button>
      </form>
    </div>
  </main>
</template>

<style scoped>
.new-project { min-width: 132px; }
.project-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 22px; }
.project-card { overflow: hidden; transition: transform .2s, box-shadow .2s; }
.project-card:hover { transform: translateY(-4px); box-shadow: var(--shadow); }
.project-cover { height: 190px; position: relative; display: grid; place-items: center; overflow: hidden; }
.project-cover::before, .project-cover::after { content: ""; position: absolute; border-radius: 50%; background: #ffffff35; }
.project-cover::before { width: 260px; height: 260px; left: -90px; top: -145px; }
.project-cover::after { width: 180px; height: 180px; right: -45px; bottom: -110px; }
.cover-1 { background: linear-gradient(145deg, #d8b895, #b98968); }
.cover-2 { background: linear-gradient(145deg, #a8b8a5, #738f7d); }
.cover-3 { background: linear-gradient(145deg, #d5aaa3, #b67f7c); }
.cover-4 { background: linear-gradient(145deg, #b9b5c9, #85829c); }
.cover-cat { position: relative; z-index: 1; color: #fffaf2; font: 600 25px Georgia, serif; text-shadow: 0 5px 15px #5e4b3a44; }
.duration { position: absolute; z-index: 2; right: 14px; top: 14px; padding: 5px 9px; border-radius: 999px; color: white; background: #423b3655; font-size: 11px; backdrop-filter: blur(6px); }
.project-content { padding: 20px; }
.project-content h2 { margin-bottom: 7px; font-size: 20px; }
.project-content p { min-height: 44px; margin-bottom: 18px; color: var(--muted); line-height: 1.55; font-size: 13px; }
.project-meta { display: flex; gap: 8px; align-items: center; color: #807870; font-size: 10px; }
.project-meta span { padding: 4px 7px; border-radius: 7px; background: #f2ece4; }
.project-meta time { margin-left: auto; }
.empty-projects { padding: 80px; }
.empty-projects p { color: var(--muted); }
.empty-illustration { margin-bottom: 18px; font-size: 34px; letter-spacing: .25em; }
.modal-backdrop { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center; background: rgb(50 40 33 / 35%); backdrop-filter: blur(5px); }
.create-modal { width: 520px; padding: 28px; display: grid; gap: 19px; box-shadow: 0 30px 90px #382b2255; }
.modal-head { display: flex; justify-content: space-between; align-items: start; }
.modal-head h2 { margin-bottom: 0; }
.modal-close { border: 0; background: transparent; color: #8a8179; font-size: 28px; cursor: pointer; }
.modal-submit { width: 100%; }
@media (max-width: 1240px) { .project-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
