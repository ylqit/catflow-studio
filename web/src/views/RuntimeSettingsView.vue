<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, reactive, ref, watch } from "vue";
import type { DeepReadonly } from "vue";

import { ApiError, api } from "../api/client";
import type {
  RuntimeModelRole,
  RuntimeProductionConfig,
  RuntimeSettingsDto,
} from "../api/types";
import { refreshRuntimeStatus, useRuntimeStatus } from "../runtimeStatus";

type EditableRuntimeConfig = Omit<
  RuntimeProductionConfig,
  "revision" | "updatedAt" | "usingOverride"
>;

const runtimeStatus = useRuntimeStatus();
const document = ref<DeepReadonly<RuntimeSettingsDto> | null>(runtimeStatus.settings.value);
const saving = ref(false);
const form = reactive<EditableRuntimeConfig>({
  planningModel: "",
  imageModel: "",
  videoModel: "",
  reviewModel: "",
  videoResolution: "480p",
  semanticReviewEnabled: true,
});

const current = computed(() => document.value?.current ?? null);
const dirty = computed(() => {
  if (!current.value) return false;
  return form.planningModel !== current.value.planningModel
    || form.imageModel !== current.value.imageModel
    || form.videoModel !== current.value.videoModel
    || form.reviewModel !== current.value.reviewModel
    || form.videoResolution !== current.value.videoResolution
    || form.semanticReviewEnabled !== current.value.semanticReviewEnabled;
});

function catalog(role: RuntimeModelRole) {
  return document.value?.modelCatalog.filter((item) => item.role === role) ?? [];
}

function applyCurrent(source: DeepReadonly<RuntimeSettingsDto>) {
  document.value = source;
  Object.assign(form, {
    planningModel: source.current.planningModel,
    imageModel: source.current.imageModel,
    videoModel: source.current.videoModel,
    reviewModel: source.current.reviewModel,
    videoResolution: source.current.videoResolution,
    semanticReviewEnabled: source.current.semanticReviewEnabled,
  });
}

