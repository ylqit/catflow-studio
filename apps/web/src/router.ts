import type { RouteRecordRaw } from "vue-router";
import { createRouter, createWebHistory } from "vue-router";

const ProjectListView = () => import("./views/ProjectListView.vue");
const WorkspaceView = () => import("./views/WorkspaceView.vue");
const RuntimeSettingsView = () => import("./views/RuntimeSettingsView.vue");
const FirstValidationView = () => import("./views/FirstValidationView.vue");

export const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/projects" },
  { path: "/projects", name: "projects", component: ProjectListView },
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
  { path: "/validation/first-three", name: "first-validation", component: FirstValidationView },
  { path: "/:pathMatch(.*)*", redirect: "/projects" },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
