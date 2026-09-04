import type { RouteRecordRaw } from "vue-router";
import { createRouter, createWebHistory } from "vue-router";

const ProjectListView = () => import("./views/ProjectListView.vue");
const SeriesListView = () => import("./views/SeriesListView.vue");
const SeriesCreateView = () => import("./views/SeriesCreateView.vue");
const SeriesWorkspaceView = () => import("./views/SeriesWorkspaceView.vue");
const StoryImportView = () => import("./views/StoryImportView.vue");
const WorkspaceView = () => import("./views/WorkspaceView.vue");
const RuntimeSettingsView = () => import("./views/RuntimeSettingsView.vue");

export const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/projects" },
  { path: "/projects", name: "projects", component: ProjectListView },
  { path: "/series", name: "series", component: SeriesListView },
  { path: "/series/new", name: "series-create", component: SeriesCreateView },
  {
    path: "/series/:seriesId",
    name: "series-workspace",
    component: SeriesWorkspaceView,
  },
  { path: "/story-imports/new", name: "story-import", component: StoryImportView },
  {
    path: "/story-imports/:documentId",
    name: "story-import-detail",
    component: StoryImportView,
  },
  {
    path: "/projects/:projectId/planner",
    name: "project-planner",
    component: WorkspaceView,
    props: { step: "planner" },
  },
  {
    path: "/projects/:projectId/assets",
    name: "project-assets",
    component: WorkspaceView,
    props: { step: "assets" },
  },
  {
    path: "/projects/:projectId/storyboard",
    name: "project-storyboard",
    component: WorkspaceView,
    props: { step: "storyboard" },
  },
  {
    path: "/projects/:projectId/generation",
    name: "project-generation",
    component: WorkspaceView,
    props: { step: "generation" },
  },
  {
    path: "/projects/:projectId/delivery",
    name: "project-delivery",
    component: WorkspaceView,
    props: { step: "delivery" },
  },
  { path: "/settings", name: "settings", component: RuntimeSettingsView },
  { path: "/:pathMatch(.*)*", redirect: "/projects" },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
