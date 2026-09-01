import { afterEach, describe, expect, it } from "vitest";

import { router } from "./router";
import ProjectListView from "./views/ProjectListView.vue";
import ProjectWorkspaceView from "./views/ProjectWorkspaceView.vue";

describe("project workspace routes", () => {
  afterEach(async () => { await router.replace("/projects"); });

  it("sends the application entry to the project list", async () => {
    await router.push("/");
    expect(router.currentRoute.value).toMatchObject({ fullPath: "/projects", name: "projects" });
    expect(router.currentRoute.value.matched.at(-1)?.components?.default).toBe(ProjectListView);
  });

  it.each([
    ["project-script", "/projects/project-42/script", "script"],
    ["project-assets", "/projects/project-42/assets", "assets"],
    ["project-production", "/projects/project-42/production", "production"],
  ])("registers the canonical %s workspace", (name, path, workspaceModule) => {
    const route = router.resolve({ name, params: { projectId: "project-42" }, query: { item: "artifact-1" } });
    expect(route.path).toBe(path);
    expect(route.query).toEqual({ item: "artifact-1" });
    expect(route.meta.workspaceModule).toBe(workspaceModule);
    expect(route.matched.at(-1)?.components?.default).toBe(ProjectWorkspaceView);
  });

  it.each([
    ["/canvas", "/projects"],
    ["/canvas/project-42?stage=story", "/projects/project-42/script"],
    ["/canvas/project-42?zone=canon", "/projects/project-42/assets"],
    ["/canvas/project-42?stage=shots", "/projects/project-42/production"],
    ["/canvas/project-42?stage=video", "/projects/project-42/production?workspace=video&tab=generate"],
    ["/studio?project=project-42", "/projects/project-42/production?workspace=video&tab=edit"],
    ["/studio/projects/project-42/shots/shot-7", "/projects/project-42/production?workspace=video&tab=edit&shot=shot-7"],
  ])("normalizes legacy %s once into %s", async (legacy, canonical) => {
    await router.push(legacy);
    expect(router.currentRoute.value.fullPath).toBe(canonical);
  });

  it("has no public system graph route", async () => {
    await router.push("/projects/project-42/director/system?node=system-node");
    expect(router.currentRoute.value.fullPath).toBe("/projects/project-42/production");
    expect(router.currentRoute.value.name).toBe("project-production");
  });
});
