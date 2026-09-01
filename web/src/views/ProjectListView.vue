<script setup lang="ts">
import { Plus, Refresh, VideoPlay } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { api, canvasApi } from "../api/client";
import type { ProjectSummary, VisualPresetProfileDto } from "../api/types";

const router = useRouter();
const projects = ref<ProjectSummary[]>([]);
const presets = ref<VisualPresetProfileDto[]>([]);
const loading = ref(true);
const loadError = ref("");
const createOpen = ref(false);
const creating = ref(false);
const form = reactive({
  title: "",
  body: "",
  durationSeconds: 8,
  aspectRatio: "9:16" as "9:16" | "16:9" | "1:1",
  qualityTier: "quick" as "quick" | "balanced" | "premium",
  wardrobe: "",
  environment: "",
});

const canonV4 = computed(() => presets.value.find((preset) => preset.key === "healing_child_cat_style_board_v4"));
const styleBoard = computed(() => canonV4.value?.slots.find((slot) => slot.semanticKey === "style:healing_line_texture_v4" && slot.authority?.providerEligible));
const createBlocker = computed(() => {
  if (!canonV4.value?.ready) return "Canon v4 基础证据尚未齐备";
  if (!styleBoard.value?.assetId) return "净化画风板尚不可用";
  if (!form.title.trim()) return "请输入项目名称";
  if (!form.body.trim()) return "请输入故事主题或完整创作要求";
  return "";
});

async function loadProjects() {
  loading.value = true;
  loadError.value = "";
  const [projectResult, presetResult] = await Promise.allSettled([api.projects(), canvasApi.visualPresets()]);
  if (projectResult.status === "fulfilled") projects.value = projectResult.value;
  if (presetResult.status === "fulfilled") presets.value = presetResult.value;
  const failures = [
    ...(projectResult.status === "rejected" ? [`项目列表：${String(projectResult.reason)}`] : []),
    ...(presetResult.status === "rejected" ? [`Canon 预设：${String(presetResult.reason)}`] : []),
  ];
  loadError.value = failures.join("\n");
  loading.value = false;
}

function openProject(projectId: string) {
  void router.push({ name: "project-production", params: { projectId } });
}

async function createProject() {
  if (createBlocker.value || !canonV4.value || !styleBoard.value?.assetId || creating.value) return;
  creating.value = true;
  try {
    const constraints = [
      form.wardrobe.trim() ? `本集服装要求：${form.wardrobe.trim()}` : "",
      form.environment.trim() ? `环境要求：${form.environment.trim()}` : "",
      "固定同一名 8–9 岁短发儿童与同一只灰白虎斑猫；原创柔和数字插画；不复制任何外部角色或品牌。",
    ].filter(Boolean).join("\n");
    const created = await canvasApi.createChildCatProject({
      title: form.title.trim(),
      contentDate: new Date().toISOString().slice(0, 10),
      brief: { body: `${form.body.trim()}\n\n${constraints}`, durationSeconds: form.durationSeconds, aspectRatio: form.aspectRatio, qualityTier: form.qualityTier },
      childCanonProfileId: canonV4.value.canonProfileId,
      catCanonProfileId: canonV4.value.canonProfileId,
      styleBoardAssetId: styleBoard.value.assetId,
    });
    if (created.providerCallCount !== 0) throw new Error("项目创建不应产生 Provider 调用");
    createOpen.value = false;
    ElMessage.success("一人一猫项目已原子创建，未调用 Provider");
    await router.push({ name: "project-script", params: { projectId: created.projectId } });
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    creating.value = false;
  }
}

onMounted(loadProjects);
</script>

