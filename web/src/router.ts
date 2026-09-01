import { createRouter, createWebHistory } from "vue-router";

import CanonView from "./views/CanonView.vue";
import ProjectListView from "./views/ProjectListView.vue";
import ProjectWorkspaceView from "./views/ProjectWorkspaceView.vue";
import RuntimeSettingsView from "./views/RuntimeSettingsView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/projects" },
    { path: "/projects", component: ProjectListView, name: "projects" },
    {
      path: "/projects/:projectId/script",
      component: ProjectWorkspaceView,
      name: "project-script",
      meta: { workspaceModule: "script" },
    },
    {
      path: "/projects/:projectId/assets",
      component: ProjectWorkspaceView,
      name: "project-assets",
      meta: { workspaceModule: "assets" },
    },
    {
      path: "/projects/:projectId/production",
      component: ProjectWorkspaceView,
      name: "project-production",
      meta: { workspaceModule: "production" },
    },
    { path: "/canvas", redirect: { name: "projects" } },
    {
      path: "/canvas/:projectId",
      redirect: (to) => {
        const projectId = String(to.params.projectId);
        const legacyTarget = String(to.query.stage ?? to.query.zone ?? "");
        if (["story", "script"].includes(legacyTarget)) {
          return { name: "project-script", params: { projectId }, query: {}, replace: true };
        }
        if (["canon", "cast", "references", "assets"].includes(legacyTarget)) {
          return { name: "project-assets", params: { projectId }, query: {}, replace: true };
        }
        if (["video", "delivery"].includes(legacyTarget)) {
          return {
            name: "project-production",
            params: { projectId },
            query: { workspace: "video", tab: legacyTarget === "delivery" ? "edit" : "generate" },
            replace: true,
          };
        }
        return { name: "project-production", params: { projectId }, query: {}, replace: true };
      },
    },
    {
      path: "/projects/:projectId/director/:moduleId",
      redirect: (to) => {
        const projectId = String(to.params.projectId);
        const moduleId = String(to.params.moduleId);
        if (moduleId === "script") {
          return { name: "project-script", params: { projectId }, query: {}, replace: true };
        }
        if (moduleId === "assets") {
          return { name: "project-assets", params: { projectId }, query: {}, replace: true };
        }
        if (["video", "delivery"].includes(moduleId)) {
          return {
            name: "project-production",
            params: { projectId },
            query: { workspace: "video", tab: moduleId === "delivery" ? "edit" : "generate" },
            replace: true,
          };
        }
        return { name: "project-production", params: { projectId }, query: {}, replace: true };
      },
    },
    {
      path: "/projects/:projectId/director/system",
      redirect: (to) => ({
        name: "project-production",
        params: { projectId: String(to.params.projectId) },
        query: {},
        replace: true,
      }),
    },
    {
      path: "/studio",
      redirect: (to) => {
        const projectId = typeof to.query.project === "string" ? to.query.project : "";
        if (!projectId) return { name: "projects", replace: true };
        return {
          name: "project-production",
          params: { projectId },
          query: { workspace: "video", tab: "edit" },
          replace: true,
        };
      },
    },
    {
      path: "/studio/projects/:projectId/shots/:shotId",
      redirect: (to) => ({
        name: "project-production",
        params: { projectId: String(to.params.projectId) },
        query: { workspace: "video", tab: "edit", shot: String(to.params.shotId) },
        replace: true,
      }),
    },
    { path: "/canon", component: CanonView },
    { path: "/settings", component: RuntimeSettingsView },
    { path: "/:pathMatch(.*)*", redirect: "/projects" },
  ],
});
