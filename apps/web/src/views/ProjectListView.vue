<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "../api/client";
import type {
  ProjectCollectionDto,
  ProjectLibraryBatchAction,
  ProjectLibraryFacetsDto,
  ProjectLibraryGroupMode,
  ProjectLibraryItemDto,
  ProjectLibraryLayout,
  ProjectLibraryQuery,
  ProjectLibrarySort,
  ProjectStage,
  ProjectSystemView,
} from "../api/types";
import { groupLibraryItems, mergeLibraryItems } from "../projectLibrary";

const router = useRouter();
const route = useRoute();

const emptyFacets: ProjectLibraryFacetsDto = {
  systemViews: {
    all: 0,
    recent: 0,
    in_progress: 0,
    needs_attention: 0,
    completed: 0,
    pinned: 0,
    archived: 0,
  },
  stages: { story: 0, assets: 0, storyboard: 0, generation: 0, editing: 0, completed: 0 },
  collections: [],
  tags: [],
};

const systemViews: Array<{ id: ProjectSystemView; label: string }> = [
  { id: "all", label: "全部项目" },
  { id: "recent", label: "最近更新" },
  { id: "in_progress", label: "创作中" },
  { id: "needs_attention", label: "待处理" },
  { id: "completed", label: "已完成" },
  { id: "pinned", label: "已固定" },
  { id: "archived", label: "已归档" },
];

const stageLabels: Record<ProjectStage, string> = {
  story: "故事",
  assets: "角色与画风",
  storyboard: "分镜",
  generation: "生成",
  editing: "剪辑",
  completed: "已完成",
};

const items = ref<ProjectLibraryItemDto[]>([]);
const collections = ref<ProjectCollectionDto[]>([]);
const facets = ref<ProjectLibraryFacetsDto>(emptyFacets);
const total = ref(0);
const nextCursor = ref<string | null>(null);
const loading = ref(true);
const loadingMore = ref(false);
const error = ref("");
const selectedIds = ref(new Set<string>());

const searchInput = ref(readStringQuery("q"));
const searchQuery = ref(searchInput.value);
const systemView = ref<ProjectSystemView>(readSystemView());
const collectionFilter = ref(readStringQuery("collection"));
const selectedTags = ref(readArrayQuery("tags"));
const stageFilter = ref<ProjectStage | "">(readStage());
const dateFrom = ref(readStringQuery("dateFrom"));
const dateTo = ref(readStringQuery("dateTo"));
const sort = ref<ProjectLibrarySort>(readSort());
const groupMode = ref<ProjectLibraryGroupMode>(
  readPreference("catflow.library.group", "date", ["date", "collection", "none"]),
);
const layout = ref<ProjectLibraryLayout>(
  readPreference("catflow.library.layout", "grid", ["grid", "list"]),
);

const showCreate = ref(false);
const creating = ref(false);
const draft = reactive({ title: "", theme: "", targetDurationSeconds: 12 });
const showCollectionCreate = ref(false);
const creatingCollection = ref(false);
const collectionDraft = reactive<{ name: string; colorKey: ProjectCollectionDto["colorKey"] }>({
  name: "",
  colorKey: "sage",
});
const batchCollectionId = ref("");
let searchTimer: ReturnType<typeof setTimeout> | undefined;

const groups = computed(() => groupLibraryItems(items.value, groupMode.value));
const selectedCount = computed(() => selectedIds.value.size);
const allLoadedSelected = computed(
  () => items.value.length > 0 && items.value.every((item) => selectedIds.value.has(item.id)),
);
const unassignedCount = computed(() =>
  Math.max(
    0,
    facets.value.systemViews.all -
      facets.value.collections.reduce((count, collection) => count + collection.count, 0),
  ),
);

function readStringQuery(name: string): string {
  const value = route.query[name];
  return typeof value === "string" ? value : "";
}

function readArrayQuery(name: string): string[] {
  const value = route.query[name];
  if (Array.isArray(value)) {
    return value.filter((entry): entry is string => typeof entry === "string");
  }
  return typeof value === "string" && value ? [value] : [];
}

function readSystemView(): ProjectSystemView {
  const value = readStringQuery("view") as ProjectSystemView;
  return systemViews.some((candidate) => candidate.id === value) ? value : "all";
}

function readStage(): ProjectStage | "" {
  const value = readStringQuery("stage") as ProjectStage;
  return value in stageLabels ? value : "";
}

function readSort(): ProjectLibrarySort {
  const value = readStringQuery("sort") as ProjectLibrarySort;
  return ["activity", "created", "title", "stage"].includes(value) ? value : "activity";
}

function readPreference<T extends string>(key: string, fallback: T, allowed: T[]): T {
  const stored = window.localStorage.getItem(key) as T | null;
  return stored !== null && allowed.includes(stored) ? stored : fallback;
}