async function load() {
  try {
    applyCurrent(await api.runtimeSettings());
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

async function save() {
  if (!current.value || !dirty.value) return;
  saving.value = true;
  try {
    const updated = await api.updateRuntimeSettings(current.value.revision, { ...form });
    applyCurrent(updated);
    await refreshRuntimeStatus();
    ElMessage.success(`全局配置 revision ${updated.current.revision} 已立即应用于新任务`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      ElMessage.warning("其他页面已更新配置；当前表单已保留，请刷新后人工合并");
    } else {
      ElMessage.error(error instanceof Error ? error.message : String(error));
    }
  } finally {
    saving.value = false;
  }
}

async function restoreDefaults() {
  if (!current.value) return;
  await ElMessageBox.confirm(
    "将删除 Web 覆盖并恢复服务端 .env 的非敏感模型默认值。已提交任务不受影响。",
    "恢复部署默认",
    { type: "warning", confirmButtonText: "恢复默认" },
  );
  saving.value = true;
  try {
    const updated = await api.restoreRuntimeSettings(current.value.revision);
    applyCurrent(updated);
    await refreshRuntimeStatus();
    ElMessage.success(`已恢复部署默认，当前 revision ${updated.current.revision}`);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    saving.value = false;
  }
}

watch(
  () => runtimeStatus.settings.value,
  (value) => {
    if (value && !dirty.value) applyCurrent(value);
  },
  { immediate: true },
);

if (!document.value) void load();
</script>

<template>
  <main class="settings-page" v-loading="!document">
    <header>
      <div>
        <h1>系统设置</h1>
        <p>全局 Ark 模型配置；保存后只影响新提交任务，运行中任务继续使用其提交快照。</p>
      </div>
      <el-tag v-if="current" type="info">revision {{ current.revision }}</el-tag>
    </header>

    <template v-if="document">
      <section class="panel">
        <h2>Ark 运行状态</h2>
        <div class="status-grid">
          <div><span>数据库</span><b>{{ document.databaseReady ? "已就绪" : "迁移未就绪" }}</b></div>
          <div><span>Ark Key</span><b>{{ document.arkApiKeyConfigured ? "已在服务端配置" : "未配置" }}</b></div>
          <div><span>Ark 付费能力</span><b :class="{ bad: !document.arkReady }">{{ document.arkReady ? "可用" : "不可用" }}</b></div>
          <div><span>FFmpeg</span><b>{{ document.ffmpegAvailable ? "可用" : "不可用" }}</b></div>
          <div><span>FFprobe</span><b>{{ document.ffprobeAvailable ? "可用" : "不可用" }}</b></div>
          <div><span>视频生成 / 本地成片</span><b>{{ document.videoGenerationReady ? "视频可生成" : "视频条件不足" }} / {{ document.localCompositionReady ? "成片可用" : "成片不可用" }}</b></div>
        </div>
        <el-alert
          v-if="document.diagnostics.configurationIssues.length"
          type="warning"
          :closable="false"
          :title="document.diagnostics.configurationIssues.join('；')"
        />
        <p class="secret-note">API Key、数据库密码和完整连接信息永不返回浏览器。</p>
      </section>

      <section class="panel">
        <h2>全局模型</h2>
        <div class="form-grid">
          <label>
            <span>剧情、诊断与分镜规划</span>
            <el-select v-model="form.planningModel">
              <el-option v-for="item in catalog('planning')" :key="item.id" :label="item.displayName" :value="item.id" />
            </el-select>
          </label>
          <label>
            <span>图片与开场锚点</span>
            <el-select v-model="form.imageModel">
              <el-option v-for="item in catalog('image')" :key="item.id" :label="item.displayName" :value="item.id" />
            </el-select>
          </label>
          <label>
            <span>视频片段</span>
            <el-select v-model="form.videoModel">
              <el-option v-for="item in catalog('video')" :key="item.id" :label="item.displayName" :value="item.id" />
            </el-select>
          </label>
          <label>
            <span>视频语义审稿</span>
            <el-select v-model="form.reviewModel">
              <el-option v-for="item in catalog('review')" :key="item.id" :label="item.displayName" :value="item.id" />
            </el-select>
          </label>
        </div>
        <small>模型 ID 由服务端白名单管理，Web 不接受任意文本模型。</small>
      </section>

      <section class="panel">
        <h2>视频输出</h2>
        <div class="output-row">
          <label>
            <span>全局分辨率</span>
            <el-radio-group v-model="form.videoResolution">
              <el-radio-button value="480p">480p</el-radio-button>
              <el-radio-button value="720p">720p</el-radio-button>
            </el-radio-group>
          </label>
          <label class="switch-row">
            <el-switch v-model="form.semanticReviewEnabled" />
            <span>视频完成后执行语义审稿（会使用审稿模型）</span>
          </label>
        </div>
      </section>

      <section class="panel expert">
        <h2>专家诊断（只读）</h2>
        <dl>
          <dt>Provider</dt><dd>{{ document.diagnostics.provider }}</dd>
          <dt>Endpoint 档案</dt><dd>{{ document.diagnostics.arkBaseUrlProfile }}</dd>
          <dt>导演 / 审稿超时</dt><dd>{{ document.diagnostics.directorRequestTimeoutSeconds }}s / {{ document.diagnostics.reviewRequestTimeoutSeconds }}s</dd>
          <dt>视频轮询 / 总超时</dt><dd>{{ document.diagnostics.pollIntervalSeconds }}s / {{ document.diagnostics.taskTimeoutSeconds }}s</dd>
          <dt>工作目录</dt><dd>{{ document.diagnostics.workRoot }}</dd>
          <dt>资产目录</dt><dd>{{ document.diagnostics.assetRoot }}</dd>
        </dl>
      </section>
    </template>

    <footer v-if="document" class="action-bar">
      <div>
        <b>revision {{ document.current.revision }}</b>
        <span>{{ document.current.updatedAt ? new Date(document.current.updatedAt).toLocaleString() : "尚无 Web 覆盖" }}</span>
      </div>
      <el-button :disabled="!dirty || saving" @click="applyCurrent(document)">放弃修改</el-button>
      <el-button :loading="saving" @click="restoreDefaults">恢复部署默认</el-button>
      <el-button type="primary" :disabled="!dirty" :loading="saving" @click="save">保存并立即应用</el-button>
    </footer>
  </main>
</template>

<style scoped>
.settings-page { min-height: 100%; padding: 28px 32px 104px; color: #dce6f4; background: #0d1016; }
header, .action-bar, .output-row { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
h1 { margin: 0 0 6px; font-size: 28px; } h2 { margin: 0 0 18px; font-size: 17px; } p { color: #8f9bb0; }
.panel { max-width: 1080px; margin: 0 auto 18px; padding: 22px; border: 1px solid #2b3443; border-radius: 12px; background: #121722; }
.status-grid, .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.status-grid div { display: grid; gap: 6px; padding: 14px; border: 1px solid #303b4d; border-radius: 8px; background: #0e131c; }
.status-grid span, label > span, .action-bar span { color: #8f9bb0; font-size: 13px; }.status-grid .bad { color: #f7a3a3; }
.form-grid label, .output-row label { display: grid; gap: 8px; min-width: 0; }.form-grid .el-select { width: 100%; }
.secret-note { margin-bottom: 0; font-size: 12px; }.switch-row { display: flex !important; grid-auto-flow: column; align-items: center; }
dl { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 10px 18px; margin: 0; }dt { color: #8f9bb0; }dd { margin: 0; overflow-wrap: anywhere; }
.action-bar { position: fixed; z-index: 30; right: 0; bottom: 0; left: 190px; padding: 14px 28px; border-top: 1px solid #2b3443; background: rgba(13, 16, 22, .96); backdrop-filter: blur(10px); }
.action-bar div { display: grid; gap: 3px; margin-right: auto; }
@media (max-width: 760px) { .status-grid, .form-grid { grid-template-columns: 1fr; }.settings-page { padding: 18px 16px 130px; }.action-bar { left: 0; flex-wrap: wrap; }.output-row { align-items: stretch; flex-direction: column; } }
</style>