<template>
  <div class="projects-page">
    <header class="projects-header"><div><span>ONE CHILD · ONE CAT</span><h1>项目</h1><p>从剧本、角色资产到生产画布，围绕固定儿童、固定猫咪和统一画风完成每一集。</p></div><button type="button" @click="createOpen = true"><Plus />新建项目</button></header>
    <div v-if="loadError" class="load-warning" role="alert"><span>{{ loadError }}</span><button type="button" @click="loadProjects"><Refresh />重新加载</button></div>
    <div v-if="loading" class="project-loading" aria-busy="true">正在加载项目…</div>
    <section v-else class="project-grid">
      <article v-for="project in projects" :key="project.id" :data-testid="`project-${project.id}`" class="project-card">
        <div class="card-preview"><VideoPlay /></div>
        <div class="card-body"><span>{{ project.contentDate }}</span><h2>{{ project.title }}</h2><p>{{ project.status }} · 一人一猫原创短视频</p></div>
        <button class="open-project" type="button" @click="openProject(project.id)">打开项目</button>
      </article>
      <button class="new-project-card" type="button" @click="createOpen = true"><Plus /><b>新建一人一猫项目</b><span>创建项目不会触发付费 Provider</span></button>
    </section>

    <el-dialog v-model="createOpen" title="新建原创一人一猫项目" width="min(720px, calc(100vw - 28px))" destroy-on-close>
      <form class="create-form" @submit.prevent="createProject">
        <label><span>项目名称</span><input v-model="form.title" autocomplete="off" placeholder="例如：窗边的纸星星" /></label>
        <label class="brief"><span>故事主题或完整创作要求</span><textarea v-model="form.body" rows="7" placeholder="写清楚核心事件、情绪、动作节拍和不希望出现的内容。" /></label>
        <div class="form-row"><label><span>目标时长</span><select v-model.number="form.durationSeconds"><option :value="5">5 秒</option><option :value="8">8 秒</option><option :value="10">10 秒</option><option :value="15">15 秒</option></select></label><label><span>画幅</span><select v-model="form.aspectRatio"><option value="9:16">9:16 竖屏</option><option value="16:9">16:9 横屏</option><option value="1:1">1:1 方形</option></select></label><label><span>质量档</span><select v-model="form.qualityTier"><option value="quick">Quick</option><option value="balanced">Balanced</option><option value="premium">Premium</option></select></label></div>
        <div class="form-row two"><label><span>本集服装（可选）</span><input v-model="form.wardrobe" placeholder="只改变本集服装，不改变发型和身份" /></label><label><span>环境要求（可选）</span><input v-model="form.environment" placeholder="例如：清晨窗边、柔和晨光" /></label></div>
        <section class="canon-summary"><div><b>儿童 Canon</b><span>{{ canonV4?.title ?? '未加载' }}</span></div><div><b>猫咪 Canon</b><span>{{ canonV4?.title ?? '未加载' }}</span></div><div><b>画风板</b><span>{{ styleBoard?.title ?? '未加载' }}</span></div><p>项目、Brief、两个主体、Canon 引用和初始 Recipe 在同一事务中创建；Provider 调用为 0。</p></section>
      </form>
      <template #footer><el-button @click="createOpen = false">取消</el-button><el-button type="primary" :loading="creating" :disabled="Boolean(createBlocker)" :title="createBlocker" @click="createProject">创建并进入剧本</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.projects-page{height:100%;padding:clamp(22px,4vw,48px);overflow:auto;color:#e8eef7;background:radial-gradient(circle at 75% 0%,#172334 0,transparent 35%),#0d1218}.projects-header{max-width:1440px;margin:0 auto 28px;display:flex;align-items:flex-end;gap:24px}.projects-header>div{min-width:0}.projects-header span{color:#6f91b2;font-size:10px;font-weight:800;letter-spacing:.16em}.projects-header h1{margin:7px 0 5px;font-size:clamp(30px,4vw,46px)}.projects-header p{max-width:700px;margin:0;color:#8896a8;line-height:1.6}.projects-header>button{min-height:46px;margin-left:auto;padding:0 17px;display:flex;align-items:center;gap:8px;color:#eff8ff;background:#28648f;border:1px solid #4281ad;border-radius:10px;cursor:pointer}.projects-header svg{width:17px}.load-warning{max-width:1440px;margin:0 auto 16px;padding:11px 13px;display:flex;align-items:center;gap:10px;color:#ddaa86;background:#2a211c;border:1px solid #694d3c;border-radius:10px;white-space:pre-wrap}.load-warning button{min-height:44px;margin-left:auto;padding:0 12px;display:flex;align-items:center;gap:6px;color:#e7d6ca;background:#362a24;border:1px solid #765a4b;border-radius:8px}.project-loading{min-height:260px;display:grid;place-items:center;color:#7f8d9f}.project-grid{max-width:1440px;margin:0 auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px}.project-card,.new-project-card{min-height:270px;overflow:hidden;background:#151c24;border:1px solid #2f3a47;border-radius:14px}.project-card{display:grid;grid-template-rows:140px minmax(0,1fr) auto}.card-preview{display:grid;place-items:center;color:#4f6880;background:linear-gradient(135deg,#1b2b3b,#111820);border-bottom:1px solid #2a3541}.card-preview svg{width:42px}.card-body{padding:15px}.card-body span{color:#6e8196;font-size:9px;font-weight:800;letter-spacing:.1em}.card-body h2{margin:7px 0 5px;font-size:17px}.card-body p{margin:0;color:#8290a2;font-size:11px}.open-project{min-height:46px;margin:0 14px 14px;color:#dce9f5;background:#202c38;border:1px solid #3b5065;border-radius:9px;cursor:pointer}.open-project:hover,.open-project:focus-visible{color:#fff;background:#29415a;border-color:#4f7da7;outline:2px solid rgb(77 136 185 / 18%)}.new-project-card{padding:20px;display:grid;place-items:center;align-content:center;gap:9px;color:#8da0b4;background:rgb(19 27 35 / 70%);border-style:dashed;cursor:pointer}.new-project-card svg{width:30px}.new-project-card b{color:#cad6e2}.new-project-card span{font-size:10px}.create-form{display:grid;gap:13px}.create-form label{display:grid;gap:6px;color:#8291a4;font-size:11px}.create-form input,.create-form textarea,.create-form select{box-sizing:border-box;width:100%;min-height:44px;padding:10px 11px;color:#edf3fa;background:#11171e;border:1px solid #354250;border-radius:8px;font:inherit;line-height:1.5}.create-form textarea{resize:vertical}.form-row{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.form-row.two{grid-template-columns:1fr 1fr}.canon-summary{padding:12px;display:grid;grid-template-columns:repeat(3,1fr);gap:8px;background:#151d25;border:1px solid #303c49;border-radius:10px}.canon-summary div{display:grid;gap:4px}.canon-summary b{color:#d8e3ee;font-size:10px}.canon-summary span{color:#7790a7;font-size:9px}.canon-summary p{grid-column:1/-1;margin:5px 0 0;color:#778697;font-size:10px;line-height:1.5}@media(max-width:720px){.projects-header{align-items:flex-start;flex-direction:column}.projects-header>button{margin-left:0}.form-row,.form-row.two,.canon-summary{grid-template-columns:1fr}.canon-summary p{grid-column:1}}
</style>