function nextDayIso(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  date.setDate(date.getDate() + 1);
  return date.toISOString();
}

function libraryQuery(cursor?: string | null): ProjectLibraryQuery {
  return {
    q: searchQuery.value || undefined,
    systemView: systemView.value,
    collectionId:
      collectionFilter.value && collectionFilter.value !== "ungrouped"
        ? collectionFilter.value
        : undefined,
    unassigned: collectionFilter.value === "ungrouped" || undefined,
    tags: selectedTags.value,
    stage: stageFilter.value || undefined,
    dateFrom: dateFrom.value ? new Date(`${dateFrom.value}T00:00:00`).toISOString() : undefined,
    dateTo: dateTo.value ? nextDayIso(dateTo.value) : undefined,
    sort: sort.value,
    cursor: cursor || undefined,
    limit: 36,
  };
}

function userError(reason: unknown, fallback: string): string {
  if (!(reason instanceof Error)) return fallback;
  if (reason.message.includes("active jobs") || reason.message.includes("running")) {
    return "所选项目仍在生成。请等待任务结束，或先进入项目取消任务。";
  }
  return reason.message || fallback;
}

async function loadLibrary(options: { append?: boolean } = {}) {
  const append = options.append === true;
  if (append) loadingMore.value = true;
  else loading.value = true;
  error.value = "";
  try {
    const page = await api.projectLibrary(libraryQuery(append ? nextCursor.value : null));
    items.value = append ? mergeLibraryItems(items.value, page.items) : page.items;
    nextCursor.value = page.nextCursor ?? null;
    total.value = page.total;
    facets.value = page.facets;
    if (!append) selectedIds.value = new Set();
  } catch (reason) {
    error.value = userError(reason, "项目库暂时无法读取，请重新加载。");
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
}

async function loadCollections() {
  try {
    collections.value = await api.projectCollections();
  } catch (reason) {
    error.value = userError(reason, "收藏夹暂时无法读取。");
  }
}

function syncUrl() {
  const query: Record<string, string | string[]> = {};
  if (searchQuery.value) query.q = searchQuery.value;
  if (systemView.value !== "all") query.view = systemView.value;
  if (collectionFilter.value) query.collection = collectionFilter.value;
  if (selectedTags.value.length) query.tags = selectedTags.value;
  if (stageFilter.value) query.stage = stageFilter.value;
  if (dateFrom.value) query.dateFrom = dateFrom.value;
  if (dateTo.value) query.dateTo = dateTo.value;
  if (sort.value !== "activity") query.sort = sort.value;
  void router.replace({ query });
}

function reloadFromFilters() {
  syncUrl();
  void loadLibrary();
}

function chooseSystemView(id: ProjectSystemView) {
  systemView.value = id;
  collectionFilter.value = "";
}

function chooseCollection(id: string) {
  collectionFilter.value = id;
  systemView.value = "all";
}

function toggleTag(name: string) {
  selectedTags.value = selectedTags.value.includes(name)
    ? selectedTags.value.filter((tag) => tag !== name)
    : [...selectedTags.value, name];
}

function clearFilters() {
  searchInput.value = "";
  searchQuery.value = "";
  systemView.value = "all";
  collectionFilter.value = "";
  selectedTags.value = [];
  stageFilter.value = "";
  dateFrom.value = "";
  dateTo.value = "";
  sort.value = "activity";
}

function toggleSelected(projectId: string, checked: boolean) {
  const next = new Set(selectedIds.value);
  if (checked) next.add(projectId);
  else next.delete(projectId);
  selectedIds.value = next;
}

function toggleAllLoaded() {
  selectedIds.value = allLoadedSelected.value
    ? new Set()
    : new Set(items.value.map((item) => item.id));
}

async function performBatch(action: ProjectLibraryBatchAction) {
  error.value = "";
  try {
    await api.projectLibraryAction(action);
    selectedIds.value = new Set();
    await loadLibrary();
  } catch (reason) {
    error.value = userError(reason, "批量操作没有完成，请重试。");
  }
}

function simpleBatch(action: "pin" | "unpin" | "archive" | "restore") {
  if (!selectedCount.value) return;
  void performBatch({ action, projectIds: [...selectedIds.value] });
}

function moveSelected() {
  if (!selectedCount.value) return;
  void performBatch({
    action: "move_collection",
    projectIds: [...selectedIds.value],
    collectionId: batchCollectionId.value || null,
  });
}

function changeSelectedTags(action: "add_tags" | "remove_tags") {
  if (!selectedCount.value) return;
  const message =
    action === "add_tags"
      ? "输入要添加的标签，多个标签用逗号分隔"
      : "输入要移除的标签，多个标签用逗号分隔";
  const response = window.prompt(message);
  const tags = response?.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean) ?? [];
  if (tags.length) void performBatch({ action, projectIds: [...selectedIds.value], tags });
}

async function createProject() {
  creating.value = true;
  error.value = "";
  try {
    const project = await api.createProject(draft);
    await router.push(`/projects/${project.id}/planner`);
  } catch (reason) {
    error.value = userError(reason, "项目没有创建，请重试。");
  } finally {
    creating.value = false;
  }
}

async function createCollection() {
  creatingCollection.value = true;
  error.value = "";
  try {
    await api.createProjectCollection(collectionDraft);
    collectionDraft.name = "";
    showCollectionCreate.value = false;
    await Promise.all([loadCollections(), loadLibrary()]);
  } catch (reason) {
    error.value = userError(reason, "收藏夹没有创建，请检查名称。");
  } finally {
    creatingCollection.value = false;
  }
}

async function archiveCollection(collection: ProjectCollectionDto) {
  if (!window.confirm(`移除收藏夹“${collection.name}”？其中的项目会回到“未分组”。`)) return;
  try {
    await api.archiveProjectCollection(collection.id);
    if (collectionFilter.value === collection.id) collectionFilter.value = "";
    await Promise.all([loadCollections(), loadLibrary()]);
  } catch (reason) {
    error.value = userError(reason, "收藏夹没有移除，请重试。");
  }
}

function formatActivity(value: string): string {
  const date = new Date(value);
  const elapsedMinutes = Math.floor((Date.now() - date.getTime()) / 60_000);
  if (elapsedMinutes >= 0 && elapsedMinutes < 60) {
    return elapsedMinutes < 1 ? "刚刚" : `${elapsedMinutes} 分钟前`;
  }
  if (elapsedMinutes < 24 * 60) return `${Math.floor(elapsedMinutes / 60)} 小时前`;
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function attentionLabel(item: ProjectLibraryItemDto): string {
  if (item.attentionReasons.includes("submission_unknown")) return "提交状态待确认";
  if (item.attentionReasons.includes("generation_failed")) return "任务需要检查";
  if (item.stage === "assets" && item.attention === "needs_attention") {
    return "角色与画风待补全";
  }
  if (item.attentionReasons.includes("edit_candidate_ready")) return "修改结果待采用";
  if (item.attentionReasons.includes("video_candidate_ready")) return "视频待选择";
  if (item.attentionReasons.includes("storyboard_outdated")) return "分镜待更新";
  if (item.attention === "needs_attention") return "需要处理";
  if (item.attention === "running") return "正在处理";
  return stageLabels[item.stage];
}

function projectDestination(item: ProjectLibraryItemDto): string {
  if (item.attentionReasons.includes("edit_candidate_ready")) {
    return `/projects/${item.id}/delivery`;
  }
  if (
    item.attentionReasons.includes("video_candidate_ready") ||
    ((item.attentionReasons.includes("generation_failed") ||
      item.attentionReasons.includes("submission_unknown")) &&
      ["generation", "editing", "completed"].includes(item.stage))
  ) {
    return `/projects/${item.id}/generation`;
  }
  const destination = {
    story: "planner",
    assets: "assets",
    storyboard: "storyboard",
    generation: "generation",
    editing: "delivery",
    completed: "delivery",
  }[item.stage];
  return `/projects/${item.id}/${destination}`;
}

async function organizeSingle(
  project: ProjectLibraryItemDto,
  command: { pinned?: boolean; archived?: boolean },
) {
  error.value = "";
  try {
    await api.organizeProject(project.id, command);
    await loadLibrary();
  } catch (reason) {
    error.value = userError(reason, "项目整理没有完成，请重试。");
  }
}

function collectionCount(id: string): number {
  return facets.value.collections.find((entry) => entry.id === id)?.count ?? 0;
}

watch(searchInput, (value) => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    searchQuery.value = value.trim();
  }, 300);
});

watch(
  [searchQuery, systemView, collectionFilter, stageFilter, dateFrom, dateTo, sort, selectedTags],
  reloadFromFilters,
  { deep: true },
);
watch(groupMode, (value) => window.localStorage.setItem("catflow.library.group", value));
watch(layout, (value) => window.localStorage.setItem("catflow.library.layout", value));

onMounted(() => {
  const topic = readStringQuery("topic");
  if (topic) {
    draft.title = topic;
    draft.theme = topic;
    showCreate.value = true;
  }
  void Promise.all([loadCollections(), loadLibrary()]);
});

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer);
});
</script>

<template>
  <main class="page library-page">
    <section class="library-heading">
      <div><h1>项目库</h1><p>整理、查找并继续你的一人一猫生活短片。</p></div>
      <div class="library-create-actions">
        <button class="primary new-project" @click="showCreate = true">＋ 新建短片</button>
        <RouterLink class="secondary create-link" to="/series/new">新建系列</RouterLink>
        <RouterLink class="ghost create-link" to="/story-imports/new">导入故事</RouterLink>
      </div>
    </section>

    <p v-if="error" class="notice error library-error">
      {{ error }}
      <button class="retry-link" @click="loadLibrary()">重新加载</button>
    </p>

    <div class="library-shell">
      <aside class="library-sidebar card" aria-label="项目分类">
        <RouterLink class="series-library-link" to="/series">系列</RouterLink>
        <nav class="sidebar-section" aria-label="项目视图">
          <button
            v-for="view in systemViews"
            :key="view.id"
            :class="{ active: systemView === view.id && !collectionFilter }"
            @click="chooseSystemView(view.id)"
          ><span>{{ view.label }}</span><b>{{ facets.systemViews[view.id] ?? 0 }}</b></button>
        </nav>

        <section class="sidebar-section">
          <header><span>收藏夹</span><button aria-label="新建收藏夹" @click="showCollectionCreate = true">＋</button></header>
          <button :class="{ active: collectionFilter === 'ungrouped' }" @click="chooseCollection('ungrouped')">
            <span><i class="collection-dot sand" />未分组</span><b>{{ unassignedCount }}</b>
          </button>
          <div v-for="collection in collections" :key="collection.id" class="collection-row">
            <button :class="{ active: collectionFilter === collection.id }" @click="chooseCollection(collection.id)">
              <span><i class="collection-dot" :class="collection.colorKey" />{{ collection.name }}</span>
              <b>{{ collectionCount(collection.id) }}</b>
            </button>
            <button class="collection-remove" :aria-label="`移除收藏夹 ${collection.name}`" @click="archiveCollection(collection)">×</button>
          </div>
        </section>

        <section v-if="facets.tags.length" class="sidebar-section tag-filter">
          <header><span>主题标签</span></header>
          <button
            v-for="tag in facets.tags.slice(0, 16)"
            :key="tag.name"
            :class="{ active: selectedTags.includes(tag.name) }"
            @click="toggleTag(tag.name)"
          ><span># {{ tag.name }}</span><b>{{ tag.count }}</b></button>
        </section>
      </aside>

      <section class="library-content">
        <div class="library-toolbar card">
          <label class="search-box"><span aria-hidden="true">⌕</span><input v-model="searchInput" aria-label="搜索项目" placeholder="搜索名称、主题、标签或收藏夹" /></label>
          <select v-model="stageFilter" aria-label="按制作阶段筛选">
            <option value="">全部阶段</option>
            <option v-for="(label, value) in stageLabels" :key="value" :value="value">{{ label }}</option>
          </select>
          <select v-model="sort" aria-label="项目排序"><option value="activity">最近活动</option><option value="created">创建时间</option><option value="title">项目名称</option><option value="stage">制作阶段</option></select>
          <select v-model="groupMode" aria-label="项目分组"><option value="date">按日期分组</option><option value="collection">按收藏夹分组</option><option value="none">不分组</option></select>
          <div class="layout-switch" role="group" aria-label="布局方式">
            <button :class="{ active: layout === 'grid' }" aria-label="切换为网格" @click="layout = 'grid'">▦</button>
            <button :class="{ active: layout === 'list' }" aria-label="切换为列表" @click="layout = 'list'">☷</button>
          </div>
        </div>

        <details class="date-filter"><summary>按活动日期筛选</summary><label>从 <input v-model="dateFrom" type="date" /></label><label>至 <input v-model="dateTo" type="date" /></label></details>
        <div class="library-summary">
          <span>{{ total }} 个项目</span>
          <button v-if="searchQuery || collectionFilter || selectedTags.length || stageFilter || dateFrom || dateTo || systemView !== 'all'" @click="clearFilters">清除筛选</button>
          <label class="select-loaded"><input type="checkbox" :checked="allLoadedSelected" @change="toggleAllLoaded" />选择已加载项目</label>
        </div>

        <section v-if="loading" class="card empty">正在整理项目…</section>
        <section v-else-if="items.length === 0" class="card empty empty-library">
          <template v-if="facets.systemViews.all === 0 && systemView !== 'archived'">
            <div class="empty-illustration">☁︎　🐾　☀︎</div><h2>创建第一条生活短片</h2><p>从雨天擦爪、整理早餐，或一束窗边阳光开始。</p><button class="primary" @click="showCreate = true">新建短片</button>
          </template>
          <template v-else-if="systemView === 'archived'"><h2>还没有归档项目</h2><p>归档只会把项目移出默认列表，不会删除任务、媒体和历史版本。</p></template>
          <template v-else><h2>没有符合条件的项目</h2><p>换一个关键词，或清除当前筛选。</p><button class="secondary" @click="clearFilters">清除筛选</button></template>
        </section>

        <div v-else class="project-groups">
          <section v-for="group in groups" :key="group.key" class="project-group">
            <header v-if="group.label" class="group-heading"><h2>{{ group.label }}</h2><span>{{ group.items.length }}</span></header>
            <div v-if="layout === 'grid'" class="compact-grid">
              <article v-for="(project, index) in group.items" :key="project.id" class="project-card card" data-project-card>
                <label class="project-check"><input type="checkbox" :aria-label="`选择${project.title}`" :checked="selectedIds.has(project.id)" @change="toggleSelected(project.id, ($event.target as HTMLInputElement).checked)" /></label>
                <RouterLink class="project-link" :to="projectDestination(project)">
                  <div class="project-cover" :class="`cover-${(index % 6) + 1}`">
                    <img v-if="project.coverAssetId" :src="`/api/v1/assets/${project.coverAssetId}/content`" :alt="`${project.title}封面`" loading="lazy" />
                    <span v-else class="cover-mark">🐾</span><span class="duration">{{ project.targetDurationSeconds }} 秒</span>
                  </div>
                  <div class="project-body">
                    <div class="project-title-line"><h3>{{ project.title }}</h3><span v-if="project.pinned" aria-label="已固定">★</span></div>
                    <span v-if="project.series" class="series-chip">{{ project.series.seriesTitle }} · 第 {{ project.series.episodeOrder }} 集</span>
                    <p>{{ project.themeSummary }}</p>
                    <div class="project-context">
                      <span v-if="project.collection" class="collection-chip"><i class="collection-dot" :class="project.collection.colorKey" />{{ project.collection.name }}</span>
                      <span v-for="tag in project.tags.slice(0, 2)" :key="tag.normalizedName" class="tag-chip">#{{ tag.name }}</span>
                      <span v-if="project.tags.length > 2" class="tag-chip">+{{ project.tags.length - 2 }}</span>
                    </div>
                    <div class="project-status"><span :class="`attention-${project.attention}`">{{ attentionLabel(project) }}</span><time :datetime="project.lastActivityAt">{{ formatActivity(project.lastActivityAt) }}</time></div>
                  </div>
                </RouterLink>
              </article>
            </div>

            <div v-else class="management-list card">
              <div class="list-row list-head" aria-hidden="true"><span /><span>项目</span><span>收藏夹与标签</span><span>阶段</span><span>状态</span><span>最近活动</span><span>创建日期</span><span>更多</span></div>
              <article v-for="project in group.items" :key="project.id" class="list-row" data-project-card>
                <input type="checkbox" :aria-label="`选择${project.title}`" :checked="selectedIds.has(project.id)" @change="toggleSelected(project.id, ($event.target as HTMLInputElement).checked)" />
                <RouterLink class="list-project" :to="projectDestination(project)">
                  <img v-if="project.coverAssetId" :src="`/api/v1/assets/${project.coverAssetId}/content`" alt="" loading="lazy" /><span v-else class="list-cover">🐾</span>
                  <span><b>{{ project.title }}</b><small>{{ project.themeSummary }}</small></span>
                </RouterLink>
                <span class="list-tags"><b>{{ project.series ? `${project.series.seriesTitle} · 第 ${project.series.episodeOrder} 集` : project.collection?.name ?? "未分组" }}</b><small>{{ project.tags.map((tag) => `#${tag.name}`).join(" ") || "暂无标签" }}</small></span>
                <span>{{ stageLabels[project.stage] }}</span><span :class="`attention-${project.attention}`">{{ attentionLabel(project) }}</span>
                <time :datetime="project.lastActivityAt">{{ formatActivity(project.lastActivityAt) }}</time><time :datetime="project.createdAt">{{ new Date(project.createdAt).toLocaleDateString("zh-CN") }}</time>
                <details class="row-actions">
                  <summary :aria-label="`${project.title}的更多操作`">•••</summary>
                  <div>
                    <button @click="organizeSingle(project, { pinned: !project.pinned })">{{ project.pinned ? "取消固定" : "固定" }}</button>
                    <button @click="organizeSingle(project, { archived: !project.archived })">{{ project.archived ? "恢复" : "归档" }}</button>
                  </div>
                </details>
              </article>
            </div>
          </section>
        </div>

        <div v-if="nextCursor" class="load-more-row"><button class="secondary load-more" :disabled="loadingMore" @click="loadLibrary({ append: true })">{{ loadingMore ? "正在加载" : "加载更多" }}</button></div>
      </section>
    </div>

    <section v-if="selectedCount" class="batch-bar" aria-label="批量操作">
      <strong>已选择 {{ selectedCount }} 个项目</strong>
      <select v-model="batchCollectionId" aria-label="目标收藏夹"><option value="">未分组</option><option v-for="collection in collections" :key="collection.id" :value="collection.id">{{ collection.name }}</option></select>
      <button @click="moveSelected">移动</button><button @click="changeSelectedTags('add_tags')">添加标签</button><button @click="changeSelectedTags('remove_tags')">移除标签</button><button data-action="pin" @click="simpleBatch('pin')">固定</button><button @click="simpleBatch('unpin')">取消固定</button><button v-if="systemView !== 'archived'" class="archive-action" @click="simpleBatch('archive')">归档</button><button v-else @click="simpleBatch('restore')">恢复</button>
    </section>

    <div v-if="showCreate" class="modal-backdrop" @click.self="showCreate = false">
      <form class="create-modal card" @submit.prevent="createProject">
        <div class="modal-head"><div><h2>新建生活短片</h2><p>先写下一个很小的日常。</p></div><button type="button" class="modal-close" aria-label="关闭" @click="showCreate = false">×</button></div>
        <div class="field"><label for="project-title">短片名称</label><input id="project-title" v-model="draft.title" required maxlength="160" placeholder="雨天擦爪" /></div>
        <div class="field"><label for="project-theme">最初的生活灵感</label><textarea id="project-theme" v-model="draft.theme" required placeholder="孩子替刚回家的猫咪擦干湿爪…" /></div>
        <div class="field"><label for="project-duration">目标时长：{{ draft.targetDurationSeconds }} 秒</label><input id="project-duration" v-model.number="draft.targetDurationSeconds" type="range" min="8" max="15" /></div>
        <p class="notice">固定 9:16。简短主题会同时作为第一个标签，之后可以在项目库中调整。</p>
        <button class="primary modal-submit" :disabled="creating || !draft.title || !draft.theme"><span v-if="creating" class="spinner" />{{ creating ? "正在创建" : "进入故事灵感" }}</button>
      </form>
    </div>

    <div v-if="showCollectionCreate" class="modal-backdrop" @click.self="showCollectionCreate = false">
      <form class="collection-modal card" @submit.prevent="createCollection">
        <div class="modal-head"><div><h2>新建收藏夹</h2><p>收藏夹用于长期归类，一个项目只能属于一个收藏夹。</p></div><button type="button" class="modal-close" aria-label="关闭" @click="showCollectionCreate = false">×</button></div>
        <div class="field"><label for="collection-name">收藏夹名称</label><input id="collection-name" v-model="collectionDraft.name" required maxlength="40" placeholder="居家日常" /></div>
        <div class="field"><label for="collection-color">色标</label><select id="collection-color" v-model="collectionDraft.colorKey"><option value="clay">陶土</option><option value="sage">鼠尾草</option><option value="sky">天空</option><option value="lavender">薰衣草</option><option value="sand">暖沙</option><option value="rose">柔粉</option></select></div>
        <button class="primary modal-submit" :disabled="creatingCollection || !collectionDraft.name.trim()">{{ creatingCollection ? "正在创建" : "创建收藏夹" }}</button>
      </form>
    </div>
  </main>
</template>

<style scoped>
.library-page { width: min(1880px, calc(100% - 44px)); }
.library-heading { display: flex; justify-content: space-between; align-items: end; margin-bottom: 22px; }
.library-heading h1 { margin-bottom: 5px; }.library-heading p { margin: 0; color: var(--muted); }.new-project { min-width: 132px; }
.library-create-actions { display: flex; gap: 8px; align-items: center; }.create-link { min-height: 40px; display: inline-flex; align-items: center; }
.library-error { display: flex; justify-content: space-between; align-items: center; }.retry-link { border: 0; color: inherit; background: transparent; font-weight: 700; cursor: pointer; text-decoration: underline; }
.library-shell { display: grid; grid-template-columns: 218px minmax(0, 1fr); gap: 18px; align-items: start; }
.library-sidebar { position: sticky; top: 90px; padding: 14px 10px; max-height: calc(100vh - 112px); overflow: auto; border-radius: 16px; }
.series-library-link { margin: 2px 0 8px; padding: 10px; display: block; border-radius: 10px; color: #5b6f5e; background: var(--sage-soft); font-weight: 700; }
.sidebar-section { display: grid; gap: 2px; padding: 7px 0 12px; border-bottom: 1px solid var(--line); }.sidebar-section:last-child { border-bottom: 0; }
.sidebar-section header { display: flex; justify-content: space-between; align-items: center; padding: 7px 10px; color: #8a8179; font-size: 11px; font-weight: 700; }.sidebar-section header button { border: 0; background: transparent; color: var(--accent-dark); font-size: 18px; cursor: pointer; }
.sidebar-section > button, .collection-row > button:first-child { min-width: 0; padding: 8px 10px; display: flex; justify-content: space-between; align-items: center; gap: 8px; border: 0; border-radius: 9px; background: transparent; color: #5f5852; cursor: pointer; text-align: left; }
.sidebar-section button span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.sidebar-section button b { color: #aaa098; font-size: 10px; font-weight: 600; }.sidebar-section button.active { color: #9e4f3d; background: #f7e8df; font-weight: 700; }
.collection-row { display: grid; grid-template-columns: minmax(0, 1fr) 26px; align-items: center; }.collection-remove { border: 0; background: transparent; color: #aaa098; cursor: pointer; opacity: 0; }.collection-row:hover .collection-remove, .collection-remove:focus-visible { opacity: 1; }
.collection-dot { width: 8px; height: 8px; margin-right: 7px; display: inline-block; border-radius: 50%; background: #c97b60; }.collection-dot.sage { background: #79927d; }.collection-dot.sky { background: #759cad; }.collection-dot.lavender { background: #948ca9; }.collection-dot.sand { background: #c4a77e; }.collection-dot.rose { background: #ba7f82; }.collection-dot.clay { background: #c97b60; }.tag-filter { max-height: 280px; overflow: auto; }
.library-content { min-width: 0; }.library-toolbar { padding: 10px; display: grid; grid-template-columns: minmax(260px, 1fr) auto auto auto auto; gap: 9px; border-radius: 16px; }.search-box { min-width: 0; display: flex; align-items: center; gap: 8px; padding: 0 12px; border: 1px solid #ded4ca; border-radius: 11px; background: white; }.search-box input { width: 100%; height: 38px; border: 0; outline: 0; background: transparent; }
.library-toolbar > select, .batch-bar select { min-height: 40px; padding: 0 30px 0 11px; border: 1px solid #ded4ca; border-radius: 11px; color: #5e5751; background: white; }.layout-switch { padding: 3px; display: flex; border: 1px solid #ded4ca; border-radius: 11px; background: white; }.layout-switch button { width: 34px; border: 0; border-radius: 8px; background: transparent; color: #867d75; cursor: pointer; }.layout-switch button.active { color: #9f513d; background: #f5e4db; }
.date-filter { margin: 10px 4px 0; color: var(--muted); font-size: 12px; }.date-filter summary { cursor: pointer; width: fit-content; }.date-filter label { margin: 10px 12px 0 0; display: inline-flex; gap: 6px; align-items: center; }.date-filter input { padding: 6px; border: 1px solid var(--line); border-radius: 8px; }.library-summary { min-height: 46px; display: flex; gap: 15px; align-items: center; color: #827970; font-size: 12px; }.library-summary button { border: 0; background: transparent; color: var(--accent-dark); cursor: pointer; }.select-loaded { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.project-groups { display: grid; gap: 25px; }.group-heading { margin: 2px 2px 10px; display: flex; align-items: baseline; gap: 9px; }.group-heading h2 { margin: 0; font: 600 16px Inter, sans-serif; }.group-heading span { color: var(--muted); font-size: 11px; }.compact-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.project-card { min-width: 0; position: relative; overflow: hidden; border-radius: 15px; transition: transform .16s, box-shadow .16s; }.project-card:hover { transform: translateY(-2px); box-shadow: var(--shadow); }.project-link { min-height: 152px; display: grid; grid-template-columns: 82px minmax(0, 1fr); }.project-check { position: absolute; z-index: 2; left: 7px; top: 7px; width: 25px; height: 25px; display: grid; place-items: center; border-radius: 8px; background: rgb(255 253 249 / 88%); box-shadow: 0 3px 10px #30241e26; }
.project-cover { min-height: 152px; position: relative; display: grid; place-items: center; overflow: hidden; background: #c9ac8a; }.project-cover img { width: 100%; height: 100%; object-fit: cover; }.cover-2 { background: #9aad9d; }.cover-3 { background: #c99791; }.cover-4 { background: #aaa6bb; }.cover-5 { background: #9daeb7; }.cover-6 { background: #c3aa83; }.cover-mark { font-size: 24px; opacity: .75; filter: grayscale(.3); }.duration { position: absolute; right: 6px; bottom: 6px; padding: 3px 6px; border-radius: 7px; color: white; background: #40362f99; font-size: 9px; }
.project-body { min-width: 0; padding: 12px 11px; display: flex; flex-direction: column; }.project-title-line { display: flex; align-items: start; gap: 4px; }.project-title-line h3 { min-width: 0; margin: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }.project-title-line span { color: #c77956; font-size: 11px; }.series-chip { width: fit-content; max-width: 100%; margin-top: 5px; padding: 3px 6px; overflow: hidden; border-radius: 6px; color: #526b58; background: var(--sage-soft); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }.project-body > p { min-height: 36px; margin: 6px 0 8px; display: -webkit-box; overflow: hidden; color: var(--muted); font-size: 11px; line-height: 1.55; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.project-context { min-height: 21px; display: flex; gap: 4px; overflow: hidden; white-space: nowrap; }.collection-chip, .tag-chip { max-width: 92px; padding: 3px 5px; overflow: hidden; border-radius: 6px; color: #756d65; background: #f2ede6; font-size: 9px; text-overflow: ellipsis; }.project-status { margin-top: auto; padding-top: 8px; display: flex; justify-content: space-between; gap: 5px; align-items: center; color: #8a8178; font-size: 10px; }.attention-needs_attention { color: #aa573c; font-weight: 700; }.attention-running { color: #4d7559; font-weight: 700; }.attention-normal { color: #756d65; }
.management-list { overflow: visible; border-radius: 14px; }.list-row { min-height: 59px; padding: 7px 11px; display: grid; grid-template-columns: 26px minmax(220px, 1.4fr) minmax(150px, 1fr) 85px 110px 90px 90px 44px; gap: 10px; align-items: center; border-bottom: 1px solid #eee7df; color: #645c55; background: var(--surface); font-size: 11px; }.list-row:first-child { border-radius: 14px 14px 0 0; }.list-row:last-child { border-bottom: 0; border-radius: 0 0 14px 14px; }.list-head { min-height: 38px; color: #999087; background: #faf7f2; font-size: 10px; }.list-project { min-width: 0; display: grid; grid-template-columns: 32px minmax(0, 1fr); gap: 8px; align-items: center; }.list-project img, .list-cover { width: 32px; height: 44px; object-fit: cover; border-radius: 6px; background: #e8ddd1; display: grid; place-items: center; }.list-project span, .list-tags { min-width: 0; display: grid; gap: 3px; }.list-project b, .list-project small, .list-tags b, .list-tags small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.list-project small, .list-tags small { color: #978e85; }
.row-actions { position: relative; }.row-actions summary { width: 32px; height: 30px; display: grid; place-items: center; border-radius: 8px; cursor: pointer; list-style: none; }.row-actions summary::-webkit-details-marker { display: none; }.row-actions[open] summary { background: #f3e9df; }.row-actions div { position: absolute; z-index: 8; right: 0; top: 34px; min-width: 105px; padding: 5px; display: grid; gap: 3px; border: 1px solid var(--line); border-radius: 10px; background: white; box-shadow: var(--shadow); }.row-actions button { padding: 7px 9px; border: 0; border-radius: 7px; background: transparent; color: #625a53; cursor: pointer; text-align: left; }.row-actions button:hover, .row-actions button:focus-visible { background: #f7eee7; }
.load-more-row { padding-top: 20px; display: grid; place-items: center; }.empty-library { padding: 70px; }.empty-library p { color: var(--muted); }.empty-illustration { margin-bottom: 15px; font-size: 30px; }.batch-bar { position: fixed; z-index: 50; left: 50%; bottom: 22px; transform: translateX(-50%); min-width: min(1060px, calc(100% - 56px)); padding: 10px 13px; display: flex; align-items: center; gap: 7px; border: 1px solid #dacfc4; border-radius: 16px; background: rgb(255 253 249 / 96%); box-shadow: 0 18px 55px #46352a33; backdrop-filter: blur(12px); }.batch-bar strong { margin-right: auto; font-size: 12px; }.batch-bar button { min-height: 36px; padding: 0 10px; border: 1px solid var(--line); border-radius: 9px; background: white; color: #655d56; cursor: pointer; }.batch-bar .archive-action { color: #a45442; }
.modal-backdrop { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center; background: rgb(50 40 33 / 35%); backdrop-filter: blur(5px); }.create-modal, .collection-modal { width: min(520px, calc(100% - 40px)); padding: 28px; display: grid; gap: 18px; box-shadow: 0 30px 90px #382b2255; }.collection-modal { width: min(460px, calc(100% - 40px)); }.modal-head { display: flex; justify-content: space-between; gap: 25px; align-items: start; }.modal-head h2 { margin: 0 0 5px; }.modal-head p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }.modal-close { border: 0; background: transparent; color: #8a8179; font-size: 28px; cursor: pointer; }.modal-submit { width: 100%; }
button:focus-visible, input:focus-visible, select:focus-visible, a:focus-visible { outline: 3px solid #d8765855; outline-offset: 2px; }
@media (max-width: 1600px) { .compact-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }.library-page { width: calc(100% - 38px); } }
@media (max-width: 1240px) { .compact-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }.library-toolbar { grid-template-columns: minmax(240px, 1fr) auto auto; }.layout-switch { grid-column: 3; }.list-row { grid-template-columns: 26px minmax(220px, 1.4fr) minmax(140px, 1fr) 80px 95px 44px; }.list-row > :nth-child(6), .list-row > :nth-child(7) { display: none; } }
</style>
